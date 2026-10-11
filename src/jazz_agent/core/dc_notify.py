"""The "new at LaPortas" push (DC section 12, ADR-023). Pure formatting."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, time

TITLE = "AlxJazz · new at LaPortas"
TAGS = ("new-artist",)  # distinguishes it from chronic-failure alerts on the same topic
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True, slots=True)
class NewArtist:
    name: str
    show_date: date
    start_time: time | None


def format_time(t: time) -> str:
    """18:30 -> ``6:30pm``; 12:00 -> ``12:00pm``; 00:15 -> ``12:15am``."""
    hour = t.hour % 12 or 12
    return f"{hour}:{t.minute:02d}{'pm' if t.hour >= 12 else 'am'}"


def format_line(artist: NewArtist) -> str:
    when = (
        f"{_WEEKDAYS[artist.show_date.weekday()]} {artist.show_date.month}/{artist.show_date.day}"
    )
    if artist.start_time is not None:
        when += f", {format_time(artist.start_time)}"
    return f"{artist.name} — {when}"


def format_message(artists: Sequence[NewArtist]) -> str:
    ordered = sorted(
        artists, key=lambda a: (a.show_date, a.start_time or time(0), a.name.casefold())
    )
    return "\n".join(format_line(a) for a in ordered)
