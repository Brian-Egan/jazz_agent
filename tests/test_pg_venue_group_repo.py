from __future__ import annotations

from datetime import UTC, datetime

import pytest
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.venue_group_repo import PgVenueGroupRepo
from jazz_agent.ports.repository import DedicatedGroupConflict


def _venue(db: ConnectionPool, slug: str, source: str = "capitalbop_venue") -> str:
    club_id = f"cb-{slug}"
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, source, capitalbop_slug, schedule_url) "
            "VALUES (%s, %s, %s, %s, %s)",
            (club_id, slug, source, slug, None if source == "capitalbop_venue" else "https://x"),
        )
    return club_id


def test_venue_cannot_hold_two_active_dedicated_group_memberships(db: ConnectionPool) -> None:
    repo = PgVenueGroupRepo(db)
    venue = _venue(db, "blues-alley")
    repo.add_member(venue, "blues-alley", "all", "mcp")

    with pytest.raises(DedicatedGroupConflict) as exc:
        repo.add_member(venue, "alxjazz", "all", "mcp")

    assert exc.value.existing_group == "blues-alley"
    assert [m.group_key for m in repo.active_members()] == ["blues-alley"]


def test_dc_group_is_not_dedicated_so_it_coexists_with_a_dedicated_group(
    db: ConnectionPool,
) -> None:
    repo = PgVenueGroupRepo(db)
    venue = _venue(db, "birchmere")
    repo.add_member(venue, "alxjazz", "standard_no_pick", "mcp")

    repo.add_member(venue, "dc", "standard", "mcp")

    assert {m.group_key for m in repo.active_members()} == {"alxjazz", "dc"}


def test_deactivation_stamps_and_frees_the_venue_for_another_group(db: ConnectionPool) -> None:
    repo = PgVenueGroupRepo(db)
    venue = _venue(db, "laportas")
    first = repo.add_member(venue, "alxjazz", "early_evening", "mcp")
    assert repo.deactivate_member(venue, "alxjazz", datetime(2026, 10, 10, tzinfo=UTC)) is True

    second = repo.add_member(venue, "blues-alley", "all", "mcp")

    assert second != first
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT group_key, deactivated_at IS NOT NULL FROM venue_group_members ORDER BY id"
        ).fetchall()
    assert rows == [("alxjazz", True), ("blues-alley", False)]  # old row kept, never deleted


def test_readding_after_deactivation_opens_a_new_row(db: ConnectionPool) -> None:
    repo = PgVenueGroupRepo(db)
    venue = _venue(db, "laportas")
    first = repo.add_member(venue, "alxjazz", "early_evening", "mcp")
    repo.deactivate_member(venue, "alxjazz", datetime(2026, 10, 10, tzinfo=UTC))

    second = repo.add_member(venue, "alxjazz", "all", "mcp")

    assert second != first
    (member,) = repo.active_members("alxjazz")
    assert member.filter_profile == "all"


def test_adding_an_active_membership_again_is_a_no_op(db: ConnectionPool) -> None:
    repo = PgVenueGroupRepo(db)
    venue = _venue(db, "laportas")
    first = repo.add_member(venue, "alxjazz", "early_evening", "mcp")

    assert repo.add_member(venue, "alxjazz", "all", "mcp") == first
    (member,) = repo.active_members()
    assert member.filter_profile == "early_evening"  # the existing row is not rewritten


def test_deactivating_a_missing_membership_reports_false(db: ConnectionPool) -> None:
    repo = PgVenueGroupRepo(db)
    venue = _venue(db, "laportas")

    assert repo.deactivate_member(venue, "alxjazz", datetime(2026, 10, 10, tzinfo=UTC)) is False


def test_rejects_a_non_venue_club_unknown_group_and_unknown_profile(db: ConnectionPool) -> None:
    repo = PgVenueGroupRepo(db)
    nyc = _venue(db, "vanguard", source="scrape")
    venue = _venue(db, "laportas")

    with pytest.raises(ValueError, match="not a CapitalBop venue"):
        repo.add_member(nyc, "dc", "standard", "mcp")
    with pytest.raises(ValueError, match="not a CapitalBop venue"):
        repo.add_member("cb-nowhere", "dc", "standard", "mcp")
    with pytest.raises(ValueError, match="unknown group"):
        repo.add_member(venue, "jazz-fest", "standard", "mcp")
    with pytest.raises(ValueError, match="unknown filter profile"):
        repo.add_member(venue, "dc", "everything", "mcp")
