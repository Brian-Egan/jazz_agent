"""Postgres implementation of ports.repository.RunRepo."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from jazz_agent.core.models import RunLogEntry

_COLUMNS = "run_id, outcome, club_id, shows_found, detail, duration_ms"


def _row_to_entry(row: dict[str, Any]) -> RunLogEntry:
    return RunLogEntry(
        run_id=str(row["run_id"]),
        outcome=row["outcome"],
        club_id=row["club_id"],
        shows_found=row["shows_found"],
        detail=row["detail"],
        duration_ms=row["duration_ms"],
    )


class PgRunRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def log_run_outcome(self, entry: RunLogEntry) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO run_log ({_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    uuid.UUID(entry.run_id),
                    entry.outcome,
                    entry.club_id,
                    entry.shows_found,
                    entry.detail,
                    entry.duration_ms,
                ),
            )

    def recent_runs(self, club_id: str | None = None, days: int = 7) -> list[RunLogEntry]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_COLUMNS} FROM run_log
                WHERE run_at >= now() - make_interval(days => %s)
                  AND (%s::text IS NULL OR club_id = %s)
                ORDER BY run_at DESC
                """,
                (days, club_id, club_id),
            )
            return [_row_to_entry(row) for row in cur.fetchall()]
