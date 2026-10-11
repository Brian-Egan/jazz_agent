from __future__ import annotations

from datetime import UTC, datetime

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.rolling_playlist_repo import PgRollingPlaylistRepo
from jazz_agent.core.models import RollingPlaylist, RollingPlaylistEvent, RollingPlaylistTrack

T0 = datetime(2026, 10, 10, 12, tzinfo=UTC)
T1 = datetime(2026, 10, 11, 12, tzinfo=UTC)
T2 = datetime(2026, 10, 12, 12, tzinfo=UTC)


def _playlist(slot_key: str = "alxjazz", **overrides: object) -> RollingPlaylist:
    fields = {
        "region": "dc",
        "slot_key": slot_key,
        "group_key": "alxjazz",
        "title": "AlxJazz · Next 6 Weeks",
        "is_public": False,
        **overrides,
    }
    return RollingPlaylist(**fields)  # type: ignore[arg-type]


def _artist(db: ConnectionPool, spotify_artist_id: str = "artist1") -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO artists (spotify_artist_id, name, match_method, match_confidence) "
            "VALUES (%s, 'A', 'exact_name', 0.9) ON CONFLICT DO NOTHING",
            (spotify_artist_id,),
        )


def _track(playlist_id: int, track_id: str = "t1", **overrides: object) -> RollingPlaylistTrack:
    fields = {
        "rolling_playlist_id": playlist_id,
        "spotify_track_id": track_id,
        "spotify_album_id": "alb1",
        "spotify_artist_id": "artist1",
        "position": 0,
        "isrc": "USABC1234567",
        **overrides,
    }
    return RollingPlaylistTrack(**fields)  # type: ignore[arg-type]


def test_upsert_is_keyed_on_region_and_slot_and_preserves_the_spotify_link(
    db: ConnectionPool,
) -> None:
    repo = PgRollingPlaylistRepo(db)
    first = repo.upsert_rolling_playlist(_playlist())
    repo.link_spotify_playlist(first, "sp123", "https://open.spotify.com/playlist/sp123")

    second = repo.upsert_rolling_playlist(_playlist(title="Renamed", description="d"))

    assert second == first
    got = repo.get_rolling_playlist("dc", "alxjazz")
    assert got is not None
    assert (got.title, got.description, got.is_public) == ("Renamed", "d", False)
    assert got.spotify_playlist_id == "sp123"
    assert repo.get_rolling_playlist("dc", "nope") is None


def test_rolling_playlist_tracks_rejects_a_second_open_stint(db: ConnectionPool) -> None:
    _artist(db)
    repo = PgRollingPlaylistRepo(db)
    pid = repo.upsert_rolling_playlist(_playlist())
    repo.open_stint(_track(pid))

    with pytest.raises(psycopg.errors.UniqueViolation):
        repo.open_stint(_track(pid))

    other = repo.upsert_rolling_playlist(_playlist("blues-alley", group_key="blues-alley"))
    repo.open_stint(_track(other))  # same track on a different playlist is fine


def test_closing_a_stint_allows_a_new_one_and_keeps_history(db: ConnectionPool) -> None:
    _artist(db)
    repo = PgRollingPlaylistRepo(db)
    pid = repo.upsert_rolling_playlist(_playlist())
    first = repo.open_stint(_track(pid))

    repo.close_stint(first, "rolled_off", T1)
    second = repo.open_stint(_track(pid, position=3))

    assert second != first
    assert [s.id for s in repo.open_stints(pid)] == [second]
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT id, removal_reason FROM rolling_playlist_tracks ORDER BY id"
        ).fetchall()
    assert rows == [(first, "rolled_off"), (second, None)]


def test_close_stint_never_overwrites_an_already_closed_stint(db: ConnectionPool) -> None:
    _artist(db)
    repo = PgRollingPlaylistRepo(db)
    pid = repo.upsert_rolling_playlist(_playlist())
    stint = repo.open_stint(_track(pid))
    repo.close_stint(stint, "user", T1)

    repo.close_stint(stint, "rolled_off", T2)

    with db.connection() as conn:
        row = conn.execute(
            "SELECT removal_reason, removed_at FROM rolling_playlist_tracks WHERE id = %s",
            (stint,),
        ).fetchone()
    assert row == ("user", T1)


def test_removal_reason_and_removed_at_must_be_set_together(db: ConnectionPool) -> None:
    _artist(db)
    repo = PgRollingPlaylistRepo(db)
    pid = repo.upsert_rolling_playlist(_playlist())
    stint = repo.open_stint(_track(pid))

    with pytest.raises(psycopg.errors.CheckViolation), db.connection() as conn:
        conn.execute(
            "UPDATE rolling_playlist_tracks SET removal_reason = 'user' WHERE id = %s", (stint,)
        )
    with pytest.raises(psycopg.errors.CheckViolation), db.connection() as conn:
        conn.execute(
            "UPDATE rolling_playlist_tracks SET removed_at = now(), removal_reason = 'bored' "
            "WHERE id = %s",
            (stint,),
        )


def test_user_removed_track_ids_spans_playlists_and_follows_the_latest_stint(
    db: ConnectionPool,
) -> None:
    _artist(db)
    repo = PgRollingPlaylistRepo(db)
    alx = repo.upsert_rolling_playlist(_playlist())
    other = repo.upsert_rolling_playlist(_playlist("blues-alley", group_key="blues-alley"))

    repo.close_stint(repo.open_stint(_track(alx, "gone")), "user", T1)
    repo.close_stint(repo.open_stint(_track(alx, "rolled")), "rolled_off", T1)
    repo.open_stint(_track(other, "live"))

    assert repo.user_removed_track_ids() == {"gone"}


def test_record_event_attaches_to_the_rolling_playlist(db: ConnectionPool) -> None:
    repo = PgRollingPlaylistRepo(db)
    pid = repo.upsert_rolling_playlist(_playlist())

    repo.record_event(
        RollingPlaylistEvent(pid, "tracks_removed", "artist1", "user", {"track_ids": ["t1"]})
    )

    with db.connection() as conn:
        row = conn.execute(
            "SELECT week_playlist_id, rolling_playlist_id, event_type, detail FROM playlist_events"
        ).fetchone()
    assert row == (None, pid, "tracks_removed", {"track_ids": ["t1"]})
