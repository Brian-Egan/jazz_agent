"""SpotifyClient tests. No live calls -- httpx is stubbed with respx."""

from __future__ import annotations

import json

import httpx
import respx

from jazz_agent.adapters.spotify import SpotifyClient
from jazz_agent.ports.music import MusicService

TOKEN_URL = "https://accounts.spotify.com/api/token"


def _client(**kwargs: object) -> SpotifyClient:
    return SpotifyClient(client_id="cid", client_secret="csecret", refresh_token="rtoken", **kwargs)


def _mock_token(access_token: str = "access-1", expires_in: int = 3600) -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(
            200, json={"access_token": access_token, "expires_in": expires_in}
        )
    )


@respx.mock
def test_search_artists() -> None:
    _mock_token()
    respx.get("https://api.spotify.com/v1/search").mock(
        return_value=httpx.Response(
            200, json={"artists": {"items": [{"id": "abc", "name": "Bill Frisell"}]}}
        )
    )
    client = _client()

    results = client.search_artists("Bill Frisell")

    assert results == [{"id": "abc", "name": "Bill Frisell"}]


@respx.mock
def test_get_artist_albums_requests_album_include_group_only() -> None:
    _mock_token()
    route = respx.get("https://api.spotify.com/v1/artists/abc/albums").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "alb1"}]})
    )
    client = _client()

    albums = client.get_artist_albums("abc")

    assert albums == [{"id": "alb1"}]
    assert route.calls.last.request.url.params["include_groups"] == "album"
    assert route.calls.last.request.url.params["limit"] == "10"


@respx.mock
def test_get_artist_albums_pages_through_all_results() -> None:
    # limit=10 is this endpoint's real ceiling for apps registered after
    # Nov 2024 (ADR-005) -- confirmed by hand against the live API, since
    # it 400s above 10 despite older docs citing 50. A prolific artist still
    # needs every album-type release collected across pages, not just the
    # first ten, since select_album compares popularity across the full set.
    _mock_token()
    route = respx.get("https://api.spotify.com/v1/artists/abc/albums").mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "items": [{"id": "alb1"}],
                    "next": "https://api.spotify.com/v1/artists/abc/albums?offset=1&limit=10",
                },
            ),
            httpx.Response(200, json={"items": [{"id": "alb2"}], "next": None}),
        ]
    )
    client = _client()

    albums = client.get_artist_albums("abc")

    assert albums == [{"id": "alb1"}, {"id": "alb2"}]
    assert route.calls[0].request.url.params["offset"] == "0"
    assert route.calls[1].request.url.params["offset"] == "1"


@respx.mock
def test_get_album_tracks() -> None:
    _mock_token()
    respx.get("https://api.spotify.com/v1/albums/alb1/tracks").mock(
        return_value=httpx.Response(200, json={"items": [{"id": "trk1"}]})
    )
    client = _client()

    tracks = client.get_album_tracks("alb1")

    assert tracks == [{"id": "trk1"}]


@respx.mock
def test_create_playlist_posts_to_me_playlists() -> None:
    # POST /users/{user_id}/playlists 403s for apps registered after Nov 2024
    # (ADR-005) -- confirmed live against the real API. /me/playlists is the
    # one that still works, and needs no separate GET /me lookup first.
    _mock_token()
    route = respx.post("https://api.spotify.com/v1/me/playlists").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": "playlist1",
                "external_urls": {"spotify": "https://open.spotify.com/playlist/playlist1"},
            },
        )
    )
    client = _client()

    playlist = client.create_playlist("This Week", "desc")

    assert playlist["id"] == "playlist1"
    assert playlist["external_urls"]["spotify"] == "https://open.spotify.com/playlist/playlist1"
    assert route.calls.last.request.content
    body = json.loads(route.calls.last.request.content)
    assert body == {"name": "This Week", "description": "desc", "public": False}


@respx.mock
def test_get_playlist_found() -> None:
    _mock_token()
    respx.get("https://api.spotify.com/v1/playlists/p1").mock(
        return_value=httpx.Response(200, json={"id": "p1", "name": "This Week"})
    )
    client = _client()

    playlist = client.get_playlist("p1")

    assert playlist == {"id": "p1", "name": "This Week"}


@respx.mock
def test_get_playlist_missing_returns_none() -> None:
    _mock_token()
    respx.get("https://api.spotify.com/v1/playlists/nope").mock(return_value=httpx.Response(404))
    client = _client()

    assert client.get_playlist("nope") is None


