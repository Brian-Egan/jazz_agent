"""Postgres implementation of ports.repository.ClubRepo."""

from __future__ import annotations

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from jazz_agent.core.models import Club

_COLUMNS = (
    "club_id, name, schedule_url, render_mode, week_start_dow, timezone, active, notes, venue_label"
)


class PgClubRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def get_active_clubs(self) -> list[Club]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM clubs WHERE active ORDER BY club_id")
            return [Club(**row) for row in cur.fetchall()]

    def get_club(self, club_id: str) -> Club | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_COLUMNS} FROM clubs WHERE club_id = %s", (club_id,))
            row = cur.fetchone()
            return Club(**row) if row else None
