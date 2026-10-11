"""Storage Protocols: narrow, intention-revealing methods, no leaking SQL types.

Every method takes and returns core/ domain objects or primitives. This is the
seam that keeps hosting and the backend swappable (PRD 8.6); one concrete
implementation lives in adapters/pg/.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol

from jazz_agent.core.models import (
    Artist,
    CapitalbopEvent,
    Club,
    Feedback,
    MatchMiss,
    MbArtist,
    MbArtistEdge,
    MbLookupMiss,
    Performer,
    PlaylistEvent,
    PlaylistTrack,
    RollingPlaylist,
    RollingPlaylistEvent,
    RollingPlaylistTrack,
    RunLogEntry,
    Show,
    VenueGroupMember,
    WeekPlaylist,
)


class ClubRepo(Protocol):
    def get_active_clubs(self) -> list[Club]: ...
    def get_club(self, club_id: str) -> Club | None: ...


class ShowRepo(Protocol):
    def upsert_show(self, show: Show) -> int:
        """Insert, or on a (club_id, show_date, act_name_norm) conflict update
        last_seen_at rather than inserting a duplicate. Returns show_id."""
        ...

    def get_show(self, show_id: int) -> Show | None: ...
    def record_performers(self, show_id: int, performers: Sequence[Performer]) -> None: ...
    def performers_for_show(self, show_id: int) -> list[Performer]: ...
    def record_match_miss(self, miss: MatchMiss) -> None: ...
    def match_misses_for_show(self, show_id: int) -> list[MatchMiss]: ...


class ArtistRepo(Protocol):
    def upsert_artist(self, artist: Artist) -> None: ...
    def get_artist(self, spotify_artist_id: str) -> Artist | None: ...
    def link_show_artist(self, show_id: int, spotify_artist_id: str) -> None: ...
    def artists_for_show(self, show_id: int) -> list[Artist]: ...


class GraphRepo(Protocol):
    def upsert_mb_artist(self, artist: MbArtist) -> None: ...
    def get_mb_artist(self, mbid: str) -> MbArtist | None: ...
    def get_mb_artist_by_name(self, name: str) -> MbArtist | None:
        """Best-effort case-insensitive exact-name cache lookup, so a
        previously-resolved artist doesn't trigger a second MusicBrainz search."""
        ...

    def record_edges(self, edges: Sequence[MbArtistEdge]) -> None: ...
    def edges_for(self, mbid: str) -> list[MbArtistEdge]: ...
    def record_lookup_miss(self, miss: MbLookupMiss) -> None: ...
    def get_lookup_miss(self, name_norm: str) -> MbLookupMiss | None: ...


class PlaylistRepo(Protocol):
    def upsert_week_playlist(self, playlist: WeekPlaylist) -> int:
        """Insert, or update title/description, on a (club_id, week_start_date)
        conflict. Returns the row's id. spotify_playlist_id/url are untouched by
        this call -- they are usually not known yet; see link_spotify_playlist."""
        ...

    def link_spotify_playlist(
        self, week_playlist_id: int, spotify_playlist_id: str, spotify_url: str
    ) -> None: ...
    def get_week_playlist(self, club_id: str, week_start_date: date) -> WeekPlaylist | None: ...
    def playlists_for_club(self, club_id: str) -> list[WeekPlaylist]:
        """Every week_playlists row for this club, including already-unfollowed
        ones -- retention (pipeline/prune.py) needs the full set to decide."""
        ...

    def add_tracks(self, week_playlist_id: int, tracks: Sequence[PlaylistTrack]) -> None: ...
    def remove_track(self, week_playlist_id: int, spotify_track_id: str) -> None: ...
    def tracks_for(self, week_playlist_id: int) -> list[PlaylistTrack]: ...
    def record_event(self, event: PlaylistEvent) -> None: ...
    def events_for(self, week_playlist_id: int) -> list[PlaylistEvent]: ...
    def mark_removed(self, week_playlist_id: int, removed_at: datetime) -> None: ...


class FeedbackRepo(Protocol):
    def record_feedback(self, feedback: Feedback) -> int: ...
    def feedback_for_target(self, target_type: str, target_id: str) -> list[Feedback]: ...