@respx.mock
def test_add_tracks_batches_over_100() -> None:
    _mock_token()
    route = respx.post("https://api.spotify.com/v1/playlists/p1/tracks").mock(
        return_value=httpx.Response(201, json={"snapshot_id": "s1"})
    )
    client = _client()
    track_ids = [f"t{i}" for i in range(150)]

    client.add_tracks("p1", track_ids)

    assert route.call_count == 2  # 100 + 50


@respx.mock
def test_remove_tracks() -> None:
    _mock_token()
    route = respx.delete("https://api.spotify.com/v1/playlists/p1/tracks").mock(
        return_value=httpx.Response(200, json={"snapshot_id": "s1"})
    )
    client = _client()

    client.remove_tracks("p1", ["t1", "t2"])

    body = json.loads(route.calls.last.request.content)
    assert body == {"tracks": [{"uri": "spotify:track:t1"}, {"uri": "spotify:track:t2"}]}


@respx.mock
def test_unfollow_playlist_calls_followers_endpoint() -> None:
    _mock_token()
    route = respx.delete("https://api.spotify.com/v1/playlists/p1/followers").mock(
        return_value=httpx.Response(200)
    )
    client = _client()

    client.unfollow_playlist("p1")

    assert route.called


def test_no_method_is_named_or_documented_as_delete() -> None:
    """unfollow_playlist is the only removal method (issue #6 acceptance criteria)."""
    for name in dir(SpotifyClient):
        if name.startswith("_"):
            continue
        assert "delete" not in name.lower(), f"{name} is named like a delete method"
        method = getattr(SpotifyClient, name)
        doc = (method.__doc__ or "").lower()
        assert "delete" not in doc, f"{name} is documented as a delete method: {doc!r}"


@respx.mock
def test_currently_playing_returns_none_on_204() -> None:
    _mock_token()
    respx.get("https://api.spotify.com/v1/me/player/currently-playing").mock(
        return_value=httpx.Response(204)
    )
    client = _client()

    assert client.currently_playing() is None


@respx.mock
def test_currently_playing_returns_track_data() -> None:
    _mock_token()
    respx.get("https://api.spotify.com/v1/me/player/currently-playing").mock(
        return_value=httpx.Response(200, json={"item": {"id": "t1"}})
    )
    client = _client()

    assert client.currently_playing() == {"item": {"id": "t1"}}


@respx.mock
def test_recently_played() -> None:
    _mock_token()
    respx.get("https://api.spotify.com/v1/me/player/recently-played").mock(
        return_value=httpx.Response(200, json={"items": [{"track": {"id": "t1"}}]})
    )
    client = _client()

    assert client.recently_played() == [{"track": {"id": "t1"}}]


@respx.mock
def test_access_token_is_cached_across_calls() -> None:
    token_route = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"access_token": "a1", "expires_in": 3600})
    )
    respx.get("https://api.spotify.com/v1/search").mock(
        return_value=httpx.Response(200, json={"artists": {"items": []}})
    )
    client = _client()

    client.search_artists("a")
    client.search_artists("b")

    assert token_route.call_count == 1


@respx.mock
def test_401_triggers_refresh_and_retries_automatically() -> None:
    token_route = respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(200, json={"access_token": "expired-token", "expires_in": 3600}),
            httpx.Response(200, json={"access_token": "fresh-token", "expires_in": 3600}),
        ]
    )
    search_route = respx.get("https://api.spotify.com/v1/search").mock(
        side_effect=[
            httpx.Response(401, json={"error": "token expired"}),
            httpx.Response(200, json={"artists": {"items": [{"id": "abc"}]}}),
        ]
    )
    client = _client()

    results = client.search_artists("a")

    assert results == [{"id": "abc"}]
    assert token_route.call_count == 2  # initial token + forced refresh after 401
    assert search_route.call_count == 2
    # the retried request used the newly-refreshed token
    assert search_route.calls[1].request.headers["Authorization"] == "Bearer fresh-token"


@respx.mock
def test_429_respects_retry_after_header() -> None:
    _mock_token()
    route = respx.get("https://api.spotify.com/v1/search").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"artists": {"items": []}}),
        ]
    )
    sleeps: list[float] = []
    client = _client(sleep=sleeps.append)

    client.search_artists("a")

    assert route.call_count == 2
    assert sleeps == [2.0]


def test_client_satisfies_music_service_protocol() -> None:
    # The real check is mypy verifying this assignment structurally satisfies
    # MusicService; the assert just gives pytest something to run.
    client: MusicService = _client()
    assert client is not None
