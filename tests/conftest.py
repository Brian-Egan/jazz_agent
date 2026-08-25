"""Shared fixtures. db-marked tests need a reachable Postgres (see pytest.ini_options)."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.pool import make_pool

# The database name here deliberately differs from DATABASE_URL's -- this
# fixture runs DROP SCHEMA public CASCADE, and the previous default matched
# DATABASE_URL's host/port/db-name exactly, one missing TEST_DATABASE_URL
# away from wiping a real database. Caught before it ever ran that way.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://jazz_agent:CHANGEME@localhost:5432/jazz_agent_test"
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


def _ensure_database_exists(database_url: str) -> None:
    """Create the target database if it doesn't exist yet, so `make test` keeps
    working with zero manual setup -- connects to the standard `postgres`
    maintenance database to issue the CREATE DATABASE, since you can't run
    that against the database you're trying to create."""
    parsed = urlsplit(database_url)
    db_name = parsed.path.lstrip("/")
    admin_url = urlunsplit(parsed._replace(path="/postgres"))
    with psycopg.connect(admin_url, autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db_name)))


@pytest.fixture(scope="session")
def pg_pool() -> Iterator[ConnectionPool]:
    """A pool against a freshly-migrated database, built once per test session."""
    _ensure_database_exists(TEST_DATABASE_URL)
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
