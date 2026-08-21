"""Shared fixtures. db-marked tests need a reachable Postgres (see pytest.ini_options)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.pool import make_pool

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://jazz_agent:CHANGEME@localhost:5432/jazz_agent"
)
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Listed child-to-parent so a plain DELETE would work too; CASCADE makes the
# order irrelevant, but a fixed order keeps error messages easy to read.
ALL_TABLES = (
    "feedback",
    "playlist_events",
    "playlist_tracks",
    "week_playlists",
    "mb_artist_edges",
    "mb_lookup_misses",
    "mb_artists",
    "match_misses",
    "show_artists",
    "artists",
    "show_performers",
    "performers",
    "shows",
    "run_log",
    "clubs",
)


def _apply_migrations(database_url: str) -> None:
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        subprocess.run(
            ["psql", database_url, "-v", "ON_ERROR_STOP=1", "-f", str(migration)],
            check=True,
            capture_output=True,
            text=True,
        )


@pytest.fixture(scope="session")
def pg_pool() -> Iterator[ConnectionPool]:
    """A pool against a freshly-migrated database, built once per test session."""
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    _apply_migrations(TEST_DATABASE_URL)

    pool = make_pool(TEST_DATABASE_URL)
    yield pool
    pool.close()


@pytest.fixture
def db(pg_pool: ConnectionPool) -> ConnectionPool:
    """The same pool, with every table truncated so each test starts clean."""
    with pg_pool.connection() as conn:
        conn.execute(f"TRUNCATE {', '.join(ALL_TABLES)} RESTART IDENTITY CASCADE")
    return pg_pool
