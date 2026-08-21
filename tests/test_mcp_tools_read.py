"""mcp.tools_read tests against a real Postgres (see conftest.py). No fakes
needed here -- these are plain SELECT queries, tested directly."""

from __future__ import annotations

from datetime import date

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.adapters.pg.feedback_repo import PgFeedbackRepo
from jazz_agent.adapters.pg.graph_repo import PgGraphRepo
from jazz_agent.adapters.pg.show_repo import PgShowRepo
from jazz_agent.core.models import Artist, Feedback, MbArtist, MbArtistEdge, Performer, Show
from jazz_agent.mcp.tools_read import (
    artist_connections,
    artist_profile,
    recent_feedback,
    search_notes,
    search_shows,
    whats_playing_at,
)

CLUB_ID = "village-vanguard"
FRISELL_ID = "3JsHnjpbhX4SnySpvpa9DK"
FRISELL_MBID = "a21318db-f228-4a4d-8bce-6947a62985a5"


def _insert_club(db: ConnectionPool) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, schedule_url) VALUES (%s, %s, %s)",
            (CLUB_ID, "Village Vanguard", "https://example.com"),
        )


def test_search_notes_stemming_pianist_matches_piano(db: ConnectionPool) -> None:
    PgFeedbackRepo(db).record_feedback(
        Feedback(
            target_type="artist",
            target_id=FRISELL_ID,
            sentiment="liked",
            note_text="what a gorgeous solo piano set",
        )
    )

    results = search_notes(db, "pianist")

    assert len(results) == 1
    assert "piano" in results[0]["note_text"]


def test_search_notes_fuzzy_matches_misspelling(db: ConnectionPool) -> None:
    PgFeedbackRepo(db).record_feedback(
        Feedback(
            target_type="artist",
            target_id=FRISELL_ID,
            sentiment="liked",
            note_text="incredible guitarist tonight",
        )
    )

    results = search_notes(db, "guitarest")  # misspelled

    assert len(results) == 1
    assert "guitarist" in results[0]["note_text"]


def test_search_notes_no_match_returns_empty(db: ConnectionPool) -> None:
    assert search_notes(db, "completely unrelated query xyz") == []


def test_artist_connections_labels_musicbrainz_and_co_performance(db: ConnectionPool) -> None:
    _insert_club(db)
    PgArtistRepo(db).upsert_artist(
        Artist(
            spotify_artist_id=FRISELL_ID,
            name="Bill Frisell",
            match_method="llm_adjudicated",
            match_confidence=0.9,
            mbid=FRISELL_MBID,
        )
    )
    graph_repo = PgGraphRepo(db)
    graph_repo.upsert_mb_artist(
        MbArtist(mbid=FRISELL_MBID, name="Bill Frisell", entity_type="Person")
    )
    graph_repo.record_edges(
        [
            MbArtistEdge(
                src_mbid=FRISELL_MBID,
                dst_mbid="c7059057-b57a-401c-90ab-264dbd9742b1",
                dst_name="Paul Motian Quintet",
                edge_type="member of band",
                instruments=("electric guitar",),
            )
        ]
    )

    show_repo = PgShowRepo(db)
    show_id = show_repo.upsert_show(
        Show(
            club_id=CLUB_ID,
            show_date=date(2026, 8, 18),
            act_name_raw="Bill Frisell Trio",
            act_name_norm="Bill Frisell",
        )
    )
    show_repo.record_performers(
        show_id,
        [
            Performer(name="Bill Frisell", instrument="guitar", is_leader=True),
            Performer(name="Rudy Royston", instrument="drums"),
        ],
    )

    connections = artist_connections(db, "Bill Frisell")

    sources = {c["source"] for c in connections}
    assert sources == {"musicbrainz", "co_performance"}
    mb = next(c for c in connections if c["source"] == "musicbrainz")
    assert mb["dst_name"] == "Paul Motian Quintet"
    co = next(c for c in connections if c["source"] == "co_performance")
    assert co["dst_name"] == "Rudy Royston"


