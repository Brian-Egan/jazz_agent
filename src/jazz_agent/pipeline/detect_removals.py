"""Record a user's deletions from AlxJazz as removals, and LaPortas ones as dislikes
(DC section 11, ADR-022, AGENTS.md invariant 9).

Runs first in the AlxJazz reconcile. The playlist is read in full or not at all; nothing is
inferred from a partial read, from a playlist whose own write has not landed
(``sync_pending``), or from a mass disappearance (which alerts instead). Closing a stint with
``user`` is what makes the reconciler's step-2 exclusion keep the track off every playlist
forever, including when the artist is booked again.
"""

from __future__ import annotations

import logging
from datetime import datetime

from jazz_agent.core.models import Feedback, RollingPlaylist, RollingPlaylistEvent
from jazz_agent.core.removals import detect_removals
from jazz_agent.pipeline.reconcile_rolling import RemovalDetector
from jazz_agent.ports.music import MusicService
from jazz_agent.ports.notifier import Notifier
from jazz_agent.ports.repository import FeedbackRepo, RollingPlaylistRepo, ShowRepo

logger = logging.getLogger(__name__)

# Only AlxJazz is watched in this version (DC section 11).
WATCHED_GROUP = "alxjazz"
# Only removals of tracks from shows here are recorded as dislikes (ADR-022).
DISLIKE_CLUB_ID = "cb-laportas"


def make_removal_detector(
    music: MusicService,
    rolling_repo: RollingPlaylistRepo,
    feedback_repo: FeedbackRepo,
    show_repo: ShowRepo,
    notifier: Notifier,
    now: datetime,
) -> RemovalDetector:
    def detect(playlist: RollingPlaylist) -> None:
        if playlist.group_key != WATCHED_GROUP or playlist.spotify_playlist_id is None:
            return
        assert playlist.id is not None
        if playlist.sync_pending:
            # The database holds stints Spotify has not caught up with: their absence is our
            # own unfinished write, not a deletion.
            logger.info("%s: sync pending, skipping removal detection", playlist.slot_key)
            return
        try:
            live = music.get_playlist_items(playlist.spotify_playlist_id)
        except Exception:
            logger.exception(
                "%s: playlist read failed, skipping removal detection", playlist.slot_key
            )
            return

        stints = rolling_repo.open_stints(playlist.id)
        verdict = detect_removals(stints, live)
        if verdict.anomaly:
            logger.warning(
                "%s: %d of %d stints missing; recording nothing",
                playlist.slot_key,
                verdict.missing_count,
                verdict.live_stint_count,
            )
            try:
                notifier.alert(
                    f"{playlist.title}: {verdict.missing_count} of {verdict.live_stint_count} "
                    "tracks vanished at once -- nothing was recorded; check the Spotify account"
                )
            except Exception:
                logger.exception("ntfy alert failed")
            return

        removed_ids = {s.id for s in verdict.confirmed}
        remaining_by_artist: dict[str, int] = {}
        for stint in stints:
            if stint.id not in removed_ids:
                remaining_by_artist[stint.spotify_artist_id] = (
                    remaining_by_artist.get(stint.spotify_artist_id, 0) + 1
                )

        artist_dislikes: dict[str, int | None] = {}
        for stint in verdict.confirmed:
            assert stint.id is not None
            # Database first, and the closure before the feedback: a crash in between loses
            # one dislike rather than writing it twice on the next run.
            rolling_repo.close_stint(stint.id, "user", now)
            rolling_repo.record_event(
                RollingPlaylistEvent(
                    playlist.id,
                    "tracks_removed",
                    spotify_artist_id=stint.spotify_artist_id,
                    reason="user",
                    detail={"track_id": stint.spotify_track_id},
                )
            )
            if not _at_dislike_venue(stint.show_id, show_repo):
                continue
            feedback_repo.record_feedback(
                Feedback(
                    target_type="track",
                    target_id=stint.spotify_track_id,
                    sentiment="disliked",
                    show_id=stint.show_id,
                    rolling_playlist_id=playlist.id,
                    source="spotify",
                )
            )
            if remaining_by_artist.get(stint.spotify_artist_id, 0) == 0:
                artist_dislikes.setdefault(stint.spotify_artist_id, stint.show_id)
        for artist_id, show_id in artist_dislikes.items():
            feedback_repo.record_feedback(
                Feedback(
                    target_type="artist",
                    target_id=artist_id,
                    sentiment="disliked",
                    show_id=show_id,
                    rolling_playlist_id=playlist.id,
                    source="spotify",
                )
            )

    return detect


def _at_dislike_venue(show_id: int | None, show_repo: ShowRepo) -> bool:
    if show_id is None:
        return False
    show = show_repo.get_show(show_id)
    return show is not None and show.club_id == DISLIKE_CLUB_ID
