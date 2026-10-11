"""pipeline.dc_extract against a real Postgres and the committed CapitalBop fixtures.

Haiku, Spotify and MusicBrainz are fakes. Assertions are semantic: how many calls were made,
which leaders were matched, what verification_state each case ended in.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.capitalbop import DEFAULT_BASE_URL, CapitalbopClient
from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.adapters.pg.capitalbop_repo import PgCapitalbopRepo
from jazz_agent.adapters.pg.show_repo import PgShowRepo
from jazz_agent.adapters.pg.venue_repo import PgVenueRepo
from jazz_agent.core.models import CapitalbopEvent, MbArtist, Performer, PersonnelExtraction
from jazz_agent.pipeline.dc_extract import ExtractMatchResult, extract_and_match
from jazz_agent.pipeline.dc_mirror import mirror_calendar

FIXTURES = Path(__file__).parent / "fixtures" / "capitalbop"
START, END = date(2026, 6, 1), date(2026, 11, 15)
NOW = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)

RAGSDALE_SERIES, DOUBLE_BILL_SERIES, FEST_DAY_2_SERIES, SCOFIELD_SERIES = 25683, 37202, 35507, 37190


class FakeMusic:
    """search_artists answers from a name -> candidates table; the rest is unused."""

    def __init__(self, catalog: dict[str, list[dict[str, Any]]]) -> None:
        self.catalog = {k.casefold(): v for k, v in catalog.items()}
        self.queries: list[str] = []

    def search_artists(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        self.queries.append(query)
        return self.catalog.get(query.casefold(), [])

    def __getattr__(self, name: str) -> Callable[..., Any]:
        raise NotImplementedError(name)


@dataclass
class _ToolUse:
    input: dict[str, Any]
    name: str = "adjudicate_match"
    type: str = "tool_use"


@dataclass
class _Message:
    content: list[_ToolUse]


class FakeAdjudicator:
    """Stands in for Haiku's judgement: picks the candidate whose name equals the query."""

    def __init__(self, music: FakeMusic, confidence: float = 0.95) -> None:
        self.messages = self
        self._music, self._confidence = music, confidence
        self.calls = 0

    def create(self, **_: Any) -> _Message:
        self.calls += 1
        candidates = self._music.catalog.get(self._music.queries[-1].casefold(), [])
        pick = next(
            (c for c in candidates if c["name"].casefold() == self._music.queries[-1].casefold()),
            None,
        )
        return _Message(
            [
                _ToolUse(
                    {
                        "artist_id": pick["id"] if pick else None,
                        "confidence": self._confidence if pick else 0.1,
                        "reasoning": "stub",
                    }
                )
            ]
        )


@dataclass
class FakeExtractor:
    """Haiku stand-in: a table of act name -> extraction, counting calls."""

    table: dict[str, PersonnelExtraction]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def extract_personnel(self, act_name: str, description: str) -> PersonnelExtraction:
        self.calls.append((act_name, description))
        return self.table.get(act_name, PersonnelExtraction(leaders=(act_name,)))

    def extract(self, *_: Any, **__: Any) -> list[Any]:
        raise NotImplementedError


class FakeGraph:
    def __init__(self, mb: MbArtist | None = None, raises: bool = False) -> None:
        self._mb, self._raises = mb, raises

    def resolve_mbid(self, name: str) -> MbArtist | None:
        if self._raises:
            raise RuntimeError("MusicBrainz is down")
        return self._mb

    def spotify_url_for(self, mbid: str) -> str | None:
        return None

    def edges_for(self, mbid: str) -> list[Any]:
        return []


def _artist(artist_id: str, name: str, genres: Sequence[str] = ("jazz",)) -> dict[str, Any]:
    return {
        "id": artist_id,
        "name": name,
        "genres": list(genres),
        "popularity": 30,
        "followers": {"total": 5000},
    }


