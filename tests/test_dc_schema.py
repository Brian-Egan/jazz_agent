"""Migration 0003 and seed_dc.sql against a real Postgres (issue #23).

conftest's session setup already applied every migrations/*.sql file once, so 0003 applying
cleanly on top of 0001 and 0002 is proven by every db-marked test in the suite; the tests
here pin down what 0003 promises.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from jazz_agent.core import dc_groups

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://jazz_agent:CHANGEME@localhost:5432/jazz_agent_test"
)
MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def _psql(path: Path) -> None:
    subprocess.run(
        ["psql", TEST_DATABASE_URL, "-v", "ON_ERROR_STOP=1", "-f", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )


def _seed_all(db: ConnectionPool) -> None:
    _psql(MIGRATIONS / "seed_clubs.sql")
    _psql(MIGRATIONS / "seed_dc.sql")


def _snapshot(db: ConnectionPool) -> dict[str, list[tuple[object, ...]]]:
    queries = {
        "clubs": "SELECT * FROM clubs ORDER BY club_id",
        "members": "SELECT club_id, group_key, filter_profile, added_via, deactivated_at "
        "FROM venue_group_members ORDER BY club_id, group_key",
    }
    with db.connection() as conn:
        return {k: conn.execute(q).fetchall() for k, q in queries.items()}


def test_seed_creates_feed_row_memberships_and_city_overrides(db: ConnectionPool) -> None:
    _seed_all(db)

    with db.connection() as conn:
        feed = conn.execute(
            "SELECT source, schedule_url FROM clubs WHERE club_id = 'capitalbop'"
        ).fetchone()
        members = conn.execute(
            "SELECT group_key, capitalbop_slug, filter_profile, added_via "
            "FROM venue_group_members m JOIN clubs c USING (club_id) "
            "WHERE deactivated_at IS NULL ORDER BY group_key, capitalbop_slug"
        ).fetchall()
        overrides = dict(
            conn.execute(
                "SELECT capitalbop_slug, city_override FROM clubs WHERE city_override IS NOT NULL"
            ).fetchall()
        )

    assert feed == ("capitalbop_feed", "https://www.capitalbop.com/dcjazzcalendar/")
    assert members == [
        ("alxjazz", "good-fortune-rooftop-bar-hotel-heron", "all", "seed"),
        ("alxjazz", "hotel-heron", "all", "seed"),
        ("alxjazz", "laportas", "early_evening", "seed"),
        ("alxjazz", "old-waterfront-park", "all", "seed"),
        ("alxjazz", "the-birchmere", "standard_no_pick", "seed"),
        ("blues-alley", "blues-alley", "all", "seed"),
    ]
    assert overrides == {
        "hotel-heron": "Alexandria",
        "good-fortune-rooftop-bar-hotel-heron": "Alexandria",
        "old-waterfront-park": "Alexandria",
        "galaxy-hut": "Arlington",
    }


def test_seed_is_idempotent(db: ConnectionPool) -> None:
    _seed_all(db)
    first = _snapshot(db)
    _seed_all(db)

    assert _snapshot(db) == first


def test_seed_does_not_reactivate_a_deactivated_membership(db: ConnectionPool) -> None:
    _seed_all(db)
    with db.connection() as conn:
        conn.execute(
            "UPDATE venue_group_members SET deactivated_at = now() "
            "WHERE club_id = 'cb-laportas' AND group_key = 'alxjazz'"
        )

    _psql(MIGRATIONS / "seed_dc.sql")

    with db.connection() as conn:
        active = conn.execute(
            "SELECT count(*) FROM venue_group_members "
            "WHERE club_id = 'cb-laportas' AND deactivated_at IS NULL"
        ).fetchone()
    assert active == (0,)


def test_seed_leaves_nyc_rows_untouched(db: ConnectionPool) -> None:
    _psql(MIGRATIONS / "seed_clubs.sql")
    with db.connection() as conn:
        before = conn.execute("SELECT * FROM clubs WHERE source = 'scrape' ORDER BY 1").fetchall()

    _psql(MIGRATIONS / "seed_dc.sql")

    with db.connection() as conn:
        after = conn.execute("SELECT * FROM clubs WHERE source = 'scrape' ORDER BY 1").fetchall()
    assert len(before) == 6
    assert after == before


def _check_literals(db: ConnectionPool, table: str, column: str) -> set[str]:
    """Every string literal in the CHECK constraint that mentions ``column``."""
    with db.connection() as conn:
        defs = conn.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = %s::regclass AND contype = 'c'",
            (table,),
        ).fetchall()
    matching = [d[0] for d in defs if re.search(rf"\b{column}\b", d[0]) and "'" in d[0]]
    assert len(matching) == 1, f"expected one CHECK on {table}.{column}, found {matching}"
    return set(re.findall(r"'([^']+)'", matching[0]))


@pytest.mark.parametrize(
    ("table", "column", "expected"),
    [
        ("venue_group_members", "group_key", set(dc_groups.GROUP_KEYS)),
        ("rolling_playlists", "group_key", set(dc_groups.GROUP_KEYS)),
        ("venue_group_members", "filter_profile", set(dc_groups.FILTER_PROFILES)),
        ("rolling_playlist_tracks", "removal_reason", set(dc_groups.REMOVAL_REASONS)),
    ],
)
def test_check_constraints_agree_with_dc_groups(
    db: ConnectionPool, table: str, column: str, expected: set[str]
) -> None:
    assert _check_literals(db, table, column) == expected


def test_seeded_playlist_definitions_are_consistent() -> None:
    assert len(dc_groups.PLAYLISTS) == 5
    assert {(p.region, p.slot_key) for p in dc_groups.PLAYLISTS} == {
        ("dc", "next-2-weeks"),
        ("dc", "weeks-3-4"),
        ("dc", "weeks-5-6"),
        ("dc", "blues-alley"),
        ("dc", "alxjazz"),
    }
    assert {p.slot_key: p.is_public for p in dc_groups.PLAYLISTS} == {
        "next-2-weeks": True,
        "weeks-3-4": True,
        "weeks-5-6": True,
        "blues-alley": True,
        "alxjazz": False,
    }
    assert {p.slot_key: p.cap for p in dc_groups.PLAYLISTS if p.cap} == {
        "next-2-weeks": 12,
        "weeks-3-4": 12,
        "weeks-5-6": 12,
    }
    assert dc_groups.DEDICATED_GROUP_KEYS == ("blues-alley", "alxjazz")


def test_playlist_events_requires_exactly_one_playlist(db: ConnectionPool) -> None:
    with db.connection() as conn:
        rolling_id = conn.execute(
            "INSERT INTO rolling_playlists (region, slot_key, group_key, title, is_public) "
            "VALUES ('dc', 'alxjazz', 'alxjazz', 't', false) RETURNING id"
        ).fetchone()
        assert rolling_id is not None

    with db.connection() as conn:
        conn.execute(
            "INSERT INTO playlist_events (rolling_playlist_id, event_type) VALUES (%s, 'created')",
            (rolling_id[0],),
        )
    with pytest.raises(psycopg.errors.CheckViolation), db.connection() as conn:
        conn.execute("INSERT INTO playlist_events (event_type) VALUES ('created')")


def test_rolling_playlists_are_unique_on_region_and_slot(db: ConnectionPool) -> None:
    insert = (
        "INSERT INTO rolling_playlists (region, slot_key, group_key, title, is_public) "
        "VALUES ('dc', 'alxjazz', 'alxjazz', 't', false)"
    )
    with db.connection() as conn:
        conn.execute(insert)
    with pytest.raises(psycopg.errors.UniqueViolation), db.connection() as conn:
        conn.execute(insert)
