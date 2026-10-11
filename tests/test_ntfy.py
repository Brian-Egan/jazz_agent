"""adapters/ntfy.py and core/dc_notify.py (DC section 12, ADR-023). httpx stubbed with respx."""

from __future__ import annotations

import base64
from datetime import date, time

import httpx
import respx

from jazz_agent.adapters.ntfy import NtfyNotifier
from jazz_agent.core.dc_notify import NewArtist, format_line, format_message, format_time


@respx.mock
def test_a_plain_alert_posts_the_body_with_no_title_or_tags() -> None:
    route = respx.post("https://ntfy.sh/jazz").mock(return_value=httpx.Response(200))

    NtfyNotifier("jazz").alert("Club has failed 3 runs")

    request = route.calls.last.request
    assert request.content == b"Club has failed 3 runs"
    assert "title" not in request.headers and "tags" not in request.headers


@respx.mock
def test_a_titled_push_sends_title_and_tags_on_the_same_topic() -> None:
    route = respx.post("https://ntfy.sh/jazz").mock(return_value=httpx.Response(200))

    NtfyNotifier("jazz").alert("Esther", title="AlxJazz · new at LaPortas", tags=("new-artist",))

    headers = route.calls.last.request.headers
    assert headers["tags"] == "new-artist"
    encoded = headers["title"]
    assert encoded.startswith("=?UTF-8?B?")  # non-ASCII, so RFC 2047, which ntfy decodes
    decoded = base64.b64decode(encoded.removeprefix("=?UTF-8?B?").removesuffix("?=")).decode()
    assert decoded == "AlxJazz · new at LaPortas"


def test_time_and_line_formatting() -> None:
    assert format_time(time(18, 30)) == "6:30pm"
    assert format_time(time(12, 0)) == "12:00pm"
    assert format_time(time(0, 15)) == "12:15am"
    line = format_line(NewArtist("Esther Haynes", date(2026, 10, 21), time(18, 30)))
    assert line == "Esther Haynes — Wed 10/21, 6:30pm"
    assert format_line(NewArtist("Kai", date(2026, 10, 21), None)) == "Kai — Wed 10/21"


def test_the_message_lists_artists_in_show_order() -> None:
    message = format_message(
        [
            NewArtist("Later", date(2026, 10, 28), time(19, 0)),
            NewArtist("Sooner", date(2026, 10, 21), time(18, 30)),
        ]
    )

    assert message.splitlines()[0].startswith("Sooner")
