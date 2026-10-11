"""Album choice and track ordering (ARCHITECTURE.md section 7, ADR-010).

Pure logic over album/track metadata already fetched by the caller -- no
network. get_artist_albums(include_groups='album') (issue #6) already
excludes singles, compilations, and appears_on sideman credits at the API
level; the title heuristic here is a second, local pass for the compilation
and reissue albums that slip through with album_type=album anyway.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

# "Obvious" reissues and compilations only (ARCHITECTURE.md section 7, step 2)
# -- deliberately not "remastered" or "deluxe edition" alone, since nearly
# all catalog jazz on streaming is a remaster, and rejecting those would
# leave nothing eligible for most artists.
_COMPILATION_KEYWORDS = (
    "greatest hits",
    "best of",
    "the best of",
    "essential",
    "anthology",
    "collection",
    "hits collection",
    "very best",
)
_REISSUE_KEYWORDS = (
    "anniversary edition",
    "legacy edition",
    "reissue",
    "expanded edition",
)

# Working musicians frequently have no album with meaningful popularity
# (ARCHITECTURE.md section 7, step 4) -- below this, recency is a better
# tiebreak than an arbitrary near-zero score.
_POPULARITY_FLOOR = 5


@dataclass(frozen=True, slots=True)
class AlbumSelection:
    """Exactly one of album / reason is set."""

    album: dict[str, Any] | None
    reason: str | None


def select_album(albums: list[dict[str, Any]]) -> AlbumSelection:
    eligible = [a for a in albums if not _is_compilation_or_reissue(a.get("name", ""))]
    if not eligible:
        return AlbumSelection(album=None, reason="NO_ELIGIBLE_ALBUM")

    top = max(eligible, key=lambda a: a.get("popularity") or 0)
    if (top.get("popularity") or 0) < _POPULARITY_FLOOR:
        top = max(eligible, key=_release_date_sort_key)

    return AlbumSelection(album=top, reason=None)


def order_tracks(tracks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Order tracks the way the album plays: disc, then track_number within it.

    Albums are composed sequences; scattering or shuffling them is the worse
    listen (ADR-010)."""
    return sorted(tracks, key=lambda t: (t.get("disc_number") or 1, t.get("track_number") or 0))


def _is_compilation_or_reissue(title: str) -> bool:
    lowered = title.lower()
    return any(kw in lowered for kw in _COMPILATION_KEYWORDS) or any(
        kw in lowered for kw in _REISSUE_KEYWORDS
    )


def _release_date_sort_key(album: dict[str, Any]) -> str:
    """Normalize a possibly year-only or year-month release_date to a fully
    zero-padded YYYY-MM-DD string, so lexicographic comparison sorts
    correctly regardless of Spotify's release_date_precision."""
    segments = (album.get("release_date") or "0000").split("-")
    year = segments[0].zfill(4)
    month = segments[1].zfill(2) if len(segments) > 1 else "01"
    day = segments[2].zfill(2) if len(segments) > 2 else "01"
    return f"{year}-{month}-{day}"


# --- DC: two tracks per artist (DC section 8, ADR-020) -----------------------------------

TRACKS_PER_ARTIST = 2

# Version markers that make two listings the same song: "- Live at ...", "(Remastered 2011)",
# "- 2009 Remaster", "Alternate Take". A marker is dropped whether it trails after a dash or
# sits in brackets, so "So What (Live)" and "So What - Remastered" both normalise to "so what".
_VERSION_WORDS = r"(?:live|remaster(?:ed)?|alternate\s+take|alt\.?\s+take)"
_BRACKETED_VERSION = re.compile(rf"\s*[(\[][^)\]]*\b{_VERSION_WORDS}\b[^)\]]*[)\]]", re.IGNORECASE)
_DASHED_VERSION = re.compile(
    rf"\s+[-\u2013\u2014]\s+[^-\u2013\u2014]*\b{_VERSION_WORDS}\b.*$", re.IGNORECASE
)
_BARE_ALTERNATE = re.compile(r"\s+alternate\s+take\b.*$", re.IGNORECASE)


def normalize_track_title(title: str) -> str:
    cleaned = _BRACKETED_VERSION.sub("", title)
    cleaned = _DASHED_VERSION.sub("", cleaned)
    cleaned = _BARE_ALTERNATE.sub("", cleaned)
    return " ".join(cleaned.casefold().split())


def select_tracks(
    matched_artist_id: str,
    search_results: Sequence[dict[str, Any]],
    fallback_album_tracks: Sequence[dict[str, Any]] | Callable[[], Sequence[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Up to two tracks for one DC artist, in search-rank order.

    ``search_results`` is Spotify's track search in relevance order, standing in for
    popularity (removed in the Feb 2026 migration, ADR-020). Only tracks whose **first**
    listed artist is ``matched_artist_id`` count: a sideman credit on another leader's record
    is not this artist's music. Titles are deduped after normalisation, keeping the first;
    the second track prefers a different album from the first. When fewer than two survive,
    the opening tracks of the ARCHITECTURE section 7 album fill the rest; pass a callable to
    defer that album fetch until it is needed.
    """
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for track in search_results:
        artists = track.get("artists") or []
        if not artists or artists[0].get("id") != matched_artist_id:
            continue
        key = normalize_track_title(track.get("name", ""))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(track)

    chosen = _first_two_preferring_different_albums(candidates)
    if len(chosen) < TRACKS_PER_ARTIST:
        fallback = (
            fallback_album_tracks() if callable(fallback_album_tracks) else fallback_album_tracks
        )
        taken = {t["id"] for t in chosen}
        for track in order_tracks(list(fallback)):
            if len(chosen) >= TRACKS_PER_ARTIST:
                break
            key = normalize_track_title(track.get("name", ""))
            if track["id"] in taken or key in seen:
                continue
            seen.add(key)
            taken.add(track["id"])
            chosen.append(track)
    return chosen


def _album_id(track: dict[str, Any]) -> str | None:
    album_id = (track.get("album") or {}).get("id")
    return album_id if isinstance(album_id, str) else None


def _first_two_preferring_different_albums(
    candidates: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(candidates) <= 1:
        return list(candidates)
    first = candidates[0]
    second = next(
        (c for c in candidates[1:] if _album_id(c) != _album_id(first)),
        candidates[1],
    )
    return [first, second]
