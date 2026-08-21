"""Booking-week arithmetic. Tuesday-Monday, not ISO Monday-Sunday (ADR-008)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date, timedelta

DEFAULT_WEEK_START_DOW = 2  # ISO weekday: 1=Monday ... 7=Sunday. 2=Tuesday.


def week_start_date(d: date, week_start_dow: int = DEFAULT_WEEK_START_DOW) -> date:
    """Return the booking week's first day (the Tuesday, by default) on or before ``d``."""
    if not 1 <= week_start_dow <= 7:
        raise ValueError(f"week_start_dow must be an ISO weekday 1-7, got {week_start_dow}")
    days_since_start = (d.isoweekday() - week_start_dow) % 7
    return d - timedelta(days=days_since_start)


def week_range(week_start: date) -> tuple[date, date]:
    """Return (start, end), the inclusive 7-day span of the week starting at ``week_start``."""
    return week_start, week_start + timedelta(days=6)


def week_horizon(
    today: date, weeks_ahead: int, week_start_dow: int = DEFAULT_WEEK_START_DOW
) -> Iterator[date]:
    """Yield each week_start_date from this week through ``weeks_ahead`` weeks out, inclusive."""
    start = week_start_date(today, week_start_dow)
    for i in range(weeks_ahead + 1):
        yield start + timedelta(weeks=i)
