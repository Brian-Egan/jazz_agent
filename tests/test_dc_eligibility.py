"""core/eligibility.py and the Monday-window arithmetic in core/weeks.py (issue #25)."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import replace
from datetime import date, time, timedelta
from pathlib import Path

import pytest

from jazz_agent.core import dc_groups
from jazz_agent.core.dc_geo import resolve_city
from jazz_agent.core.eligibility import (
    DcEvent,
    Membership,
    act_date_counts,
    eligible_groups,
    is_cb_pick,
    is_evening,
    is_in_wanted_city,
    is_not_cancelled,
    is_not_jam,
    is_not_resident,
    is_ticketed,
    passes_profile,
    plan_playlists,
    planned_instance_ids,
    starts_early_evening,
)
from jazz_agent.core.normalize import clean_act_name, normalize_listing_name
from jazz_agent.core.weeks import dc_week_number, dc_window

FIXTURES = Path(__file__).parent / "fixtures" / "capitalbop"

# 2026-10-12 is a Monday, 10-16 a Friday, 10-17 a Saturday, 10-18 a Sunday.
MON, FRI, SAT, SUN = date(2026, 10, 12), date(2026, 10, 16), date(2026, 10, 17), date(2026, 10, 18)
TODAY = date(2026, 10, 11)  # a Sunday: week 1 began Monday 10-05


def event(**overrides: object) -> DcEvent:
    fields: dict[str, object] = {
        "instance_id": 1,
        "act_name": "Some Act",
        "act_name_norm": "Some Act",
        "show_date": FRI,
        "start_time": time(20, 0),
        "club_id": "cb-somewhere",
        "cb_pick": True,
        "tags": (),
        "price": "$20",
        "city": "Washington",
    }
    return DcEvent(**{**fields, **overrides})  # type: ignore[arg-type]


# --- weeks -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("today", "end"),
    [
        (date(2026, 10, 11), date(2026, 11, 15)),  # Sunday: still week 1 (Mon 10-05)
        (date(2026, 10, 12), date(2026, 11, 22)),  # Monday starts a new week 1
        (date(2026, 10, 10), date(2026, 11, 15)),  # Saturday
    ],
)
def test_window_runs_from_today_to_the_sunday_ending_week_six(today: date, end: date) -> None:
    assert dc_window(today) == (today, end)
    assert end.isoweekday() == 7


@pytest.mark.parametrize(
    ("d", "week"),
    [
        (date(2026, 10, 11), 1),
        (date(2026, 10, 18), 2),  # Sunday ending week 2
        (date(2026, 10, 19), 3),  # Monday starting week 3
        (date(2026, 11, 15), 6),  # last day of the window
        (date(2026, 11, 16), 7),
        (date(2026, 10, 10), 1),  # same week as today, though before it
        (date(2026, 10, 4), 0),
    ],
)
def test_week_numbers_are_monday_based(d: date, week: int) -> None:
    assert dc_week_number(d, TODAY) == week


# --- predicates --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "start", "expected"),
    [
        (FRI, time(18, 59), False),  # Friday is a weekday
        (FRI, time(19, 0), True),
        (MON, time(18, 59), False),
        (MON, time(19, 0), True),
        (SAT, time(16, 59), False),
        (SAT, time(17, 0), True),
        (SUN, time(16, 59), False),
        (SUN, time(17, 0), True),
        (FRI, None, False),
    ],
)
def test_is_evening_boundaries(day: date, start: time | None, expected: bool) -> None:
    assert is_evening(event(show_date=day, start_time=start)) is expected


@pytest.mark.parametrize(
    ("price", "tags", "expected"),
    [
        ("$20", (), True),
        ("", (), True),  # blank means unknown, not free
        ("   ", (), True),
        ("Free", (), False),
        ("free", (), False),
        ("No Cover", (), False),
        ("$20", ("free",), False),
        ("", ("no-cover",), False),
        ("$20", ("all-ages", "drink-minimum"), True),
    ],
)
def test_is_ticketed(price: str, tags: tuple[str, ...], expected: bool) -> None:
    assert is_ticketed(event(price=price, tags=tags)) is expected


@pytest.mark.parametrize(
    ("name", "tags", "expected"),
    [
        ("DC Jazz Jam", (), False),
        ("Takoma Station Jazz Jam", (), False),
        ("Jam Session at Marx", (), False),
        ("Some Quartet", ("jam-session",), False),
        ("The Jamal Moore Trio", (), True),  # a word, not a substring
        ("Jambalaya Brass", (), True),
        ("Some Quartet", ("live-music",), True),
    ],
)
def test_is_not_jam(name: str, tags: tuple[str, ...], expected: bool) -> None:
    assert is_not_jam(event(act_name=name, tags=tags)) is expected


@pytest.mark.parametrize(("dates", "expected"), [(1, True), (3, True), (4, False), (30, False)])
def test_resident_boundary_is_three_versus_four_dates(dates: int, expected: bool) -> None:
    window_events = [
        event(instance_id=i, show_date=TODAY + timedelta(days=i), club_id=f"cb-v{i}")
        for i in range(1, dates + 1)
    ]
    counts = act_date_counts(window_events, TODAY)

    assert counts == {"some act": dates}
    assert is_not_resident(window_events[0], counts) is expected


def test_resident_count_spans_venues_and_ignores_cancelled_and_out_of_window() -> None:
    events = [
        event(instance_id=1, show_date=date(2026, 10, 13), club_id="cb-a"),
        event(instance_id=2, show_date=date(2026, 10, 14), club_id="cb-b"),
        event(instance_id=3, show_date=date(2026, 10, 15), club_id="cb-c"),
        event(instance_id=4, show_date=date(2026, 10, 16), club_id="cb-d", cancelled=True),
        event(instance_id=5, show_date=date(2026, 12, 1), club_id="cb-e"),  # outside window
        event(instance_id=6, show_date=date(2026, 10, 15), club_id="cb-f"),  # same date again
    ]

    assert act_date_counts(events, TODAY) == {"some act": 3}


def test_resident_count_is_case_insensitive_on_the_normalized_name() -> None:
    events = [
        event(instance_id=i, act_name_norm=name, show_date=TODAY + timedelta(days=i))
        for i, name in enumerate(["Some Act", "SOME ACT", "some act", "Some Act"], start=1)
    ]

    assert act_date_counts(events, TODAY) == {"some act": 4}


@pytest.mark.parametrize(
    ("city", "expected"),
    [
        ("Washington", True),
        ("Alexandria", True),
        ("Arlington", True),
        ("Bethesda", False),
        (None, False),
    ],
)
def test_geography(city: str | None, expected: bool) -> None:
    assert is_in_wanted_city(event(city=city)) is expected


def test_remaining_predicates() -> None:
    assert is_cb_pick(event(cb_pick=True)) and not is_cb_pick(event(cb_pick=False))
    assert is_not_cancelled(event()) and not is_not_cancelled(event(cancelled=True))
    assert starts_early_evening(event(start_time=time(18, 0)))
    assert not starts_early_evening(event(start_time=time(17, 59)))
    assert not starts_early_evening(event(start_time=None))


# --- profiles -------------------------------------------------------------------------------


@pytest.mark.parametrize("profile", dc_groups.FILTER_PROFILES)
def test_jams_and_cancelled_events_are_excluded_under_every_profile(profile: str) -> None:
    assert not passes_profile(profile, event(act_name="DC Jazz Jam"), {})
    assert not passes_profile(profile, event(tags=("jam-session",)), {})
    assert not passes_profile(profile, event(cancelled=True), {})


def test_profile_rules() -> None:
    unpicked_free_early_resident = event(
        cb_pick=False, price="Free", start_time=time(18, 0), show_date=FRI
    )
    resident = {"some act": 9}

    assert passes_profile("all", unpicked_free_early_resident, resident)
    assert passes_profile("early_evening", unpicked_free_early_resident, resident)  # no ticket rule
    assert not passes_profile("standard", unpicked_free_early_resident, resident)
    assert not passes_profile("standard_no_pick", unpicked_free_early_resident, resident)

    standard_without_pick = event(cb_pick=False)
    assert passes_profile("standard_no_pick", standard_without_pick, {})
    assert not passes_profile("standard", standard_without_pick, {})
    assert passes_profile("standard", event(), {})
    assert not passes_profile("standard", event(), resident)  # resident cap
    assert not passes_profile("standard", event(start_time=time(18, 59)), {})


def test_unknown_profile_is_an_error_not_a_silent_pass() -> None:
    with pytest.raises(ValueError, match="unknown filter profile"):
        passes_profile("everything", event(), {})


# --- group assignment -----------------------------------------------------------------------


def _members(*rows: tuple[str, str, str]) -> dict[str, list[Membership]]:
    out: dict[str, list[Membership]] = {}
    for club, group, profile in rows:
        out.setdefault(club, []).append(Membership(club, group, profile))
    return out


def test_cb_pick_in_a_wanted_city_reaches_dc() -> None:
    assert eligible_groups(event(), {}, {}) == {"dc"}


def test_cb_pick_in_an_unwanted_or_unknown_city_does_not() -> None:
    assert eligible_groups(event(city="Bethesda"), {}, {}) == set()
    assert eligible_groups(event(city=None), {}, {}) == set()


def test_a_non_pick_without_membership_does_not() -> None:
    assert eligible_groups(event(cb_pick=False), {}, {}) == set()


def test_a_dedicated_group_venue_never_lands_in_dc_even_as_a_cb_pick() -> None:
    members = _members(("cb-blues-alley", "blues-alley", "all"))

    groups = eligible_groups(event(club_id="cb-blues-alley", cb_pick=True), members, {})

    assert groups == {"blues-alley"}


def test_a_dedicated_venue_failing_its_own_profile_reaches_nothing_not_dc() -> None:
    members = _members(("cb-laportas", "alxjazz", "early_evening"))

    groups = eligible_groups(
        event(club_id="cb-laportas", start_time=time(11, 30), cb_pick=True), members, {}
    )

    assert groups == set()


def test_dc_membership_at_a_dedicated_venue_is_ignored() -> None:
    members = _members(
        ("cb-birchmere", "alxjazz", "standard_no_pick"), ("cb-birchmere", "dc", "all")
    )

    assert eligible_groups(event(club_id="cb-birchmere"), members, {}) == {"alxjazz"}


def test_dc_member_venue_uses_its_own_profile_and_skips_geography() -> None:
    members = _members(("cb-cafe", "dc", "standard_no_pick"))
    show = event(club_id="cb-cafe", cb_pick=False, city=None)

    assert eligible_groups(show, members, {}) == {"dc"}
    assert eligible_groups(replace(show, price="Free"), members, {}) == set()


def test_a_dc_member_venue_failing_its_profile_can_still_qualify_as_a_cb_pick() -> None:
    members = _members(("cb-cafe", "dc", "early_evening"))

    # Starts 17:00 Friday: fails early_evening (18:00) and `standard` (evening) alike.
    assert eligible_groups(event(club_id="cb-cafe", start_time=time(17, 0)), members, {}) == set()
    assert eligible_groups(event(club_id="cb-cafe"), members, {}) == {"dc"}


# --- planning: caps, determinism ------------------------------------------------------


def _act(n: int, day: date, **overrides: object) -> DcEvent:
    name = f"Act {n:02d}"
    fields: dict[str, object] = {
        "instance_id": n,
        "act_name": name,
        "act_name_norm": name,
        "show_date": day,
        "club_id": f"cb-v{n}",
        **overrides,
    }
    return event(**fields)


def test_the_dc_playlist_covers_all_six_weeks_and_lists_an_act_once() -> None:
    events = [
        event(instance_id=1, show_date=date(2026, 11, 3)),  # week 4
        event(instance_id=2, show_date=date(2026, 10, 13)),  # week 1
        event(instance_id=3, show_date=date(2026, 10, 21), act_name="Other", act_name_norm="Other"),
    ]

    plan = plan_playlists(events, [], TODAY)

    assert [a.act_name_norm for a in plan["next-6-weeks"]] == ["Some Act", "Other"]
    assert plan["blues-alley"] == plan["alxjazz"] == ()


def test_the_cap_is_twelve_ranked_cb_pick_then_date_then_name() -> None:
    # 14 acts: 3 are not CB Picks (but are dc members), so they rank last.
    events = [_act(n, date(2026, 10, 13) + timedelta(days=n % 3)) for n in range(1, 12)]
    events += [_act(n, date(2026, 10, 12), cb_pick=False, club_id="cb-cafe") for n in range(12, 15)]
    members = [Membership("cb-cafe", "dc", "all")]

    plan = plan_playlists(events, members, TODAY)

    kept = {a.act_name_norm for a in plan["next-6-weeks"]}
    assert len(kept) == dc_groups.DC_CAP == 12
    # All 11 CB Picks are in; only one non-pick fits, and it is the alphabetically first
    # among those with the earliest date: Act 12.
    assert {f"Act {n:02d}" for n in range(1, 12)} <= kept
    assert "Act 12" in kept and "Act 13" not in kept and "Act 14" not in kept


def test_cap_ordering_is_deterministic_regardless_of_input_order() -> None:
    events = [_act(n, date(2026, 10, 13)) for n in range(1, 20)]  # 19 identical-rank acts

    first = plan_playlists(events, [], TODAY)
    second = plan_playlists(list(reversed(events)), [], TODAY)

    assert first == second
    assert [a.act_name_norm for a in first["next-6-weeks"]] == [
        f"Act {n:02d}" for n in range(1, 13)
    ]


def test_playlist_order_is_earliest_show_then_name() -> None:
    events = [
        _act(1, date(2026, 10, 15)),
        _act(2, date(2026, 10, 13)),
        _act(3, date(2026, 10, 13)),
    ]
    events[0] = replace(events[0], act_name="Zed", act_name_norm="Zed")

    plan = plan_playlists(events, [], TODAY)

    assert [a.act_name_norm for a in plan["next-6-weeks"]] == ["Act 02", "Act 03", "Zed"]


def test_venue_lists_are_uncapped_and_single_list_across_six_weeks() -> None:
    members = [Membership("cb-blues-alley", "blues-alley", "all")]
    events = [
        _act(n, TODAY + timedelta(days=n), club_id="cb-blues-alley", cb_pick=False)
        for n in range(1, 31)
    ]

    plan = plan_playlists(events, members, TODAY)

    assert len(plan["blues-alley"]) == 30
    assert plan["next-6-weeks"] == ()


def test_events_outside_the_window_are_ignored() -> None:
    events = [
        event(instance_id=1, show_date=date(2026, 10, 10)),  # yesterday
        event(instance_id=2, show_date=date(2026, 11, 16), act_name_norm="Later"),
    ]

    assert all(not acts for acts in plan_playlists(events, [], TODAY).values())


# --- the snapshot ------------------------------------------------------------------------------

SEEDED_MEMBERSHIPS = [
    Membership("cb-blues-alley", "blues-alley", "all"),
    Membership("cb-laportas", "alxjazz", "early_evening"),
    Membership("cb-the-birchmere", "alxjazz", "standard_no_pick"),
    Membership("cb-hotel-heron", "alxjazz", "all"),
    Membership("cb-good-fortune-rooftop-bar-hotel-heron", "alxjazz", "all"),
    Membership("cb-old-waterfront-park", "alxjazz", "all"),
]
SEEDED_CITY_OVERRIDES = {
    "cb-hotel-heron": "Alexandria",
    "cb-good-fortune-rooftop-bar-hotel-heron": "Alexandria",
    "cb-old-waterfront-park": "Alexandria",
    "cb-galaxy-hut": "Arlington",
}


def _snapshot_events() -> list[DcEvent]:
    raw = json.loads((FIXTURES / "eligibility_snapshot.json").read_text())["events"]
    events = []
    for r in raw:
        club_id = f"cb-{r['venue_slug']}"
        cleaned = clean_act_name(r["name"])
        events.append(
            DcEvent(
                instance_id=r["instance_id"],
                act_name=cleaned,
                act_name_norm=normalize_listing_name(r["name"]),
                show_date=date.fromisoformat(r["start_date"]),
                start_time=time.fromisoformat(r["start_time"]) if r["start_time"] else None,
                club_id=club_id,
                cb_pick=r["cb_pick"],
                tags=tuple(r["tags"]),
                price=r["price"] or "",
                city=resolve_city(
                    SEEDED_CITY_OVERRIDES.get(club_id), r["geocoded_city"], r["neighborhoods"]
                ),
            )
        )
    return events


def test_the_live_snapshot_reproduces_dc_section_5_counts_to_within_feed_drift() -> None:
    """DC section 5 measured `dc` 5 / 4 / 0, `blues-alley` 16, `alxjazz` 13 on 2026-10-10.

    That snapshot is not recoverable: CapitalBop's calendar fills in daily. On the
    2026-10-11 snapshot committed here (today = 10-11, window 10-11..11-15) the rules give
    the same 16 and 13 for the two venue lists and 10 for dc (it was 5 / 5 / 0 across three slots
    before the dc slots were merged into one six-week playlist).
    These are the numbers the rules produce on this data, pinned so a rule change shows up.
    """
    plan = plan_playlists(_snapshot_events(), SEEDED_MEMBERSHIPS, date(2026, 10, 11))

    counts = {slot: len(acts) for slot, acts in plan.items()}
    assert counts == {
        "next-6-weeks": 10,
        "blues-alley": 16,
        "alxjazz": 13,
    }
    assert (
        Counter(a.act_name_norm for acts in plan.values() for a in acts).most_common(1)[0][1] <= 2
    )


def test_the_snapshot_plan_is_stable_across_input_order() -> None:
    events = _snapshot_events()

    assert plan_playlists(events, SEEDED_MEMBERSHIPS, date(2026, 10, 11)) == plan_playlists(
        list(reversed(events)), SEEDED_MEMBERSHIPS, date(2026, 10, 11)
    )


def test_no_snapshot_show_lands_in_two_playlists() -> None:
    events = _snapshot_events()
    members = {}
    for m in SEEDED_MEMBERSHIPS:
        members.setdefault(m.club_id, []).append(m)
    counts = act_date_counts(events, date(2026, 10, 11))

    multi = [e for e in events if len(eligible_groups(e, members, counts)) > 1]

    assert multi == []


def test_planned_instance_ids_cover_every_eligible_date_of_a_planned_act_only() -> None:
    events = [
        event(instance_id=1, show_date=date(2026, 10, 13)),  # planned act, first date
        event(instance_id=2, show_date=date(2026, 10, 27)),  # same act, a later slot
        event(
            instance_id=3,
            show_date=date(2026, 10, 14),
            act_name="Free",
            act_name_norm="Free",
            price="Free",
        ),  # ineligible: free
        event(
            instance_id=4,
            show_date=date(2026, 10, 14),
            act_name="Late",
            act_name_norm="Late",
            start_time=time(15, 0),
        ),  # ineligible: not evening
        event(
            instance_id=5,
            show_date=date(2026, 10, 14),
            act_name="Jam",
            act_name_norm="Jam",
            tags=("jam-session",),
        ),
    ]

    assert planned_instance_ids(events, [], TODAY) == {1, 2}


def test_planned_instance_ids_leave_out_acts_the_cap_dropped() -> None:
    day = date(2026, 10, 13)
    events = [_act(n, day, cb_pick=n <= 12) for n in range(1, 15)]  # 12 picks, 2 non-picks

    planned = planned_instance_ids(events, [], TODAY)

    assert len(plan_playlists(events, [], TODAY)["next-6-weeks"]) == 12
    assert planned == set(range(1, 13))
