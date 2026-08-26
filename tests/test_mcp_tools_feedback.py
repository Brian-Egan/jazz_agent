"""mcp.tools_feedback tests against a real Postgres. MusicService is a fake --
no live Spotify calls."""

from __future__ import annotations

from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.core.models import Artist
from jazz_agent.mcp.tools_feedback import get_listening_candidates, record_feedback

FRISELL_ID = "3JsHnjpbhX4SnySpvpa9DK"


class FakeMusicService:
    def __init__(
        self,
        currently_playing: dict[str, Any] | None = None,
        recently_played: list[dict[str, Any]] | None = None,
    ) -> None:
        self._currently_playing = currently_playing
        self._recently_played = recently_played or []

    def currently_playing(self) -> dict[str, Any] | None:
        return self._currently_playing

    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._recently_played

    def search_artists(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_artist_albums(self, spotify_artist_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_album_tracks(self, spotify_album_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def create_playlist(self, title: str, description: str) -> dict[str, Any]:
        raise NotImplementedError

    def get_playlist(self, spotify_playlist_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def add_tracks(self, spotify_playlist_id: str, spotify_track_ids: Any) -> None:
        raise NotImplementedError

    def remove_tracks(self, spotify_playlist_id: str, spotify_track_ids: Any) -> None:
        raise NotImplementedError

    def unfollow_playlist(self, spotify_playlist_id: str) -> None:
        raise NotImplementedError


def _seed_artist(db: ConnectionPool) -> None:
    PgArtistRepo(db).upsert_artist(
        Artist(
            spotify_artist_id=FRISELL_ID,
            name="Bill Frisell",
            match_method="llm_adjudicated",
            match_confidence=0.9,
        )
    )


def test_record_feedback_rejects_a_target_that_does_not_resolve(db: ConnectionPool) -> None:
    with pytest.raises(ValueError, match="does not resolve"):
        record_feedback(db, "artist", "no-such-artist", sentiment="liked")


def test_record_feedback_rejects_an_invalid_target_type(db: ConnectionPool) -> None:
    with pytest.raises(ValueError, match="target_type"):
        record_feedback(db, "playlist", "some-id", sentiment="liked")


def test_record_feedback_rejects_an_invalid_sentiment(db: ConnectionPool) -> None:
    # Live incident: a caller guessed "dislike", "negative", "down", and
    # "thumbs_down" in turn, hitting the raw DB CHECK constraint's opaque
    # error four times with no way to discover the actual valid values
    # (liked/disliked/neutral) short of reading the schema directly. This
    # must fail fast with the valid options listed, same as target_type does.
    with pytest.raises(ValueError, match="sentiment must be one of") as exc_info:
        record_feedback(db, "artist", "some-id", sentiment="dislike")
    assert "disliked" in str(exc_info.value)
    assert "liked" in str(exc_info.value)
    assert "neutral" in str(exc_info.value)


def test_record_feedback_rejects_empty_content(db: ConnectionPool) -> None:
    _seed_artist(db)
    with pytest.raises(ValueError, match="sentiment, a note, or both"):
        record_feedback(db, "artist", FRISELL_ID)


def test_record_feedback_succeeds_for_a_resolved_artist(db: ConnectionPool) -> None:
    _seed_artist(db)

    result = record_feedback(db, "artist", FRISELL_ID, sentiment="liked", note="loved it")

    assert result["feedback_id"] > 0


def test_get_listening_candidates_flags_items_not_in_the_log(db: ConnectionPool) -> None:
    music = FakeMusicService(
        currently_playing={"item": {"id": "unlogged-track", "name": "Some Track", "artists": []}}
    )

    result = get_listening_candidates(db, music)  # type: ignore[arg-type]

    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["in_log"] is False
    assert result["candidates"][0]["currently_playing"] is True


def test_get_listening_candidates_no_current_track(db: ConnectionPool) -> None:
    music = FakeMusicService(currently_playing=None, recently_played=[])

    result = get_listening_candidates(db, music)  # type: ignore[arg-type]

    assert result["candidates"] == []
