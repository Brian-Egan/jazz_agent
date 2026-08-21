from datetime import date

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.adapters.pg.show_repo import PgShowRepo
from jazz_agent.core.models import Artist, Show


def _insert_club(db: ConnectionPool) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, schedule_url) VALUES (%s, %s, %s)",
            ("village-vanguard", "Village Vanguard", "https://villagevanguard.com"),
        )


def _artist(**overrides: object) -> Artist:
    fields: dict[str, object] = {
        "spotify_artist_id": "3JsHnjpbhX4SnySpvpa9DK",
        "name": "Bill Frisell",
        "match_method": "llm_adjudicated",
        "match_confidence": 0.95,
        "genres": ("contemporary jazz", "jazz fusion"),
        "popularity": 42,
        "followers": 123456,
        "mbid": "a21318db-f228-4a4d-8bce-6947a62985a5",
        "plausibility_score": 0.9,
        "needs_review": False,
        "verification_state": "verified",
        "match_notes": "exact name, genre-consistent",
    }
    fields.update(overrides)
    return Artist(**fields)  # type: ignore[arg-type]


def test_upsert_artist_round_trips(db: ConnectionPool) -> None:
    repo = PgArtistRepo(db)

    repo.upsert_artist(_artist())
    fetched = repo.get_artist("3JsHnjpbhX4SnySpvpa9DK")

    assert fetched == _artist()


def test_upsert_artist_on_conflict_updates_in_place(db: ConnectionPool) -> None:
    repo = PgArtistRepo(db)
    repo.upsert_artist(_artist())

    repo.upsert_artist(_artist(needs_review=True, verification_state="disputed"))

    with db.connection() as conn:
        count = conn.execute("SELECT count(*) FROM artists").fetchone()[0]
    fetched = repo.get_artist("3JsHnjpbhX4SnySpvpa9DK")

    assert count == 1
    assert fetched is not None
    assert fetched.needs_review is True
    assert fetched.verification_state == "disputed"


def test_get_artist_missing_returns_none(db: ConnectionPool) -> None:
    repo = PgArtistRepo(db)

    assert repo.get_artist("no-such-artist") is None


def test_link_show_artist_and_artists_for_show(db: ConnectionPool) -> None:
    _insert_club(db)
    show_repo = PgShowRepo(db)
    artist_repo = PgArtistRepo(db)
    show_id = show_repo.upsert_show(
        Show(
            club_id="village-vanguard",
            show_date=date(2026, 5, 5),
            act_name_raw="The Bill Frisell Four",
            act_name_norm="Bill Frisell",
        )
    )
    artist_repo.upsert_artist(_artist())

    artist_repo.link_show_artist(show_id, "3JsHnjpbhX4SnySpvpa9DK")
    # Idempotent: linking the same pair twice must not duplicate or error.
    artist_repo.link_show_artist(show_id, "3JsHnjpbhX4SnySpvpa9DK")

    artists = artist_repo.artists_for_show(show_id)

    assert artists == [_artist()]
