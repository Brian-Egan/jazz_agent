"""migrations/seed_clubs.sql tests against a real, freshly-migrated Postgres
(see conftest.py -- session setup already applies every migrations/*.sql
file once, including this one). Issue #15 acceptance criteria: every seeded
URL has actually been fetched successfully (see the issue #15 PR/commit for
the live verification output; this test only proves the seed data itself is
well-formed and idempotent, not that the URLs are still reachable today --
that would be a live network test in a suite that must never make one)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from psycopg_pool import ConnectionPool

# Matches conftest.py's own default -- duplicated rather than imported, since
# tests/ isn't a package and a relative import here would be fragile.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://jazz_agent:CHANGEME@localhost:5432/jazz_agent"
)
SEED_PATH = Path(__file__).resolve().parent.parent / "migrations" / "seed_clubs.sql"

EXPECTED_CLUB_IDS = {
    "village-vanguard",
    "smalls-live",
    "blue-note",
    "birdland",
    "mezzrow",
    "smoke",
}


def _apply_seed() -> None:
    subprocess.run(
        ["psql", TEST_DATABASE_URL, "-v", "ON_ERROR_STOP=1", "-f", str(SEED_PATH)],
        check=True,
        capture_output=True,
        text=True,
    )


def test_seed_clubs_produces_exactly_six_clubs(db: ConnectionPool) -> None:
    # db fixture truncates clubs; conftest's session setup already applied
    # seed_clubs.sql once, so re-apply it explicitly for this test's own
    # clean-then-seed sequence.
    _apply_seed()

    with db.connection() as conn:
        rows = conn.execute("SELECT club_id, schedule_url, render_mode FROM clubs").fetchall()

    club_ids = {row[0] for row in rows}
    assert club_ids == EXPECTED_CLUB_IDS
    assert len(rows) == 6
    for _club_id, schedule_url, render_mode in rows:
        assert schedule_url.startswith("https://")
        assert render_mode == "http"


def test_seed_clubs_is_idempotent(db: ConnectionPool) -> None:
    _apply_seed()
    _apply_seed()

    with db.connection() as conn:
        count = conn.execute("SELECT count(*) FROM clubs").fetchone()[0]

    assert count == 6  # applying twice does not duplicate or error
