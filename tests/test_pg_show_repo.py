import time
from datetime import date

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.show_repo import PgShowRepo
from jazz_agent.core.models import MatchMiss, Performer, Show


def _insert_club(db: ConnectionPool, club_id: str = "village-vanguard") -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, schedule_url) VALUES (%s, %s, %s)",
            (club_id, "Village Vanguard", "https://villagevanguard.com"),
        )


def _show(**overrides: object) -> Show:
    fields: dict[str, object] = {
        "club_id": "village-vanguard",
        "show_date": date(2026, 5, 5),
        "act_name_raw": "The Bill Frisell Four",
        "act_name_norm": "Bill Frisell",
        "set_times": ("8:00 PM", "10:30 PM"),
        "album_mentioned": None,
        "raw_text": "Bill Frisell Four, 8pm & 10:30pm",
    }
    fields.update(overrides)
    return Show(**fields)  # type: ignore[arg-type]


def test_upsert_show_round_trips(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgShowRepo(db)

    show_id = repo.upsert_show(_show())
    fetched = repo.get_show(show_id)

    assert fetched == _show()


def test_upsert_show_on_conflict_updates_last_seen_not_inserts_duplicate(
    db: ConnectionPool,
) -> None:
    _insert_club(db)
    repo = PgShowRepo(db)

    first_id = repo.upsert_show(_show())
    with db.connection() as conn:
        first_seen = conn.execute(
            "SELECT last_seen_at FROM shows WHERE show_id = %s", (first_id,)
        ).fetchone()[0]

    time.sleep(0.01)
    second_id = repo.upsert_show(_show(raw_text="re-scraped, same act"))

    with db.connection() as conn:
        count = conn.execute(
            """
            SELECT count(*) FROM shows
            WHERE club_id = %s AND show_date = %s AND act_name_norm = %s
            """,
            ("village-vanguard", date(2026, 5, 5), "Bill Frisell"),
        ).fetchone()[0]
        second_seen = conn.execute(
            "SELECT last_seen_at FROM shows WHERE show_id = %s", (second_id,)
        ).fetchone()[0]

    assert second_id == first_id
    assert count == 1
    assert second_seen > first_seen


def test_record_and_fetch_performers(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgShowRepo(db)
    show_id = repo.upsert_show(_show())

    repo.record_performers(
        show_id,
        [
            Performer(name="Bill Frisell", instrument="guitar", is_leader=True),
            Performer(name="Rudy Royston", instrument="drums"),
        ],
    )

    performers = repo.performers_for_show(show_id)

    assert performers == [
        Performer(name="Bill Frisell", instrument="guitar", is_leader=True),
        Performer(name="Rudy Royston", instrument="drums", is_leader=False),
    ]


def test_record_performers_upserts_shared_performer_across_shows(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgShowRepo(db)
    show_one = repo.upsert_show(_show(show_date=date(2026, 5, 5)))
    show_two = repo.upsert_show(_show(show_date=date(2026, 5, 6), act_name_norm="Bill Frisell 2"))

    repo.record_performers(show_one, [Performer(name="Bill Frisell", instrument="guitar")])
    repo.record_performers(show_two, [Performer(name="bill frisell", instrument="guitar")])

    with db.connection() as conn:
        performer_count = conn.execute("SELECT count(*) FROM performers").fetchone()[0]

    assert performer_count == 1


def test_record_and_fetch_match_miss(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgShowRepo(db)
    show_id = repo.upsert_show(_show())

    repo.record_match_miss(
        MatchMiss(
            show_id=show_id,
            act_name_raw="Some Unknown Trio",
            reason="NO_CONFIDENT_MATCH",
            best_guess_id="abc123",
            best_guess_confidence=0.42,
        )
    )

    misses = repo.match_misses_for_show(show_id)

    assert misses == [
        MatchMiss(
            show_id=show_id,
            act_name_raw="Some Unknown Trio",
            reason="NO_CONFIDENT_MATCH",
            best_guess_id="abc123",
            best_guess_confidence=0.42,
        )
    ]
