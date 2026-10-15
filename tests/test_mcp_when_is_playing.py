"""mcp/tools_read.when_is_playing against a real Postgres (issue #31)."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.adapters.pg.capitalbop_repo import PgCapitalbopRepo
from jazz_agent.adapters.pg.rolling_playlist_repo import PgRollingPlaylistRepo
from jazz_agent.adapters.pg.show_repo import PgShowRepo
from jazz_agent.adapters.pg.venue_repo import PgVenueRepo
from jazz_agent.core.models import (
    Artist,
    CapitalbopEvent,
    Performer,
    RollingPlaylist,
    RollingPlaylistTrack,
    Show,
)
from jazz_agent.core.normalize import normalize_act_name
from jazz_agent.mcp.tools_read import when_is_playing

TODAY = date(2026, 10, 11)
FETCH = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)


def add_event(
    db: ConnectionPool,
    instance_id: int,
    name: str,
    day: date,
    seen: datetime = FETCH,
    **overrides: object,
) -> int:
    PgVenueRepo(db).upsert_capitalbop_venue("rhizome", 1, "Rhizome")
    show_id = PgShowRepo(db).upsert_show(
        Show("cb-rhizome", day, name, normalize_act_name(name), raw_text=name)
    )
    fields: dict[str, object] = {
        "instance_id": instance_id,
        "event_id": instance_id,
        "show_id": show_id,
        "start_time": time(20, 0),
        "price_text": "$25",
        "ticket_link": "https://tickets.example/x",
        "venue_address": "1 Main St NW, Washington, DC",
        **overrides,
    }
    PgCapitalbopRepo(db).upsert_event(CapitalbopEvent(**fields), seen_at=seen)  # type: ignore[arg-type]
    return show_id


def test_sharon_clark_finds_sharon_clark_trio_regardless_of_accents(db: ConnectionPool) -> None:
    add_event(db, 1, "Sharón Clark Trio", date(2026, 10, 20))

    found = when_is_playing(db, "Sharon Clark", today=TODAY)

    assert [r["act"] for r in found] == ["Sharón Clark Trio"]
    assert when_is_playing(db, "sharón clark", today=TODAY) == found


def test_each_row_carries_time_venue_address_price_ticket_link_and_article(
    db: ConnectionPool,
) -> None:
    add_event(
        db,
        1,
        "Sharon Clark Trio",
        date(2026, 10, 20),
        article_title="A profile",
        article_url="https://capitalbop.com/a-profile",
    )

    row = when_is_playing(db, "Sharon Clark", today=TODAY)[0]

    assert row["date"] == date(2026, 10, 20) and row["start_time"] == "20:00"
    assert row["venue"] == "Rhizome" and row["address"] == "1 Main St NW, Washington, DC"
    assert row["price"] == "$25" and row["ticket_link"] == "https://tickets.example/x"
    assert row["article"] == {"title": "A profile", "url": "https://capitalbop.com/a-profile"}
    assert row["playlists"] == []


def test_past_and_cancelled_shows_never_appear(db: ConnectionPool) -> None:
    add_event(db, 1, "Sharon Clark Trio", date(2026, 10, 9))  # already happened
    add_event(db, 2, "Sharon Clark Trio", date(2026, 10, 18), seen=FETCH - timedelta(days=1))
    add_event(db, 3, "Sharon Clark Trio", date(2026, 10, 25))  # seen in the latest fetch

    found = when_is_playing(db, "Sharon Clark", today=TODAY)

    assert [r["date"] for r in found] == [date(2026, 10, 25)]


def test_results_are_soonest_first(db: ConnectionPool) -> None:
    add_event(db, 1, "Sharon Clark Trio", date(2026, 11, 2))
    add_event(db, 2, "Sharon Clark Quartet", date(2026, 10, 14))

    assert [r["date"] for r in when_is_playing(db, "Sharon Clark", today=TODAY)] == [
        date(2026, 10, 14),
        date(2026, 11, 2),
    ]


def test_a_named_performer_finds_the_show_even_when_the_act_name_differs(
    db: ConnectionPool,
) -> None:
    show_id = add_event(db, 1, "Late Night Quintet", date(2026, 10, 20))
    PgShowRepo(db).record_performers(show_id, [Performer("Sharón Clark", "vocals")])

    found = when_is_playing(db, "Sharon Clark", today=TODAY)

    assert [r["act"] for r in found] == ["Late Night Quintet"]


def test_unrelated_names_do_not_match(db: ConnectionPool) -> None:
    add_event(db, 1, "Sharon Clark Trio", date(2026, 10, 20))

    assert when_is_playing(db, "Kamasi Washington", today=TODAY) == []


def test_every_stored_show_is_answerable_whether_or_not_it_made_a_playlist(
    db: ConnectionPool,
) -> None:
    add_event(
        db, 1, "Free Afternoon Jam", date(2026, 10, 20), price_text="Free", start_time=time(14, 0)
    )

    found = when_is_playing(db, "Afternoon Jam", today=TODAY)

    assert found[0]["playlists"] == [] and found[0]["start_time"] == "14:00"


def test_the_row_names_the_rolling_playlist_the_act_is_currently_in(db: ConnectionPool) -> None:
    show_id = add_event(db, 1, "Maria Grand", date(2026, 10, 20))
    PgArtistRepo(db).upsert_artist(
        Artist("a1", "Maria Grand", match_method="llm_adjudicated", match_confidence=0.9)
    )
    PgArtistRepo(db).link_show_artist(show_id, "a1")
    repo = PgRollingPlaylistRepo(db)
    playlist_id = repo.upsert_rolling_playlist(
        RollingPlaylist("dc", "next-6-weeks", "dc", "DC Jazz · Next 6 Weeks", True)
    )
    repo.link_spotify_playlist(playlist_id, "sp1", "https://open.spotify.com/playlist/sp1")
    stint_id = repo.open_stint(RollingPlaylistTrack(playlist_id, "t1", "alb", "a1", 0))

    row = when_is_playing(db, "Maria Grand", today=TODAY)[0]
    assert row["playlists"] == [
        {
            "slot_key": "next-6-weeks",
            "title": "DC Jazz · Next 6 Weeks",
            "url": "https://open.spotify.com/playlist/sp1",
        }
    ]

    repo.close_stint(stint_id, "rolled_off", FETCH)  # no longer on the list
    assert when_is_playing(db, "Maria Grand", today=TODAY)[0]["playlists"] == []


def test_region_filters_split_dc_from_nyc_shows(db: ConnectionPool) -> None:
    add_event(db, 1, "Sharon Clark Trio", date(2026, 10, 20))
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, schedule_url) VALUES ('smalls', 'Smalls', 'https://x')"
        )
    PgShowRepo(db).upsert_show(
        Show("smalls", date(2026, 10, 21), "Sharon Clark Group", "Sharon Clark Group", ("9:00 PM",))
    )

    both = when_is_playing(db, "Sharon Clark", today=TODAY)
    dc_only = when_is_playing(db, "Sharon Clark", region="dc", today=TODAY)
    nyc_only = when_is_playing(db, "Sharon Clark", region="nyc", today=TODAY)

    assert [r["venue"] for r in both] == ["Rhizome", "Smalls"]
    assert [r["venue"] for r in dc_only] == ["Rhizome"]
    assert [r["venue"] for r in nyc_only] == ["Smalls"]
    assert nyc_only[0]["set_times"] == ["9:00 PM"]


def test_an_unknown_region_is_an_error(db: ConnectionPool) -> None:
    with pytest.raises(ValueError, match="region must be one of"):
        when_is_playing(db, "x", region="paris")
