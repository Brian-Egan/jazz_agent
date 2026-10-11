"""Inferring a user's removal from a complete read of a rolling playlist (DC section 11).

Pure. A removal is inferred from *absence*, which is why every guard errs toward recording
nothing: a wrong inference permanently blacklists a track (invariant 9), and a missed one is
simply caught on the next run.

The caller owns the first guard, **complete read or nothing**: if any page of the playlist
failed to read it must not call this at all.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from jazz_agent.core.models import RollingPlaylistTrack

# More than this share of live stints missing at once is a Spotify or account problem, not a
# change of taste.
ANOMALY_FRACTION = 0.5


@dataclass(frozen=True, slots=True)
class RemovalVerdict:
    confirmed: tuple[RollingPlaylistTrack, ...] = ()
    # True when too many stints went missing at once: confirmed is then empty and the caller
    # alerts instead of recording anything.
    anomaly: bool = False
    missing_count: int = 0
    live_stint_count: int = 0


def detect_removals(
    open_stints: Sequence[RollingPlaylistTrack], live_items: Sequence[Mapping[str, Any]]
) -> RemovalVerdict:
    """Which open stints the user removed, given every item currently on the playlist.

    Relinking guard: Spotify can serve the same recording under a different track ID (the
    ``linked_from`` field that used to say so was removed in February 2026), so a stint whose
    ISRC is present in the read is the same track, not a removal. A stint with no recorded
    ISRC cannot be checked this way and is judged by track ID alone.
    """
    live_ids = {item["id"] for item in live_items if item.get("id")}
    live_isrcs = {item["isrc"] for item in live_items if item.get("isrc")}

    missing = tuple(
        s
        for s in open_stints
        if s.spotify_track_id not in live_ids and not (s.isrc and s.isrc in live_isrcs)
    )
    if len(missing) > ANOMALY_FRACTION * len(open_stints):
        return RemovalVerdict(
            anomaly=True, missing_count=len(missing), live_stint_count=len(open_stints)
        )
    return RemovalVerdict(
        confirmed=missing, missing_count=len(missing), live_stint_count=len(open_stints)
    )
