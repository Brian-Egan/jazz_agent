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

from jazz_agent.core.models import ExtractedShow, Performer, PersonnelExtraction

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


_PERSONNEL_SYSTEM_PROMPT = (
    "You read one jazz event listing from a Washington, D.C. calendar and record who is "
    "performing using the record_personnel tool. leaders: the act or acts that headline. A "
    "festival day or a double bill ('A / B', 'A and B') has several leaders, one entry each; "
    "a band led by a named musician has that musician (or the billed band name) as its one "
    "leader. Never list a musician honoured by a tribute or repertory act ('the music of X', "
    "'X Jazz Project') as a leader -- the performing act leads. performers: every musician "
    "the description names, with their instrument where it is stated. album_mentioned: the "
    "album if one is named, else null. is_tribute: true for a repertory or tribute act. Use "
    "only what the text says; do not guess names or instruments."
)

_PERSONNEL_TOOL_NAME = "record_personnel"
_PERSONNEL_TOOL_SCHEMA: dict[str, Any] = {
    "name": _PERSONNEL_TOOL_NAME,
    "description": "Record the leaders and named musicians of one event listing.",
    "input_schema": {
        "type": "object",
        "properties": {
            "leaders": {"type": "array", "items": {"type": "string"}},
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
            "is_tribute": {"type": "boolean"},
        },
        "required": ["leaders", "performers", "album_mentioned", "is_tribute"],
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
        self,
        text: str,
        window: int,
        today: date,
        venue_label: str | None = None,
        render_mode: str = "http",
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

        return self._extract_with_llm(text, window, today, venue_label, render_mode)

    def extract_personnel(self, act_name: str, description: str) -> PersonnelExtraction:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=_PERSONNEL_SYSTEM_PROMPT,
                tools=[_PERSONNEL_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": _PERSONNEL_TOOL_NAME},
                messages=[
                    {
                        "role": "user",
                        "content": f"Act: {act_name}\n\nDescription:\n{description}",
                    }
                ],
            )  # type: ignore[call-overload]
        except anthropic.APIError as e:
            raise ExtractionFailed(f"Anthropic API error: {e}") from e

        for block in response.content:
            if block.type == "tool_use" and block.name == _PERSONNEL_TOOL_NAME:
                return _parse_personnel(block.input)
        raise ExtractionFailed("Model response contained no record_personnel tool call")

    def _extract_with_llm(
        self, html: str, window: int, today: date, venue_label: str | None, render_mode: str
    ) -> list[ExtractedShow]:
        # trafilatura.extract()'s "main content" heuristic assumes a single
        # article-like page and confidently picks the wrong section on a
        # widget-heavy one -- confirmed live on two different failure shapes:
        # smallslive.com's combined calendar (returns the site's
        # mission-statement boilerplate instead of the listings) and
        # birdlandjazz.com's JS calendar widget (returns a cookie-consent
        # banner instead). Both return real, non-empty text, so the
        # empty-prose check below never catches either case. baseline() is
        # trafilatura's own simpler, less opinionated fallback extractor;
        # confirmed live that it keeps the actual listings on both pages.
        # venue_label (combined multi-venue page) and render_mode == 'js'
        # (needed JS rendering in the first place, itself evidence of a
        # widget-heavy page) are the two known triggers for this failure.
        if venue_label is not None or render_mode == "js":
            prose = trafilatura.baseline(html)[1] or ""
        else:
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


def _parse_personnel(payload: Any) -> PersonnelExtraction:
    try:
        leaders = tuple(str(n).strip() for n in payload["leaders"] if str(n).strip())
        performers = tuple(
            Performer(name=p["name"].strip(), instrument=p.get("instrument"))
            for p in payload.get("performers", [])
            if p["name"].strip()
        )
        return PersonnelExtraction(
            leaders=leaders,
            performers=performers,
            album_mentioned=payload.get("album_mentioned"),
            is_tribute=bool(payload.get("is_tribute", False)),
        )
    except (KeyError, TypeError, AttributeError) as e:
        raise ExtractionFailed(f"Malformed personnel payload {payload!r}: {e}") from e
