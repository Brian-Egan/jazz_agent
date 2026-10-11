"""Calendar source port: the CapitalBop feed (DC section 2, section 15)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from jazz_agent.core.models import CalendarArticle, CalendarEvent, CalendarVenue


class CalendarFetchError(Exception):
    """The feed could not be read completely."""


class CalendarSource(Protocol):
    def venues(self) -> list[CalendarVenue]: ...

    def events(self, start: date, end: date) -> list[CalendarEvent]:
        """Every event from ``start`` through ``end`` inclusive. Returns the **complete**
        window or raises ``CalendarFetchError``; never a partial one, because a partial fetch
        would let the cancellation sweep cancel the rest of the calendar (DC section 2)."""
        ...

    def article(self, instance_id: int) -> CalendarArticle | None:
        """The linked magazine article for an instance, if it has one."""
        ...