@pytest.fixture
def mirrored(db: ConnectionPool) -> dict[int, list[CapitalbopEvent]]:
    """The fixture feed mirrored into Postgres; returns stored events by series id."""
    with respx.mock:
        respx.get(f"{DEFAULT_BASE_URL}/calendar").mock(
            return_value=httpx.Response(
                200, json=json.loads((FIXTURES / "calendar.json").read_text())
            )
        )
        respx.get(f"{DEFAULT_BASE_URL}/events").mock(
            side_effect=lambda r: httpx.Response(
                200,
                json=json.loads((FIXTURES / f"events_page{r.url.params['page']}.json").read_text()),
            )
        )
        source = CapitalbopClient(sleep=lambda _s: None)
        mirror_calendar(
            source,
            PgVenueRepo(db),
            PgShowRepo(db),
            PgCapitalbopRepo(db),
            START,
            END,
            fetch_started_at=NOW,
        )
    repo = PgCapitalbopRepo(db)
    with db.connection() as conn:
        ids = [r[0] for r in conn.execute("SELECT instance_id FROM capitalbop_events")]
    by_series: dict[int, list[CapitalbopEvent]] = {}
    for instance_id in sorted(ids):
        event = repo.get_event(instance_id)
        assert event is not None
        by_series.setdefault(event.event_id, []).append(event)
    return by_series


def _run(
    db: ConnectionPool,
    events: Sequence[CapitalbopEvent],
    extractor: FakeExtractor,
    music: FakeMusic,
    graph: FakeGraph | None = None,
) -> tuple[ExtractMatchResult, FakeAdjudicator]:
    llm = FakeAdjudicator(music)
    result = extract_and_match(
        events,
        PgCapitalbopRepo(db),
        PgShowRepo(db),
        PgArtistRepo(db),
        extractor,
        graph or FakeGraph(),
        music,
        llm,  # type: ignore[arg-type]
        "claude-haiku-4-5",
        "claude-haiku-4-5",
        NOW,
    )
    return result, llm


def _linked(db: ConnectionPool) -> set[tuple[int, str]]:
    with db.connection() as conn:
        return {
            (r[0], r[1])
            for r in conn.execute("SELECT show_id, spotify_artist_id FROM show_artists")
        }


def test_double_bill_yields_two_leaders_each_matched(
    db: ConnectionPool, mirrored: dict[int, list[CapitalbopEvent]]
) -> None:
    event = mirrored[DOUBLE_BILL_SERIES][0]
    music = FakeMusic(
        {
            "Raphael Rogiński": [_artist("rr1", "Raphael Rogiński")],
            "Sarah Hughes": [_artist("sh1", "Sarah Hughes")],
        }
    )
    extractor = FakeExtractor(
        {
            "Raphael Rogiński / Sarah Hughes": PersonnelExtraction(
                leaders=("Raphael Rogiński", "Sarah Hughes"),
                performers=(Performer("Sarah Hughes", "saxophone"), Performer("Dan Smith", "bass")),
            )
        }
    )

    result, _ = _run(db, [event], extractor, music)

    assert result.leaders_matched == 2 and result.leaders_missed == 0
    assert {a for _, a in _linked(db)} == {"rr1", "sh1"}
    performers = PgShowRepo(db).performers_for_show(event.show_id)
    assert {p.name for p in performers} == {"Sarah Hughes", "Dan Smith"}
    assert {p.name: p.is_leader for p in performers} == {"Sarah Hughes": True, "Dan Smith": False}


def test_festival_day_yields_several_leaders(
    db: ConnectionPool, mirrored: dict[int, list[CapitalbopEvent]]
) -> None:
    event = mirrored[FEST_DAY_2_SERIES][0]
    leaders = ("Act One", "Act Two", "Act Three")
    music = FakeMusic({n: [_artist(f"fest-{i}", n)] for i, n in enumerate(leaders)})
    extractor = FakeExtractor({event_name(db, event): PersonnelExtraction(leaders=leaders)})

    result, _ = _run(db, [event], extractor, music)

    assert result.leaders_matched == len(leaders) >= 3
    assert len({a for s, a in _linked(db) if s == event.show_id}) == len(leaders)


