"""Rolling-playlist descriptions (DC section 10): who is playing, when and where.

Pure. ``Maria Grand (Sun 10/18, Rhizome) · Aaron Myers (Fri 10/23, Mr. Henry's)``, in show
order, cut at Spotify's 300-character limit on an entry boundary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

DESCRIPTION_LIMIT = 300
_SEPARATOR = " · "
_ELLIPSIS = "…"
_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # locale-independent


@dataclass(frozen=True, slots=True)
class DescriptionEntry:
    name: str
    show_date: date
    venue: str


def format_entry(entry: DescriptionEntry) -> str:
    when = f"{_WEEKDAYS[entry.show_date.weekday()]} {entry.show_date.month}/{entry.show_date.day}"
    return f"{entry.name} ({when}, {entry.venue})"


def build_description(entries: Sequence[DescriptionEntry], limit: int = DESCRIPTION_LIMIT) -> str:
    """Whole entries only, then an ellipsis if any were left out. Never exceeds ``limit``."""
    text = ""
    for i, entry in enumerate(entries):
        candidate = format_entry(entry) if not text else text + _SEPARATOR + format_entry(entry)
        left_out = i < len(entries) - 1
        if len(candidate) + (len(_ELLIPSIS) if left_out else 0) > limit:
            return _truncate(text, limit) if text else _truncate(format_entry(entry), limit)
        text = candidate
    return text


def _truncate(text: str, limit: int) -> str:
    if len(text) + len(_ELLIPSIS) <= limit:
        return text + _ELLIPSIS
    return text[: limit - len(_ELLIPSIS)].rstrip() + _ELLIPSIS
