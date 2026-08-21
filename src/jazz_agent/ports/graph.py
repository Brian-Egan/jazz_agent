"""MusicBrainz-backed graph service port (ARCHITECTURE.md section 6 stage 5, section 12)."""

from __future__ import annotations

from typing import Protocol

from jazz_agent.core.models import MbArtist, MbArtistEdge


class GraphService(Protocol):
    def resolve_mbid(self, name: str) -> MbArtist | None: ...
    def spotify_url_for(self, mbid: str) -> str | None: ...
    def edges_for(self, mbid: str) -> list[MbArtistEdge]: ...
