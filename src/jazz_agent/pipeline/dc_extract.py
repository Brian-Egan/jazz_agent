"""Personnel extraction and matching for eligible DC events (DC sections 6 and 7).

Feed fields already give date, time, venue and price, so Haiku reads one thing: who leads and
who else is named. It runs once per ``(event_id, description_hash)``: a residency is extracted
once, again only when CapitalBop edits its description (``capitalbop_extractions`` is the
cache). Every leader then takes ARCHITECTURE section 6 unchanged: the same Spotify search,
plausibility floor and adjudication thresholds as an NYC act (invariant 8), and MusicBrainz
verification that can only ever change ``verification_state`` (invariant 3).

Callers pass only events that passed eligibility (``core.eligibility.planned_instance_ids``):
ineligible ones keep a name and no performers. Repositories are injected so the DC run can
hand in DRY_RUN wrappers; nothing here reads the environment.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anthropic

from jazz_agent.core.html_text import html_to_text
from jazz_agent.core.models import (
    Artist,
    CapitalbopEvent,
    MatchMiss,
    Performer,
    PersonnelExtraction,
)
from jazz_agent.core.normalize import normalize_act_name
from jazz_agent.pipeline.adjudicate import adjudicate
from jazz_agent.pipeline.verify import apply_verification, verify_artist
from jazz_agent.ports.extractor import Extractor
from jazz_agent.ports.graph import GraphService
from jazz_agent.ports.music import MusicService
from jazz_agent.ports.repository import ArtistRepo, CapitalbopRepo, ShowRepo

logger = logging.getLogger(__name__)


@dataclass
class ExtractMatchResult:
    series_seen: int = 0
    haiku_calls: int = 0
    cache_hits: int = 0
    leaders_matched: int = 0
    leaders_missed: int = 0
    # Series whose extraction or matching raised; the rest of the run is unaffected.
    failures: list[str] = field(default_factory=list)

    def detail(self) -> str:
        parts = [
            f"{self.series_seen} series",
            f"{self.haiku_calls} extracted",
            f"{self.cache_hits} cached",
            f"{self.leaders_matched} leaders matched",
            f"{self.leaders_missed} missed",
        ]
        if self.failures:
            parts.append("failed: " + "; ".join(self.failures))
        return "; ".join(parts)


def extract_and_match(
    events: Iterable[CapitalbopEvent],
    capitalbop_repo: CapitalbopRepo,
    show_repo: ShowRepo,
    artist_repo: ArtistRepo,
    extractor: Extractor,
    graph: GraphService,
    music: MusicService,
    llm_client: anthropic.Anthropic,
    extraction_model: str,
    adjudication_model: str,
    now: datetime,
) -> ExtractMatchResult:
    """Never raises for one series' sake: a failing series is reported in ``failures`` and
    every other series still runs (AGENTS.md: errors are caught per unit, never per run)."""
    result = ExtractMatchResult()
    # Per-run memos: a residency has one extraction and each leader one adjudication, however
    # many dates the run covers.
    extractions: dict[tuple[int, str | None], PersonnelExtraction] = {}
    adjudicated: dict[str, _Verdict] = {}
    seen_series: set[int] = set()

    for event in sorted(events, key=lambda e: e.instance_id):
        if event.event_id not in seen_series:
            seen_series.add(event.event_id)
            result.series_seen += 1
        show = show_repo.get_show(event.show_id)
        if show is None:
            continue
        key = (event.event_id, event.description_hash)
        try:
            if key not in extractions:
                extractions[key] = _personnel_for(
                    event, show.act_name_raw, capitalbop_repo, extractor, extraction_model, result
                )
            personnel = extractions[key]
            leaders = personnel.leaders or (show.act_name_raw,)
            show_repo.record_performers(event.show_id, _flag_leaders(personnel, leaders))
            for leader in leaders:
                _match_leader(
                    leader,
                    event.show_id,
                    show.raw_text,
                    adjudicated,
                    artist_repo,
                    show_repo,
                    music,
                    llm_client,
                    adjudication_model,
                    result,
                )
        except Exception as e:
            logger.exception("DC extraction failed for event %s", event.event_id)
            result.failures.append(f"event {event.event_id}: {e}")
            extractions.pop(key, None)

    _verify_matched(adjudicated, artist_repo, graph, now)
    return result


@dataclass(frozen=True, slots=True)
class _Verdict:
    artist: Artist | None
    miss: MatchMiss | None


def _personnel_for(
    event: CapitalbopEvent,
    act_name: str,
    capitalbop_repo: CapitalbopRepo,
    extractor: Extractor,
    model: str,
    result: ExtractMatchResult,
) -> PersonnelExtraction:
    description = html_to_text(event.description_html or "")
    if not description.strip():
        # Nothing for Haiku to read; the act name is the only leader there is.
        return PersonnelExtraction()
    assert event.description_hash is not None
    cached = capitalbop_repo.get_extraction(event.event_id, event.description_hash)
    if cached is not None:
        result.cache_hits += 1
        return _from_cache(cached)
    personnel = extractor.extract_personnel(act_name, description)
    result.haiku_calls += 1
    capitalbop_repo.put_extraction(
        event.event_id, event.description_hash, _to_cache(personnel), model
    )
    return personnel


def _to_cache(p: PersonnelExtraction) -> dict[str, object]:
    return {
        "leaders": list(p.leaders),
        "performers": [{"name": x.name, "instrument": x.instrument} for x in p.performers],
        "album_mentioned": p.album_mentioned,
        "is_tribute": p.is_tribute,
    }


def _from_cache(raw: dict[str, Any] | Any) -> PersonnelExtraction:
    return PersonnelExtraction(
        leaders=tuple(raw.get("leaders", ())),
        performers=tuple(
            Performer(name=x["name"], instrument=x.get("instrument"))
            for x in raw.get("performers", ())
        ),
        album_mentioned=raw.get("album_mentioned"),
        is_tribute=bool(raw.get("is_tribute", False)),
    )


def _flag_leaders(p: PersonnelExtraction, leaders: tuple[str, ...]) -> list[Performer]:
    led = {normalize_act_name(n).casefold() for n in leaders}
    return [
        Performer(
            name=x.name,
            instrument=x.instrument,
            is_leader=normalize_act_name(x.name).casefold() in led,
        )
        for x in p.performers
    ]


def _match_leader(
    leader: str,
    show_id: int,
    raw_text: str,
    adjudicated: dict[str, _Verdict],
    artist_repo: ArtistRepo,
    show_repo: ShowRepo,
    music: MusicService,
    llm_client: anthropic.Anthropic,
    model: str,
    result: ExtractMatchResult,
) -> None:
    memo_key = normalize_act_name(leader).casefold()
    verdict = adjudicated.get(memo_key)
    if verdict is None:
        outcome = adjudicate(show_id, leader, raw_text, music, llm_client, model)
        verdict = _Verdict(outcome.matched_artist, outcome.match_miss)
        adjudicated[memo_key] = verdict

    if verdict.artist is not None:
        artist_repo.upsert_artist(verdict.artist)
        artist_repo.link_show_artist(show_id, verdict.artist.spotify_artist_id)
        result.leaders_matched += 1
        return

    assert verdict.miss is not None
    # The daily run repeats; a miss already logged for this show and name is not logged again.
    if not any(m.act_name_raw == leader for m in show_repo.match_misses_for_show(show_id)):
        show_repo.record_match_miss(
            MatchMiss(
                show_id=show_id,
                act_name_raw=leader,
                reason=verdict.miss.reason,
                best_guess_id=verdict.miss.best_guess_id,
                best_guess_confidence=verdict.miss.best_guess_confidence,
            )
        )
    result.leaders_missed += 1


def _verify_matched(
    adjudicated: dict[str, _Verdict], artist_repo: ArtistRepo, graph: GraphService, now: datetime
) -> None:
    """ARCHITECTURE section 6 stage 5 for every distinct matched artist. Correction of a
    disputed match (stages 6-7) repairs a playlist, so it belongs to the rolling reconciler
    (DC section 10); here MusicBrainz can only change ``verification_state`` (invariant 3)."""
    seen: set[str] = set()
    for verdict in adjudicated.values():
        artist = verdict.artist
        if artist is None or artist.spotify_artist_id in seen:
            continue
        seen.add(artist.spotify_artist_id)
        outcome = verify_artist(artist, graph)
        artist_repo.upsert_artist(apply_verification(artist, outcome, now))
