"""core/dc_description.py (DC section 10)."""

from __future__ import annotations

from datetime import date

from jazz_agent.core.dc_description import DESCRIPTION_LIMIT, DescriptionEntry, build_description


def entry(name: str, day: date = date(2026, 10, 18), venue: str = "Rhizome") -> DescriptionEntry:
    return DescriptionEntry(name, day, venue)


def test_entries_are_joined_in_order_with_weekday_date_and_venue() -> None:
    text = build_description(
        [entry("Maria Grand"), entry("Aaron Myers", date(2026, 10, 23), "Mr. Henry's")]
    )

    assert text == "Maria Grand (Sun 10/18, Rhizome) · Aaron Myers (Fri 10/23, Mr. Henry's)"


def test_never_exceeds_the_limit_and_cuts_on_an_entry_boundary() -> None:
    entries = [entry(f"Artist Number {i:02d}") for i in range(40)]

    text = build_description(entries)

    assert len(text) <= DESCRIPTION_LIMIT
    assert text.endswith("…")
    kept = text.removesuffix("…").split(" · ")
    assert all(part.endswith("Rhizome)") for part in kept)  # no half entry


def test_exactly_fitting_list_is_not_marked_truncated() -> None:
    text = build_description([entry("A")])

    assert not text.endswith("…")


def test_a_single_overlong_entry_is_still_cut_to_the_limit() -> None:
    assert len(build_description([entry("x" * 500)])) <= DESCRIPTION_LIMIT


def test_empty_list_is_an_empty_description() -> None:
    assert build_description([]) == ""
