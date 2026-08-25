"""Extraction port (ARCHITECTURE.md section 5)."""

from __future__ import annotations

from datetime import date
from typing import Protocol

from jazz_agent.core.models import ExtractedShow


class Extractor(Protocol):
    def extract(
        self, text: str, window: int, today: date, venue_label: str | None = None
    ) -> list[ExtractedShow]:
        """Extract every show mentioned in club listing text, ``today`` through
        ``window`` weeks ahead. ``today`` grounds the model for dates the page
        lists without a year -- without it, a year-ambiguous date is a guess.

        ``venue_label``, when set, means the text covers multiple venues on one
        page (clubs.venue_label) -- only shows explicitly labeled for this venue
        are returned, everything else on the page is ignored."""
        ...
