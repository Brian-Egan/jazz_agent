"""Postgres implementation of ports.repository.ArtistRepo."""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from jazz_agent.core.models import Artist

_ARTIST_COLUMNS = (
    "spotify_artist_id, name, genres, popularity, followers, mbid, match_method, "
    "match_confidence, plausibility_score, needs_review, verification_state, "
    "verified_at, match_notes"
)
_ARTIST_COLUMNS_QUALIFIED = ", ".join(f"a.{c.strip()}" for c in _ARTIST_COLUMNS.split(","))


def _row_to_artist(row: dict[str, Any]) -> Artist:
    return Artist(
        spotify_artist_id=row["spotify_artist_id"],
        name=row["name"],
        match_method=row["match_method"],
        match_confidence=float(row["match_confidence"]),
        genres=tuple(row["genres"]),
        popularity=row["popularity"],
        followers=row["followers"],
        mbid=str(row["mbid"]) if row["mbid"] else None,
        plausibility_score=(
            float(row["plausibility_score"]) if row["plausibility_score"] is not None else None
        ),
        needs_review=row["needs_review"],
        verification_state=row["verification_state"],
        verified_at=row["verified_at"],
        match_notes=row["match_notes"],
    )


class PgArtistRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert_artist(self, artist: Artist) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO artists ({_ARTIST_COLUMNS})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (spotify_artist_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    genres = EXCLUDED.genres,
                    popularity = EXCLUDED.popularity,
                    followers = EXCLUDED.followers,
                    mbid = EXCLUDED.mbid,
                    match_method = EXCLUDED.match_method,
                    match_confidence = EXCLUDED.match_confidence,
                    plausibility_score = EXCLUDED.plausibility_score,
                    needs_review = EXCLUDED.needs_review,
                    verification_state = EXCLUDED.verification_state,
                    verified_at = EXCLUDED.verified_at,
                    match_notes = EXCLUDED.match_notes,
                    updated_at = now()
                """,
                (
                    artist.spotify_artist_id,
                    artist.name,
                    list(artist.genres),
                    artist.popularity,
                    artist.followers,
                    artist.mbid,
                    artist.match_method,
                    artist.match_confidence,
                    artist.plausibility_score,
                    artist.needs_review,
                    artist.verification_state,
                    artist.verified_at,
                    artist.match_notes,
                ),
            )

    def get_artist(self, spotify_artist_id: str) -> Artist | None:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"SELECT {_ARTIST_COLUMNS} FROM artists WHERE spotify_artist_id = %s",
                (spotify_artist_id,),
            )
            row = cur.fetchone()
            return _row_to_artist(row) if row else None

    def link_show_artist(self, show_id: int, spotify_artist_id: str) -> None:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO show_artists (show_id, spotify_artist_id)
                VALUES (%s, %s)
                ON CONFLICT (show_id, spotify_artist_id) DO NOTHING
                """,
                (show_id, spotify_artist_id),
            )

    def artists_for_show(self, show_id: int) -> list[Artist]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_ARTIST_COLUMNS_QUALIFIED}
                FROM show_artists sa
                JOIN artists a ON a.spotify_artist_id = sa.spotify_artist_id
                WHERE sa.show_id = %s
                ORDER BY a.name
                """,
                (show_id,),
            )
            return [_row_to_artist(row) for row in cur.fetchall()]
