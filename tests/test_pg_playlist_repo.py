from datetime import UTC, date, datetime

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.adapters.pg.playlist_repo import PgPlaylistRepo
from jazz_agent.core.models import Artist, PlaylistEvent, PlaylistTrack, WeekPlaylist

CLUB_ID = "village-vanguard"
WEEK_START = date(2026, 5, 5)


def _insert_club(db: ConnectionPool) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, schedule_url) VALUES (%s, %s, %s)",
            (CLUB_ID, "Village Vanguard", "https://villagevanguard.com"),
        )


def _insert_artist(db: ConnectionPool, spotify_artist_id: str = "artist1") -> None:
    PgArtistRepo(db).upsert_artist(
        Artist(
            spotify_artist_id=spotify_artist_id,
            name="Bill Frisell",
            match_method="llm_adjudicated",
            match_confidence=0.95,
        )
    )


def test_upsert_week_playlist_round_trips(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgPlaylistRepo(db)

    playlist_id = repo.upsert_week_playlist(
        WeekPlaylist(
            club_id=CLUB_ID,
            week_start_date=WEEK_START,
            title="This Week at Village Vanguard - May 5-11",
            description="Bill Frisell Four",
        )
    )
    fetched = repo.get_week_playlist(CLUB_ID, WEEK_START)

    assert fetched is not None
    assert fetched.id == playlist_id
    assert fetched.title == "This Week at Village Vanguard - May 5-11"
    assert fetched.spotify_playlist_id is None


def test_upsert_week_playlist_on_conflict_updates_title_not_duplicate(
    db: ConnectionPool,
) -> None:
    _insert_club(db)
    repo = PgPlaylistRepo(db)
    first_id = repo.upsert_week_playlist(
        WeekPlaylist(club_id=CLUB_ID, week_start_date=WEEK_START, title="v1", description="")
    )

    second_id = repo.upsert_week_playlist(
        WeekPlaylist(club_id=CLUB_ID, week_start_date=WEEK_START, title="v2", description="")
    )

    with db.connection() as conn:
        count = conn.execute("SELECT count(*) FROM week_playlists").fetchone()[0]
    assert second_id == first_id
    assert count == 1
    assert repo.get_week_playlist(CLUB_ID, WEEK_START).title == "v2"  # type: ignore[union-attr]


def test_link_spotify_playlist(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgPlaylistRepo(db)
    playlist_id = repo.upsert_week_playlist(
        WeekPlaylist(club_id=CLUB_ID, week_start_date=WEEK_START, title="t", description="")
    )

    repo.link_spotify_playlist(
        playlist_id, "spotify:playlist:abc", "https://open.spotify.com/playlist/abc"
    )

    fetched = repo.get_week_playlist(CLUB_ID, WEEK_START)
    assert fetched is not None
    assert fetched.spotify_playlist_id == "spotify:playlist:abc"
    assert fetched.spotify_url == "https://open.spotify.com/playlist/abc"


def test_add_and_remove_tracks(db: ConnectionPool) -> None:
    _insert_club(db)
    _insert_artist(db)
    repo = PgPlaylistRepo(db)
    playlist_id = repo.upsert_week_playlist(
        WeekPlaylist(club_id=CLUB_ID, week_start_date=WEEK_START, title="t", description="")
    )
    track = PlaylistTrack(
        week_playlist_id=playlist_id,
        spotify_track_id="track1",
        spotify_album_id="album1",
        spotify_artist_id="artist1",
        position=1,
        track_name="Motian's Traffic",
    )

    repo.add_tracks(playlist_id, [track])
    tracks = repo.tracks_for(playlist_id)
    assert tracks == [track]

    repo.remove_track(playlist_id, "track1")
    removed = repo.tracks_for(playlist_id)
    assert removed[0].removed_at is not None


def test_record_and_fetch_events(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgPlaylistRepo(db)
    playlist_id = repo.upsert_week_playlist(
        WeekPlaylist(club_id=CLUB_ID, week_start_date=WEEK_START, title="t", description="")
    )
    event = PlaylistEvent(
        week_playlist_id=playlist_id,
        event_type="tracks_added",
        spotify_artist_id="artist1",
        reason="new booking",
        detail={"track_count": 12},
    )

    repo.record_event(event)

    assert repo.events_for(playlist_id) == [event]


def test_mark_removed(db: ConnectionPool) -> None:
    _insert_club(db)
    repo = PgPlaylistRepo(db)
    playlist_id = repo.upsert_week_playlist(
        WeekPlaylist(club_id=CLUB_ID, week_start_date=WEEK_START, title="t", description="")
    )
    removed_at = datetime(2026, 6, 1, tzinfo=UTC)

    repo.mark_removed(playlist_id, removed_at)

    fetched = repo.get_week_playlist(CLUB_ID, WEEK_START)
    assert fetched is not None
    assert fetched.spotify_removed_at == removed_at
