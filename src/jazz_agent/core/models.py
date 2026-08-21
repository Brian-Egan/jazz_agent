"""Domain models. Frozen, zero I/O (ARCHITECTURE.md section 2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class Club:
    """A row in the hand-edited ``clubs`` table (ARCHITECTURE.md section 3)."""

    club_id: str
    name: str
    schedule_url: str
    render_mode: str = "http"
    week_start_dow: int = 2
    timezone: str = "America/New_York"
    active: bool = True
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class Performer:
    name: str
    instrument: str | None = None
    is_leader: bool = False


@dataclass(frozen=True, slots=True)
class ExtractedShow:
    """One event as extracted from a club's schedule page (ARCHITECTURE.md section 5)."""

    show_date: date
    act_name: str
    set_times: tuple[str, ...] = ()
    performers: tuple[Performer, ...] = ()
    album_mentioned: str | None = None
    raw_text: str = ""


@dataclass(frozen=True, slots=True)
class Show:
    """A club-night listing for one act, after normalization."""

    club_id: str
    show_date: date
    act_name_raw: str
    act_name_norm: str
    set_times: tuple[str, ...] = ()
    performers: tuple[Performer, ...] = ()
    album_mentioned: str | None = None
    raw_text: str = ""


@dataclass(frozen=True, slots=True)
class ArtistMatch:
    """Outcome of Spotify-first adjudication for one act (ARCHITECTURE.md section 6)."""

    spotify_artist_id: str | None
    confidence: float
    reasoning: str
    needs_review: bool = False


@dataclass(frozen=True, slots=True)
class Artist:
    """A persisted row in ``artists``: a match plus everything verification adds."""

    spotify_artist_id: str
    name: str
    match_method: str  # 'llm_adjudicated' | 'dispute_resolved' | 'exact_name'
    match_confidence: float
    genres: tuple[str, ...] = ()
    popularity: int | None = None
    followers: int | None = None
    mbid: str | None = None
    plausibility_score: float | None = None
    needs_review: bool = False
    verification_state: str = "unverified"
    verified_at: datetime | None = None
    match_notes: str | None = None


@dataclass(frozen=True, slots=True)
class MatchMiss:
    """An act that could not be resolved to an artist (ARCHITECTURE.md section 6, stage 4)."""

    show_id: int
    act_name_raw: str
    reason: str
    best_guess_id: str | None = None
    best_guess_confidence: float | None = None


@dataclass(frozen=True, slots=True)
class MbArtist:
    """A cached MusicBrainz artist entity (ARCHITECTURE.md section 6, stage 5)."""

    mbid: str
    name: str
    entity_type: str | None = None  # 'Person' | 'Group' | None
    disambiguation: str | None = None
    tags: tuple[str, ...] = ()
    spotify_url: str | None = None


@dataclass(frozen=True, slots=True)
class MbArtistEdge:
    """A MusicBrainz relationship edge (ARCHITECTURE.md section 12)."""

    src_mbid: str
    dst_mbid: str
    dst_name: str
    edge_type: str  # 'member of band' | 'collaboration' | 'parent' | ...
    instruments: tuple[str, ...] = ()
    begin_date: date | None = None
    end_date: date | None = None


@dataclass(frozen=True, slots=True)
class MbLookupMiss:
    """Negative cache entry for a MusicBrainz name lookup (DATA_MODEL.md section 1)."""

    name_norm: str
    miss_until: datetime
    attempts: int = 1
    last_error: str | None = None


@dataclass(frozen=True, slots=True)
class AlbumChoice:
    """The album selected for an artist, tracks in play order (ARCHITECTURE.md section 7)."""

    spotify_album_id: str
    spotify_artist_id: str
    track_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeekPlaylist:
    """One club's booking week (ARCHITECTURE.md section 8).

    ``id``, ``spotify_playlist_id``, ``spotify_url``, and ``spotify_removed_at``
    are unset for a playlist that has not been persisted yet; the repository
    fills them in.
    """

    club_id: str
    week_start_date: date
    title: str
    description: str
    id: int | None = None
    spotify_playlist_id: str | None = None
    spotify_url: str | None = None
    spotify_removed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlaylistTrack:
    """A track on a week playlist (ARCHITECTURE.md section 8)."""

    week_playlist_id: int
    spotify_track_id: str
    spotify_album_id: str
    spotify_artist_id: str
    position: int
    track_name: str | None = None
    show_id: int | None = None
    removed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class PlaylistEvent:
    """An audit-trail entry for a playlist mutation (ARCHITECTURE.md section 8, ADR-011)."""

    week_playlist_id: int
    event_type: str  # 'created' | 'tracks_added' | 'tracks_removed' | 'correction' | 'unfollowed'
    spotify_artist_id: str | None = None
    reason: str | None = None
    detail: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class Feedback:
    """A listener's sentiment on an artist, track, or album (ARCHITECTURE.md section 11)."""

    target_type: str  # 'artist' | 'track' | 'album'
    target_id: str
    sentiment: str | None = None  # 'liked' | 'disliked' | 'neutral'
    note_text: str | None = None
    show_id: int | None = None
    week_playlist_id: int | None = None
    source: str = "mcp"  # 'mcp' | 'manual' | 'implicit'


@dataclass(frozen=True, slots=True)
class RunLogEntry:
    """One row of the durable run log (ARCHITECTURE.md section 10)."""

    run_id: str
    outcome: str  # 'success' | 'no_shows' | 'fetch_fail' | 'extract_fail' | 'no_match' | 'partial'
    club_id: str | None = None
    shows_found: int | None = None
    detail: str | None = None
    duration_ms: int | None = None
