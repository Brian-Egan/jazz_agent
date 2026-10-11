"""Postgres implementation of ports.repository.CapitalbopRepo."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from jazz_agent.core.models import CapitalbopEvent

_COLUMNS = (
    "instance_id, event_id, show_id, start_time, price_text, ticket_link, cb_pick, tags, "
    "repeats, neighborhoods, geocoded_city, description_html, description_hash, "
    "article_title, article_url, article_date, first_seen_at, last_seen_at"
)


def _row_to_event(row: dict[str, Any]) -> CapitalbopEvent:
    return CapitalbopEvent(
        **{**row, "tags": tuple(row["tags"]), "neighborhoods": tuple(row["neighborhoods"])}
    )


class PgCapitalbopRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert_event(self, event: CapitalbopEvent, seen_at: datetime | None = None) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO capitalbop_events AS e
                    (instance_id, event_id, show_id, start_time, price_text, ticket_link,
                     cb_pick, tags, repeats, neighborhoods, geocoded_city, description_html,
                     description_hash, article_title, article_url, article_date, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        COALESCE(%s, now()))
                ON CONFLICT (instance_id) DO UPDATE SET
                    event_id = EXCLUDED.event_id, show_id = EXCLUDED.show_id,
                    start_time = EXCLUDED.start_time, price_text = EXCLUDED.price_text,
                    ticket_link = EXCLUDED.ticket_link, cb_pick = EXCLUDED.cb_pick,
                    tags = EXCLUDED.tags, repeats = EXCLUDED.repeats,
                    neighborhoods = EXCLUDED.neighborhoods,
                    geocoded_city = EXCLUDED.geocoded_city,
                    description_html = EXCLUDED.description_html,
                    description_hash = EXCLUDED.description_hash,
                    -- A re-fetch that carries no article must not erase one already captured.
                    article_title = COALESCE(EXCLUDED.article_title, e.article_title),
                    article_url = COALESCE(EXCLUDED.article_url, e.article_url),
                    article_date = COALESCE(EXCLUDED.article_date, e.article_date),
                    last_seen_at = EXCLUDED.last_seen_at
                """,
                (
                    event.instance_id,
                    event.event_id,
                    event.show_id,
                    event.start_time,
                    event.price_text,
                    event.ticket_link,
                    event.cb_pick,
                    list(event.tags),
                    event.repeats,
                    list(event.neighborhoods),
                    event.geocoded_city,
                    event.description_html,
                    event.description_hash,
                    event.article_title,
                    event.article_url,
                    event.article_date,
                    seen_at,
                ),
            )

    def get_event(self, instance_id: int) -> CapitalbopEvent | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_COLUMNS} FROM capitalbop_events WHERE instance_id = %s",
                (instance_id,),
            )
            row = cur.fetchone()
            return _row_to_event(row) if row else None

    def get_extraction(self, event_id: int, description_hash: str) -> dict[str, object] | None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT result FROM capitalbop_extractions
                WHERE event_id = %s AND description_hash = %s
                """,
                (event_id, description_hash),
            )
            row = cur.fetchone()
            return row[0] if row else None

    def put_extraction(
        self, event_id: int, description_hash: str, result: dict[str, object], model: str
    ) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO capitalbop_extractions (event_id, description_hash, result, model)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (event_id, description_hash) DO NOTHING
                """,
                (event_id, description_hash, Jsonb(result), model),
            )

    def known_event_ids(self, event_ids: Sequence[int]) -> set[int]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT event_id FROM capitalbop_events WHERE event_id = ANY(%s)",
                (list(event_ids),),
            )
            return {row[0] for row in cur.fetchall()}

    def cancelled_instance_ids(
        self, start: date, end: date, fetch_started_at: datetime
    ) -> set[int]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                SELECT e.instance_id
                FROM capitalbop_events e JOIN shows s ON s.show_id = e.show_id
                WHERE s.show_date BETWEEN %s AND %s AND e.last_seen_at < %s
                """,
                (start, end, fetch_started_at),
            )
            return {row[0] for row in cur.fetchall()}

    def set_series_article(
        self, event_id: int, title: str, url: str, published: date | None
    ) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE capitalbop_events
                SET article_title = %s, article_url = %s, article_date = %s
                WHERE event_id = %s
                """,
                (title, url, published, event_id),
            )
