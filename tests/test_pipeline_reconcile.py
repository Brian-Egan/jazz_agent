"""pipeline.reconcile tests, including the idempotency test -- run against a
real, freshly-migrated Postgres (see conftest.py), which is the whole point:
this is where week_playlists' (club_id, week_start_date) constraint and
playlist_tracks' ON CONFLICT upsert actually get exercised, not just their
Python call sites. MusicService is a fake -- no live Spotify calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.adapters.pg.playlist_repo import PgPlaylistRepo
from jazz_agent.core.models import Artist
from jazz_agent.pipeline.reconcile import WeekBooking, reconcile_week

CLUB_ID = "village-vanguard"
WEEK_START = date(2026, 8, 18)  # a Tuesday


def _seed_artist(db: ConnectionPool, spotify_artist_id: str, name: str = "") -> None:
    """playlist_tracks.spotify_artist_id has a real FK to artists -- in the
    actual pipeline, adjudication (issue #7) persists the Artist before
    reconciliation ever runs, so tests must too."""
    PgArtistRepo(db).upsert_artist(
        Artist(
            spotify_artist_id=spotify_artist_id,
            name=name or spotify_artist_id,
            match_method="llm_adjudicated",
            match_confidence=0.9,
        )
    )


class FakeMusicService:
    def __init__(self) -> None:
        self._albums: dict[str, list[dict[str, Any]]] = {}
        self._tracks: dict[str, list[dict[str, Any]]] = {}
        self.create_playlist_calls: list[tuple[str, str]] = []
        self.add_tracks_calls: list[tuple[str, list[str]]] = []
        self._next_playlist_id = 1

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
        self.create_playlist_calls.append((title, description))
        playlist_id = f"spotify-playlist-{self._next_playlist_id}"
        self._next_playlist_id += 1
        return {
            "id": playlist_id,
            "external_urls": {"spotify": f"https://open.spotify.com/playlist/{playlist_id}"},
        }

    def add_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        self.add_tracks_calls.append((spotify_playlist_id, list(spotify_track_ids)))

    def search_artists(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_playlist(self, spotify_playlist_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def remove_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        raise NotImplementedError

    def unfollow_playlist(self, spotify_playlist_id: str) -> None:
        raise NotImplementedError

    def currently_playing(self) -> dict[str, Any] | None:
        raise NotImplementedError

    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError


def _insert_club(
    db: ConnectionPool, club_id: str = CLUB_ID, name: str = "Village Vanguard"
) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, schedule_url) VALUES (%s, %s, %s)",
            (club_id, name, "https://example.com"),
        )


def _tracks(album_id: str, count: int) -> list[dict[str, Any]]:
    return [
        {"id": f"{album_id}-t{i}", "name": f"Track {i}", "track_number": i}
        for i in range(1, count + 1)
    ]


def test_idempotency_no_duplicate_writes_on_second_run(db: ConnectionPool) -> None:
    _insert_club(db)
    music = FakeMusicService()
    music.register_artist("artist1", "album1", _tracks("album1", 5))
    _seed_artist(db, "artist1")
    repo = PgPlaylistRepo(db)
    bookings = [
        WeekBooking(show_date=WEEK_START, artist_name="Bill Frisell", spotify_artist_id="artist1")
    ]

    first = reconcile_week(CLUB_ID, "Village Vanguard", WEEK_START, bookings, music, repo)
    second = reconcile_week(CLUB_ID, "Village Vanguard", WEEK_START, bookings, music, repo)

    assert first.id == second.id
    assert music.create_playlist_calls == [music.create_playlist_calls[0]]  # exactly one
    assert len(music.create_playlist_calls) == 1
    assert len(music.add_tracks_calls) == 1  # second run added nothing new

    with db.connection() as conn:
        playlist_count = conn.execute(
            "SELECT count(*) FROM week_playlists WHERE club_id = %s", (CLUB_ID,)
        ).fetchone()[0]
        track_count = conn.execute(
            "SELECT count(*) FROM playlist_tracks WHERE week_playlist_id = %s", (first.id,)
        ).fetchone()[0]

    assert playlist_count == 1
    assert track_count == 5

    tracks_after_first = repo.tracks_for(first.id)  # type: ignore[arg-type]
    tracks_after_second = repo.tracks_for(second.id)  # type: ignore[arg-type]
    assert tracks_after_first == tracks_after_second


def test_six_night_residency_produces_exactly_one_album(db: ConnectionPool) -> None:
    _insert_club(db)
    music = FakeMusicService()
    music.register_artist("artist1", "album1", _tracks("album1", 8))
    _seed_artist(db, "artist1")
    repo = PgPlaylistRepo(db)
    bookings = [
        WeekBooking(
            show_date=date(2026, 8, 18 + i), artist_name="Bill Frisell", spotify_artist_id="artist1"
        )
        for i in range(6)  # Tue through Sun
    ]

    playlist = reconcile_week(CLUB_ID, "Village Vanguard", WEEK_START, bookings, music, repo)

    assert len(music.create_playlist_calls) == 1
    tracks = repo.tracks_for(playlist.id)  # type: ignore[arg-type]
    assert {t.spotify_album_id for t in tracks} == {"album1"}
    assert len(tracks) == 8  # one album's worth, not six copies of it


def test_same_artist_at_two_different_clubs_gets_an_album_in_each(db: ConnectionPool) -> None:
    _insert_club(db, club_id="village-vanguard", name="Village Vanguard")
    _insert_club(db, club_id="smalls", name="SmallsLIVE")
    music = FakeMusicService()
    music.register_artist("artist1", "album1", _tracks("album1", 4))
    _seed_artist(db, "artist1")
    repo = PgPlaylistRepo(db)
    bookings = [
        WeekBooking(show_date=WEEK_START, artist_name="Bill Frisell", spotify_artist_id="artist1")
    ]

    vanguard_playlist = reconcile_week(
        "village-vanguard", "Village Vanguard", WEEK_START, bookings, music, repo
    )
    smalls_playlist = reconcile_week("smalls", "SmallsLIVE", WEEK_START, bookings, music, repo)

    assert vanguard_playlist.id != smalls_playlist.id
    assert len(repo.tracks_for(vanguard_playlist.id)) == 4  # type: ignore[arg-type]
    assert len(repo.tracks_for(smalls_playlist.id)) == 4  # type: ignore[arg-type]
    assert len(music.create_playlist_calls) == 2


def test_adding_a_newly_announced_act_appends_without_disturbing_existing(
    db: ConnectionPool,
) -> None:
    _insert_club(db)
    music = FakeMusicService()
    music.register_artist("artist1", "album1", _tracks("album1", 3))
    _seed_artist(db, "artist1")
    music.register_artist("artist2", "album2", _tracks("album2", 2))
    _seed_artist(db, "artist2")
    repo = PgPlaylistRepo(db)
    week_one_booking = [
        WeekBooking(show_date=WEEK_START, artist_name="Bill Frisell", spotify_artist_id="artist1")
    ]

    first = reconcile_week(CLUB_ID, "Village Vanguard", WEEK_START, week_one_booking, music, repo)
    original_tracks = repo.tracks_for(first.id)  # type: ignore[arg-type]

    both_bookings = [
        *week_one_booking,
        WeekBooking(
            show_date=date(2026, 8, 20), artist_name="Ravi Coltrane", spotify_artist_id="artist2"
        ),
    ]
    second = reconcile_week(CLUB_ID, "Village Vanguard", WEEK_START, both_bookings, music, repo)
    updated_tracks = repo.tracks_for(second.id)  # type: ignore[arg-type]

    assert second.id == first.id
    assert updated_tracks[:3] == original_tracks  # existing tracks untouched, same order
    assert len(updated_tracks) == 5
    assert {t.spotify_album_id for t in updated_tracks[3:]} == {"album2"}


def test_playlist_events_recorded_for_creation_and_addition_but_not_a_no_op_run(
    db: ConnectionPool,
) -> None:
    _insert_club(db)
    music = FakeMusicService()
    music.register_artist("artist1", "album1", _tracks("album1", 2))
    _seed_artist(db, "artist1")
    music.register_artist("artist2", "album2", _tracks("album2", 2))
    _seed_artist(db, "artist2")
    repo = PgPlaylistRepo(db)
    bookings = [
        WeekBooking(show_date=WEEK_START, artist_name="Bill Frisell", spotify_artist_id="artist1")
    ]

    first = reconcile_week(CLUB_ID, "Village Vanguard", WEEK_START, bookings, music, repo)
    events_after_create = repo.events_for(first.id)  # type: ignore[arg-type]
    assert [e.event_type for e in events_after_create] == ["created"]

    bookings_with_addition = [
        *bookings,
        WeekBooking(
            show_date=date(2026, 8, 20), artist_name="Ravi Coltrane", spotify_artist_id="artist2"
        ),
    ]
    reconcile_week(CLUB_ID, "Village Vanguard", WEEK_START, bookings_with_addition, music, repo)
    events_after_addition = repo.events_for(first.id)  # type: ignore[arg-type]
    assert [e.event_type for e in events_after_addition] == ["created", "tracks_added"]

    reconcile_week(CLUB_ID, "Village Vanguard", WEEK_START, bookings_with_addition, music, repo)
    events_after_noop = repo.events_for(first.id)  # type: ignore[arg-type]
    assert events_after_noop == events_after_addition  # no new event for an unchanged re-run


def test_description_lists_artists_in_chronological_order_including_misses(
    db: ConnectionPool,
) -> None:
    _insert_club(db)
    music = FakeMusicService()
    music.register_artist("artist2", "album2", _tracks("album2", 1))
    _seed_artist(db, "artist2")
    repo = PgPlaylistRepo(db)
    bookings = [
        WeekBooking(
            show_date=date(2026, 8, 20), artist_name="Ravi Coltrane", spotify_artist_id="artist2"
        ),
        WeekBooking(
            show_date=WEEK_START, artist_name="Some Unmatched Trio", spotify_artist_id=None
        ),
    ]

    playlist = reconcile_week(CLUB_ID, "Village Vanguard", WEEK_START, bookings, music, repo)

    assert playlist.description == "Some Unmatched Trio, Ravi Coltrane"


def test_title_format(db: ConnectionPool) -> None:
    _insert_club(db)
    music = FakeMusicService()
    repo = PgPlaylistRepo(db)

    playlist = reconcile_week(CLUB_ID, "Village Vanguard", WEEK_START, [], music, repo)

    assert playlist.title == "This Week at Village Vanguard - Aug 18-Aug 24"
