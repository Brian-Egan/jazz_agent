"""Plausibility guard: does this Spotify candidate look like a working jazz act?

Pure local logic over data already in a Spotify artist search result -- no
network, no I/O. Primary defence against wrong-genre mismatches (ADR-007),
not MusicBrainz, because it still works when MusicBrainz is unreachable,
which is exactly when a guard is needed most. This is the least testable
component in the system -- "does this look like a working jazz act" is
judgement encoded as thresholds -- so plausibility_score is meant to be
persisted by the caller and these thresholds are expected to need tuning
against real match history rather than being final on the first pass.
"""

from __future__ import annotations

from typing import Any

# Below this, a candidate is filtered out before ever reaching LLM
# adjudication -- not worth spending a call on an implausible guess.
PLAUSIBILITY_FLOOR = 0.3

_JAZZ_ADJACENT_GENRES = {
    "jazz",
    "contemporary jazz",
    "jazz fusion",
    "bebop",
    "hard bop",
    "cool jazz",
    "free jazz",
    "smooth jazz",
    "vocal jazz",
    "big band",
    "swing",
    "latin jazz",
    "afro-cuban jazz",
    "avant-garde jazz",
    "post-bop",
    "jazz blues",
    "jazz and blues",
    "jazz funk",
    "nu jazz",
    "modern jazz",
    "jazz orchestra",
    "jazz trio",
    "jazz piano",
    "chamber jazz",
    "jazz rap",
    "jazz pop",
}

# A same-named act with a huge following and zero jazz signal is a much
# stronger "wrong act" tell than an unclassified nobody -- a real working
# jazz musician being small or unpopular is completely normal on its own,
# so low numbers never count against a candidate by themselves.
_VERY_POPULAR_FOLLOWERS = 500_000


def plausibility_score(candidate: dict[str, Any]) -> float:
    """Score 0.0-1.0: could this candidate plausibly be a working jazz act.

    ``candidate`` is a Spotify artist object as returned by search_artists:
    at minimum ``genres: list[str]``, ``followers: {"total": int}``,
    ``popularity: int`` are read; other fields are ignored.
    """
    genres = {g.lower() for g in candidate.get("genres", [])}
    followers = candidate.get("followers", {}).get("total") or 0

    has_jazz_genre = bool(genres & _JAZZ_ADJACENT_GENRES)

    if has_jazz_genre:
        score = 0.9
    elif not genres:
        # No genre tags at all is common for small or newly-added artists
        # Spotify hasn't classified yet -- ambiguous, not disqualifying.
        score = 0.4
    else:
        # Has genre tags, none of them jazz-adjacent: the case this guard
        # exists to catch -- a same-named act from an unrelated genre.
        score = 0.1

    if not has_jazz_genre and followers >= _VERY_POPULAR_FOLLOWERS:
        score = max(0.0, score - 0.2)

    return round(min(1.0, max(0.0, score)), 2)
