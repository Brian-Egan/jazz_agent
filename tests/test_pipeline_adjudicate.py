"""pipeline.adjudicate tests. No live Spotify or Anthropic calls -- both are
fakes/stubs, per AGENTS.md."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import pytest

from jazz_agent.pipeline.adjudicate import adjudicate


class FakeMusicService:
    """Only search_artists is exercised by adjudicate(); the rest exist so this
    structurally satisfies ports.music.MusicService for mypy."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self._results = results
        self.search_calls: list[tuple[str, int]] = []

    def search_artists(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        self.search_calls.append((query, limit))
        return self._results

    def get_artist_albums(self, spotify_artist_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_album_tracks(self, spotify_album_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def create_playlist(self, title: str, description: str) -> str:
        raise NotImplementedError

    def get_playlist(self, spotify_playlist_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def add_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        raise NotImplementedError

    def remove_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        raise NotImplementedError

    def unfollow_playlist(self, spotify_playlist_id: str) -> None:
        raise NotImplementedError

    def currently_playing(self) -> dict[str, Any] | None:
        raise NotImplementedError

    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError


@dataclass
class _FakeToolUseBlock:
    input: dict[str, Any]
    name: str = "adjudicate_match"
    type: str = "tool_use"


@dataclass
class _FakeMessage:
    content: list[_FakeToolUseBlock]


class _FakeMessages:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeMessage:
        self.calls.append(kwargs)
        return _FakeMessage(content=[_FakeToolUseBlock(input=self._payload)])


@dataclass
class FakeAnthropicClient:
    payload: dict[str, Any] = field(default_factory=dict)
    messages: _FakeMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _FakeMessages(self.payload)


BILL_FRISELL = {
    "id": "3JsHnjpbhX4SnySpvpa9DK",
    "name": "Bill Frisell",
    "genres": ["contemporary jazz", "jazz fusion", "jazz"],
    "popularity": 42,
    "followers": {"total": 123_456},
}

SAME_NAMED_METAL_BAND = {
    "id": "wrongband1",
    "name": "Bill Frisell",  # hypothetical same-named act, different genre entirely
    "genres": ["death metal", "thrash metal"],
    "popularity": 65,
    "followers": {"total": 2_000_000},
}


def test_accept_threshold_produces_matched_artist_with_match_notes() -> None:
    music = FakeMusicService([BILL_FRISELL])
    llm = FakeAnthropicClient(
        {"artist_id": BILL_FRISELL["id"], "confidence": 0.95, "reasoning": "exact name match"}
    )

    result = adjudicate(1, "The Bill Frisell Four", "raw text", music, llm, "claude-haiku-4-5")  # type: ignore[arg-type]

    assert result.match_miss is None
    assert result.matched_artist is not None
    assert result.matched_artist.spotify_artist_id == BILL_FRISELL["id"]
    assert result.matched_artist.needs_review is False
    assert result.matched_artist.match_notes == "exact name match"
    assert result.matched_artist.match_confidence == 0.95


def test_review_threshold_produces_matched_artist_flagged_needs_review() -> None:
    music = FakeMusicService([BILL_FRISELL])
    llm = FakeAnthropicClient(
        {"artist_id": BILL_FRISELL["id"], "confidence": 0.6, "reasoning": "plausible but uncertain"}
    )

    result = adjudicate(1, "Bill Frisell Trio", "raw text", music, llm, "claude-haiku-4-5")  # type: ignore[arg-type]

    assert result.matched_artist is not None
    assert result.matched_artist.needs_review is True
    assert result.match_miss is None


def test_low_confidence_produces_match_miss_not_a_match() -> None:
    music = FakeMusicService([BILL_FRISELL])
    llm = FakeAnthropicClient(
        {"artist_id": BILL_FRISELL["id"], "confidence": 0.3, "reasoning": "not confident"}
    )

    result = adjudicate(1, "Bill Frisell Trio", "raw text", music, llm, "claude-haiku-4-5")  # type: ignore[arg-type]

    assert result.matched_artist is None
    assert result.match_miss is not None
    assert result.match_miss.reason == "NO_CONFIDENT_MATCH"
    assert result.match_miss.best_guess_id == BILL_FRISELL["id"]
    assert result.match_miss.best_guess_confidence == 0.3
    assert result.match_miss.act_name_raw == "Bill Frisell Trio"


def test_same_named_non_jazz_act_is_filtered_before_the_llm_ever_sees_it() -> None:
    """The plausibility guard, not the model, is the primary defence (ADR-007)."""
    music = FakeMusicService([SAME_NAMED_METAL_BAND])
    llm = FakeAnthropicClient({"artist_id": None, "confidence": 0.0, "reasoning": "n/a"})

    result = adjudicate(1, "Bill Frisell Trio", "raw text", music, llm, "claude-haiku-4-5")  # type: ignore[arg-type]

    assert result.matched_artist is None
    assert result.match_miss is not None
    assert result.match_miss.reason == "NO_PLAUSIBLE_CANDIDATE"
    assert result.match_miss.best_guess_id == SAME_NAMED_METAL_BAND["id"]
    assert not llm.messages.calls  # never called -- filtered before adjudication


def test_no_candidates_at_all_produces_match_miss_with_no_best_guess() -> None:
    music = FakeMusicService([])
    llm = FakeAnthropicClient({"artist_id": None, "confidence": 0.0, "reasoning": "n/a"})

    result = adjudicate(1, "Some Totally Obscure Act", "raw text", music, llm, "claude-haiku-4-5")  # type: ignore[arg-type]

    assert result.matched_artist is None
    assert result.match_miss is not None
    assert result.match_miss.best_guess_id is None
    assert not llm.messages.calls


def test_tribute_act_does_not_resolve_to_the_honouree() -> None:
    """A tribute act's raw listing text should lead the model to reject the
    honouree's own candidate. This tests that our plumbing correctly turns a
    null/low-confidence verdict into a miss rather than force-matching the
    honouree -- not the model's own judgement, which is stubbed here."""
    music = FakeMusicService([BILL_FRISELL])
    llm = FakeAnthropicClient(
        {
            "artist_id": None,
            "confidence": 0.1,
            "reasoning": "raw text describes a tribute act playing Frisell's music, not Frisell",
        }
    )

    result = adjudicate(
        1,
        "A Tribute to Bill Frisell",
        "The Downtown Trio plays the music of Bill Frisell tonight.",
        music,
        llm,
        "claude-haiku-4-5",
    )  # type: ignore[arg-type]

    assert result.matched_artist is None
    assert result.match_miss is not None
    assert result.match_miss.act_name_raw == "A Tribute to Bill Frisell"


def test_llm_receives_raw_text_and_candidate_context() -> None:
    music = FakeMusicService([BILL_FRISELL])
    llm = FakeAnthropicClient(
        {"artist_id": BILL_FRISELL["id"], "confidence": 0.9, "reasoning": "ok"}
    )

    adjudicate(
        1, "Bill Frisell Trio", "tonight: Bill Frisell Trio, 8pm", music, llm, "claude-haiku-4-5"
    )  # type: ignore[arg-type]

    assert len(llm.messages.calls) == 1
    sent = llm.messages.calls[0]
    user_content = sent["messages"][0]["content"]
    assert "tonight: Bill Frisell Trio, 8pm" in user_content
    assert BILL_FRISELL["id"] in user_content


def test_search_uses_normalized_act_name() -> None:
    music = FakeMusicService([BILL_FRISELL])
    llm = FakeAnthropicClient(
        {"artist_id": BILL_FRISELL["id"], "confidence": 0.9, "reasoning": "ok"}
    )

    adjudicate(1, "The Bill Frisell Four", "raw text", music, llm, "claude-haiku-4-5")  # type: ignore[arg-type]

    assert music.search_calls == [("Bill Frisell", 10)]


@pytest.mark.parametrize("confidence", [0.0, 0.49, 0.5, 0.79, 0.8, 1.0])
def test_needs_review_boundary_matches_accept_threshold(confidence: float) -> None:
    music = FakeMusicService([BILL_FRISELL])
    llm = FakeAnthropicClient(
        {"artist_id": BILL_FRISELL["id"], "confidence": confidence, "reasoning": "ok"}
    )

    result = adjudicate(1, "Bill Frisell", "raw text", music, llm, "claude-haiku-4-5")  # type: ignore[arg-type]

    if confidence < 0.5:
        assert result.matched_artist is None
    else:
        assert result.matched_artist is not None
        assert result.matched_artist.needs_review == (confidence < 0.80)