class RunRepo(Protocol):
    def log_run_outcome(self, entry: RunLogEntry) -> None: ...
    def recent_runs(self, club_id: str | None = None, days: int = 7) -> list[RunLogEntry]: ...


class DedicatedGroupConflict(Exception):
    """A venue already holds an active membership in a different dedicated group."""

    def __init__(self, club_id: str, existing_group: str) -> None:
        super().__init__(
            f"{club_id} is already in dedicated group {existing_group!r}; "
            "a venue may belong to at most one"
        )
        self.club_id = club_id
        self.existing_group = existing_group


class VenueGroupRepo(Protocol):
    def add_member(self, club_id: str, group_key: str, filter_profile: str, added_via: str) -> int:
        """Open a membership; returns its id. Raises ``ValueError`` for an unknown group,
        profile, or a club that is not a capitalbop_venue row, and
        ``DedicatedGroupConflict`` if the venue is active in another dedicated group.
        Adding an already-active (club, group) pair returns the existing id unchanged."""
        ...

    def deactivate_member(self, club_id: str, group_key: str, at: datetime) -> bool:
        """Stamp ``deactivated_at`` on the active row; True if one was open."""
        ...

    def active_members(self, group_key: str | None = None) -> list[VenueGroupMember]: ...


class RollingPlaylistRepo(Protocol):
    def upsert_rolling_playlist(self, playlist: RollingPlaylist) -> int:
        """Insert, or update title/is_public/description, on a (region, slot_key) conflict.
        spotify_playlist_id/url are untouched; see link_spotify_playlist."""
        ...

    def link_spotify_playlist(
        self, rolling_playlist_id: int, spotify_playlist_id: str, spotify_url: str
    ) -> None: ...
    def get_rolling_playlist(self, region: str, slot_key: str) -> RollingPlaylist | None: ...
    def open_stints(self, rolling_playlist_id: int) -> list[RollingPlaylistTrack]: ...
    def open_stint(self, track: RollingPlaylistTrack) -> int:
        """Open a stint. Raises if the track already has an open stint on this playlist."""
        ...

    def close_stint(self, stint_id: int, reason: str, at: datetime) -> None:
        """Close with ``removed_at`` and ``removal_reason``. Never deletes (invariant 5)."""
        ...

    def user_removed_track_ids(self) -> set[str]:
        """Tracks whose latest stint, on any playlist, closed with reason 'user'
        (invariant 9: never re-added)."""
        ...

    def record_event(self, event: RollingPlaylistEvent) -> None: ...


class VenueMirrorRepo(Protocol):
    def upsert_capitalbop_venue(self, slug: str, term_id: int, name: str) -> str:
        """Mirror one venue as a ``cb-<slug>`` clubs row and return its club_id. Writes the
        name and term id only: never ``city_override``, notes, or ``active`` (DC section 3)."""
        ...

    def city_overrides(self) -> dict[str, str]:
        """club_id -> hand-set city, for venues that have one."""
        ...


class CapitalbopRepo(Protocol):
    def upsert_event(self, event: CapitalbopEvent, seen_at: datetime | None = None) -> None:
        """Insert, or refresh every field and last_seen_at, keyed on instance_id.
        first_seen_at is preserved. ``seen_at`` (default now) is the fetch's timestamp,
        shared by every event of one run so the cancellation sweep can compare against it."""
        ...

    def known_event_ids(self, event_ids: Sequence[int]) -> set[int]:
        """Which of these series already have at least one stored instance."""
        ...

    def cancelled_instance_ids(
        self, start: date, end: date, fetch_started_at: datetime
    ) -> set[int]:
        """Stored instances showing in [start, end] whose last_seen_at predates this run's
        fetch: absent from a complete fetch, hence cancelled (DC section 2). Call only after
        a complete fetch; rows are never deleted."""
        ...

    def set_series_article(
        self, event_id: int, title: str, url: str, published: date | None
    ) -> None:
        """Record a series' linked article on every stored instance of it."""
        ...

    def get_event(self, instance_id: int) -> CapitalbopEvent | None: ...
    def get_extraction(self, event_id: int, description_hash: str) -> dict[str, object] | None: ...
    def put_extraction(
        self, event_id: int, description_hash: str, result: dict[str, object], model: str
    ) -> None: ...
