"""Alerting port (ARCHITECTURE.md section 10, ADR-015)."""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    def alert(self, msg: str) -> None: ...
