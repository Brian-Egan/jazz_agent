"""Postgres implementation of ports.repository.FeedbackRepo."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from jazz_agent.core.models import Feedback

_COLUMNS = "target_type, target_id, sentiment, note_text, show_id, week_playlist_id, source"


def _row_to_feedback(row: dict[str, Any]) -> Feedback:
    return Feedback(
        target_type=row["target_type"],
        target_id=row["target_id"],
        sentiment=row["sentiment"],
        note_text=row["note_text"],
        show_id=row["show_id"],
        week_playlist_id=row["week_playlist_id"],
        source=row["source"],
    )


class PgFeedbackRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def record_feedback(self, feedback: Feedback) -> int:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO feedback ({_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    feedback.target_type,
                    feedback.target_id,
                    feedback.sentiment,
                    feedback.note_text,
                    feedback.show_id,
                    feedback.week_playlist_id,
                    feedback.source,
                ),
            )
            row = cur.fetchone()
            assert row is not None
            return row[0]  # type: ignore[no-any-return]

    def feedback_for_target(self, target_type: str, target_id: str) -> list[Feedback]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_COLUMNS} FROM feedback
                WHERE target_type = %s AND target_id = %s
                ORDER BY created_at DESC
                """,
                (target_type, target_id),
            )
            return [_row_to_feedback(row) for row in cur.fetchall()]
