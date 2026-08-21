"""httpx-based fetcher with browser-like headers, robots.txt, and backoff (ARCHITECTURE.md
section 4, ADR-004).

A realistic header set is not optional politeness: Birdland returns 403
Forbidden to a bare request.

get() returns raw HTML, matching the Fetcher port's `-> Html` signature
(ARCHITECTURE.md section 2) -- not pre-converted text. Extraction (issue #5)
needs the raw markup to opportunistically read JSON-LD schema.org/Event
blocks before falling back to trafilatura + an LLM call, and JSON-LD lives
in <script> tags that a prose extractor discards.
"""

from __future__ import annotations

import logging
import time
import urllib.robotparser
from collections.abc import Callable
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    """robots.txt disallows fetching this URL for our user agent."""


class HttpFetcher:
    def __init__(
        self,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        backoff_base_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._client = client or httpx.Client(headers=_HEADERS, timeout=10.0, follow_redirects=True)
        self._max_retries = max_retries
        self._backoff_base_seconds = backoff_base_seconds
        self._sleep = sleep
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

    def get(self, url: str, render_mode: str = "http") -> str:
        if render_mode != "http":
            raise ValueError(f"HttpFetcher only handles render_mode='http', got {render_mode!r}")

        if not self._allowed_by_robots(url):
            logger.warning("robots.txt disallows fetching %s", url)
            raise RobotsDisallowed(f"robots.txt disallows fetching {url}")

        return self._get_with_backoff(url)

    def _allowed_by_robots(self, url: str) -> bool:
        host = urlparse(url).netloc
        parser = self._robots_cache.get(host)
        if parser is None:
            parser = self._fetch_robots(url)
            self._robots_cache[host] = parser
        return parser.can_fetch(_HEADERS["User-Agent"], url)

    def _fetch_robots(self, url: str) -> urllib.robotparser.RobotFileParser:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        try:
            response = self._client.get(robots_url)
        except httpx.HTTPError:
            response = None

        if response is not None and response.status_code == 200:
            parser.parse(response.text.splitlines())
            parser.modified()  # activates rule evaluation in can_fetch()
        else:
            # Missing or unreachable robots.txt: RobotFileParser defaults to
            # disallowing everything until it's told otherwise, which is the
            # wrong default here -- nothing published means nothing to obey.
            parser.allow_all = True  # type: ignore[attr-defined]  # real attr, missing from typeshed
        return parser

    def _get_with_backoff(self, url: str) -> str:
        attempt = 0
        while True:
            try:
                response = self._client.get(url)
            except httpx.TimeoutException:
                if attempt >= self._max_retries:
                    raise
                self._sleep(self._backoff_base_seconds * (2**attempt))
                attempt += 1
                continue

            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                self._sleep(self._backoff_base_seconds * (2**attempt))
                attempt += 1
                continue

            response.raise_for_status()
            return response.text
