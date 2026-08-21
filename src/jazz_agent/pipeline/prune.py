"""Retention: unfollow playlists outside [-RETAIN_WEEKS_PAST, +HORIZON_WEEKS_AHEAD]
(ARCHITECTURE.md section 9, ADR-009).

Two things to get exactly right:

1. Spotify has no delete-playlist endpoint. unfollow_playlist removes it from
   the library; the playlist continues to exist at its URI. Nothing here
   deletes a playlist, because nothing can.
2. The log is never pruned. Removing a playlist from Spotify and deleting its
   history are different operations -- this module only ever calls
   mark_removed (stamps spotify_removed_at) and record_event, never a row
   delete.
"""

from __future__ import annotations

from datetime import date, datetime

from jazz_agent.core.models import PlaylistEvent
from jazz_agent.core.weeks import retention_window
from jazz_agent.ports.music import MusicService
from jazz_agent.ports.repository import PlaylistRepo


def prune_club(
    club_id: str,
    today: date,
    retain_weeks_past: int,
    horizon_weeks_ahead: int,
    music: MusicService,
    playlist_repo: PlaylistRepo,
    now: datetime,
) -> int:
    """Unfollow every playlist for this club outside the retention window that
    isn't already unfollowed. Returns the count newly unfollowed."""
    keep_from, keep_to = retention_window(today, retain_weeks_past, horizon_weeks_ahead)

    pruned = 0
    for playlist in playlist_repo.playlists_for_club(club_id):
        if keep_from <= playlist.week_start_date <= keep_to:
            continue
        if playlist.spotify_removed_at is not None:
            continue  # already unfollowed: a no-op, not re-unfollowed

        if playlist.spotify_playlist_id is not None:
            music.unfollow_playlist(playlist.spotify_playlist_id)

        assert playlist.id is not None
        playlist_repo.mark_removed(playlist.id, now)
        playlist_repo.record_event(
            PlaylistEvent(week_playlist_id=playlist.id, event_type="unfollowed")
        )
        pruned += 1

    return pruned
