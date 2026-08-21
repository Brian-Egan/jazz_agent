"""Extraction port (ARCHITECTURE.md section 5)."""

from __future__ import annotations

from typing import Protocol

from jazz_agent.core.models import ExtractedShow


class Extractor(Protocol):
    def extract(self, text: str, window: int) -> list[ExtractedShow]:
        """Extract every show mentioned in club listing text, today through
        ``window`` weeks ahead."""
        ...
