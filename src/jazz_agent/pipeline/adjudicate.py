"""Spotify-first artist adjudication (ARCHITECTURE.md section 6 stages 1-4;
ADR-006, ADR-007).

normalize (no network) -> Spotify search, up to 10 candidates -> plausibility
filter (no network) -> Haiku adjudication over the surviving candidates,
returning {artist_id | null, confidence, reasoning}. Thresholds are
deliberately conservative (ADR-007): with one full album per artist, a wrong
match costs 45 minutes of irrelevant music, so a logged miss is preferred
over a confident-sounding guess. Do not lower these to raise coverage.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import anthropic

from jazz_agent.core.models import Artist, MatchMiss
from jazz_agent.core.normalize import normalize_act_name
from jazz_agent.core.plausibility import PLAUSIBILITY_FLOOR, plausibility_score
from jazz_agent.ports.music import MusicService

ACCEPT_THRESHOLD = 0.80
REVIEW_THRESHOLD = 0.50

_TOOL_NAME = "adjudicate_match"
_TOOL_SCHEMA: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "Decide which candidate, if any, is the act named in the listing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "artist_id": {
                "type": ["string", "null"],
                "description": "spotify_artist_id of the correct candidate, or null if none fit",
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "reasoning": {"type": "string"},
        },
        "required": ["artist_id", "confidence", "reasoning"],
    },
}

_SYSTEM_PROMPT = (
    "You match a jazz club listing's headline act to the correct Spotify artist "
    "from a short list of candidates. Read the raw listing text carefully: a "
    "tribute act or a 'plays the music of' billing is a different artist from "
    "the musician it honours, and must never be matched to that musician's own "
    "candidate. If a candidate's genres are not jazz-adjacent, that is a strong "
    "signal it is a same-named act in an unrelated genre, not the working jazz "
    "musician the listing means. When genuinely unsure, or if no candidate is a "
    "good fit, set artist_id to null and give a low confidence rather than "
    "guessing -- a missed match is far cheaper than a wrong one."
)


@dataclass(frozen=True, slots=True)
class AdjudicationResult:
    """Exactly one of matched_artist / match_miss is set."""

    matched_artist: Artist | None
    match_miss: MatchMiss | None


def adjudicate(
    show_id: int,
    act_name_raw: str,
    raw_text: str,
    music: MusicService,
    llm_client: anthropic.Anthropic,
    model: str,
) -> AdjudicationResult:
    act_name = normalize_act_name(act_name_raw)
    candidates = music.search_artists(act_name, limit=10)

    scored = sorted(
        ((c, plausibility_score(c)) for c in candidates), key=lambda cs: cs[1], reverse=True
    )
    plausible = [(c, s) for c, s in scored if s >= PLAUSIBILITY_FLOOR]

    if not plausible:
        best = scored[0] if scored else None
        return AdjudicationResult(
            matched_artist=None,
            match_miss=MatchMiss(
                show_id=show_id,
                act_name_raw=act_name_raw,
                reason="NO_PLAUSIBLE_CANDIDATE",
                best_guess_id=best[0]["id"] if best else None,
                best_guess_confidence=best[1] if best else None,
            ),
        )

    verdict = _ask_llm(act_name_raw, raw_text, [c for c, _ in plausible], llm_client, model)
    artist_id = verdict["artist_id"]
    confidence = float(verdict["confidence"])

    if artist_id is None or confidence < REVIEW_THRESHOLD:
        fallback = next((c for c, _ in plausible if c["id"] == artist_id), plausible[0][0])
        return AdjudicationResult(
            matched_artist=None,
            match_miss=MatchMiss(
                show_id=show_id,
                act_name_raw=act_name_raw,
                reason="NO_CONFIDENT_MATCH",
                best_guess_id=artist_id or fallback["id"],
                best_guess_confidence=confidence,
            ),
        )

    matched, matched_score = next((c, s) for c, s in plausible if c["id"] == artist_id)
    return AdjudicationResult(
        matched_artist=Artist(
            spotify_artist_id=matched["id"],
            name=matched["name"],
            genres=tuple(matched.get("genres", [])),
            popularity=matched.get("popularity"),
            followers=(matched.get("followers") or {}).get("total"),
            match_method="llm_adjudicated",
            match_confidence=confidence,
            plausibility_score=matched_score,
            needs_review=confidence < ACCEPT_THRESHOLD,
            match_notes=verdict["reasoning"],
        ),
        match_miss=None,
    )


def _ask_llm(
    act_name_raw: str,
    raw_text: str,
    candidates: list[dict[str, Any]],
    llm_client: anthropic.Anthropic,
    model: str,
) -> dict[str, Any]:
    candidate_summaries = [
        {
            "id": c["id"],
            "name": c["name"],
            "genres": c.get("genres", []),
            "followers": (c.get("followers") or {}).get("total"),
            "popularity": c.get("popularity"),
        }
        for c in candidates
    ]
    response = llm_client.messages.create(
        model=model,
        max_tokens=1024,
        system=_SYSTEM_PROMPT,
        tools=[_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": _TOOL_NAME},
        messages=[
            {
                "role": "user",
                "content": (
                    f"Listed act, as the club wrote it: {act_name_raw!r}\n\n"
                    f"Raw listing text:\n{raw_text}\n\n"
                    f"Candidates:\n{candidate_summaries}"
                ),
            }
        ],
    )  # type: ignore[call-overload]

    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            return block.input  # type: ignore[no-any-return]
    raise ValueError("Model response contained no adjudicate_match tool call")
