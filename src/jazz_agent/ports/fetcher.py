"""Fetch layer port (ARCHITECTURE.md section 4)."""

from __future__ import annotations

from typing import Protocol


class Fetcher(Protocol):
    def get(self, url: str, render_mode: str = "http") -> str:
        """Fetch a page and return its raw HTML. render_mode is 'http' or 'js'."""
        ...
