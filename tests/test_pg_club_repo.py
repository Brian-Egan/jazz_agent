"""ClubRepo is read-only: clubs are hand-edited via psql (ARCHITECTURE.md section 3),
so the round trip here is raw SQL insert -> typed read, not insert-via-repo."""

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.club_repo import PgClubRepo
from jazz_agent.core.models import Club


def _insert_club(db: ConnectionPool, **overrides: object) -> None:
    fields = {
        "club_id": "village-vanguard",
        "name": "Village Vanguard",
        "schedule_url": "https://villagevanguard.com",
        "render_mode": "http",
        "week_start_dow": 2,
        "timezone": "America/New_York",
        "active": True,
        "notes": None,
        "venue_label": None,
        **overrides,
    }
    with db.connection() as conn:
        conn.execute(
            """
            INSERT INTO clubs
                (club_id, name, schedule_url, render_mode, week_start_dow, timezone, active,
                 notes, venue_label)
            VALUES (%(club_id)s, %(name)s, %(schedule_url)s, %(render_mode)s,
                    %(week_start_dow)s, %(timezone)s, %(active)s, %(notes)s, %(venue_label)s)
            """,
            fields,
        )


def test_get_club_round_trips(db: ConnectionPool) -> None:
    _insert_club(db, notes="403s without a realistic User-Agent")
    repo = PgClubRepo(db)

    club = repo.get_club("village-vanguard")

    assert club == Club(
        club_id="village-vanguard",
        name="Village Vanguard",
        schedule_url="https://villagevanguard.com",
        render_mode="http",
        week_start_dow=2,
        timezone="America/New_York",
        active=True,
        notes="403s without a realistic User-Agent",
    )


def test_get_club_round_trips_venue_label(db: ConnectionPool) -> None:
    # A combined-page club (e.g. smalls-live, mezzrow -- both on
    # smallslive.com) sets venue_label so extraction can filter to its own
    # shows on a page that lists multiple venues.
    _insert_club(db, club_id="smalls-live", name="SmallsLIVE", venue_label="Smalls")
    repo = PgClubRepo(db)

    club = repo.get_club("smalls-live")

    assert club is not None
    assert club.venue_label == "Smalls"


def test_get_club_missing_returns_none(db: ConnectionPool) -> None:
    repo = PgClubRepo(db)

    assert repo.get_club("no-such-club") is None


def test_get_active_clubs_excludes_inactive(db: ConnectionPool) -> None:
    _insert_club(db, club_id="village-vanguard", name="Village Vanguard")
    _insert_club(
        db,
        club_id="closed-club",
        name="Closed Club",
        schedule_url="https://example.com",
        active=False,
    )
    repo = PgClubRepo(db)

    active = repo.get_active_clubs()

    assert [c.club_id for c in active] == ["village-vanguard"]
