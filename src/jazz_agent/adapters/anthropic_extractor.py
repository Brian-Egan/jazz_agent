"""Claude Haiku extraction: raw HTML -> structured shows (ARCHITECTURE.md section 5,
ADR-003, ADR-009, ADR-013).

Opportunistically prefers JSON-LD schema.org/Event (or MusicEvent) blocks when
present in the page -- deterministic and free -- falling back to a Haiku call
over trafilatura-converted prose text. Never required: as of this writing none
of the four seed-candidate sites publish Event JSON-LD (checked directly
against their live pages while building this -- Village Vanguard's only
JSON-LD is generic Yoast SEO site metadata, not show data), so this path
exists for whichever venue eventually does, not because it's exercised by the
current fixtures.

performers[] is mandatory in the prompt, not optional: it is the only
taste-graph substrate that cannot be backfilled (ADR-013).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

import anthropic
import trafilatura

from jazz_agent.core.models import ExtractedShow, Performer

logger = logging.getLogger(__name__)

_JSON_LD_PATTERN = re.compile(
    r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>""",
    re.DOTALL | re.IGNORECASE,
)
_EVENT_TYPES = {"Event", "MusicEvent", "TheaterEvent"}

_SYSTEM_PROMPT = (
    "You extract jazz club schedule listings from web page text into structured "
    "data using the record_shows tool. Read every show mentioned in the text. "
    "For each show, name every musician in the band and their instrument where "
    "the text states one -- club listings usually name the full lineup, e.g. "
    "'Bill Frisell - Guitar, Greg Tardy - Saxophone, Gerald Clayton - Piano'; "
    "capture all of them, not just the headline act. Take each date exactly as "
    "the page states it -- a show listed under a given day belongs to that day "
    "even if its set time is after midnight; never reinterpret or shift a date. "
    "If the text describes no shows, call the tool with an empty shows list."
)

_TOOL_NAME = "record_shows"
_TOOL_SCHEMA: dict[str, Any] = {
    "name": _TOOL_NAME,
    "description": "Record every show found in the club listing text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "shows": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "show_date": {
                            "type": "string",
                            "description": "ISO 8601 date (YYYY-MM-DD), as labeled by the club",
                        },
                        "act_name": {"type": "string"},
                        "set_times": {"type": "array", "items": {"type": "string"}},
                        "performers": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "instrument": {"type": ["string", "null"]},
                                },
                                "required": ["name"],
                            },
                        },
                        "album_mentioned": {"type": ["string", "null"]},
                        "raw_text": {"type": "string"},
                    },
                    "required": ["show_date", "act_name", "raw_text"],
                },
            }
        },
        "required": ["shows"],
    },
}


class ExtractionFailed(Exception):
    """Malformed model output or an API failure.

    The caller is expected to catch this per club, never per run
    (AGENTS.md: "Errors: catch per club, never per run")."""


class AnthropicExtractor:
    def __init__(self, client: anthropic.Anthropic, model: str) -> None:
        self._client = client
        self._model = model

    def extract(
        self, text: str, window: int, today: date, venue_label: str | None = None
    ) -> list[ExtractedShow]:
        # A venue_label means this page mixes multiple venues (clubs.venue_label,
        # e.g. smallslive.com covers Smalls/Mezzrow/Jazz Cultural Theatre) --
        # the JSON-LD path doesn't know how to filter by venue, so it would
        # silently reintroduce cross-venue mixing if a page ever adds Event
        # JSON-LD later. Force the LLM path, which does filter, whenever a
        # venue_label is set.
        if venue_label is None:
            json_ld_shows = _extract_from_json_ld(text)
            if json_ld_shows is not None:
                return json_ld_shows

        return self._extract_with_llm(text, window, today, venue_label)

    def _extract_with_llm(
        self, html: str, window: int, today: date, venue_label: str | None
    ) -> list[ExtractedShow]:
        prose = trafilatura.extract(html) or ""
        if not prose.strip():
            return []

        venue_instruction_text = ""
        if venue_label is not None:
            venue_instruction_text = (
                f"This page lists shows for multiple venues. Only extract shows "
                f"explicitly labeled for {venue_label!r} (e.g. text like 'Live at "
                f"{venue_label}'). Ignore every show labeled for any other venue. "
            )

        try:
            # Constructed as plain dicts matching the SDK's TypedDict shapes at
            # runtime; mypy can't verify a dict literal against a TypedDict here
            # without hand-satisfying the SDK's very long tool-union Literal type.
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=_SYSTEM_PROMPT,
                tools=[_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": _TOOL_NAME},
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"Today's date is {today.isoformat()}. Extract every show from "
                            f"today through {window} weeks ahead from this club listing "
                            f"text. Where the text gives a date without a year, resolve it "
                            f"against today's date rather than guessing. "
                            f"{venue_instruction_text}\n\n{prose}"
                        ),
                    }
                ],
            )  # type: ignore[call-overload]
        except anthropic.APIError as e:
            raise ExtractionFailed(f"Anthropic API error: {e}") from e

        return _parse_tool_response(response)


