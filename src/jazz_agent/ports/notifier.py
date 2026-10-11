"""Alerting port (ARCHITECTURE.md section 10, ADR-015, ADR-023)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class Notifier(Protocol):
    def alert(self, msg: str, title: str | None = None, tags: Sequence[str] = ()) -> None:
        """Push one notification. ``title`` and ``tags`` tell kinds apart on the same topic:
        chronic-failure alerts carry neither, new-artist pushes carry both (ADR-023)."""
        ...
