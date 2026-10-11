from __future__ import annotations

from datetime import date, time

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.capitalbop_repo import PgCapitalbopRepo
from jazz_agent.core.models import CapitalbopEvent


def _show(db: ConnectionPool) -> int:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, source, capitalbop_slug) "
            "VALUES ('cb-blues-alley', 'Blues Alley', 'capitalbop_venue', 'blues-alley')"
        )
        row = conn.execute(
            "INSERT INTO shows (club_id, show_date, act_name_raw, act_name_norm) "
            "VALUES ('cb-blues-alley', '2026-10-20', 'Act', 'act') RETURNING show_id"
        ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def _event(show_id: int, **overrides: object) -> CapitalbopEvent:
    fields = {
        "instance_id": 9001,
        "event_id": 77,
        "show_id": show_id,
        "start_time": time(19, 30),
        "price_text": "$40",
        "cb_pick": True,
        "tags": ("jazz",),
        "neighborhoods": ("dc",),
        "geocoded_city": "Washington",
        "description_html": "<p>hi</p>",
        "description_hash": "h1",
        **overrides,
    }
    return CapitalbopEvent(**fields)  # type: ignore[arg-type]


def test_upsert_round_trips_and_refreshes_without_duplicating(db: ConnectionPool) -> None:
    repo = PgCapitalbopRepo(db)
    show_id = _show(db)
    repo.upsert_event(_event(show_id))
    first = repo.get_event(9001)
    assert first is not None

    repo.upsert_event(_event(show_id, price_text="$45"))
    second = repo.get_event(9001)

    assert second is not None
    assert first.tags == ("jazz",) and first.start_time == time(19, 30) and first.cb_pick
    assert second.price_text == "$45"
    assert second.first_seen_at == first.first_seen_at  # preserved
    assert second.last_seen_at is not None and first.last_seen_at is not None
    assert second.last_seen_at >= first.last_seen_at
    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM capitalbop_events").fetchone() == (1,)


def test_a_refetch_without_an_article_keeps_the_captured_one(db: ConnectionPool) -> None:
    repo = PgCapitalbopRepo(db)
    show_id = _show(db)
    repo.upsert_event(
        _event(
            show_id,
            article_title="A profile",
            article_url="https://capitalbop.com/a",
            article_date=date(2016, 1, 2),
        )
    )

    repo.upsert_event(_event(show_id))

    got = repo.get_event(9001)
    assert got is not None
    assert (got.article_title, got.article_date) == ("A profile", date(2016, 1, 2))


def test_get_event_missing_returns_none(db: ConnectionPool) -> None:
    assert PgCapitalbopRepo(db).get_event(1) is None


def test_extraction_cache_is_keyed_on_event_and_hash(db: ConnectionPool) -> None:
    repo = PgCapitalbopRepo(db)
    result: dict[str, object] = {"leaders": ["Sharón Clark"], "is_tribute": False}

    assert repo.get_extraction(77, "h1") is None
    repo.put_extraction(77, "h1", result, "claude-haiku-4-5")
    repo.put_extraction(77, "h1", {"leaders": ["changed"]}, "other")  # first write wins

    assert repo.get_extraction(77, "h1") == result
    assert repo.get_extraction(77, "h2") is None  # an edited description misses the cache