def test_artist_connections_unknown_artist_returns_empty(db: ConnectionPool) -> None:
    assert artist_connections(db, "Nobody At All") == []


def test_artist_profile_combines_everything_in_one_call(db: ConnectionPool) -> None:
    _insert_club(db)
    PgArtistRepo(db).upsert_artist(
        Artist(
            spotify_artist_id=FRISELL_ID,
            name="Bill Frisell",
            match_method="llm_adjudicated",
            match_confidence=0.9,
            mbid=FRISELL_MBID,
            genres=("contemporary jazz",),
        )
    )
    graph_repo = PgGraphRepo(db)
    graph_repo.upsert_mb_artist(
        MbArtist(mbid=FRISELL_MBID, name="Bill Frisell", entity_type="Person", tags=("jazz",))
    )
    show_repo = PgShowRepo(db)
    show_id = show_repo.upsert_show(
        Show(
            club_id=CLUB_ID,
            show_date=date(2026, 8, 18),
            act_name_raw="Bill Frisell Trio",
            act_name_norm="Bill Frisell",
        )
    )
    show_repo.record_performers(show_id, [Performer(name="Bill Frisell", instrument="guitar")])
    PgFeedbackRepo(db).record_feedback(
        Feedback(target_type="artist", target_id=FRISELL_ID, sentiment="liked")
    )

    profile = artist_profile(db, "Bill Frisell")

    assert profile is not None
    assert profile["genres"] == ["contemporary jazz"]
    assert profile["mb_tags"] == ["jazz"]
    assert profile["instruments"] == ["guitar"]
    assert len(profile["feedback"]) == 1


def test_artist_profile_unknown_artist_returns_none(db: ConnectionPool) -> None:
    assert artist_profile(db, "Nobody At All") is None


def test_search_shows_by_club_and_date_range(db: ConnectionPool) -> None:
    _insert_club(db)
    show_repo = PgShowRepo(db)
    show_repo.upsert_show(
        Show(
            club_id=CLUB_ID,
            show_date=date(2026, 8, 18),
            act_name_raw="Bill Frisell Trio",
            act_name_norm="Bill Frisell",
        )
    )

    results = search_shows(db, club=CLUB_ID, date_from=date(2026, 8, 1), date_to=date(2026, 8, 31))

    assert len(results) == 1
    assert results[0]["act_name_raw"] == "Bill Frisell Trio"


def test_whats_playing_at_returns_matched_artist(db: ConnectionPool) -> None:
    _insert_club(db)
    PgArtistRepo(db).upsert_artist(
        Artist(
            spotify_artist_id=FRISELL_ID,
            name="Bill Frisell",
            match_method="llm_adjudicated",
            match_confidence=0.9,
        )
    )
    show_repo = PgShowRepo(db)
    show_id = show_repo.upsert_show(
        Show(
            club_id=CLUB_ID,
            show_date=date(2026, 8, 18),
            act_name_raw="Bill Frisell Trio",
            act_name_norm="Bill Frisell",
        )
    )
    PgArtistRepo(db).link_show_artist(show_id, FRISELL_ID)

    results = whats_playing_at(db, CLUB_ID, date(2026, 8, 18))

    assert len(results) == 1
    assert results[0]["matched_artist_name"] == "Bill Frisell"


def test_recent_feedback_filters_by_sentiment(db: ConnectionPool) -> None:
    PgFeedbackRepo(db).record_feedback(
        Feedback(target_type="artist", target_id=FRISELL_ID, sentiment="liked")
    )
    PgFeedbackRepo(db).record_feedback(
        Feedback(target_type="artist", target_id="other", sentiment="disliked")
    )

    liked = recent_feedback(db, sentiment="liked")

    assert len(liked) == 1
    assert liked[0]["sentiment"] == "liked"
