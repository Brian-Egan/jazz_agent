"""CapitalBopClient tests. No live HTTP: httpx is stubbed with respx, fixtures are real
responses saved 2026-10-11 (tests/fixtures/capitalbop/README.md)."""

from __future__ import annotations

import json
from datetime import date, time
from pathlib import Path

import httpx
import pytest
import respx

from jazz_agent.adapters.capitalbop import DEFAULT_BASE_URL, CapitalbopClient
from jazz_agent.ports.calendar import CalendarFetchError

FIXTURES = Path(__file__).parent / "fixtures" / "capitalbop"
EVENTS_URL = f"{DEFAULT_BASE_URL}/events"


def _load(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text())  # type: ignore[no-any-return]


class _Clock:
    """A fake clock whose sleep advances it, so spacing can be asserted exactly."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    def time(self) -> float:
        return self.now


def _client(clock: _Clock | None = None, **kwargs: object) -> CapitalbopClient:
    clock = clock or _Clock()
    return CapitalbopClient(sleep=clock.sleep, clock=clock.time, **kwargs)  # type: ignore[arg-type]


def _mock_pages() -> respx.Route:
    return respx.get(EVENTS_URL).mock(
        side_effect=lambda request: httpx.Response(
            200, json=_load(f"events_page{request.url.params['page']}.json")
        )
    )


@respx.mock
def test_events_reads_every_page_up_to_max_pages() -> None:
    route = _mock_pages()

    events = _client().events(date(2026, 10, 11), date(2026, 11, 15))

    assert [c.request.url.params["page"] for c in route.calls] == ["1", "2"]
    assert route.calls[0].request.url.params["start_date"] == "2026-10-11"
    assert route.calls[0].request.url.params["end_date"] == "2026-11-15"
    assert len(events) == 14
    assert len({e.instance_id for e in events}) == 14


@respx.mock
def test_event_fields_are_parsed() -> None:
    _mock_pages()

    by_id = {e.instance_id: e for e in _client().events(date(2026, 10, 11), date(2026, 11, 15))}

    ragsdale = by_id[30785]
    assert ragsdale.event_id == 25683
    assert ragsdale.start_date == date(2026, 10, 11)
    assert ragsdale.start_time == time(11, 30)
    assert ragsdale.venue.slug == "laportas"
    assert ragsdale.geocoded_city == "Alexandria"
    assert ragsdale.neighborhoods == ("virginia",)
    assert "all-ages" in ragsdale.tags and ragsdale.repeats and not ragsdale.cb_pick
    assert "Guitarist Steve Ragsdale" in ragsdale.description_html

    assert by_id[37139].name == "Kofi B ***SOLD OUT***"  # raw: cleaning is the mirror's job
    assert "jam-session" in by_id[31579].tags
    assert by_id[21607].geocoded_city is None  # venue_detail.geolocation is null in the feed
    assert by_id[36773].geocoded_city == "Washington"  # empty address, but geolocated
    assert by_id[35505].name.startswith("Alexandria Jazz Fest")


@respx.mock
def test_requests_are_sequential_and_at_least_one_second_apart() -> None:
    clock = _Clock()
    _mock_pages()
    respx.get(f"{DEFAULT_BASE_URL}/calendar").mock(
        return_value=httpx.Response(200, json=_load("calendar.json"))
    )
    client = _client(clock)

    client.venues()
    client.events(date(2026, 10, 11), date(2026, 11, 15))

    # calendar -> events p1 -> events p2: two gaps, each topped up to a full second.
    assert clock.sleeps == [1.0, 1.0]


@respx.mock
def test_requests_carry_the_realistic_user_agent() -> None:
    route = _mock_pages()
    CapitalbopClient(sleep=lambda _s: None).events(date(2026, 10, 11), date(2026, 11, 15))

    user_agent = route.calls[0].request.headers["user-agent"]
    assert user_agent.startswith("Mozilla/5.0") and "Chrome" in user_agent


@respx.mock
def test_a_page_failure_mid_fetch_raises_instead_of_returning_a_partial_window() -> None:
    respx.get(EVENTS_URL).mock(
        side_effect=lambda request: (
            httpx.Response(200, json=_load("events_page1.json"))
            if request.url.params["page"] == "1"
            else httpx.Response(500)
        )
    )

    with pytest.raises(CalendarFetchError, match="HTTP 500"):
        _client().events(date(2026, 10, 11), date(2026, 11, 15))


@respx.mock
def test_transient_failure_is_retried_with_spacing_then_succeeds() -> None:
    clock = _Clock()
    responses = iter([httpx.Response(503), httpx.Response(200, json=_load("events_page1.json"))])
    respx.get(EVENTS_URL).mock(
        side_effect=lambda request: (
            next(responses)
            if request.url.params["page"] == "1"
            else httpx.Response(200, json=_load("events_page2.json"))
        )
    )

    events = _client(clock).events(date(2026, 10, 11), date(2026, 11, 15))

    assert len(events) == 14
    assert all(s >= 1.0 for s in clock.sleeps) and len(clock.sleeps) == 2


@respx.mock
def test_non_retryable_status_fails_immediately() -> None:
    route = respx.get(EVENTS_URL).mock(return_value=httpx.Response(404))

    with pytest.raises(CalendarFetchError, match="HTTP 404"):
        _client().events(date(2026, 10, 11), date(2026, 11, 15))

    assert route.call_count == 1


@respx.mock
def test_transport_error_raises() -> None:
    respx.get(EVENTS_URL).mock(side_effect=httpx.ConnectTimeout("slow"))

    with pytest.raises(CalendarFetchError, match="ConnectTimeout"):
        _client().events(date(2026, 10, 11), date(2026, 11, 15))


@respx.mock
@pytest.mark.parametrize("body", [{"nope": []}, {"events": [{"id": 1}], "max_pages": 1}])
def test_malformed_payload_raises(body: dict[str, object]) -> None:
    respx.get(EVENTS_URL).mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(CalendarFetchError):
        _client().events(date(2026, 10, 11), date(2026, 11, 15))


@respx.mock
def test_implausible_max_pages_is_refused() -> None:
    respx.get(EVENTS_URL).mock(
        return_value=httpx.Response(200, json={"events": [], "max_pages": 9999})
    )

    with pytest.raises(CalendarFetchError, match="max_pages"):
        _client().events(date(2026, 10, 11), date(2026, 11, 15))


@respx.mock
def test_venues_parses_the_calendar_payload() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/calendar").mock(
        return_value=httpx.Response(200, json=_load("calendar.json"))
    )

    venues = {v.slug: v for v in _client().venues()}

    assert venues["laportas"].name == "LaPortas"
    assert venues["laportas"].term_id == 2268
    assert {"blues-alley", "hotel-heron", "galaxy-hut"} <= set(venues)


@respx.mock
def test_article_is_parsed_from_the_event_endpoint() -> None:
    route = respx.get(f"{DEFAULT_BASE_URL}/event").mock(
        return_value=httpx.Response(200, json=_load("event_with_article.json"))
    )

    article = _client().article(37233)

    assert route.calls[0].request.url.params["instance"] == "37233"
    assert article is not None
    assert article.title == "The top 5 D.C. jazz albums of 2024"
    assert article.url == "https://www.capitalbop.com/best-washington-dc-jazz-2024/"
    assert article.published == date(2024, 12, 19)


@respx.mock
def test_article_is_none_when_the_event_has_none() -> None:
    respx.get(f"{DEFAULT_BASE_URL}/event").mock(
        return_value=httpx.Response(200, json={"event": {}, "article": False})
    )

    assert _client().article(1) is None
