"""Spotify-backed music service port (ARCHITECTURE.md sections 6, 7, 9, 11).

Search and catalogue methods return the provider's response shape as-is
(dicts of JSON-ish data) rather than a bespoke domain type: core/plausibility.py
and core/selection.py are specified in ARCHITECTURE.md to operate directly on
fields Spotify returns (genres, popularity, followers, album_group,
release_date), and inventing a parallel type would just be a lossy copy of
the same data with an extra translation step. Anything returned by *our own*
storage instead (repository.py) is a typed core/ model, per the "no leaking
SQL types" rule -- this port has no such rule, since nothing here is SQL.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol


class MusicService(Protocol):
    def search_artists(self, query: str, limit: int = 10) -> list[dict[str, Any]]: ...
    def get_artist_albums(self, spotify_artist_id: str) -> list[dict[str, Any]]: ...
    def get_album_tracks(self, spotify_album_id: str) -> list[dict[str, Any]]: ...

    def create_playlist(self, title: str, description: str, public: bool = False) -> dict[str, Any]:
        """Create a new playlist for the authenticated user, returning the full
        playlist object (id, external_urls, etc.) -- Spotify's create response
        already includes it, so a caller needing the URL doesn't need a
        separate get_playlist round-trip right after creating. NYC weekly playlists
        stay private; DC's shared rolling playlists pass ``public=True`` (ADR-024)."""
        ...

    def get_playlist(self, spotify_playlist_id: str) -> dict[str, Any] | None: ...
    def add_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None: ...
    def remove_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None: ...

    def unfollow_playlist(self, spotify_playlist_id: str) -> None:
        """DELETE /v1/playlists/{id}/followers. There is no delete-playlist endpoint --
        this is the only removal method that exists, deliberately (AGENTS.md invariant 6)."""
        ...

    def search_tracks(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """``GET /search?type=track``, in Spotify's relevance order. Pages past the 10-result
        per-request ceiling when ``limit`` is larger."""
        ...

    def get_playlist_items(self, spotify_playlist_id: str) -> list[dict[str, Any]]:
        """Every item of a playlist as ``{"id", "isrc", "name"}``. Reads **all** pages and
        raises if any page fails -- never a partial list, because DC infers user removals
        from absence (invariant 9). Unplayable entries with no track id are skipped."""
        ...

    def replace_playlist_items(
        self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]
    ) -> None:
        """Replace the whole playlist contents with these tracks, in order."""
        ...

    def update_playlist_details(
        self, spotify_playlist_id: str, title: str, description: str
    ) -> None: ...

    def currently_playing(self) -> dict[str, Any] | None: ...
    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]: ...
