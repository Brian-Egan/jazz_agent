"""Golden-file extraction tests over committed HTML fixtures (issue #5).

No live API call: anthropic.Anthropic is replaced with a fake client that
returns a canned tool_use response. Assert semantics (show counts, that dates
parse, that personnel/instruments are populated), not exact strings --
extraction is non-deterministic in production even though these fixtures are
matched to hand-written stub responses for testing our parsing code, not the
model's accuracy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from jazz_agent.adapters.anthropic_extractor import AnthropicExtractor, ExtractionFailed
from jazz_agent.core.models import Performer

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "club_html"


@dataclass
class _FakeToolUseBlock:
    input: dict[str, Any]
    name: str = "record_shows"
    type: str = "tool_use"


@dataclass
class _FakeMessage:
    content: list[_FakeToolUseBlock]


class _FakeMessages:
    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeMessage:
        self.calls.append(kwargs)
        if isinstance(self._payload, Exception):
            raise self._payload
        return _FakeMessage(content=[_FakeToolUseBlock(input=self._payload)])


@dataclass
class FakeAnthropicClient:
    """Duck-types anthropic.Anthropic's public surface: a .messages.create()."""

    payload: dict[str, Any] | Exception = field(default_factory=dict)
    messages: _FakeMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _FakeMessages(self.payload)


def _extractor(
    payload: dict[str, Any] | Exception,
) -> tuple[AnthropicExtractor, FakeAnthropicClient]:
    client = FakeAnthropicClient(payload)
    return AnthropicExtractor(client=client, model="claude-haiku-4-5"), client  # type: ignore[arg-type]


def test_village_vanguard_personnel_and_instruments_are_populated() -> None:
    html = (FIXTURES_DIR / "village_vanguard.html").read_text()
    extractor, client = _extractor(
        {
            "shows": [
                {
                    "show_date": "2026-08-18",
                    "act_name": "Bill Frisell Four",
                    "set_times": ["8:00 PM", "10:30 PM"],
                    "performers": [
                        {"name": "Bill Frisell", "instrument": "Guitar"},
                        {"name": "Greg Tardy", "instrument": "Saxophone"},
                        {"name": "Gerald Clayton", "instrument": "Piano"},
                    ],
                    "album_mentioned": None,
                    "raw_text": "Bill Frisell Four - Guitar, Saxophone, Piano",
                }
            ]
        }
    )

    shows = extractor.extract(html, window=4)

    assert len(shows) == 1
    show = shows[0]
    assert show.show_date == date(2026, 8, 18)
    assert len(show.performers) == 3
    assert all(p.instrument for p in show.performers)
    assert {p.name for p in show.performers} == {"Bill Frisell", "Greg Tardy", "Gerald Clayton"}
    assert client.messages.calls  # went through the LLM path, not JSON-LD


@pytest.mark.parametrize(
    ("fixture_name", "act_name", "show_date"),
    [
        ("smalls_live", "SmallsLIVE House Band", "2026-08-18"),
        ("blue_note", "Various Artists", "2026-08-18"),
        ("birdland", "Birdland Big Band", "2026-08-18"),
    ],
)
def test_golden_fixtures_extract_semantically_sane_shows(
    fixture_name: str, act_name: str, show_date: str
) -> None:
    html = (FIXTURES_DIR / f"{fixture_name}.html").read_text()
    extractor, _ = _extractor(
        {
            "shows": [
                {
                    "show_date": show_date,
                    "act_name": act_name,
                    "set_times": [],
                    "performers": [{"name": act_name, "instrument": None}],
                    "album_mentioned": None,
                    "raw_text": act_name,
                }
            ]
        }
    )

    shows = extractor.extract(html, window=4)

    assert len(shows) == 1
    assert shows[0].show_date == date.fromisoformat(show_date)
    assert shows[0].act_name


def test_no_shows_yields_empty_list_not_an_error() -> None:
    html = (
        "<html><body><article><p>The club is dark tonight. "
        "Check back soon.</p></article></body></html>"
    )
    extractor, _ = _extractor({"shows": []})

    shows = extractor.extract(html, window=4)

    assert shows == []


def test_malformed_tool_input_missing_shows_key_fails_the_club_not_the_run() -> None:
    html = "<html><body><article><p>Some listing text.</p></article></body></html>"
    extractor, _ = _extractor({"unexpected_key": []})

    with pytest.raises(ExtractionFailed):
        extractor.extract(html, window=4)


def test_malformed_show_entry_fails_the_club_not_the_run() -> None:
    html = "<html><body><article><p>Some listing text.</p></article></body></html>"
    extractor, _ = _extractor(
        {"shows": [{"show_date": "not-a-date", "act_name": "X", "raw_text": ""}]}
    )

    with pytest.raises(ExtractionFailed):
        extractor.extract(html, window=4)


def test_api_error_fails_the_club_not_the_run() -> None:
    import anthropic

    html = "<html><body><article><p>Some listing text.</p></article></body></html>"
    error = anthropic.APIConnectionError(request=None)  # type: ignore[arg-type]
    extractor, _ = _extractor(error)

    with pytest.raises(ExtractionFailed):
        extractor.extract(html, window=4)


def test_json_ld_event_is_preferred_over_the_llm() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "MusicEvent",
     "name": "Ravi Coltrane Quartet", "startDate": "2026-09-01",
     "performer": [{"@type": "Person", "name": "Ravi Coltrane"}]}
    </script>
    </head><body></body></html>
    """
    extractor, client = _extractor({"shows": []})  # would prove the JSON-LD path was skipped

    shows = extractor.extract(html, window=4)

    assert len(shows) == 1
    assert shows[0].act_name == "Ravi Coltrane Quartet"
    assert shows[0].show_date == date(2026, 9, 1)
    assert shows[0].performers == (Performer(name="Ravi Coltrane"),)
    assert client.messages.calls == []  # never fell through to the LLM


def test_json_ld_present_but_no_event_type_falls_back_to_llm() -> None:
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context": "https://schema.org", "@type": "WebSite", "name": "Some Club"}
    </script>
    </head><body><article><p>Ravi Coltrane Quartet plays tonight.</p></article></body></html>
    """
    extractor, client = _extractor(
        {
            "shows": [
                {
                    "show_date": "2026-09-01",
                    "act_name": "Ravi Coltrane Quartet",
                    "set_times": [],
                    "performers": [],
                    "album_mentioned": None,
                    "raw_text": "Ravi Coltrane Quartet plays tonight.",
                }
            ]
        }
    )

    shows = extractor.extract(html, window=4)

    assert len(shows) == 1
    assert client.messages.calls  # fell through to the LLM, as JSON-LD had no Event data
