"""Mirror CapitalBop venues and events into the log (DC sections 2 and 3).

Every event in the window is stored, eligible or not: eligibility only decides what reaches
a playlist, and everything stored is answerable over MCP (DC section 1).
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date, datetime

from jazz_agent.core.dc_geo import resolve_city
from jazz_agent.core.html_text import html_to_text
from jazz_agent.core.models import CalendarEvent, CapitalbopEvent, Show
from jazz_agent.core.normalize import clean_act_name, normalize_act_name
from jazz_agent.ports.calendar import CalendarSource
from jazz_agent.ports.repository import CapitalbopRepo, ShowRepo, VenueMirrorRepo

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MirrorResult:
    events_stored: int
    # Absent from this complete fetch but stored for the window: treated as cancelled.
    cancelled_instance_ids: frozenset[int]
    # Series with no stored instance before this run; only these get an article request.
    new_series_ids: frozenset[int]
    # Venue names whose city could not be resolved: add a city_override (DC section 3).
    unknown_city_venues: tuple[str, ...]

    def detail(self) -> str:
        parts = [
            f"{self.events_stored} events",
            f"{len(self.cancelled_instance_ids)} cancelled",
            f"{len(self.new_series_ids)} new series",
        ]
        if self.unknown_city_venues:
            parts.append("unknown city: " + ", ".join(self.unknown_city_venues))
        return "; ".join(parts)


def mirror_calendar(
    source: CalendarSource,
    venue_repo: VenueMirrorRepo,
    show_repo: ShowRepo,
    event_repo: CapitalbopRepo,
    start: date,
    end: date,
    fetch_started_at: datetime,
) -> MirrorResult:
    """Fetch the whole window, then write. Fetching completes (or raises) before the first
    write, so a page failure stores nothing and cancels nothing: prior events are untouched
    and the sweep below only ever runs on a complete fetch."""
    venues = source.venues()
    events = source.events(start, end)

    for venue in venues:
        venue_repo.upsert_capitalbop_venue(venue.slug, venue.term_id, venue.name)
    # An event may name a venue the /calendar list lacks; shows.club_id needs the row.
    for event in events:
        venue_repo.upsert_capitalbop_venue(event.venue.slug, event.venue.term_id, event.venue.name)

    known_series = event_repo.known_event_ids(sorted({e.event_id for e in events}))
    overrides = venue_repo.city_overrides()
    unknown_city: dict[str, None] = {}

    for event in events:
        club_id = f"cb-{event.venue.slug}"
        if resolve_city(overrides.get(club_id), event.geocoded_city, event.neighborhoods) is None:
            unknown_city[event.venue.name] = None
        show_id = show_repo.upsert_show(_to_show(event, club_id))
        event_repo.upsert_event(_to_capitalbop_event(event, show_id), seen_at=fetch_started_at)

    # Safe only because fetching completed above.
    cancelled = event_repo.cancelled_instance_ids(start, end, fetch_started_at)
    new_series = {e.event_id for e in events} - known_series

    if unknown_city:
        logger.warning("capitalbop venues with unresolved city: %s", list(unknown_city))
    return MirrorResult(
        events_stored=len(events),
        cancelled_instance_ids=frozenset(cancelled),
        new_series_ids=frozenset(new_series),
        unknown_city_venues=tuple(unknown_city),
    )


def _to_show(event: CalendarEvent, club_id: str) -> Show:
    cleaned = clean_act_name(event.name)
    # The start time goes into set_times so every tool that reads it sees one shape.
    set_times = (event.start_time.strftime("%H:%M"),) if event.start_time else ()
    return Show(
        club_id=club_id,
        show_date=event.start_date,
        act_name_raw=cleaned,
        act_name_norm=normalize_act_name(cleaned),
        set_times=set_times,
        raw_text="\n".join(p for p in (event.name, html_to_text(event.description_html)) if p),
    )


def _to_capitalbop_event(event: CalendarEvent, show_id: int) -> CapitalbopEvent:
    return CapitalbopEvent(
        instance_id=event.instance_id,
        event_id=event.event_id,
        show_id=show_id,
        start_time=event.start_time,
        price_text=event.price,
        ticket_link=event.ticket_link,
        cb_pick=event.cb_pick,
        tags=event.tags,
        repeats=event.repeats,
        neighborhoods=event.neighborhoods,
        geocoded_city=event.geocoded_city,
        venue_address=event.venue_address or None,
        description_html=event.description_html,
        description_hash=hashlib.sha256(event.description_html.encode()).hexdigest(),
    )
