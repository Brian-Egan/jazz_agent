"""MusicBrainz artist graph adapter (ARCHITECTURE.md section 6 stage 5, section 12;
ADR-005).

MusicBrainz is the only replacement for the artist adjacency Spotify
withdrew in November 2024 (related-artists, recommendations both gone).
Caching is built into resolve_mbid() itself: a permanent cache in
mb_artists/mb_artist_edges and a 30-day negative cache in mb_lookup_misses
mean a previously-resolved or recently-missed name issues no HTTP request.

Genuine "no hit" (searched, nothing matched) is negative-cached for
MISS_TTL_DAYS. A request failure (timeout, 5xx after retries) is NOT
negative-cached -- ARCHITECTURE.md section 6 stage 5 treats these as two
different outcomes ("no MBID hit" vs "timeout or 5xx"), and caching a
transient infrastructure failure for 30 days would wrongly suppress a
retry the next run would otherwise make.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from jazz_agent.core.models import MbArtist, MbArtistEdge, MbLookupMiss
from jazz_agent.ports.repository import GraphRepo

_API_BASE = "https://musicbrainz.org/ws/2"
_RETRYABLE_STATUS = {500, 502, 503, 504}
_EDGE_TYPES = {"member of band", "collaboration", "parent"}


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MusicBrainzClient:
    def __init__(
        self,
        graph_repo: GraphRepo,
        user_agent: str,
        timeout_seconds: float = 2.0,
        miss_ttl_days: int = 30,
        client: httpx.Client | None = None,
        max_retries: int = 2,
        min_interval_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = _utcnow,
        now_monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._graph_repo = graph_repo
        self._timeout_seconds = timeout_seconds
        self._miss_ttl_days = miss_ttl_days
        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent, "Accept": "application/json"}
        )
        self._max_retries = max_retries
        self._min_interval_seconds = min_interval_seconds
        self._sleep = sleep
        self._now = now
        self._now_monotonic = now_monotonic
        self._last_request_at: float | None = None

    def resolve_mbid(self, name: str) -> MbArtist | None:
        cached_artist = self._graph_repo.get_mb_artist_by_name(name)
        if cached_artist is not None:
            return cached_artist  # cache hit: no HTTP request

        name_norm = _normalize(name)
        cached_miss = self._graph_repo.get_lookup_miss(name_norm)
        if cached_miss is not None and cached_miss.miss_until > self._now():
            return None  # cached miss inside TTL: no request

        candidates = self._search(name)
        if candidates is None:
            return None  # request failed; not cached, so it's retried next call

        if not candidates:
            self._record_miss(name_norm, cached_miss)
            return None  # confirmed no hit: negative-cached

        detail = self._fetch_detail(candidates[0]["id"])
        if detail is None:
            return None  # request failed; not cached

        artist = _parse_artist(detail)
        self._graph_repo.upsert_mb_artist(artist)
        edges = _parse_edges(artist.mbid, detail)
        if edges:
            self._graph_repo.record_edges(edges)
        return artist

    def spotify_url_for(self, mbid: str) -> str | None:
        artist = self._graph_repo.get_mb_artist(mbid)
        return artist.spotify_url if artist else None

    def edges_for(self, mbid: str) -> list[MbArtistEdge]:
        return self._graph_repo.edges_for(mbid)

    def _record_miss(self, name_norm: str, cached_miss: MbLookupMiss | None) -> None:
        self._graph_repo.record_lookup_miss(
            MbLookupMiss(
                name_norm=name_norm,
                miss_until=self._now() + timedelta(days=self._miss_ttl_days),
                attempts=(cached_miss.attempts + 1) if cached_miss else 1,
            )
        )

    def _search(self, name: str) -> list[dict[str, Any]] | None:
        response = self._rate_limited_get(f"{_API_BASE}/artist", {"query": name, "fmt": "json"})
        if response is None:
            return None
        result: list[dict[str, Any]] = response.json().get("artists", [])
        return result

    def _fetch_detail(self, mbid: str) -> dict[str, Any] | None:
        response = self._rate_limited_get(
            f"{_API_BASE}/artist/{mbid}",
            {"inc": "artist-rels+url-rels+tags", "fmt": "json"},
        )
        if response is None:
            return None
        result: dict[str, Any] = response.json()
        return result

    def _rate_limited_get(self, url: str, params: dict[str, Any]) -> httpx.Response | None:
        attempt = 0
        while True:
            self._wait_for_rate_limit()
            try:
                response = self._client.get(url, params=params, timeout=self._timeout_seconds)
            except httpx.TimeoutException:
                self._last_request_at = self._now_monotonic()
                if attempt >= self._max_retries:
                    return None
                self._sleep(2**attempt)
                attempt += 1
                continue

            self._last_request_at = self._now_monotonic()

            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                self._sleep(2**attempt)
                attempt += 1
                continue

            return response if response.status_code == 200 else None

    def _wait_for_rate_limit(self) -> None:
        if self._last_request_at is None:
            return
        elapsed = self._now_monotonic() - self._last_request_at
        remaining = self._min_interval_seconds - elapsed
        if remaining > 0:
            self._sleep(remaining)


def _normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


def _parse_artist(detail: dict[str, Any]) -> MbArtist:
    return MbArtist(
        mbid=detail["id"],
        name=detail["name"],
        entity_type=detail.get("type"),
        disambiguation=detail.get("disambiguation") or None,
        tags=tuple(t["name"] for t in detail.get("tags", [])),
        spotify_url=_spotify_url(detail.get("relations", [])),
    )


def _spotify_url(relations: list[dict[str, Any]]) -> str | None:
    for rel in relations:
        if rel.get("target-type") != "url":
            continue
        resource = (rel.get("url") or {}).get("resource", "")
        if "open.spotify.com/artist/" in resource:
            return str(resource)
    return None


def _parse_edges(src_mbid: str, detail: dict[str, Any]) -> list[MbArtistEdge]:
    edges = []
    for rel in detail.get("relations", []):
        if rel.get("target-type") != "artist" or rel.get("type") not in _EDGE_TYPES:
            continue
        target = rel.get("artist") or {}
        if not target.get("id"):
            continue
        edges.append(
            MbArtistEdge(
                src_mbid=src_mbid,
                dst_mbid=target["id"],
                dst_name=target.get("name", ""),
                edge_type=rel["type"],
                instruments=tuple(a for a in rel.get("attributes", []) if a),
                begin_date=_parse_partial_date(rel.get("begin")),
                end_date=_parse_partial_date(rel.get("end")),
            )
        )
    return edges


def _parse_partial_date(value: str | None) -> date | None:
    if not value:
        return None
    parts = value.split("-")
    try:
        year = int(parts[0])
        month = int(parts[1]) if len(parts) > 1 else 1
        day = int(parts[2]) if len(parts) > 2 else 1
        return date(year, month, day)
    except ValueError:
        return None
