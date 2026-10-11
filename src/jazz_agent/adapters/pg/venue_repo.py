"""Postgres implementation of ports.repository.VenueMirrorRepo.

CapitalBop venues are the one place ``clubs`` is written by the pipeline rather than by hand
(DC section 3), and only for the columns below.
"""

from __future__ import annotations

from psycopg_pool import ConnectionPool


class PgVenueRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def upsert_capitalbop_venue(self, slug: str, term_id: int, name: str) -> str:
        club_id = f"cb-{slug}"
        with self._pool.connection() as conn, conn.cursor() as cur:
            # Name and term id only. city_override is hand-set and must survive every
            # mirror; seeded rows (seed_dc.sql) carry the slug as a placeholder name that
            # this fills in.
            cur.execute(
                """
                INSERT INTO clubs (club_id, name, source, capitalbop_term_id, capitalbop_slug)
                VALUES (%s, %s, 'capitalbop_venue', %s, %s)
                ON CONFLICT (club_id) DO UPDATE SET
                    name = EXCLUDED.name, capitalbop_term_id = EXCLUDED.capitalbop_term_id
                """,
                (club_id, name, term_id, slug),
            )
        return club_id

    def city_overrides(self) -> dict[str, str]:
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute("SELECT club_id, city_override FROM clubs WHERE city_override IS NOT NULL")
            return {row[0]: row[1] for row in cur.fetchall()}
