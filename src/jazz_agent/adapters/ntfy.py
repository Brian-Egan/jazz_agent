"""ntfy.sh push notifications, for chronic club failure only (ADR-015).

Deliberately simple: raises on an HTTP failure rather than swallowing it.
"Ntfy failure must not fail the run" is the caller's responsibility
(pipeline/daily.py), not something this adapter hides.
"""

from __future__ import annotations

import httpx


class NtfyNotifier:
    def __init__(
        self, topic: str, server: str = "https://ntfy.sh", client: httpx.Client | None = None
    ) -> None:
        self._topic = topic
        self._server = server.rstrip("/")
        self._client = client or httpx.Client(timeout=10.0)

    def alert(self, msg: str) -> None:
        response = self._client.post(f"{self._server}/{self._topic}", content=msg.encode("utf-8"))
        response.raise_for_status()
