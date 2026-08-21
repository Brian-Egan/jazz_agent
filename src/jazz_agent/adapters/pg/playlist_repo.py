"""Postgres implementation of ports.repository.PlaylistRepo."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from jazz_agent.core.models import PlaylistEvent, PlaylistTrack, WeekPlaylist

_PLAYLIST_COLUMNS = (
    "id, club_id, week_start_date, spotify_playlist_id, spotify_url, title, "
    "description, spotify_removed_at"
)
_TRACK_COLUMNS = (
    "week_playlist_id, spotify_track_id, spotify_album_id, spotify_artist_id, "
    "track_name, position, show_id, removed_at"
)
_EVENT_COLUMNS = "week_playlist_id, event_type, spotify_artist_id, reason, detail"


def _row_to_playlist(row: dict[str, Any]) -> WeekPlaylist:
    return WeekPlaylist(
        id=row["id"],
        club_id=row["club_id"],
        week_start_date=row["week_start_date"],
        title=row["title"],
        description=row["description"] or "",
        spotify_playlist_id=row["spotify_playlist_id"],
        spotify_url=row["spotify_url"],
        spotify_removed_at=row["spotify_removed_at"],
    )


def _row_to_track(row: dict[str, Any]) -> PlaylistTrack:
    return PlaylistTrack(
        week_playlist_id=row["week_playlist_id"],
        spotify_track_id=row["spotify_track_id"],
        spotify_album_id=row["spotify_album_id"],
        spotify_artist_id=row["spotify_artist_id"],
        position=row["position"],
        track_name=row["track_name"],
        show_id=row["show_id"],
        removed_at=row["removed_at"],
    )


def _row_to_event(row: dict[str, Any]) -> PlaylistEvent:
    return PlaylistEvent(
        week_playlist_id=row["week_playlist_id"],
        event_type=row["event_type"],
        spotify_artist_id=row["spotify_artist_id"],
        reason=row["reason"],
        detail=row["detail"],
    )


class PgPlaylistRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert_week_playlist(self, playlist: WeekPlaylist) -> int:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO week_playlists (club_id, week_start_date, title, description)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT week_playlists_club_week_key
                DO UPDATE SET title = EXCLUDED.title, description = EXCLUDED.description,
                              updated_at = now()
                RETURNING id
                """,
                (playlist.club_id, playlist.week_start_date, playlist.title, playlist.description),
            )
            row = cur.fetchone()
            assert row is not None
            return row[0]  # type: ignore[no-any-return]

    def link_spotify_playlist(
        self, week_playlist_id: int, spotify_playlist_id: str, spotify_url: str
    ) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE week_playlists
                SET spotify_playlist_id = %s, spotify_url = %s, updated_at = now()
                WHERE id = %s
                """,
                (spotify_playlist_id, spotify_url, week_playlist_id),
            )

    def get_week_playlist(self, club_id: str, week_start_date: date) -> WeekPlaylist | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_PLAYLIST_COLUMNS} FROM week_playlists
                WHERE club_id = %s AND week_start_date = %s
                """,
                (club_id, week_start_date),
            )
            row = cur.fetchone()
            return _row_to_playlist(row) if row else None

    def add_tracks(self, week_playlist_id: int, tracks: Sequence[PlaylistTrack]) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            for track in tracks:
                cur.execute(
                    """
                    INSERT INTO playlist_tracks
                        (week_playlist_id, spotify_track_id, spotify_album_id,
                         spotify_artist_id, track_name, position, show_id)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (week_playlist_id, spotify_track_id) DO UPDATE SET
                        position = EXCLUDED.position, removed_at = NULL
                    """,
                    (
                        week_playlist_id,
                        track.spotify_track_id,
                        track.spotify_album_id,
                        track.spotify_artist_id,
                        track.track_name,
                        track.position,
                        track.show_id,
                    ),
                )

    def remove_track(self, week_playlist_id: int, spotify_track_id: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE playlist_tracks SET removed_at = now()
                WHERE week_playlist_id = %s AND spotify_track_id = %s
                """,
                (week_playlist_id, spotify_track_id),
            )

    def tracks_for(self, week_playlist_id: int) -> list[PlaylistTrack]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_TRACK_COLUMNS} FROM playlist_tracks
                WHERE week_playlist_id = %s
                ORDER BY position
                """,
                (week_playlist_id,),
            )
            return [_row_to_track(row) for row in cur.fetchall()]

    def record_event(self, event: PlaylistEvent) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO playlist_events
                    (week_playlist_id, event_type, spotify_artist_id, reason, detail)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    event.week_playlist_id,
                    event.event_type,
                    event.spotify_artist_id,
                    event.reason,
                    Jsonb(event.detail) if event.detail is not None else None,
                ),
            )

    def events_for(self, week_playlist_id: int) -> list[PlaylistEvent]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_EVENT_COLUMNS} FROM playlist_events
                WHERE week_playlist_id = %s
                ORDER BY created_at
                """,
                (week_playlist_id,),
            )
            return [_row_to_event(row) for row in cur.fetchall()]

    def mark_removed(self, week_playlist_id: int, removed_at: datetime) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE week_playlists SET spotify_removed_at = %s WHERE id = %s",
                (removed_at, week_playlist_id),
            )
