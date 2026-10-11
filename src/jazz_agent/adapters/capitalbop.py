"""CapitalBop calendar client (DC section 2, ADR-017).

The public calendar page is a Vue widget with no events in its HTML; the widget reads a
WordPress REST API, which this reads directly. robots.txt allows /wp-json/. Requests are
strictly sequential with at least ``min_interval_seconds`` between any two, and carry the
same realistic User-Agent as the NYC fetcher. Personal use, no redistribution.
"""

from __future__ import annotations

import html
import logging
import time
from collections.abc import Callable
from datetime import date, datetime
from datetime import time as dtime
from typing import Any

import httpx

from jazz_agent.adapters.http_fetcher import BROWSER_USER_AGENT
from jazz_agent.core.models import CalendarArticle, CalendarEvent, CalendarVenue
from jazz_agent.ports.calendar import CalendarFetchError

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://www.capitalbop.com/wp-json/capitalbop/v1"
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# A runaway max_pages from a misbehaving API must not turn into an unbounded crawl.
_MAX_PAGES_CEILING = 50


class CapitalbopClient:
    def __init__(
        self,
        client: httpx.Client | None = None,
        base_url: str = DEFAULT_BASE_URL,
        min_interval_seconds: float = 1.0,
        max_retries: int = 2,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client or httpx.Client(
            headers={"User-Agent": BROWSER_USER_AGENT, "Accept": "application/json"},
            timeout=20.0,
            follow_redirects=True,
        )
        self._base_url = base_url.rstrip("/")
        self._min_interval = min_interval_seconds
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None

    # -- port -------------------------------------------------------------------------

    def venues(self) -> list[CalendarVenue]:
        payload = self._get_json("/calendar")
        raw = payload.get("venues") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            raise CalendarFetchError("/calendar response has no venues list")
        try:
            return [
                CalendarVenue(term_id=int(v["term_id"]), slug=str(v["slug"]), name=str(v["name"]))
                for v in raw
            ]
        except (KeyError, TypeError, ValueError) as e:
            raise CalendarFetchError(f"/calendar venue entry malformed: {e}") from e

    def events(self, start: date, end: date) -> list[CalendarEvent]:
        params = {"start_date": start.isoformat(), "end_date": end.isoformat()}
        events: list[CalendarEvent] = []
        page, max_pages = 1, 1
        while page <= max_pages:
            payload = self._get_json("/events", {**params, "page": page})
            raw = payload.get("events") if isinstance(payload, dict) else None
            if not isinstance(raw, list):
                raise CalendarFetchError(f"/events page {page} has no events list")
            try:
                max_pages = int(payload.get("max_pages", 1))
                events.extend(_parse_event(e) for e in raw)
            except (KeyError, TypeError, ValueError) as e:
                raise CalendarFetchError(f"/events page {page} malformed: {e}") from e
            if max_pages > _MAX_PAGES_CEILING:
                raise CalendarFetchError(f"/events reports implausible max_pages={max_pages}")
            page += 1
        return events

    def article(self, instance_id: int) -> CalendarArticle | None:
        payload = self._get_json("/event", {"instance": instance_id})
        raw = payload.get("article") if isinstance(payload, dict) else None
        if not isinstance(raw, dict):  # the API sends `false` when there is none
            return None
        title, url = raw.get("post_title"), raw.get("guid")
        if not title or not url:
            return None
        return CalendarArticle(
            title=html.unescape(str(title)),
            url=str(url),
            published=_parse_datetime_date(raw.get("post_date")),
        )

    # -- http -------------------------------------------------------------------------

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self._base_url}{path}"
        last_error: str = ""
        for attempt in range(self._max_retries + 1):
            self._respect_spacing()
            try:
                response = self._client.get(url, params=params)
            except httpx.HTTPError as e:
                last_error = f"{type(e).__name__}: {e}"
            else:
                if response.status_code == 200:
                    try:
                        return response.json()
                    except ValueError as e:
                        raise CalendarFetchError(f"{path}: response is not JSON") from e
                last_error = f"HTTP {response.status_code}"
                if response.status_code not in _RETRYABLE_STATUS:
                    break
            logger.warning("capitalbop %s failed (%s), attempt %d", path, last_error, attempt + 1)
        raise CalendarFetchError(f"{path} {params or ''}: {last_error}")

    def _respect_spacing(self) -> None:
        # Spacing is measured from the previous request, so retries and the next page obey
        # the same >= 1 s rule (AGENTS.md scraping conduct).
        if self._last_request_at is not None:
            wait = self._min_interval - (self._clock() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()


def _parse_event(raw: dict[str, Any]) -> CalendarEvent:
    venue = raw["venue"]
    detail = raw.get("venue_detail") or {}
    geolocation = detail.get("geolocation") or {}
    return CalendarEvent(
        instance_id=int(raw["id"]),
        event_id=int(raw["event_id"]),
        name=str(raw["name"]),
        start_date=date.fromisoformat(raw["start_date"]),
        start_time=_parse_time(raw.get("start_time")),
        venue=CalendarVenue(
            term_id=int(venue["term_id"]), slug=str(venue["slug"]), name=str(venue["name"])
        ),
        geocoded_city=geolocation.get("city") or None,
        neighborhoods=tuple(str(n["slug"]) for n in raw.get("neighborhoods") or ()),
        cb_pick=bool(raw.get("cb_pick")),
        tags=tuple(str(t["slug"]) for t in raw.get("tags") or ()),
        price=str(raw.get("price") or ""),
        ticket_link=str(raw.get("ticket_link") or ""),
        repeats=bool(raw.get("repeats")),
        description_html=str(raw.get("description") or ""),
    )


def _parse_time(value: object) -> dtime | None:
    if not value:
        return None
    return dtime.fromisoformat(str(value))


def _parse_datetime_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)).date()
    except ValueError:
        return None
