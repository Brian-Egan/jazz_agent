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


def retention_window(
    today: date,
    retain_weeks_past: int,
    horizon_weeks_ahead: int,
    week_start_dow: int = DEFAULT_WEEK_START_DOW,
) -> tuple[date, date]:
    """Return (keep_from, keep_to): the inclusive range of week_start_dates that
    should stay followed (ARCHITECTURE.md section 9, ADR-009)."""
    current = week_start_date(today, week_start_dow)
    return (
        current - timedelta(weeks=retain_weeks_past),
        current + timedelta(weeks=horizon_weeks_ahead),
    )


# --- DC: Monday-start rolling window (DC section 5, ADR-018) -----------------------------

MONDAY = 1
DC_WINDOW_WEEKS = 6


def dc_window(today: date) -> tuple[date, date]:
    """(today, the Sunday ending week 6). Week 1 is the Monday-start week containing
    ``today``; the window opens at ``today`` itself, so shows earlier today are still in."""
    return today, week_start_date(today, MONDAY) + timedelta(weeks=DC_WINDOW_WEEKS, days=-1)


def dc_week_number(d: date, today: date) -> int:
    """1-based Monday-start week of ``d`` relative to ``today``'s week (week 1). Values below
    1 are in the past; above 6 are beyond the window."""
    return (week_start_date(d, MONDAY) - week_start_date(today, MONDAY)).days // 7 + 1


def dc_slot(d: date, today: date) -> int | None:
    """Slot 1, 2 or 3 (weeks 1-2, 3-4, 5-6) for a show date, or None outside the window."""
    start, end = dc_window(today)
    if not start <= d <= end:
        return None
    return (dc_week_number(d, today) + 1) // 2
