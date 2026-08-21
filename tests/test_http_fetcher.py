"""HttpFetcher tests. No live network calls -- httpx is stubbed with respx (AGENTS.md)."""

from __future__ import annotations

import httpx
import pytest
import respx

from jazz_agent.adapters.http_fetcher import HttpFetcher, RobotsDisallowed

URL = "https://example.com/schedule"
ROBOTS_URL = "https://example.com/robots.txt"

SAMPLE_HTML = """
<html><body><article>
<h1>Tonight</h1>
<p>Bill Frisell Quartet plays tonight at 8pm.</p>
</article></body></html>
"""


@respx.mock
def test_get_returns_raw_html_not_extracted_text() -> None:
    """get() must return raw markup: extraction (issue #5) needs JSON-LD <script>
    blocks a prose extractor would discard."""
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    respx.get(URL).mock(return_value=httpx.Response(200, text=SAMPLE_HTML))
    fetcher = HttpFetcher()

    html = fetcher.get(URL)

    assert "Bill Frisell Quartet" in html
    assert "<article>" in html
    assert "<h1>Tonight</h1>" in html


@respx.mock
def test_robots_disallow_is_respected_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    respx.get(ROBOTS_URL).mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /schedule\n")
    )
    fetcher = HttpFetcher()

    with pytest.raises(RobotsDisallowed):
        fetcher.get(URL)

    assert "robots.txt disallows" in caplog.text


@respx.mock
def test_robots_allowed_path_is_fetched() -> None:
    respx.get(ROBOTS_URL).mock(
        return_value=httpx.Response(200, text="User-agent: *\nDisallow: /private/\n")
    )
    respx.get(URL).mock(return_value=httpx.Response(200, text=SAMPLE_HTML))
    fetcher = HttpFetcher()

    assert fetcher.get(URL)


@respx.mock
def test_robots_txt_missing_is_permissive() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    respx.get(URL).mock(return_value=httpx.Response(200, text=SAMPLE_HTML))
    fetcher = HttpFetcher()

    assert fetcher.get(URL)


@respx.mock
def test_robots_txt_is_only_fetched_once_per_host() -> None:
    robots_route = respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    respx.get(URL).mock(return_value=httpx.Response(200, text=SAMPLE_HTML))
    fetcher = HttpFetcher()

    fetcher.get(URL)
    fetcher.get(URL)

    assert robots_route.call_count == 1


@respx.mock
def test_backoff_retries_on_503_then_succeeds() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    route = respx.get(URL).mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(429),
            httpx.Response(200, text=SAMPLE_HTML),
        ]
    )
    sleeps: list[float] = []
    fetcher = HttpFetcher(sleep=sleeps.append)

    text = fetcher.get(URL)

    assert "Bill Frisell Quartet" in text
    assert route.call_count == 3
    assert sleeps == [1.0, 2.0]  # exponential: base * 2**attempt


@respx.mock
def test_backoff_exhausts_retries_and_raises() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    route = respx.get(URL).mock(return_value=httpx.Response(503))
    fetcher = HttpFetcher(max_retries=2, sleep=lambda _: None)

    with pytest.raises(httpx.HTTPStatusError):
        fetcher.get(URL)

    assert route.call_count == 3  # initial attempt + 2 retries


@respx.mock
def test_non_retryable_status_raises_immediately_without_sleeping() -> None:
    respx.get(ROBOTS_URL).mock(return_value=httpx.Response(404))
    route = respx.get(URL).mock(return_value=httpx.Response(404))
    sleeps: list[float] = []
    fetcher = HttpFetcher(sleep=sleeps.append)

    with pytest.raises(httpx.HTTPStatusError):
        fetcher.get(URL)

    assert route.call_count == 1
    assert sleeps == []


def test_get_rejects_js_render_mode() -> None:
    fetcher = HttpFetcher()

    with pytest.raises(ValueError, match="render_mode"):
        fetcher.get("https://example.com", render_mode="js")
