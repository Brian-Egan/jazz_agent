"""ntfy.sh push notifications: chronic club failure (ADR-015) and new artists at LaPortas
(ADR-023).

Deliberately simple: raises on an HTTP failure rather than swallowing it.
"Ntfy failure must not fail the run" is the caller's responsibility
(pipeline/daily.py, pipeline/dc.py), not something this adapter hides.
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx


class NtfyNotifier:
    def __init__(
        self, topic: str, server: str = "https://ntfy.sh", client: httpx.Client | None = None
    ) -> None:
        self._topic = topic
        self._server = server.rstrip("/")
        self._client = client or httpx.Client(timeout=10.0)

    def alert(self, msg: str, title: str | None = None, tags: Sequence[str] = ()) -> None:
        # Same topic for every kind of push; a title and tags are how a new-artist push is
        # told apart from a failure alert (ADR-023). HTTP headers carry latin-1 only, so a
        # title with a middle dot is sent as ntfy's documented RFC 2047 encoded-word form.
        headers: dict[str, str] = {}
        if title:
            headers["Title"] = _header_value(title)
        if tags:
            headers["Tags"] = ",".join(tags)
        response = self._client.post(
            f"{self._server}/{self._topic}", content=msg.encode("utf-8"), headers=headers
        )
        response.raise_for_status()


def _header_value(text: str) -> str:
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        import base64

        return "=?UTF-8?B?" + base64.b64encode(text.encode("utf-8")).decode("ascii") + "?="
    return text