def _extract_from_json_ld(html: str) -> list[ExtractedShow] | None:
    """Return shows parsed from schema.org/Event JSON-LD, or None if the page
    publishes no Event data -- the caller should fall back to the LLM in that case."""
    events: list[dict[str, Any]] = []
    for block in _JSON_LD_PATTERN.findall(html):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        events.extend(_flatten_events(data))

    if not events:
        return None

    shows = [parsed for event in events if (parsed := _parse_json_ld_event(event)) is not None]
    return shows


def _flatten_events(data: Any) -> list[dict[str, Any]]:
    """Walk a JSON-LD document (possibly an @graph, possibly a bare list) and
    return every node whose @type is an event type."""
    nodes: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            nodes.extend(_flatten_events(item))
    elif isinstance(data, dict):
        node_type = data.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if any(t in _EVENT_TYPES for t in types):
            nodes.append(data)
        graph = data.get("@graph")
        if isinstance(graph, list):
            nodes.extend(_flatten_events(graph))
    return nodes


def _parse_json_ld_event(event: dict[str, Any]) -> ExtractedShow | None:
    start_date = event.get("startDate")
    name = event.get("name")
    if not start_date or not name:
        return None

    show_date = _parse_date(str(start_date))
    if show_date is None:
        return None

    performers = []
    performer_field = event.get("performer")
    performer_list = performer_field if isinstance(performer_field, list) else [performer_field]
    for p in performer_list:
        if isinstance(p, dict) and p.get("name"):
            performers.append(Performer(name=p["name"]))
        elif isinstance(p, str):
            performers.append(Performer(name=p))

    return ExtractedShow(
        show_date=show_date,
        act_name=str(name),
        performers=tuple(performers),
        raw_text=json.dumps(event),
    )


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _parse_tool_response(response: anthropic.types.Message) -> list[ExtractedShow]:
    for block in response.content:
        if block.type == "tool_use" and block.name == _TOOL_NAME:
            return _parse_shows_payload(block.input)
    raise ExtractionFailed("Model response contained no record_shows tool call")


def _parse_shows_payload(payload: Any) -> list[ExtractedShow]:
    if not isinstance(payload, dict) or "shows" not in payload:
        raise ExtractionFailed(f"Malformed tool input, missing 'shows': {payload!r}")

    shows = []
    for raw_show in payload["shows"]:
        try:
            shows.append(_parse_show(raw_show))
        except (KeyError, TypeError, ValueError) as e:
            raise ExtractionFailed(f"Malformed show entry {raw_show!r}: {e}") from e
    return shows


def _parse_show(raw: dict[str, Any]) -> ExtractedShow:
    show_date = date.fromisoformat(raw["show_date"])
    performers = tuple(
        Performer(name=p["name"], instrument=p.get("instrument")) for p in raw.get("performers", [])
    )
    return ExtractedShow(
        show_date=show_date,
        act_name=raw["act_name"],
        set_times=tuple(raw.get("set_times", [])),
        performers=performers,
        album_mentioned=raw.get("album_mentioned"),
        raw_text=raw["raw_text"],
    )
