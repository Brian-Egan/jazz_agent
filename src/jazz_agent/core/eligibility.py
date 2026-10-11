"""DC eligibility, group assignment, slot placement and caps (DC sections 4, 5, 10).

Pure: inputs are event records, memberships and today's date. Everything here is computed
from feed fields before extraction and matching, which is what keeps Haiku cost at cents a
week (DC section 5).
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, time

from jazz_agent.core import dc_groups
from jazz_agent.core.dc_geo import WANTED_CITIES
from jazz_agent.core.weeks import dc_slot, dc_window

WEEKDAY_EVENING_START = time(19, 0)  # Mon-Fri. Friday is a weekday.
WEEKEND_EVENING_START = time(17, 0)  # Sat-Sun
EARLY_EVENING_START = time(18, 0)  # the early_evening profile, any day
MAX_RESIDENT_DATES = 3  # more distinct dates than this in the window = a resident

_JAM_WORD = re.compile(r"\bjam\b", re.IGNORECASE)
_FREE_TAGS = frozenset({"free", "no-cover"})
_FREE_PRICES = frozenset({"free", "no cover"})


@dataclass(frozen=True, slots=True)
class DcEvent:
    """One CapitalBop instance reduced to what eligibility reads."""

    instance_id: int
    act_name: str  # cleaned (entities and status markers gone), for display
    act_name_norm: str  # identity: cleaned, then suffix-stripped (core/normalize.py)
    show_date: date
    start_time: time | None
    club_id: str
    cb_pick: bool = False
    tags: tuple[str, ...] = ()
    price: str = ""
    city: str | None = None  # resolved, DC section 3; None = unknown
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class Membership:
    club_id: str
    group_key: str
    filter_profile: str


@dataclass(frozen=True, slots=True)
class PlannedAct:
    """One act on one playlist, placed by its earliest eligible show."""

    act_name_norm: str
    act_name: str
    earliest: date
    first_instance_id: int
    cb_pick: bool  # any eligible show of the act is a CB Pick


# --- predicates (DC section 5), one per rule -------------------------------------------


def is_not_cancelled(event: DcEvent) -> bool:
    return not event.cancelled


def is_not_jam(event: DcEvent) -> bool:
    return "jam-session" not in event.tags and not _JAM_WORD.search(event.act_name)


def is_cb_pick(event: DcEvent) -> bool:
    return event.cb_pick


def is_ticketed(event: DcEvent) -> bool:
    """A blank price passes: blank means unknown, not free."""
    if _FREE_TAGS & set(event.tags):
        return False
    return event.price.strip().lower() not in _FREE_PRICES


def is_evening(event: DcEvent) -> bool:
    """Mon-Fri from 19:00, Sat-Sun from 17:00. An unknown start time cannot be shown to be
    an evening show, so it fails."""
    if event.start_time is None:
        return False
    threshold = WEEKEND_EVENING_START if event.show_date.weekday() >= 5 else WEEKDAY_EVENING_START
    return event.start_time >= threshold


def starts_early_evening(event: DcEvent) -> bool:
    """The early_evening profile: 18:00 or later, any day."""
    return event.start_time is not None and event.start_time >= EARLY_EVENING_START


def is_not_resident(event: DcEvent, date_counts: Mapping[str, int]) -> bool:
    return date_counts.get(_act_key(event), 0) <= MAX_RESIDENT_DATES


def is_in_wanted_city(event: DcEvent) -> bool:
    return event.city in WANTED_CITIES


def _act_key(event: DcEvent) -> str:
    return event.act_name_norm.casefold()


def act_date_counts(events: Iterable[DcEvent], today: date) -> dict[str, int]:
    """Distinct show dates per act across **every** venue in the window, cancelled events
    excluded. A residency's regular nights make an act a resident; the count must not depend
    on which venue or profile an event is being judged under."""
    start, end = dc_window(today)
    dates: dict[str, set[date]] = defaultdict(set)
    for event in events:
        if is_not_cancelled(event) and start <= event.show_date <= end:
            dates[_act_key(event)].add(event.show_date)
    return {act: len(d) for act, d in dates.items()}


# --- filter profiles (DC section 4) ------------------------------------------------------


def passes_profile(profile: str, event: DcEvent, date_counts: Mapping[str, int]) -> bool:
    """Compose a profile from the predicates. Cancelled events and jam sessions are
    excluded under **every** profile, including ``all``."""
    if not (is_not_cancelled(event) and is_not_jam(event)):
        return False
    if profile == "all":
        return True
    if profile == "early_evening":
        return starts_early_evening(event)  # no ticket rule, no resident cap
    if profile not in ("standard", "standard_no_pick"):
        raise ValueError(f"unknown filter profile {profile!r}")
    standard_rules = (
        is_ticketed(event) and is_evening(event) and is_not_resident(event, date_counts)
    )
    if profile == "standard_no_pick":
        return standard_rules
    return is_cb_pick(event) and standard_rules


# --- group assignment ---------------------------------------------------------------------


def eligible_groups(
    event: DcEvent,
    memberships_by_club: Mapping[str, Sequence[Membership]],
    date_counts: Mapping[str, int],
) -> frozenset[str]:
    """The groups whose playlists this event may reach.

    A venue in a dedicated group feeds that group's playlist only, under its own profile: it
    is excluded from the dc CB Pick rule and from dc membership, so no show lands in two
    playlists (DC section 4). Every other venue reaches ``dc`` through a dc membership under
    its own profile, or as a CB Pick passing ``standard`` in a wanted city. The geography
    filter applies only to that CB Pick rule: member venues are, by being added, already
    known to be somewhere wanted.
    """
    members = memberships_by_club.get(event.club_id, ())
    dedicated = [m for m in members if dc_groups.is_dedicated(m.group_key)]
    if dedicated:
        return frozenset(
            m.group_key for m in dedicated if passes_profile(m.filter_profile, event, date_counts)
        )

    for m in members:
        if m.group_key == "dc" and passes_profile(m.filter_profile, event, date_counts):
            return frozenset({"dc"})
    if passes_profile("standard", event, date_counts) and is_in_wanted_city(event):
        return frozenset({"dc"})
    return frozenset()


# --- slots and caps (DC section 10) --------------------------------------------------------


def plan_playlists(
    events: Iterable[DcEvent], memberships: Iterable[Membership], today: date
) -> dict[str, tuple[PlannedAct, ...]]:
    """Which acts belong on which of the five playlists, keyed by ``slot_key``.

    Each act is placed in the slot of its **earliest** eligible show, so it never appears
    twice across the three dc slots. dc slots cap at ``DC_SLOT_CAP`` acts ranked CB Pick
    first, then earliest date, then act name; overflow is dropped from the playlist only (it
    stays in ``shows``). Within a playlist acts are ordered by earliest show then name.
    Deterministic: ties never fall back to input order.
    """
    start, end = dc_window(today)
    window_events = [e for e in events if start <= e.show_date <= end]
    by_club: dict[str, list[Membership]] = defaultdict(list)
    for m in memberships:
        by_club[m.club_id].append(m)
    counts = act_date_counts(window_events, today)

    # group -> act key -> that act's eligible shows
    shows: dict[str, dict[str, list[DcEvent]]] = defaultdict(lambda: defaultdict(list))
    for event in window_events:
        for group_key in eligible_groups(event, by_club, counts):
            shows[group_key][_act_key(event)].append(event)

    planned: dict[str, tuple[PlannedAct, ...]] = {}
    for playlist in dc_groups.PLAYLISTS:
        acts = [
            _plan_act(act_events)
            for act_events in shows.get(playlist.group_key, {}).values()
            if playlist.slot is None
            or dc_slot(_earliest(act_events).show_date, today) == playlist.slot
        ]
        if playlist.cap is not None:
            acts = sorted(acts, key=_rank)[: playlist.cap]
        planned[playlist.slot_key] = tuple(sorted(acts, key=_order))
    return planned


def _earliest(act_events: Sequence[DcEvent]) -> DcEvent:
    return min(act_events, key=lambda e: (e.show_date, e.instance_id))


def _plan_act(act_events: Sequence[DcEvent]) -> PlannedAct:
    first = _earliest(act_events)
    return PlannedAct(
        act_name_norm=first.act_name_norm,
        act_name=first.act_name,
        earliest=first.show_date,
        first_instance_id=first.instance_id,
        cb_pick=any(e.cb_pick for e in act_events),
    )


def _rank(act: PlannedAct) -> tuple[bool, date, str, int]:
    """Cap ranking: CB Pick first, then earliest date, then act name."""
    return (not act.cb_pick, act.earliest, act.act_name_norm.casefold(), act.first_instance_id)


def _order(act: PlannedAct) -> tuple[date, str, int]:
    """Playlist order: earliest show, then act name (DC section 10, reconciliation step 2)."""
    return (act.earliest, act.act_name_norm.casefold(), act.first_instance_id)
