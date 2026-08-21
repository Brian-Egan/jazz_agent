"""pipeline.verify tests, including the MB-independence test -- the invariant
AGENTS.md calls out directly: a MusicBrainz failure must leave the produced
playlist byte-identical to the healthy case, differing only in
artists.verification_state. Runs against a real Postgres (see conftest.py);
MusicService and the Anthropic client are fakes/stubs, no live calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.adapters.pg.playlist_repo import PgPlaylistRepo
from jazz_agent.adapters.pg.show_repo import PgShowRepo
from jazz_agent.core.models import Artist, MbArtist, MbArtistEdge, Show
from jazz_agent.pipeline.reconcile import WeekBooking, reconcile_week
from jazz_agent.pipeline.verify import verify_and_correct

CLUB_ID = "village-vanguard"
WEEK_START = date(2026, 8, 18)
VERIFIED_AT = datetime(2026, 8, 19, 13, 0, tzinfo=UTC)

FRISELL_SPOTIFY_ID = "3JsHnjpbhX4SnySpvpa9DK"
FRISELL_MBID = "a21318db-f228-4a4d-8bce-6947a62985a5"
WRONG_SPOTIFY_ID = "wrongartist999"
WRONG_MBID = "00000000-0000-0000-0000-0000000000ff"


class FakeMusicService:
    def __init__(self) -> None:
        self._albums: dict[str, list[dict[str, Any]]] = {}
        self._tracks: dict[str, list[dict[str, Any]]] = {}
        self.create_playlist_calls = 0
        self.add_tracks_calls: list[tuple[str, list[str]]] = []
        self.remove_tracks_calls: list[tuple[str, list[str]]] = []

    def register_artist(
        self, spotify_artist_id: str, album_id: str, tracks: list[dict[str, Any]]
    ) -> None:
        self._albums[spotify_artist_id] = [
            {"id": album_id, "name": f"Album {album_id}", "popularity": 50}
        ]
        self._tracks[album_id] = tracks

    def get_artist_albums(self, spotify_artist_id: str) -> list[dict[str, Any]]:
        return self._albums.get(spotify_artist_id, [])

    def get_album_tracks(self, spotify_album_id: str) -> list[dict[str, Any]]:
        return self._tracks.get(spotify_album_id, [])

    def create_playlist(self, title: str, description: str) -> dict[str, Any]:
        self.create_playlist_calls += 1
        return {"id": "spotify-playlist-1", "external_urls": {"spotify": "https://x/1"}}

    def add_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        self.add_tracks_calls.append((spotify_playlist_id, list(spotify_track_ids)))

    def remove_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        self.remove_tracks_calls.append((spotify_playlist_id, list(spotify_track_ids)))

    def search_artists(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_playlist(self, spotify_playlist_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def unfollow_playlist(self, spotify_playlist_id: str) -> None:
        raise NotImplementedError

    def currently_playing(self) -> dict[str, Any] | None:
        raise NotImplementedError

    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError


class FakeGraphService:
    def __init__(
        self,
        mb_artist: MbArtist | None = None,
        edges: list[MbArtistEdge] | None = None,
        raise_error: bool = False,
    ) -> None:
        self._mb_artist = mb_artist
        self._edges = edges or []
        self._raise_error = raise_error
        self.resolve_calls: list[str] = []

    def resolve_mbid(self, name: str) -> MbArtist | None:
        self.resolve_calls.append(name)
        if self._raise_error:
            raise RuntimeError("MusicBrainz is down")
        return self._mb_artist

    def spotify_url_for(self, mbid: str) -> str | None:
        return self._mb_artist.spotify_url if self._mb_artist else None

    def edges_for(self, mbid: str) -> list[MbArtistEdge]:
        if self._raise_error:
            raise RuntimeError("MusicBrainz is down")
        return self._edges


class FakeDisputeMessages:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        from dataclasses import dataclass

        @dataclass
        class Block:
            input: dict[str, Any]
            name: str = "resolve_dispute"
            type: str = "tool_use"

        @dataclass
        class Message:
            content: list[Block]

        return Message(content=[Block(input=self._payload)])


class FakeAnthropicClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.messages = FakeDisputeMessages(payload)


def _insert_club(
    db: ConnectionPool, club_id: str = CLUB_ID, name: str = "Village Vanguard"
) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, schedule_url) VALUES (%s, %s, %s)",
            (club_id, name, "https://example.com"),
        )


def _seed_show_and_artist(
    db: ConnectionPool, spotify_artist_id: str = FRISELL_SPOTIFY_ID
) -> tuple[int, Artist]:
    show_repo = PgShowRepo(db)
    show_id = show_repo.upsert_show(
        Show(
            club_id=CLUB_ID,
            show_date=WEEK_START,
            act_name_raw="The Bill Frisell Four",
            act_name_norm="Bill Frisell",
        )
    )
    artist = Artist(
        spotify_artist_id=spotify_artist_id,
        name="Bill Frisell",
        match_method="llm_adjudicated",
        match_confidence=0.9,
    )
    PgArtistRepo(db).upsert_artist(artist)
    return show_id, artist


def test_mb_raising_leaves_playlist_byte_identical_to_the_healthy_case(db: ConnectionPool) -> None:
    _insert_club(db)

    music_broken = FakeMusicService()
    music_broken.register_artist(FRISELL_SPOTIFY_ID, "album1", _tracks("album1", 5))
    show_id_a, artist_a = _seed_show_and_artist(db)
    playlist_repo = PgPlaylistRepo(db)
    broken_playlist = reconcile_week(
        CLUB_ID,
        "Village Vanguard",
        WEEK_START,
        [WeekBooking(WEEK_START, "Bill Frisell", FRISELL_SPOTIFY_ID, show_id_a)],
        music_broken,
        playlist_repo,
    )
    broken_graph = FakeGraphService(raise_error=True)
    broken_dispute_llm = FakeAnthropicClient({"winner": "none", "reasoning": "unused"})

    outcome = verify_and_correct(
        artist_a,
        broken_playlist,
        broken_graph,
        music_broken,
        PgArtistRepo(db),
        playlist_repo,
        PgShowRepo(db),
        broken_dispute_llm,  # type: ignore[arg-type]
        "claude-haiku-4-5",
        VERIFIED_AT,
    )

    assert outcome.verification_state == "unverified"
    broken_tracks = playlist_repo.tracks_for(broken_playlist.id)  # type: ignore[arg-type]

    # Healthy case: MB confirms the same match cleanly (verified, not disputed).
    _insert_club(db, club_id="smalls")
    music_healthy = FakeMusicService()
    music_healthy.register_artist(FRISELL_SPOTIFY_ID, "album1", _tracks("album1", 5))
    show_repo = PgShowRepo(db)
    show_id_b = show_repo.upsert_show(
        Show(
            club_id="smalls",
            show_date=WEEK_START,
            act_name_raw="The Bill Frisell Four",
            act_name_norm="Bill Frisell",
        )
    )
    artist_b = Artist(
        spotify_artist_id=FRISELL_SPOTIFY_ID,
        name="Bill Frisell",
        match_method="llm_adjudicated",
        match_confidence=0.9,
    )
    PgArtistRepo(db).upsert_artist(artist_b)
    healthy_playlist = reconcile_week(
        "smalls",
        "SmallsLIVE",
        WEEK_START,
        [WeekBooking(WEEK_START, "Bill Frisell", FRISELL_SPOTIFY_ID, show_id_b)],
        music_healthy,
        playlist_repo,
    )
    healthy_graph = FakeGraphService(
        mb_artist=MbArtist(
            mbid=FRISELL_MBID,
            name="Bill Frisell",
            entity_type="Person",
            spotify_url=f"https://open.spotify.com/artist/{FRISELL_SPOTIFY_ID}",
        )
    )
    healthy_outcome = verify_and_correct(
        artist_b,
        healthy_playlist,
        healthy_graph,
        music_healthy,
        PgArtistRepo(db),
        playlist_repo,
        show_repo,
        FakeAnthropicClient({"winner": "none", "reasoning": "unused"}),  # type: ignore[arg-type]
        "claude-haiku-4-5",
        VERIFIED_AT,
    )

    assert healthy_outcome.verification_state == "verified"
    healthy_tracks = playlist_repo.tracks_for(healthy_playlist.id)  # type: ignore[arg-type]

    # The whole point: identical track lists, only verification_state differs.
    assert [(t.spotify_track_id, t.position) for t in broken_tracks] == [
        (t.spotify_track_id, t.position) for t in healthy_tracks
    ]
    assert outcome.verification_state != healthy_outcome.verification_state


def _tracks(album_id: str, count: int) -> list[dict[str, Any]]:
    return [
        {"id": f"{album_id}-t{i}", "name": f"Track {i}", "track_number": i}
        for i in range(1, count + 1)
    ]


def test_dispute_path_writes_correction_and_track_events_and_replaces_album(
    db: ConnectionPool,
) -> None:
    _insert_club(db)
    music = FakeMusicService()
    music.register_artist(FRISELL_SPOTIFY_ID, "wrong-album", _tracks("wrong-album", 3))
    music.register_artist(WRONG_SPOTIFY_ID, "right-album", _tracks("right-album", 4))
    show_id, artist = _seed_show_and_artist(db)
    playlist_repo = PgPlaylistRepo(db)
    playlist = reconcile_week(
        CLUB_ID,
        "Village Vanguard",
        WEEK_START,
        [WeekBooking(WEEK_START, "Bill Frisell", FRISELL_SPOTIFY_ID, show_id)],
        music,
        playlist_repo,
    )
    graph = FakeGraphService(
        mb_artist=MbArtist(
            mbid=FRISELL_MBID,
            name="Bill Frisell (Group)",
            entity_type="Group",
            spotify_url=f"https://open.spotify.com/artist/{WRONG_SPOTIFY_ID}",
        ),
        edges=[
            MbArtistEdge(
                src_mbid=FRISELL_MBID,
                dst_mbid="x",
                dst_name="Some Band",
                edge_type="member of band",
            )
        ],
    )
    llm = FakeAnthropicClient({"winner": "musicbrainz", "reasoning": "MB link is correct"})

    outcome = verify_and_correct(
        artist,
        playlist,
        graph,
        music,
        PgArtistRepo(db),
        playlist_repo,
        PgShowRepo(db),
        llm,  # type: ignore[arg-type]
        "claude-haiku-4-5",
        VERIFIED_AT,
    )

    assert outcome.verification_state == "disputed"
    assert llm.messages.calls  # re-adjudication actually ran

    events = playlist_repo.events_for(playlist.id)  # type: ignore[arg-type]
    event_types = [e.event_type for e in events]
    assert "tracks_removed" in event_types
    assert "tracks_added" in event_types
    assert "correction" in event_types

    live_tracks = [t for t in playlist_repo.tracks_for(playlist.id) if t.removed_at is None]  # type: ignore[arg-type]
    assert {t.spotify_album_id for t in live_tracks} == {"right-album"}
    assert len(live_tracks) == 4

    corrected_artist = PgArtistRepo(db).get_artist(WRONG_SPOTIFY_ID)
    assert corrected_artist is not None
    assert corrected_artist.verification_state == "verified"
    assert corrected_artist.match_notes == "MB link is correct"


def test_resolution_none_produces_match_miss_and_removes_album(db: ConnectionPool) -> None:
    _insert_club(db)
    music = FakeMusicService()
    music.register_artist(FRISELL_SPOTIFY_ID, "wrong-album", _tracks("wrong-album", 3))
    show_id, artist = _seed_show_and_artist(db)
    playlist_repo = PgPlaylistRepo(db)
    playlist = reconcile_week(
        CLUB_ID,
        "Village Vanguard",
        WEEK_START,
        [WeekBooking(WEEK_START, "Bill Frisell", FRISELL_SPOTIFY_ID, show_id)],
        music,
        playlist_repo,
    )
    graph = FakeGraphService(
        mb_artist=MbArtist(
            mbid=WRONG_MBID,
            name="Someone Else",
            entity_type="Person",
            spotify_url=f"https://open.spotify.com/artist/{WRONG_SPOTIFY_ID}",
        )
    )
    llm = FakeAnthropicClient({"winner": "none", "reasoning": "neither candidate fits"})
    show_repo = PgShowRepo(db)

    outcome = verify_and_correct(
        artist,
        playlist,
        graph,
        music,
        PgArtistRepo(db),
        playlist_repo,
        show_repo,
        llm,  # type: ignore[arg-type]
        "claude-haiku-4-5",
        VERIFIED_AT,
    )

    assert outcome.verification_state == "disputed"
    live_tracks = [t for t in playlist_repo.tracks_for(playlist.id) if t.removed_at is None]  # type: ignore[arg-type]
    assert live_tracks == []

    misses = show_repo.match_misses_for_show(show_id)
    assert len(misses) == 1
    assert misses[0].reason == "DISPUTE_RESOLVED_NONE"
    assert misses[0].best_guess_id == FRISELL_SPOTIFY_ID

    events = playlist_repo.events_for(playlist.id)  # type: ignore[arg-type]
    assert [e.event_type for e in events] == ["created", "tracks_removed", "correction"]


def test_verification_runs_strictly_after_playlist_already_exists(db: ConnectionPool) -> None:
    """Proves verification is never a precondition: the playlist is fully built
    and correct before verify_and_correct is even called."""
    _insert_club(db)
    music = FakeMusicService()
    music.register_artist(FRISELL_SPOTIFY_ID, "album1", _tracks("album1", 5))
    show_id, artist = _seed_show_and_artist(db)
    playlist_repo = PgPlaylistRepo(db)

    playlist = reconcile_week(
        CLUB_ID,
        "Village Vanguard",
        WEEK_START,
        [WeekBooking(WEEK_START, "Bill Frisell", FRISELL_SPOTIFY_ID, show_id)],
        music,
        playlist_repo,
    )
    # Assert the playlist is already complete -- verification hasn't run yet.
    assert playlist.spotify_playlist_id is not None
    tracks_before_verification = playlist_repo.tracks_for(playlist.id)  # type: ignore[arg-type]
    assert len(tracks_before_verification) == 5

    graph = FakeGraphService(raise_error=True)
    verify_and_correct(
        artist,
        playlist,
        graph,
        music,
        PgArtistRepo(db),
        playlist_repo,
        PgShowRepo(db),
        FakeAnthropicClient({"winner": "none", "reasoning": "unused"}),  # type: ignore[arg-type]
        "claude-haiku-4-5",
        VERIFIED_AT,
    )

    tracks_after_verification = playlist_repo.tracks_for(playlist.id)  # type: ignore[arg-type]
    assert tracks_before_verification == tracks_after_verification
