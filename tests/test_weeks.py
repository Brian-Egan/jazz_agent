from datetime import date, timedelta

from jazz_agent.core.weeks import retention_window, week_horizon, week_range, week_start_date


def test_week_start_date_properties_hold_across_boundaries() -> None:
    """The Tuesday on or before d: a Tuesday, <= d, within 7 days, and idempotent.

    These four properties uniquely determine the answer, so this holds for any
    date -- including a Monday/Tuesday boundary, a year boundary, and a leap day --
    without hand-computing expected weekdays.
    """
    candidates = [
        date(2026, 1, 1),  # arbitrary
        date(2025, 12, 29),  # year boundary
        date(2026, 1, 4),  # year boundary, other side
        date(2024, 2, 29),  # leap day
        date(2028, 2, 29),  # leap day, different leap year
        date(2028, 3, 1),  # day after a leap day
    ]
    for d in candidates:
        start = week_start_date(d)
        assert start.isoweekday() == 2
        assert start <= d
        assert (d - start).days < 7
        assert week_start_date(start) == start


def test_week_start_date_monday_tuesday_boundary() -> None:
    tuesday = date.fromisocalendar(2026, 20, 2)

    assert week_start_date(tuesday) == tuesday
    assert week_start_date(tuesday + timedelta(days=1)) == tuesday  # Wednesday
    assert week_start_date(tuesday + timedelta(days=6)) == tuesday  # following Monday

    prior_monday = tuesday - timedelta(days=1)
    assert week_start_date(prior_monday) == tuesday - timedelta(days=7)

    next_tuesday = tuesday + timedelta(days=7)
    assert week_start_date(next_tuesday) == next_tuesday


def test_week_start_date_custom_dow() -> None:
    # week_start_dow=1 is Monday.
    monday = date.fromisocalendar(2026, 20, 1)
    assert week_start_date(monday, week_start_dow=1) == monday
    assert week_start_date(monday + timedelta(days=6), week_start_dow=1) == monday


def test_week_range_is_seven_days_inclusive() -> None:
    start = date(2026, 5, 5)
    lo, hi = week_range(start)
    assert lo == start
    assert hi == start + timedelta(days=6)


def test_week_horizon_yields_weeks_ahead_inclusive() -> None:
    today = date(2026, 5, 6)  # a Wednesday, mid-week
    this_week_start = week_start_date(today)

    horizon = list(week_horizon(today, weeks_ahead=4))

    assert len(horizon) == 5
    assert horizon[0] == this_week_start
    assert horizon[-1] == this_week_start + timedelta(weeks=4)
    assert horizon == sorted(horizon)


def test_retention_window_default_six_live_playlists() -> None:
    today = date(2026, 8, 18)  # a Tuesday
    current_week = week_start_date(today)

    keep_from, keep_to = retention_window(today, retain_weeks_past=1, horizon_weeks_ahead=4)

    assert keep_from == current_week - timedelta(weeks=1)
    assert keep_to == current_week + timedelta(weeks=4)
    # six live playlists per club by default: -1, 0, +1, +2, +3, +4
    assert (keep_to - keep_from).days // 7 + 1 == 6
