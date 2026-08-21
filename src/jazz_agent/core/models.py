"""Domain models. Frozen, zero I/O (ARCHITECTURE.md section 2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


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
class AlbumChoice:
    """The album selected for an artist, tracks in play order (ARCHITECTURE.md section 7)."""

    spotify_album_id: str
    spotify_artist_id: str
    track_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WeekPlaylist:
    """One club's booking week (ARCHITECTURE.md section 8)."""

    club_id: str
    week_start_date: date
    title: str
    description: str
    track_ids: tuple[str, ...] = ()
