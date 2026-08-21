"""pipeline.prune tests against a real, freshly-migrated Postgres -- retention
must genuinely never delete a row, only stamp spotify_removed_at, and the DB
is the only reliable witness to that. MusicService is a fake; no live Spotify."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.playlist_repo import PgPlaylistRepo
from jazz_agent.core.models import WeekPlaylist
from jazz_agent.pipeline.prune import prune_club

CLUB_ID = "village-vanguard"
TODAY = date(2026, 8, 18)  # a Tuesday; also this week's week_start_date


class FakeMusicService:
    def __init__(self) -> None:
        self.unfollow_calls: list[str] = []

    def unfollow_playlist(self, spotify_playlist_id: str) -> None:
        self.unfollow_calls.append(spotify_playlist_id)

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

    def add_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        raise NotImplementedError

    def remove_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        raise NotImplementedError

    def currently_playing(self) -> dict[str, Any] | None:
        raise NotImplementedError

    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]:
        raise NotImplementedError


def _insert_club(db: ConnectionPool) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, schedule_url) VALUES (%s, %s, %s)",
            (CLUB_ID, "Village Vanguard", "https://example.com"),
        )


def _seed_playlist(repo: PgPlaylistRepo, week_offset: int) -> int:
    week_start = TODAY + timedelta(weeks=week_offset)
    playlist_id = repo.upsert_week_playlist(
        WeekPlaylist(
            club_id=CLUB_ID,
            week_start_date=week_start,
            title=f"Week {week_offset}",
            description="",
        )
    )
    repo.link_spotify_playlist(
        playlist_id,
        f"spotify-{week_offset}",
        f"https://open.spotify.com/playlist/spotify-{week_offset}",
    )
    return playlist_id


def test_prune_keeps_exactly_retain_past_through_horizon_ahead(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgPlaylistRepo(db)
    offsets = [-3, -1, 0, 2, 4, 6]
    ids = {offset: _seed_playlist(repo, offset) for offset in offsets}
    music = FakeMusicService()
    now = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)

    pruned_count = prune_club(CLUB_ID, TODAY, 1, 4, music, repo, now)

    assert pruned_count == 2

    all_playlists = repo.playlists_for_club(CLUB_ID)
    followed = {
        offset
        for offset, pid in ids.items()
        for p in all_playlists
        if p.id == pid and p.spotify_removed_at is None
    }
    assert followed == {-1, 0, 2, 4}

    removed = {
        offset
        for offset, pid in ids.items()
        for p in all_playlists
        if p.id == pid and p.spotify_removed_at is not None
    }
    assert removed == {-3, 6}


def test_no_database_row_is_ever_deleted_by_pruning(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgPlaylistRepo(db)
    for offset in [-3, -1, 0, 2, 4, 6]:
        _seed_playlist(repo, offset)
    music = FakeMusicService()
    now = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)

    with db.connection() as conn:
        before = conn.execute(
            "SELECT count(*) FROM week_playlists WHERE club_id = %s", (CLUB_ID,)
        ).fetchone()[0]

    prune_club(CLUB_ID, TODAY, 1, 4, music, repo, now)

    with db.connection() as conn:
        after = conn.execute(
            "SELECT count(*) FROM week_playlists WHERE club_id = %s", (CLUB_ID,)
        ).fetchone()[0]

    assert before == after == 6


def test_pruned_playlists_get_unfollowed_on_spotify_and_an_event_recorded(
    db: ConnectionPool,
) -> None:
    _insert_club(db)
    repo = PgPlaylistRepo(db)
    old_id = _seed_playlist(repo, -3)
    _seed_playlist(repo, 0)  # stays followed
    music = FakeMusicService()
    now = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)

    prune_club(CLUB_ID, TODAY, 1, 4, music, repo, now)

    assert music.unfollow_calls == ["spotify--3"]
    events = repo.events_for(old_id)
    assert [e.event_type for e in events] == ["unfollowed"]


def test_pruning_an_already_unfollowed_playlist_is_a_no_op(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgPlaylistRepo(db)
    old_id = _seed_playlist(repo, -3)
    music = FakeMusicService()
    now = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)

    first_pruned = prune_club(CLUB_ID, TODAY, 1, 4, music, repo, now)
    second_pruned = prune_club(CLUB_ID, TODAY, 1, 4, music, repo, now)

    assert first_pruned == 1
    assert second_pruned == 0  # no error, and nothing re-unfollowed
    assert music.unfollow_calls == ["spotify--3"]  # not called twice
    assert len(repo.events_for(old_id)) == 1  # not duplicated
