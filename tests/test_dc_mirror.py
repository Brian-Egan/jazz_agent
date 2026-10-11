"""pipeline.dc_mirror against a real Postgres, driven by the committed CapitalBop fixtures."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.capitalbop import DEFAULT_BASE_URL, CapitalbopClient
from jazz_agent.adapters.pg.capitalbop_repo import PgCapitalbopRepo
from jazz_agent.adapters.pg.show_repo import PgShowRepo
from jazz_agent.adapters.pg.venue_repo import PgVenueRepo
from jazz_agent.core.models import CalendarEvent, CalendarVenue
from jazz_agent.pipeline.dc_mirror import mirror_calendar
from jazz_agent.ports.calendar import CalendarFetchError

FIXTURES = Path(__file__).parent / "fixtures" / "capitalbop"
START, END = date(2026, 10, 11), date(2026, 11, 15)
RUN1 = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
RUN2 = RUN1 + timedelta(days=1)


def _fixture_source() -> _StubSource:
    """Real fixture payloads, parsed by the real client over a stubbed transport."""
    with respx.mock:
        respx.get(f"{DEFAULT_BASE_URL}/calendar").mock(
            return_value=httpx.Response(
                200, json=json.loads((FIXTURES / "calendar.json").read_text())
            )
        )
        respx.get(f"{DEFAULT_BASE_URL}/events").mock(
            side_effect=lambda r: httpx.Response(
                200,
                json=json.loads((FIXTURES / f"events_page{r.url.params['page']}.json").read_text()),
            )
        )
        client = CapitalbopClient(sleep=lambda _s: None)
        return _StubSource(client.venues(), client.events(START, END))


class _StubSource:
    def __init__(self, venues: list[CalendarVenue], events: list[CalendarEvent]) -> None:
        self._venues, self.event_list = venues, events
        self.fail = False

    def venues(self) -> list[CalendarVenue]:
        return self._venues

    def events(self, start: date, end: date) -> list[CalendarEvent]:
        if self.fail:
            raise CalendarFetchError("/events page 3: HTTP 500")
        return self.event_list

    def article(self, instance_id: int) -> None:
        return None


def _mirror(db: ConnectionPool, source: _StubSource, at: datetime) -> object:
    return mirror_calendar(
        source,
        PgVenueRepo(db),
        PgShowRepo(db),
        PgCapitalbopRepo(db),
        START,
        END,
        fetch_started_at=at,
    )


def _snapshot(db: ConnectionPool) -> list[tuple[object, ...]]:
    with db.connection() as conn:
        # shows.last_seen_at is stamped with the DB clock by ShowRepo on every upsert.
        shows = conn.execute(
            "SELECT show_id, club_id, show_date, set_times, act_name_raw, act_name_norm, "
            "raw_text FROM shows ORDER BY show_id"
        ).fetchall()
        events = conn.execute("SELECT * FROM capitalbop_events ORDER BY instance_id").fetchall()
        clubs = conn.execute("SELECT * FROM clubs ORDER BY club_id").fetchall()
    return [*shows, *events, *clubs]


def test_every_event_in_the_window_is_stored_as_a_show(db: ConnectionPool) -> None:
    source = _fixture_source()

    result = _mirror(db, source, RUN1)

    with db.connection() as conn:
        shows = conn.execute("SELECT count(*) FROM shows").fetchone()
        events = conn.execute("SELECT count(*) FROM capitalbop_events").fetchone()
        linked = conn.execute(
            "SELECT count(*) FROM capitalbop_events e JOIN shows s USING (show_id)"
        ).fetchone()
    assert events == (len(source.event_list),) == (14,)
    assert linked == events
    assert shows == (14,)
    assert result.events_stored == 14  # type: ignore[attr-defined]


def test_names_are_cleaned_and_start_time_lands_in_set_times(db: ConnectionPool) -> None:
    _mirror(db, _fixture_source(), RUN1)

    with db.connection() as conn:
        rows = {
            r[0]: r[1:]
            for r in conn.execute(
                "SELECT e.instance_id, s.act_name_raw, s.act_name_norm, s.set_times, s.club_id, "
                "s.raw_text FROM capitalbop_events e JOIN shows s USING (show_id)"
            ).fetchall()
        }
    kofi = rows[37139]
    assert (kofi[0], kofi[1]) == ("Kofi B", "Kofi B")  # ***SOLD OUT*** stripped
    assert "***SOLD OUT***" in kofi[4]  # the original headline survives in raw_text
    scofield = rows[37192]
    assert scofield[0] == "John Scofield: “Electrospective”"
    assert rows[30785][2] == ["11:30"] and rows[30785][3] == "cb-laportas"
    assert "Guitarist Steve Ragsdale" in rows[30785][4]


def test_the_two_dates_of_a_residency_share_a_series_but_not_a_show(db: ConnectionPool) -> None:
    _mirror(db, _fixture_source(), RUN1)

    with db.connection() as conn:
        rows = conn.execute(
            "SELECT instance_id, event_id, show_id FROM capitalbop_events "
            "WHERE event_id = 25683 ORDER BY instance_id"
        ).fetchall()
    assert [r[0] for r in rows] == [30785, 30786]
    assert len({r[2] for r in rows}) == 2


def test_venues_are_mirrored_and_city_override_is_never_touched(db: ConnectionPool) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, source, capitalbop_slug, city_override) "
            "VALUES ('cb-hotel-heron', 'hotel-heron', 'capitalbop_venue', 'hotel-heron', "
            "'Alexandria')"
        )

    _mirror(db, _fixture_source(), RUN1)

    with db.connection() as conn:
        heron = conn.execute(
            "SELECT name, city_override, capitalbop_term_id FROM clubs "
            "WHERE club_id = 'cb-hotel-heron'"
        ).fetchone()
        laportas = conn.execute(
            "SELECT name, source, schedule_url, capitalbop_term_id FROM clubs "
            "WHERE club_id = 'cb-laportas'"
        ).fetchone()
    assert heron is not None and heron[0] != "hotel-heron"  # placeholder name filled in
    assert heron[1] == "Alexandria" and heron[2] is not None
    assert laportas == ("LaPortas", "capitalbop_venue", None, 2268)


def test_an_event_naming_a_venue_missing_from_calendar_still_gets_a_club(
    db: ConnectionPool,
) -> None:
    source = _fixture_source()
    source._venues = []

    _mirror(db, source, RUN1)

    with db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM shows").fetchone() == (14,)
        assert conn.execute("SELECT name FROM clubs WHERE club_id = 'cb-laportas'").fetchone() == (
            "LaPortas",
        )


def test_rerunning_does_not_duplicate_and_new_series_only_appear_once(db: ConnectionPool) -> None:
    source = _fixture_source()
    first = _mirror(db, source, RUN1)
    snapshot_after_first = _snapshot(db)
    second = _mirror(db, source, RUN1)  # same fetch timestamp: fully idempotent

    assert _snapshot(db) == snapshot_after_first
    assert len(first.new_series_ids) > 0  # type: ignore[attr-defined]
    assert second.new_series_ids == frozenset()  # type: ignore[attr-defined]


def test_a_page_failure_raises_and_leaves_prior_events_untouched(db: ConnectionPool) -> None:
    source = _fixture_source()
    _mirror(db, source, RUN1)
    before = _snapshot(db)
    source.fail = True

    with pytest.raises(CalendarFetchError):
        _mirror(db, source, RUN2)

    assert _snapshot(db) == before  # nothing stored, nothing cancelled, last_seen_at unmoved


def test_an_event_missing_from_a_complete_fetch_is_cancelled_and_its_rows_remain(
    db: ConnectionPool,
) -> None:
    source = _fixture_source()
    _mirror(db, source, RUN1)
    source.event_list = [e for e in source.event_list if e.instance_id != 37139]

    result = _mirror(db, source, RUN2)

    assert result.cancelled_instance_ids == frozenset({37139})  # type: ignore[attr-defined]
    with db.connection() as conn:
        assert conn.execute(
            "SELECT count(*) FROM capitalbop_events WHERE instance_id = 37139"
        ).fetchone() == (1,)
        assert conn.execute("SELECT count(*) FROM shows").fetchone() == (14,)
        seen = dict(
            conn.execute("SELECT instance_id, last_seen_at FROM capitalbop_events").fetchall()
        )
    assert seen[37139] == RUN1 and seen[30785] == RUN2  # seen events advance, missing one stops


def test_the_sweep_does_not_cancel_events_outside_the_fetched_window(db: ConnectionPool) -> None:
    source = _fixture_source()
    _mirror(db, source, RUN1)
    # The June festival days are in the fixtures but outside START..END: absence from a
    # later fetch of Oct-Nov must not read as cancellation.
    source.event_list = [e for e in source.event_list if not e.name.startswith("Alexandria")]

    result = _mirror(db, source, RUN2)

    assert result.cancelled_instance_ids == frozenset()  # type: ignore[attr-defined]


def test_an_event_that_returns_is_no_longer_cancelled(db: ConnectionPool) -> None:
    source = _fixture_source()
    full = list(source.event_list)
    _mirror(db, source, RUN1)
    source.event_list = [e for e in full if e.instance_id != 37139]
    _mirror(db, source, RUN2)
    source.event_list = full

    result = _mirror(db, source, RUN2 + timedelta(days=1))

    assert result.cancelled_instance_ids == frozenset()  # type: ignore[attr-defined]


def test_a_renamed_act_repoints_the_instance_and_keeps_the_stale_show(db: ConnectionPool) -> None:
    source = _fixture_source()
    _mirror(db, source, RUN1)
    source.event_list = [
        replace(e, name="Kofi Burbridge") if e.instance_id == 37139 else e
        for e in source.event_list
    ]

    _mirror(db, source, RUN2)

    with db.connection() as conn:
        names = conn.execute(
            "SELECT act_name_norm FROM shows WHERE club_id = 'cb-bethesda-theater' ORDER BY show_id"
        ).fetchall()
        current = conn.execute(
            "SELECT s.act_name_norm FROM capitalbop_events e JOIN shows s USING (show_id) "
            "WHERE e.instance_id = 37139"
        ).fetchone()
    assert names == [("Kofi B",), ("Kofi Burbridge",)]  # old row kept
    assert current == ("Kofi Burbridge",)


def test_unknown_city_venues_are_reported_by_name(db: ConnectionPool) -> None:
    source = _fixture_source()
    mystery = replace(
        source.event_list[0],
        instance_id=999001,
        event_id=999000,
        name="Mystery Quartet",
        venue=CalendarVenue(term_id=99999, slug="mystery-room", name="The Mystery Room"),
        geocoded_city=None,
        neighborhoods=("virginia",),
    )
    source.event_list = [*source.event_list, mystery]

    result = _mirror(db, source, RUN1)

    assert result.unknown_city_venues == ("Bethesda Theater", "The Mystery Room")  # type: ignore[attr-defined]
    assert "unknown city: Bethesda Theater, The Mystery Room" in result.detail()  # type: ignore[attr-defined]


def test_known_cities_resolve_and_unresolvable_ones_are_reported(db: ConnectionPool) -> None:
    result = _mirror(db, _fixture_source(), RUN1)

    # The Golden Age has no geolocation but the `dc` neighborhood resolves it. Bethesda
    # Theater is a Maryland venue with no address, geolocation, or DC-area neighborhood: the
    # feed genuinely cannot place it, so it is reported (and fails the geography filter).
    assert result.unknown_city_venues == ("Bethesda Theater",)  # type: ignore[attr-defined]


def test_series_article_is_recorded_on_every_instance_of_the_series(db: ConnectionPool) -> None:
    _mirror(db, _fixture_source(), RUN1)
    repo = PgCapitalbopRepo(db)

    repo.set_series_article(25683, "A profile", "https://www.capitalbop.com/p/", date(2024, 12, 19))

    with db.connection() as conn:
        rows = conn.execute(
            "SELECT instance_id, article_title FROM capitalbop_events "
            "WHERE article_title IS NOT NULL ORDER BY 1"
        ).fetchall()
    assert rows == [(30785, "A profile"), (30786, "A profile")]
