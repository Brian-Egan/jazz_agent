"""Postgres implementation of ports.repository.GraphRepo."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from jazz_agent.core.models import MbArtist, MbArtistEdge, MbLookupMiss

_MB_ARTIST_COLUMNS = "mbid, name, entity_type, disambiguation, tags, spotify_url"
_MB_EDGE_COLUMNS = "src_mbid, dst_mbid, dst_name, edge_type, instruments, begin_date, end_date"
_MISS_COLUMNS = "name_norm, miss_until, attempts, last_error"


def _row_to_mb_artist(row: dict[str, Any]) -> MbArtist:
    return MbArtist(
        mbid=str(row["mbid"]),
        name=row["name"],
        entity_type=row["entity_type"],
        disambiguation=row["disambiguation"],
        tags=tuple(row["tags"]),
        spotify_url=row["spotify_url"],
    )


def _row_to_edge(row: dict[str, Any]) -> MbArtistEdge:
    return MbArtistEdge(
        src_mbid=str(row["src_mbid"]),
        dst_mbid=str(row["dst_mbid"]),
        dst_name=row["dst_name"],
        edge_type=row["edge_type"],
        instruments=tuple(row["instruments"]),
        begin_date=row["begin_date"],
        end_date=row["end_date"],
    )


def _row_to_miss(row: dict[str, Any]) -> MbLookupMiss:
    return MbLookupMiss(
        name_norm=row["name_norm"],
        miss_until=row["miss_until"],
        attempts=row["attempts"],
        last_error=row["last_error"],
    )


class PgGraphRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert_mb_artist(self, artist: MbArtist) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO mb_artists ({_MB_ARTIST_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (mbid) DO UPDATE SET
                    name = EXCLUDED.name,
                    entity_type = EXCLUDED.entity_type,
                    disambiguation = EXCLUDED.disambiguation,
                    tags = EXCLUDED.tags,
                    spotify_url = EXCLUDED.spotify_url
                """,
                (
                    artist.mbid,
                    artist.name,
                    artist.entity_type,
                    artist.disambiguation,
                    list(artist.tags),
                    artist.spotify_url,
                ),
            )

    def get_mb_artist(self, mbid: str) -> MbArtist | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(f"SELECT {_MB_ARTIST_COLUMNS} FROM mb_artists WHERE mbid = %s", (mbid,))
            row = cur.fetchone()
            return _row_to_mb_artist(row) if row else None

    def record_edges(self, edges: Sequence[MbArtistEdge]) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            for edge in edges:
                cur.execute(
                    f"""
                    INSERT INTO mb_artist_edges ({_MB_EDGE_COLUMNS})
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (src_mbid, dst_mbid, edge_type) DO UPDATE SET
                        dst_name = EXCLUDED.dst_name,
                        instruments = EXCLUDED.instruments,
                        begin_date = EXCLUDED.begin_date,
                        end_date = EXCLUDED.end_date
                    """,
                    (
                        edge.src_mbid,
                        edge.dst_mbid,
                        edge.dst_name,
                        edge.edge_type,
                        list(edge.instruments),
                        edge.begin_date,
                        edge.end_date,
                    ),
                )

    def edges_for(self, mbid: str) -> list[MbArtistEdge]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_MB_EDGE_COLUMNS} FROM mb_artist_edges WHERE src_mbid = %s", (mbid,)
            )
            return [_row_to_edge(row) for row in cur.fetchall()]

    def record_lookup_miss(self, miss: MbLookupMiss) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO mb_lookup_misses ({_MISS_COLUMNS})
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (name_norm) DO UPDATE SET
                    miss_until = EXCLUDED.miss_until,
                    attempts = EXCLUDED.attempts,
                    last_error = EXCLUDED.last_error
                """,
                (miss.name_norm, miss.miss_until, miss.attempts, miss.last_error),
            )

    def get_lookup_miss(self, name_norm: str) -> MbLookupMiss | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_MISS_COLUMNS} FROM mb_lookup_misses WHERE name_norm = %s", (name_norm,)
            )
            row = cur.fetchone()
            return _row_to_miss(row) if row else None
