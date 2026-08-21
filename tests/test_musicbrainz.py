"""MusicBrainzClient tests. No live calls -- httpx stubbed with respx. GraphRepo
is the real Postgres implementation (see conftest.py) so caching behavior is
verified against a real table, not a mock's bookkeeping.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import respx
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.musicbrainz import MusicBrainzClient
from jazz_agent.adapters.pg.graph_repo import PgGraphRepo
from jazz_agent.core.models import MbArtist, MbLookupMiss

SEARCH_URL = "https://musicbrainz.org/ws/2/artist"
FRISELL_MBID = "a21318db-f228-4a4d-8bce-6947a62985a5"
DETAIL_URL = f"https://musicbrainz.org/ws/2/artist/{FRISELL_MBID}"

SEARCH_HIT = {
    "artists": [{"id": FRISELL_MBID, "name": "Bill Frisell", "type": "Person", "score": 100}]
}
SEARCH_MISS: dict[str, Any] = {"artists": []}

MEMBER_OF_BAND_RELATION = {
    "target-type": "artist",
    "type": "member of band",
    "artist": {"id": "c7059057-b57a-401c-90ab-264dbd9742b1", "name": "Paul Motian Quintet"},
    "attributes": ["electric guitar"],
    "begin": "1990",
    "end": "2011-11-22",
}


def _detail(
    spotify_url: str | None = None, extra_relations: list[Any] | None = None
) -> dict[str, Any]:
    relations = list(extra_relations or [])
    if spotify_url:
        relations.append(
            {"target-type": "url", "type": "free streaming", "url": {"resource": spotify_url}}
        )
    return {
        "id": FRISELL_MBID,
        "name": "Bill Frisell",
        "type": "Person",
        "disambiguation": "American jazz guitarist",
        "tags": [{"name": "contemporary jazz"}, {"name": "jazz"}],
        "relations": relations,
    }


class FakeClock:
    """A controllable monotonic clock; sleep() advances it, mirroring real time."""

    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def _client(db: ConnectionPool, clock: FakeClock | None = None, **kwargs: Any) -> MusicBrainzClient:
    clock = clock or FakeClock()
    kwargs.setdefault("sleep", clock.sleep)
    kwargs.setdefault("now_monotonic", clock.now)
    return MusicBrainzClient(
        graph_repo=PgGraphRepo(db),
        user_agent="jazz_agent/1.0 (test; test@example.com)",
        **kwargs,
    )


@respx.mock
def test_resolves_hit_with_spotify_url(db: ConnectionPool) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_HIT))
    respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(
            200, json=_detail(spotify_url="https://open.spotify.com/artist/3SONlwqLIP2GtaMh9pLYe5")
        )
    )
    client = _client(db)

    artist = client.resolve_mbid("Bill Frisell")

    assert artist is not None
    assert artist.mbid == FRISELL_MBID
    assert artist.spotify_url == "https://open.spotify.com/artist/3SONlwqLIP2GtaMh9pLYe5"
    assert artist.entity_type == "Person"
    assert "jazz" in artist.tags


@respx.mock
def test_resolves_hit_without_spotify_url(db: ConnectionPool) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_HIT))
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, json=_detail(spotify_url=None)))
    client = _client(db)

    artist = client.resolve_mbid("Bill Frisell")

    assert artist is not None
    assert artist.spotify_url is None


@respx.mock
def test_no_hit_returns_none_and_negative_caches(db: ConnectionPool) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_MISS))
    client = _client(db)

    artist = client.resolve_mbid("Some Totally Obscure Act")

    assert artist is None
    repo = PgGraphRepo(db)
    miss = repo.get_lookup_miss("some totally obscure act")
    assert miss is not None
    assert miss.attempts == 1


@respx.mock
def test_503_after_retries_returns_none_and_does_not_negative_cache(db: ConnectionPool) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(503))
    client = _client(db, max_retries=2)

    artist = client.resolve_mbid("Bill Frisell")

    assert artist is None
    repo = PgGraphRepo(db)
    assert repo.get_lookup_miss("bill frisell") is None  # not cached -- retried next call


@respx.mock
def test_timeout_returns_none_and_does_not_negative_cache(db: ConnectionPool) -> None:
    respx.get(SEARCH_URL).mock(side_effect=httpx.TimeoutException("timed out"))
    client = _client(db, max_retries=1)

    artist = client.resolve_mbid("Bill Frisell")

    assert artist is None
    repo = PgGraphRepo(db)
    assert repo.get_lookup_miss("bill frisell") is None


@respx.mock
def test_cached_hit_issues_no_http_request(db: ConnectionPool) -> None:
    repo = PgGraphRepo(db)
    repo.upsert_mb_artist(MbArtist(mbid=FRISELL_MBID, name="Bill Frisell", entity_type="Person"))
    client = _client(db)

    artist = client.resolve_mbid("Bill Frisell")

    assert artist is not None
    assert artist.mbid == FRISELL_MBID
    assert not respx.calls  # no HTTP request made at all


@respx.mock
def test_cached_miss_inside_ttl_issues_no_request(db: ConnectionPool) -> None:
    repo = PgGraphRepo(db)
    repo.record_lookup_miss(
        MbLookupMiss(
            name_norm="some obscure act",
            miss_until=datetime.now(UTC) + timedelta(days=29),
            attempts=1,
        )
    )
    client = _client(db)

    artist = client.resolve_mbid("Some Obscure Act")

    assert artist is None
    assert not respx.calls


@respx.mock
def test_cached_miss_outside_ttl_retries(db: ConnectionPool) -> None:
    repo = PgGraphRepo(db)
    repo.record_lookup_miss(
        MbLookupMiss(
            name_norm="some obscure act",
            miss_until=datetime.now(UTC) - timedelta(days=1),  # expired
            attempts=1,
        )
    )
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_HIT))
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, json=_detail()))
    client = _client(db)

    artist = client.resolve_mbid("Some Obscure Act")

    assert artist is not None
    assert respx.calls  # the expired miss did not block a retry


@respx.mock
def test_rate_limiting_no_two_requests_within_1s(db: ConnectionPool) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_HIT))
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, json=_detail()))
    clock = FakeClock()
    client = _client(db, clock=clock)

    client.resolve_mbid("Bill Frisell")  # search + detail: two sequential requests

    assert clock.sleeps == [1.0]  # exactly the gap enforced between them


@respx.mock
def test_rate_limiting_across_separate_resolve_calls(db: ConnectionPool) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_MISS))
    clock = FakeClock()
    client = _client(db, clock=clock)

    client.resolve_mbid("Artist A")
    client.resolve_mbid("Artist B")

    assert clock.sleeps == [1.0]  # one gap enforced between the two searches


@respx.mock
def test_edge_date_ranges_are_persisted(db: ConnectionPool) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_HIT))
    respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(200, json=_detail(extra_relations=[MEMBER_OF_BAND_RELATION]))
    )
    client = _client(db)

    client.resolve_mbid("Bill Frisell")

    edges = client.edges_for(FRISELL_MBID)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.dst_name == "Paul Motian Quintet"
    assert edge.instruments == ("electric guitar",)
    assert edge.begin_date == date(1990, 1, 1)
    assert edge.end_date == date(2011, 11, 22)


@respx.mock
def test_spotify_url_for_reads_from_cache(db: ConnectionPool) -> None:
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=SEARCH_HIT))
    respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(
            200, json=_detail(spotify_url="https://open.spotify.com/artist/3SONlwqLIP2GtaMh9pLYe5")
        )
    )
    client = _client(db)
    client.resolve_mbid("Bill Frisell")

    assert (
        client.spotify_url_for(FRISELL_MBID)
        == "https://open.spotify.com/artist/3SONlwqLIP2GtaMh9pLYe5"
    )
    assert client.spotify_url_for("00000000-0000-0000-0000-000000000000") is None
