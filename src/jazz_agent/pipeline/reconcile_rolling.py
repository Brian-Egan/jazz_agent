"""Reconcile one rolling DC playlist toward its desired track list (DC section 10, ADR-018).

Idempotent by construction (AGENTS.md invariant 4): the key ``(region, slot_key)`` is
deterministic, the desired list is a pure function of stored state, and a list identical to
the open stints issues no Spotify write.

Order of writes matters (invariant 9). When the reconciler removes tracks itself it writes
the database **first**: a crash between the two writes then leaves Spotify holding a track
the database already closed, never a track missing from Spotify that the database still calls
live, which is the signature of a user removal. ``sync_pending`` covers the mirror-image
window for additions (database opened, Spotify not yet written): while it is set the next run
re-sends the whole list, and removal detection must not read the playlist.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime

from jazz_agent.core.dc_groups import PlaylistDef
from jazz_agent.core.models import RollingPlaylist, RollingPlaylistEvent, RollingPlaylistTrack
from jazz_agent.ports.music import MusicService
from jazz_agent.ports.repository import RollingPlaylistRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DesiredTrack:
    spotify_track_id: str
    spotify_album_id: str
    spotify_artist_id: str
    track_name: str | None = None
    isrc: str | None = None
    show_id: int | None = None


@dataclass(slots=True)
class RollingOutcome:
    slot_key: str
    status: str = "unchanged"  # 'unchanged' | 'updated' | 'partial'
    tracks_added: int = 0
    tracks_closed: int = 0
    detail: str = ""


# Hook for DC section 11 (user-removal detection, #29). Runs first, before the desired list is
# computed, so a closure it records excludes the track from that very list.
RemovalDetector = Callable[[RollingPlaylist], None]


def reconcile_rolling(
    definition: PlaylistDef,
    playlist: RollingPlaylist,
    desired: Sequence[DesiredTrack],
    title: str,
    description: str,
    departure_reason: Callable[[str], str],
    music: MusicService,
    repo: RollingPlaylistRepo,
    now: datetime,
    detect_user_removals: RemovalDetector | None = None,
) -> RollingOutcome:
    """Raises only for failures before any state changed (creating the playlist); a Spotify
    failure after the database write is returned as ``partial`` so the run can report it and
    the next run converge."""
    assert playlist.id is not None
    outcome = RollingOutcome(slot_key=definition.slot_key)
    playlist_id = playlist.id

    if playlist.spotify_playlist_id is None:
        created = music.create_playlist(title, description, public=definition.is_public)
        spotify_id = created["id"]
        spotify_url = created.get("external_urls", {}).get("spotify", "")
        repo.link_spotify_playlist(playlist_id, spotify_id, spotify_url)
        playlist = replace(playlist, spotify_playlist_id=spotify_id, spotify_url=spotify_url)
        # Created with the desired title and description, so nothing is left to push.
        playlist = replace(playlist, title=title, description=description)
        repo.upsert_rolling_playlist(playlist)
        repo.record_event(RollingPlaylistEvent(playlist_id, "created", detail={"title": title}))
        outcome.status = "updated"
    elif detect_user_removals is not None:
        detect_user_removals(playlist)

    spotify_id = playlist.spotify_playlist_id
    assert spotify_id is not None

    excluded = repo.user_removed_track_ids()
    wanted = _dedupe([t for t in desired if t.spotify_track_id not in excluded])
    wanted_ids = [t.spotify_track_id for t in wanted]
    open_stints = repo.open_stints(playlist_id)
    open_ids = [s.spotify_track_id for s in open_stints]

    track_changed = open_ids != wanted_ids
    if track_changed:
        _write_database(outcome, playlist_id, open_stints, wanted, departure_reason, repo, now)
    logger.info(
        "DC playlist %s would hold %d tracks: %s",
        definition.slot_key,
        len(wanted),
        ", ".join(f"{t.track_name or t.spotify_track_id}" for t in wanted),
    )

    if track_changed or playlist.sync_pending:
        repo.set_sync_pending(playlist_id, True)
        try:
            music.replace_playlist_items(spotify_id, wanted_ids)
        except Exception as e:
            logger.exception("DC playlist %s: Spotify replace failed", definition.slot_key)
            outcome.status = "partial"
            outcome.detail = f"replace failed: {e}"
            return outcome  # stays pending; the next run re-sends the full list
        repo.set_sync_pending(playlist_id, False)
        if outcome.status == "unchanged":
            outcome.status = "updated"

    if (title, description) != (playlist.title, playlist.description):
        try:
            music.update_playlist_details(spotify_id, title, description)
        except Exception as e:
            logger.exception("DC playlist %s: Spotify details update failed", definition.slot_key)
            outcome.status = "partial"
            outcome.detail = (outcome.detail + "; " if outcome.detail else "") + f"details: {e}"
            return outcome
        repo.upsert_rolling_playlist(replace(playlist, title=title, description=description))
        if outcome.status == "unchanged":
            outcome.status = "updated"
    return outcome


def _dedupe(tracks: Sequence[DesiredTrack]) -> list[DesiredTrack]:
    seen: set[str] = set()
    unique: list[DesiredTrack] = []
    for track in tracks:
        if track.spotify_track_id not in seen:
            seen.add(track.spotify_track_id)
            unique.append(track)
    return unique


def _write_database(
    outcome: RollingOutcome,
    playlist_id: int,
    open_stints: Sequence[RollingPlaylistTrack],
    wanted: Sequence[DesiredTrack],
    departure_reason: Callable[[str], str],
    repo: RollingPlaylistRepo,
    now: datetime,
) -> None:
    position = {t.spotify_track_id: i for i, t in enumerate(wanted)}
    by_track = {s.spotify_track_id: s for s in open_stints}

    departing: dict[tuple[str, str], int] = {}
    for stint in open_stints:
        if stint.spotify_track_id in position:
            continue
        assert stint.id is not None
        reason = departure_reason(stint.spotify_artist_id)
        repo.close_stint(stint.id, reason, now)
        outcome.tracks_closed += 1
        key = (stint.spotify_artist_id, reason)
        departing[key] = departing.get(key, 0) + 1
    for (artist_id, reason), count in departing.items():
        repo.record_event(
            RollingPlaylistEvent(
                playlist_id,
                "tracks_removed",
                spotify_artist_id=artist_id,
                reason=reason,
                detail={"track_count": count},
            )
        )

    arriving: dict[str, int] = {}
    for index, track in enumerate(wanted):
        existing = by_track.get(track.spotify_track_id)
        if existing is not None:
            assert existing.id is not None
            if existing.position != index:
                repo.set_stint_position(existing.id, index)
            continue
        repo.open_stint(
            RollingPlaylistTrack(
                rolling_playlist_id=playlist_id,
                spotify_track_id=track.spotify_track_id,
                spotify_album_id=track.spotify_album_id,
                spotify_artist_id=track.spotify_artist_id,
                position=index,
                isrc=track.isrc,
                track_name=track.track_name,
                show_id=track.show_id,
            )
        )
        outcome.tracks_added += 1
        arriving[track.spotify_artist_id] = arriving.get(track.spotify_artist_id, 0) + 1
    for artist_id, count in arriving.items():
        repo.record_event(
            RollingPlaylistEvent(
                playlist_id,
                "tracks_added",
                spotify_artist_id=artist_id,
                detail={"track_count": count},
            )
        )