def event_name(db: ConnectionPool, event: CapitalbopEvent) -> str:
    show = PgShowRepo(db).get_show(event.show_id)
    assert show is not None
    return show.act_name_raw


def test_second_run_with_unchanged_descriptions_makes_zero_haiku_calls(
    db: ConnectionPool, mirrored: dict[int, list[CapitalbopEvent]]
) -> None:
    events = mirrored[RAGSDALE_SERIES] + mirrored[SCOFIELD_SERIES]
    music = FakeMusic({"Steve Ragsdale": [_artist("sr1", "Steve Ragsdale")]})
    extractor = FakeExtractor({})

    first, _ = _run(db, events, extractor, music)
    assert first.haiku_calls == 2  # two series, even though the residency has two dates
    calls_after_first = len(extractor.calls)
    links_after_first = _linked(db)

    second, _ = _run(db, events, extractor, music)

    assert second.haiku_calls == 0 and second.cache_hits >= 2
    assert len(extractor.calls) == calls_after_first
    assert _linked(db) == links_after_first  # converges, never accumulates


def test_edited_description_triggers_exactly_one_reextraction_for_its_series(
    db: ConnectionPool, mirrored: dict[int, list[CapitalbopEvent]]
) -> None:
    ragsdale, scofield = mirrored[RAGSDALE_SERIES], mirrored[SCOFIELD_SERIES]
    music = FakeMusic({})
    extractor = FakeExtractor({})
    _run(db, ragsdale + scofield, extractor, music)
    extractor.calls.clear()

    # CapitalBop edits the residency's description on both dates; the other series is untouched.
    repo = PgCapitalbopRepo(db)
    edited = [
        replace(
            e,
            description_html=(e.description_html or "") + "<p>Now with Dan Smith, bass.</p>",
            description_hash="edited-hash",
        )
        for e in ragsdale
    ]
    for e in edited:
        repo.upsert_event(e)

    result, _ = _run(db, edited + scofield, extractor, music)

    assert result.haiku_calls == 1
    assert [name for name, _ in extractor.calls] == [event_name(db, ragsdale[0])]
    assert "Dan Smith" in extractor.calls[0][1]


def test_residency_is_extracted_and_adjudicated_once_but_every_date_is_linked(
    db: ConnectionPool, mirrored: dict[int, list[CapitalbopEvent]]
) -> None:
    dates = mirrored[RAGSDALE_SERIES]
    assert len(dates) == 2
    music = FakeMusic({"Steve Ragsdale": [_artist("sr1", "Steve Ragsdale")]})
    extractor = FakeExtractor({"Steve Ragsdale": PersonnelExtraction(leaders=("Steve Ragsdale",))})

    result, llm = _run(db, dates, extractor, music)

    assert len(extractor.calls) == 1
    assert llm.calls == 1 and music.queries == ["Steve Ragsdale"]
    assert result.leaders_matched == 2
    assert {s for s, _ in _linked(db)} == {e.show_id for e in dates}


def test_ineligible_events_keep_a_name_and_no_performers(
    db: ConnectionPool, mirrored: dict[int, list[CapitalbopEvent]]
) -> None:
    eligible = mirrored[SCOFIELD_SERIES][0]
    skipped = mirrored[DOUBLE_BILL_SERIES][0]  # never passed in: not eligible
    music = FakeMusic({})
    extractor = FakeExtractor({})

    _run(db, [eligible], extractor, music)

    assert PgShowRepo(db).performers_for_show(skipped.show_id) == []
    assert event_name(db, skipped)  # the show and its name are still there
    assert all(skipped.show_id != s for s, _ in _linked(db))


