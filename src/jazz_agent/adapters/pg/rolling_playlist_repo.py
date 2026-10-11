"""Postgres implementation of ports.repository.RollingPlaylistRepo."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from jazz_agent.core.models import RollingPlaylist, RollingPlaylistEvent, RollingPlaylistTrack

_PLAYLIST_COLUMNS = (
    "id, region, slot_key, group_key, spotify_playlist_id, spotify_url, title, "
    "description, is_public, sync_pending"
)
_TRACK_COLUMNS = (
    "id, rolling_playlist_id, spotify_track_id, isrc, spotify_album_id, spotify_artist_id, "
    "track_name, position, show_id, added_at, removed_at, removal_reason"
)


def _row_to_playlist(row: dict[str, Any]) -> RollingPlaylist:
    return RollingPlaylist(**{**row, "description": row["description"] or ""})


class PgRollingPlaylistRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert_rolling_playlist(self, playlist: RollingPlaylist) -> int:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rolling_playlists
                    (region, slot_key, group_key, title, description, is_public)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (region, slot_key) DO UPDATE SET
                    group_key = EXCLUDED.group_key, title = EXCLUDED.title,
                    description = EXCLUDED.description, is_public = EXCLUDED.is_public,
                    updated_at = now()
                RETURNING id
                """,
                (
                    playlist.region,
                    playlist.slot_key,
                    playlist.group_key,
                    playlist.title,
                    playlist.description,
                    playlist.is_public,
                ),
            )
            row = cur.fetchone()
            assert row is not None
            return row[0]  # type: ignore[no-any-return]

    def link_spotify_playlist(
        self, rolling_playlist_id: int, spotify_playlist_id: str, spotify_url: str
    ) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rolling_playlists
                SET spotify_playlist_id = %s, spotify_url = %s, updated_at = now()
                WHERE id = %s
                """,
                (spotify_playlist_id, spotify_url, rolling_playlist_id),
            )

    def set_sync_pending(self, rolling_playlist_id: int, pending: bool) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE rolling_playlists SET sync_pending = %s, updated_at = now() WHERE id = %s",
                (pending, rolling_playlist_id),
            )

    def set_stint_position(self, stint_id: int, position: int) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE rolling_playlist_tracks SET position = %s WHERE id = %s",
                (position, stint_id),
            )

    def get_rolling_playlist(self, region: str, slot_key: str) -> RollingPlaylist | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_PLAYLIST_COLUMNS} FROM rolling_playlists "
                "WHERE region = %s AND slot_key = %s",
                (region, slot_key),
            )
            row = cur.fetchone()
            return _row_to_playlist(row) if row else None

    def open_stints(self, rolling_playlist_id: int) -> list[RollingPlaylistTrack]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_TRACK_COLUMNS} FROM rolling_playlist_tracks
                WHERE rolling_playlist_id = %s AND removed_at IS NULL
                ORDER BY position
                """,
                (rolling_playlist_id,),
            )
            return [RollingPlaylistTrack(**row) for row in cur.fetchall()]

    def stinted_artist_ids(self, rolling_playlist_id: int) -> set[str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT spotify_artist_id FROM rolling_playlist_tracks "
                "WHERE rolling_playlist_id = %s",
                (rolling_playlist_id,),
            )
            return {row[0] for row in cur.fetchall()}

    def open_stint(self, track: RollingPlaylistTrack) -> int:
        # The partial unique index rejects a second open stint for the same track and
        # playlist; that error is deliberately not swallowed.
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rolling_playlist_tracks
                    (rolling_playlist_id, spotify_track_id, isrc, spotify_album_id,
                     spotify_artist_id, track_name, position, show_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    track.rolling_playlist_id,
                    track.spotify_track_id,
                    track.isrc,
                    track.spotify_album_id,
                    track.spotify_artist_id,
                    track.track_name,
                    track.position,
                    track.show_id,
                ),
            )
            row = cur.fetchone()
            assert row is not None
            return row[0]  # type: ignore[no-any-return]

    def close_stint(self, stint_id: int, reason: str, at: datetime) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rolling_playlist_tracks SET removed_at = %s, removal_reason = %s
                WHERE id = %s AND removed_at IS NULL
                """,
                (at, reason, stint_id),
            )

    def user_removed_track_ids(self) -> set[str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            # "Latest stint" per track across every playlist: a user removal is final, but
            # only while it is the most recent thing that happened to the track.
            cur.execute(
                """
                SELECT spotify_track_id FROM (
                    SELECT DISTINCT ON (spotify_track_id) spotify_track_id, removal_reason
                    FROM rolling_playlist_tracks
                    ORDER BY spotify_track_id, added_at DESC, id DESC
                ) latest
                WHERE removal_reason = 'user'
                """
            )
            return {row[0] for row in cur.fetchall()}

    def record_event(self, event: RollingPlaylistEvent) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO playlist_events
                    (rolling_playlist_id, event_type, spotify_artist_id, reason, detail)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    event.rolling_playlist_id,
                    event.event_type,
                    event.spotify_artist_id,
                    event.reason,
                    Jsonb(event.detail) if event.detail is not None else None,
                ),
            )
