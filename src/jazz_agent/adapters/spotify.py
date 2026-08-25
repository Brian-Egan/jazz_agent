"""Spotify Web API client behind ports.music.MusicService (ARCHITECTURE.md sections
6, 7, 9, 11).

Two hard constraints:

1. There is no delete-playlist endpoint. unfollow_playlist is the only
   removal method, calling DELETE /v1/playlists/{id}/followers -- the
   playlist continues to exist at its URI (AGENTS.md invariant 6).
2. related-artists, recommendations, audio-features, and audio-analysis are
   withdrawn for apps registered after November 2024 and return 403
   (ADR-005). Nothing here calls them, and nothing should be added that does.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

_TOKEN_URL = "https://accounts.spotify.com/api/token"  # noqa: S105 -- URL, not a secret
_API_BASE = "https://api.spotify.com/v1"
_RETRYABLE_STATUS = {500, 502, 503, 504}
_MAX_TRACKS_PER_REQUEST = 100


def _chunk(items: Sequence[Any], size: int) -> list[list[Any]]:
    return [list(items[i : i + size]) for i in range(0, len(items), size)]


class SpotifyClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        client: httpx.Client | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._client = client or httpx.Client(timeout=10.0)
        self._max_retries = max_retries
        self._sleep = sleep
        self._now = now
        self._access_token: str | None = None
        self._access_token_expires_at: float = 0.0

    # ---------- token management ----------

    def _get_access_token(self, force_refresh: bool = False) -> str:
        expired = self._now() >= self._access_token_expires_at
        if force_refresh or self._access_token is None or expired:
            self._refresh_access_token()
        assert self._access_token is not None
        return self._access_token

    def _refresh_access_token(self) -> None:
        response = self._client.post(
            _TOKEN_URL,
            data={"grant_type": "refresh_token", "refresh_token": self._refresh_token},
            auth=(self._client_id, self._client_secret),
        )
        response.raise_for_status()
        payload = response.json()
        self._access_token = payload["access_token"]
        # Refresh a little early so a call doesn't race a token expiring mid-flight.
        self._access_token_expires_at = self._now() + payload["expires_in"] - 30

    # ---------- request plumbing ----------

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        extra_headers = kwargs.pop("headers", {})
        attempt = 0
        force_refresh = False
        while True:
            token = self._get_access_token(force_refresh=force_refresh)
            headers = {**extra_headers, "Authorization": f"Bearer {token}"}
            response = self._client.request(method, f"{_API_BASE}{path}", headers=headers, **kwargs)

            if response.status_code == 401 and attempt < self._max_retries:
                force_refresh = True
                attempt += 1
                continue

            if response.status_code == 429 and attempt < self._max_retries:
                retry_after = float(response.headers.get("Retry-After", "1"))
                self._sleep(retry_after)
                force_refresh = False
                attempt += 1
                continue

            if response.status_code in _RETRYABLE_STATUS and attempt < self._max_retries:
                self._sleep(2**attempt)
                force_refresh = False
                attempt += 1
                continue

            response.raise_for_status()
            return response

    # ---------- MusicService ----------

    def search_artists(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        response = self._request(
            "GET", "/search", params={"q": query, "type": "artist", "limit": limit}
        )
        return response.json()["artists"]["items"]  # type: ignore[no-any-return]

    def get_artist_albums(self, spotify_artist_id: str) -> list[dict[str, Any]]:
        # include_groups=album excludes singles, compilations, and appears_on
        # sideman credits (ADR-010) -- without it you get greatest-hits
        # packages and other people's records, the common case for working
        # jazz musicians rather than an edge case.
        #
        # limit=10 is this endpoint's actual ceiling for apps registered
        # after the November 2024 changes (ADR-005) -- anything higher 400s
        # with "Invalid limit" despite older docs citing 50. Page through
        # offset so a prolific artist's later albums are still eligible for
        # select_album's popularity comparison, not silently dropped.
        albums: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = self._request(
                "GET",
                f"/artists/{spotify_artist_id}/albums",
                params={"include_groups": "album", "limit": 10, "offset": offset},
            )
            payload = response.json()
            items = payload["items"]
            albums.extend(items)
            if payload.get("next") is None:
                break
            offset += len(items)
        return albums

    def get_album_tracks(self, spotify_album_id: str) -> list[dict[str, Any]]:
        response = self._request("GET", f"/albums/{spotify_album_id}/tracks", params={"limit": 50})
        return response.json()["items"]  # type: ignore[no-any-return]

    def create_playlist(self, title: str, description: str) -> dict[str, Any]:
        # POST /users/{user_id}/playlists 403s for apps registered after the
        # Nov 2024 changes (ADR-005) -- confirmed live against the real API.
        # /me/playlists is the one that still works and needs no separate
        # GET /me lookup first.
        response = self._request(
            "POST",
            "/me/playlists",
            json={"name": title, "description": description, "public": False},
        )
        return response.json()  # type: ignore[no-any-return]

    def get_playlist(self, spotify_playlist_id: str) -> dict[str, Any] | None:
        try:
            response = self._request("GET", f"/playlists/{spotify_playlist_id}")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise
        return response.json()  # type: ignore[no-any-return]

    def add_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        # POST .../tracks 403s -- removed in Spotify's Feb 2026 API migration,
        # enforced for Development Mode apps from 9 March 2026 (docs/research/
        # spotify-playlist-write-403.md). .../items is the replacement; same
        # scopes, same {"uris": [...]} request body.
        uris = [f"spotify:track:{track_id}" for track_id in spotify_track_ids]
        for batch in _chunk(uris, _MAX_TRACKS_PER_REQUEST):
            self._request("POST", f"/playlists/{spotify_playlist_id}/items", json={"uris": batch})

    def remove_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        # Same .../tracks -> .../items migration as add_tracks, but the request
        # body key renamed too: "tracks" -> "items" (the object shape inside,
        # {"uri": ...}, is unchanged) -- verified directly against Spotify's
        # current reference docs, not just inferred from the URL rename.
        uris = [{"uri": f"spotify:track:{track_id}"} for track_id in spotify_track_ids]
        for batch in _chunk(uris, _MAX_TRACKS_PER_REQUEST):
            self._request(
                "DELETE", f"/playlists/{spotify_playlist_id}/items", json={"items": batch}
            )

    def unfollow_playlist(self, spotify_playlist_id: str) -> None:
        self._request("DELETE", f"/playlists/{spotify_playlist_id}/followers")

    def currently_playing(self) -> dict[str, Any] | None:
        response = self._request("GET", "/me/player/currently-playing")
        if response.status_code == 204 or not response.content:
            return None
        return response.json()  # type: ignore[no-any-return]

    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]:
        response = self._request("GET", "/me/player/recently-played", params={"limit": limit})
        return response.json()["items"]  # type: ignore[no-any-return]
