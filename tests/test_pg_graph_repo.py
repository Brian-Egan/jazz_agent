from datetime import UTC, date, datetime

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.graph_repo import PgGraphRepo
from jazz_agent.core.models import MbArtist, MbArtistEdge, MbLookupMiss

FRISELL_MBID = "a21318db-f228-4a4d-8bce-6947a62985a5"
MOTIAN_MBID = "b7c1c541-c807-42b6-a5ef-e04c710f66d9"


def _artist(**overrides: object) -> MbArtist:
    fields: dict[str, object] = {
        "mbid": FRISELL_MBID,
        "name": "Bill Frisell",
        "entity_type": "Person",
        "disambiguation": "American jazz guitarist",
        "tags": ("contemporary jazz", "jazz fusion"),
        "spotify_url": "https://open.spotify.com/artist/3JsHnjpbhX4SnySpvpa9DK",
    }
    fields.update(overrides)
    return MbArtist(**fields)  # type: ignore[arg-type]


def test_upsert_and_get_mb_artist_round_trips(db: ConnectionPool) -> None:
    repo = PgGraphRepo(db)

    repo.upsert_mb_artist(_artist())
    fetched = repo.get_mb_artist(FRISELL_MBID)

    assert fetched == _artist()


def test_upsert_mb_artist_on_conflict_updates_in_place(db: ConnectionPool) -> None:
    repo = PgGraphRepo(db)
    repo.upsert_mb_artist(_artist())

    repo.upsert_mb_artist(_artist(spotify_url=None))

    with db.connection() as conn:
        count = conn.execute("SELECT count(*) FROM mb_artists").fetchone()[0]
    assert count == 1
    assert repo.get_mb_artist(FRISELL_MBID) == _artist(spotify_url=None)


def test_record_and_fetch_edges(db: ConnectionPool) -> None:
    repo = PgGraphRepo(db)
    repo.upsert_mb_artist(_artist())

    edge = MbArtistEdge(
        src_mbid=FRISELL_MBID,
        dst_mbid=MOTIAN_MBID,
        dst_name="Paul Motian Trio",
        edge_type="member of band",
        instruments=("electric guitar",),
        begin_date=date(1990, 1, 1),
        end_date=date(2011, 11, 22),
    )
    repo.record_edges([edge])

    edges = repo.edges_for(FRISELL_MBID)

    assert edges == [edge]


def test_record_lookup_miss_round_trips(db: ConnectionPool) -> None:
    repo = PgGraphRepo(db)
    miss_until = datetime(2026, 6, 1, tzinfo=UTC)

    repo.record_lookup_miss(
        MbLookupMiss(
            name_norm="some unknown act",
            miss_until=miss_until,
            attempts=1,
            last_error="503 Service Temporarily Unavailable",
        )
    )

    fetched = repo.get_lookup_miss("some unknown act")

    assert fetched == MbLookupMiss(
        name_norm="some unknown act",
        miss_until=miss_until,
        attempts=1,
        last_error="503 Service Temporarily Unavailable",
    )


def test_get_lookup_miss_missing_returns_none(db: ConnectionPool) -> None:
    repo = PgGraphRepo(db)

    assert repo.get_lookup_miss("never looked up") is None
