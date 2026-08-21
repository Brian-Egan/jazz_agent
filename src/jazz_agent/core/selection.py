"""Album choice and track ordering (ARCHITECTURE.md section 7, ADR-010).

Pure logic over album/track metadata already fetched by the caller -- no
network. get_artist_albums(include_groups='album') (issue #6) already
excludes singles, compilations, and appears_on sideman credits at the API
level; the title heuristic here is a second, local pass for the compilation
and reissue albums that slip through with album_type=album anyway.
"""

from __future__ import annotations

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