def test_a_series_that_fails_extraction_does_not_stop_the_others(
    db: ConnectionPool, mirrored: dict[int, list[CapitalbopEvent]]
) -> None:
    class Flaky(FakeExtractor):
        def extract_personnel(self, act_name: str, description: str) -> PersonnelExtraction:
            if "Scofield" in act_name:
                raise RuntimeError("Anthropic API error")
            return super().extract_personnel(act_name, description)

    music = FakeMusic({"Steve Ragsdale": [_artist("sr1", "Steve Ragsdale")]})
    events = mirrored[SCOFIELD_SERIES] + mirrored[RAGSDALE_SERIES]

    result, _ = _run(db, events, Flaky({}), music)

    assert len(result.failures) == 1 and str(SCOFIELD_SERIES) in result.failures[0]
    assert result.leaders_matched == 2  # the residency still matched
    with db.connection() as conn:
        cached = conn.execute(
            "SELECT count(*) FROM capitalbop_extractions WHERE event_id = %s", (SCOFIELD_SERIES,)
        ).fetchone()
    assert cached == (0,)  # a failure caches nothing, so the next run retries


def test_unmatched_leader_logs_one_miss_even_across_repeated_runs(
    db: ConnectionPool, mirrored: dict[int, list[CapitalbopEvent]]
) -> None:
    event = mirrored[SCOFIELD_SERIES][0]
    music = FakeMusic({})  # Spotify knows nobody: invariant 8, a miss beats a guess

    first, _ = _run(db, [event], FakeExtractor({}), music)
    second, _ = _run(db, [event], FakeExtractor({}), music)

    assert first.leaders_missed == 1 and second.leaders_missed == 1
    assert len(PgShowRepo(db).match_misses_for_show(event.show_id)) == 1
    assert _linked(db) == set()


def test_musicbrainz_failure_changes_only_verification_state(
    db: ConnectionPool, mirrored: dict[int, list[CapitalbopEvent]]
) -> None:
    """Invariant 3 with DC leaders in the run: MusicBrainz down must leave the same
    matches and links as MusicBrainz healthy, differing only in verification_state."""
    events = mirrored[RAGSDALE_SERIES]
    catalog = {"Steve Ragsdale": [_artist("sr1", "Steve Ragsdale")]}
    healthy_mb = MbArtist(
        mbid="11111111-1111-1111-1111-111111111111",
        name="Steve Ragsdale",
        entity_type="Person",
        spotify_url="https://open.spotify.com/artist/sr1",
    )

    _run(db, events, FakeExtractor({}), FakeMusic(catalog), FakeGraph(raises=True))
    broken_links = _linked(db)
    broken = PgArtistRepo(db).get_artist("sr1")
    assert broken is not None

    _run(db, events, FakeExtractor({}), FakeMusic(catalog), FakeGraph(mb=healthy_mb))
    healthy_links = _linked(db)
    healthy = PgArtistRepo(db).get_artist("sr1")
    assert healthy is not None

    assert broken_links == healthy_links != set()
    assert broken.verification_state == "unverified"
    assert healthy.verification_state == "verified"
    assert (broken.name, broken.match_method) == (healthy.name, healthy.match_method)


def test_chuchito_and_chucho_valdes_are_distinct_artists(
    db: ConnectionPool, mirrored: dict[int, list[CapitalbopEvent]]
) -> None:
    """DC section 7's known hazard: father and son, both Cuban pianists with real catalogs,
    booked in one window. Each listing must resolve to its own artist, never the other."""
    chucho = _artist("chucho1", "Chucho Valdés", ["afro-cuban jazz", "latin jazz"])
    chuchito = _artist("chuchito1", "Chuchito Valdés", ["afro-cuban jazz", "latin jazz"])
    both = [chucho, chuchito]
    music = FakeMusic({"Chucho Valdés": both, "Chuchito Valdés": both})
    extractor = FakeExtractor(
        {
            "Steve Ragsdale": PersonnelExtraction(leaders=("Chucho Valdés",)),
            event_name(db, mirrored[SCOFIELD_SERIES][0]): PersonnelExtraction(
                leaders=("Chuchito Valdés",)
            ),
        }
    )

    _run(db, mirrored[RAGSDALE_SERIES][:1] + mirrored[SCOFIELD_SERIES], extractor, music)

    linked = _linked(db)
    assert {a for s, a in linked if s == mirrored[RAGSDALE_SERIES][0].show_id} == {"chucho1"}
    assert {a for s, a in linked if s == mirrored[SCOFIELD_SERIES][0].show_id} == {"chuchito1"}
