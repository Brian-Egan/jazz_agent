"""DC novelty: which artists and tracks stay off the rolling playlists (DC section 9, ADR-019).

Pure. The caller supplies feedback rows; the **most recent** artist-level row decides, so a
later ``liked`` lifts an earlier ``disliked`` and the choice stays reversible over MCP.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

NOVELTY_LIKED_DAYS_DEFAULT = 182  # six months; read from config.py as NOVELTY_LIKED_DAYS


@dataclass(frozen=True, slots=True)
class FeedbackEntry:
    target_type: str  # 'artist' | 'track' | 'album'
    target_id: str
    sentiment: str | None  # 'liked' | 'disliked' | 'neutral' | None (a note only)
    created_at: datetime


def _latest(entries: Iterable[FeedbackEntry], target_type: str) -> dict[str, FeedbackEntry]:
    """Latest row per target. Equal timestamps resolve to the later row in input order."""
    latest: dict[str, FeedbackEntry] = {}
    for entry in entries:
        if entry.target_type != target_type:
            continue
        current = latest.get(entry.target_id)
        if current is None or entry.created_at >= current.created_at:
            latest[entry.target_id] = entry
    return latest


def excluded_artist_ids(
    entries: Iterable[FeedbackEntry], now: datetime, liked_days: int = NOVELTY_LIKED_DAYS_DEFAULT
) -> frozenset[str]:
    """``disliked`` excludes permanently; ``liked`` for ``liked_days`` from its ``created_at``;
    anything else (neutral, a note only) has no effect, including as the latest row over an
    earlier dislike."""
    window = timedelta(days=liked_days)
    excluded: set[str] = set()
    for artist_id, entry in _latest(entries, "artist").items():
        still_liked = entry.sentiment == "liked" and now < entry.created_at + window
        if still_liked or entry.sentiment == "disliked":
            excluded.add(artist_id)
    return frozenset(excluded)


def excluded_track_ids(entries: Iterable[FeedbackEntry]) -> frozenset[str]:
    """A track-level dislike excludes that track only, never its artist."""
    return frozenset(
        track_id
        for track_id, entry in _latest(entries, "track").items()
        if entry.sentiment == "disliked"
    )
