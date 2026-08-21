"""Postgres implementation of ports.repository.ShowRepo.

upsert_show/get_show only touch the shows table. Performers are a separate
concern (record_performers/performers_for_show, backed by performers +
show_performers) so a caller that only needs the listing itself never pays
for the join, and Show.performers is left empty by get_show accordingly.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from jazz_agent.core.models import MatchMiss, Performer, Show

_SHOW_COLUMNS = (
    "club_id, show_date, act_name_raw, act_name_norm, set_times, album_mentioned, raw_text"
)


def _row_to_show(row: dict[str, Any]) -> Show:
    return Show(
        club_id=row["club_id"],
        show_date=row["show_date"],
        act_name_raw=row["act_name_raw"],
        act_name_norm=row["act_name_norm"],
        set_times=tuple(row["set_times"]),
        album_mentioned=row["album_mentioned"],
        raw_text=row["raw_text"] or "",
    )


def _normalize_performer_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _row_to_match_miss(row: dict[str, Any]) -> MatchMiss:
    confidence = row["best_guess_confidence"]
    return MatchMiss(
        show_id=row["show_id"],
        act_name_raw=row["act_name_raw"],
        reason=row["reason"],
        best_guess_id=row["best_guess_id"],
        best_guess_confidence=float(confidence) if confidence is not None else None,
    )


class PgShowRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert_show(self, show: Show) -> int:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO shows ({_SHOW_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT ON CONSTRAINT shows_club_date_act_key
                DO UPDATE SET last_seen_at = now()
                RETURNING show_id
                """,
                (
                    show.club_id,
                    show.show_date,
                    show.act_name_raw,
                    show.act_name_norm,
                    Jsonb(list(show.set_times)),
                    show.album_mentioned,
                    show.raw_text,
                ),
            )
            row = cur.fetchone()
            assert row is not None
            return row[0]  # type: ignore[no-any-return]

    def get_show(self, show_id: int) -> Show | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_SHOW_COLUMNS} FROM shows WHERE show_id = %s", (show_id,))
            row = cur.fetchone()
            return _row_to_show(row) if row else None

    def record_performers(self, show_id: int, performers: Sequence[Performer]) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            for performer in performers:
                name_norm = _normalize_performer_name(performer.name)
                cur.execute(
                    """
                    INSERT INTO performers (name_norm, name_display)
                    VALUES (%s, %s)
                    ON CONFLICT (name_norm) DO UPDATE SET name_display = EXCLUDED.name_display
                    RETURNING performer_id
                    """,
                    (name_norm, performer.name),
                )
                row = cur.fetchone()
                assert row is not None
                performer_id = row[0]

                cur.execute(
                    """
                    INSERT INTO show_performers (show_id, performer_id, instrument, is_leader)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (show_id, performer_id)
                    DO UPDATE SET instrument = EXCLUDED.instrument, is_leader = EXCLUDED.is_leader
                    """,
                    (show_id, performer_id, performer.instrument, performer.is_leader),
                )

    def performers_for_show(self, show_id: int) -> list[Performer]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT p.name_display AS name, sp.instrument, sp.is_leader
                FROM show_performers sp
                JOIN performers p ON p.performer_id = sp.performer_id
                WHERE sp.show_id = %s
                ORDER BY sp.is_leader DESC, p.name_display
                """,
                (show_id,),
            )
            return [Performer(**row) for row in cur.fetchall()]

    def record_match_miss(self, miss: MatchMiss) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO match_misses
                    (show_id, act_name_raw, reason, best_guess_id, best_guess_confidence)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    miss.show_id,
                    miss.act_name_raw,
                    miss.reason,
                    miss.best_guess_id,
                    miss.best_guess_confidence,
                ),
            )

    def match_misses_for_show(self, show_id: int) -> list[MatchMiss]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT show_id, act_name_raw, reason, best_guess_id, best_guess_confidence
                FROM match_misses
                WHERE show_id = %s
                ORDER BY id
                """,
                (show_id,),
            )
            return [_row_to_match_miss(row) for row in cur.fetchall()]
