"""mcp/tools_venues.py against a real Postgres (issue #31)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.rolling_playlist_repo import PgRollingPlaylistRepo
from jazz_agent.adapters.pg.venue_repo import PgVenueRepo
from jazz_agent.core.models import RollingPlaylist
from jazz_agent.mcp.tools_venues import (
    add_venue_to_group,
    deactivate_venue,
    list_venue_groups,
    resolve_venue,
)

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

VENUES = {
    "blues-alley": "Blues Alley",
    "laportas": "LaPortas",
    "the-birchmere": "The Birchmere",
    "hotel-heron": "Hotel Heron",
    "good-fortune-rooftop-bar-hotel-heron": "Good Fortune Rooftop Bar @ Hotel Heron",
    "old-waterfront-park": "Old Waterfront Park",
    "rhizome": "Rhizome",
    "cafe-saint-ex": "Café Saint-Ex",
    "blue-moon-cafe": "Blue Moon Cafe",
    "blue-moon-lounge": "Blue Moon Lounge",
}


@pytest.fixture
def venues(db: ConnectionPool) -> ConnectionPool:
    url = os.environ.get(
        "TEST_DATABASE_URL", "postgresql://jazz_agent:CHANGEME@localhost:5432/jazz_agent_test"
    )
    subprocess.run(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-f", str(MIGRATIONS / "seed_dc.sql")],
        check=True,
        capture_output=True,
        text=True,
    )
    repo = PgVenueRepo(db)
    for term_id, (slug, name) in enumerate(VENUES.items(), start=1):
        repo.upsert_capitalbop_venue(slug, term_id, name)
    return db


def rows(db: ConnectionPool, club_id: str, group: str) -> list[tuple[object, ...]]:
    with db.connection() as conn:
        return conn.execute(
            "SELECT filter_profile, added_via, deactivated_at IS NULL FROM venue_group_members "
            "WHERE club_id = %s AND group_key = %s ORDER BY id",
            (club_id, group),
        ).fetchall()


def test_one_matching_venue_is_added_with_added_via_mcp(venues: ConnectionPool) -> None:
    result = add_venue_to_group(venues, "Rhizome", "dc", "standard_no_pick")

    assert result["status"] == "added" and result["venue"] == "Rhizome"
    assert "next daily run" in result["message"]
    assert rows(venues, "cb-rhizome", "dc") == [("standard_no_pick", "mcp", True)]


def test_the_default_profile_is_standard(venues: ConnectionPool) -> None:
    add_venue_to_group(venues, "rhizome", "dc")

    assert rows(venues, "cb-rhizome", "dc") == [("standard", "mcp", True)]


def test_an_ambiguous_venue_name_writes_nothing_and_returns_every_candidate(
    venues: ConnectionPool,
) -> None:
    result = add_venue_to_group(venues, "blue moon", "dc")

    assert result["status"] == "ambiguous"
    assert {c["name"] for c in result["candidates"]} == {"Blue Moon Cafe", "Blue Moon Lounge"}
    assert rows(venues, "cb-blue-moon-cafe", "dc") == []
    assert rows(venues, "cb-blue-moon-lounge", "dc") == []


def test_an_unknown_venue_writes_nothing(venues: ConnectionPool) -> None:
    result = add_venue_to_group(venues, "Nowhere At All", "dc")

    assert result["status"] == "not_found" and result["candidates"] == []
    with venues.connection() as conn:
        assert conn.execute(
            "SELECT count(*) FROM venue_group_members WHERE added_via = 'mcp'"
        ).fetchone() == (0,)


def test_an_exact_name_is_not_made_ambiguous_by_a_longer_name_containing_it(
    venues: ConnectionPool,
) -> None:
    result = add_venue_to_group(venues, "Hotel Heron", "dc")

    # alxjazz already holds Hotel Heron, so this is rejected, but it *resolved* to one venue.
    assert result["status"] == "rejected" and result["venue"] == "Hotel Heron"


def test_matching_ignores_accents_case_and_punctuation(venues: ConnectionPool) -> None:
    result = add_venue_to_group(venues, "CAFE SAINT EX", "dc")

    assert result["status"] == "added" and result["venue"] == "Café Saint-Ex"


def test_a_venue_in_another_dedicated_group_is_rejected_naming_it(venues: ConnectionPool) -> None:
    result = add_venue_to_group(venues, "LaPortas", "blues-alley")

    assert result["status"] == "rejected"
    assert "alxjazz" in result["message"]
    assert rows(venues, "cb-laportas", "blues-alley") == []


def test_a_dc_membership_for_a_dedicated_venue_is_rejected_because_it_would_do_nothing(
    venues: ConnectionPool,
) -> None:
    result = add_venue_to_group(venues, "Blues Alley", "dc")

    assert result["status"] == "rejected" and "blues-alley" in result["message"]
    assert rows(venues, "cb-blues-alley", "dc") == []


def test_adding_an_existing_member_changes_nothing_and_reports_its_profile(
    venues: ConnectionPool,
) -> None:
    result = add_venue_to_group(venues, "LaPortas", "alxjazz", "all")

    assert result["status"] == "already_member" and result["profile"] == "early_evening"
    assert rows(venues, "cb-laportas", "alxjazz") == [("early_evening", "seed", True)]


def test_an_unknown_group_or_profile_is_an_error_not_a_new_group(venues: ConnectionPool) -> None:
    with pytest.raises(ValueError, match="group must be one of"):
        add_venue_to_group(venues, "Rhizome", "jazz-fans")
    with pytest.raises(ValueError, match="profile must be one of"):
        add_venue_to_group(venues, "Rhizome", "dc", "everything")
    with pytest.raises(ValueError, match="group must be one of"):
        deactivate_venue(venues, "Rhizome", "jazz-fans")


def test_deactivating_stamps_and_re_adding_opens_a_new_row_keeping_the_old(
    venues: ConnectionPool,
) -> None:
    add_venue_to_group(venues, "Rhizome", "dc")

    gone = deactivate_venue(venues, "Rhizome", "dc")
    again = add_venue_to_group(venues, "Rhizome", "dc", "all")

    assert gone["status"] == "deactivated" and again["status"] == "added"
    assert rows(venues, "cb-rhizome", "dc") == [("standard", "mcp", False), ("all", "mcp", True)]


def test_deactivating_a_seeded_venue_keeps_the_row(venues: ConnectionPool) -> None:
    deactivate_venue(venues, "The Birchmere", "alxjazz")

    assert rows(venues, "cb-the-birchmere", "alxjazz") == [("standard_no_pick", "seed", False)]


def test_deactivating_something_that_is_not_a_member_changes_nothing(
    venues: ConnectionPool,
) -> None:
    result = deactivate_venue(venues, "Rhizome", "dc")

    assert result["status"] == "not_found"


def test_list_venue_groups_shows_all_three_groups_with_null_urls_until_a_reconcile(
    venues: ConnectionPool,
) -> None:
    listing = list_venue_groups(venues)

    groups = {g["group"]: g for g in listing["groups"]}
    assert set(groups) == {"dc", "blues-alley", "alxjazz"}
    assert [v["venue"] for v in groups["alxjazz"]["venues"]] == [
        "Good Fortune Rooftop Bar @ Hotel Heron",
        "Hotel Heron",
        "LaPortas",
        "Old Waterfront Park",
        "The Birchmere",
    ]
    assert {v["filter_profile"] for v in groups["alxjazz"]["venues"]} == {
        "all",
        "early_evening",
        "standard_no_pick",
    }
    assert [p["slot_key"] for p in groups["dc"]["playlists"]] == [
        "next-2-weeks",
        "weeks-3-4",
        "weeks-5-6",
    ]
    assert all(p["url"] is None for g in listing["groups"] for p in g["playlists"])
    assert groups["alxjazz"]["playlists"][0]["public"] is False

    repo = PgRollingPlaylistRepo(venues)
    row_id = repo.upsert_rolling_playlist(
        RollingPlaylist("dc", "blues-alley", "blues-alley", "Blues Alley", True)
    )
    repo.link_spotify_playlist(row_id, "sp1", "https://open.spotify.com/playlist/sp1")
    after = {g["group"]: g for g in list_venue_groups(venues)["groups"]}
    assert after["blues-alley"]["playlists"][0]["url"] == "https://open.spotify.com/playlist/sp1"


def test_resolve_venue_tolerates_a_small_typo_but_not_a_different_name() -> None:
    venues = [
        {"club_id": "cb-the-birchmere", "name": "The Birchmere", "slug": "the-birchmere"},
        {"club_id": "cb-rhizome", "name": "Rhizome", "slug": "rhizome"},
    ]

    assert [v["name"] for v in resolve_venue(venues, "birchmer")] == ["The Birchmere"]
    assert resolve_venue(venues, "Smalls") == []
    assert resolve_venue(venues, "   ") == []
