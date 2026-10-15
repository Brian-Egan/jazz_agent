"""The DC run end to end against a real Postgres: mirror, eligibility, extraction, matching,
selection, reconcile (issue #28). Spotify, Haiku, MusicBrainz and CapitalBop are fakes.

The idempotency tests here are the most important in the DC suite (AGENTS.md).
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.adapters.pg.capitalbop_repo import PgCapitalbopRepo
from jazz_agent.adapters.pg.club_repo import PgClubRepo
from jazz_agent.adapters.pg.feedback_repo import PgFeedbackRepo
from jazz_agent.adapters.pg.playlist_repo import PgPlaylistRepo
from jazz_agent.adapters.pg.rolling_playlist_repo import PgRollingPlaylistRepo
from jazz_agent.adapters.pg.run_lock import PgRunLock
from jazz_agent.adapters.pg.run_repo import PgRunRepo
from jazz_agent.adapters.pg.show_repo import PgShowRepo
from jazz_agent.adapters.pg.venue_group_repo import PgVenueGroupRepo
from jazz_agent.adapters.pg.venue_repo import PgVenueRepo
from jazz_agent.core.models import (
    CalendarEvent,
    CalendarVenue,
    Feedback,
    PersonnelExtraction,
)
from jazz_agent.pipeline.daily import Dependencies, run_daily
from jazz_agent.pipeline.dc import DcDependencies, DcRunResult, run_dc
from jazz_agent.ports.calendar import CalendarFetchError

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
TODAY = date(2026, 10, 11)  # a Sunday: slot 1 = 10-05..10-18, slot 2 = 10-19..11-01
RUN1 = datetime(2026, 10, 11, 17, 0, tzinfo=UTC)
RUN2 = RUN1 + timedelta(days=1)

BLUES_ALLEY = CalendarVenue(1, "blues-alley", "Blues Alley")
RHIZOME = CalendarVenue(2, "rhizome", "Rhizome")
LAPORTAS = CalendarVenue(3, "laportas", "LaPortas")
BIRCHMERE = CalendarVenue(4, "the-birchmere", "The Birchmere")


def _seed(db: ConnectionPool) -> None:
    url = os.environ.get(
        "TEST_DATABASE_URL", "postgresql://jazz_agent:CHANGEME@localhost:5432/jazz_agent_test"
    )
    subprocess.run(
        ["psql", url, "-v", "ON_ERROR_STOP=1", "-f", str(MIGRATIONS / "seed_dc.sql")],
        check=True,
        capture_output=True,
        text=True,
    )


def ev(
    instance_id: int,
    event_id: int,
    name: str,
    day: date,
    venue: CalendarVenue = RHIZOME,
    hour: int = 20,
    pick: bool = True,
) -> CalendarEvent:
    return CalendarEvent(
        instance_id=instance_id,
        event_id=event_id,
        name=name,
        start_date=day,
        start_time=time(hour, 0),
        venue=venue,
        geocoded_city="Washington",
        cb_pick=pick,
        price="$20",
        ticket_link="https://example.com/t",
        description_html="<p>Live jazz.</p>",
    )


class StubCalendar:
    def __init__(self, events: list[CalendarEvent]) -> None:
        self.event_list = events
        self.fail = False

    def venues(self) -> list[CalendarVenue]:
        return [BLUES_ALLEY, RHIZOME, LAPORTAS, BIRCHMERE]

    def events(self, start: date, end: date) -> list[CalendarEvent]:
        if self.fail:
            raise CalendarFetchError("/events page 2: HTTP 500")
        return list(self.event_list)

    def article(self, instance_id: int) -> None:
        return None


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


class FakeSpotify:
    """A small stateful Spotify: a catalog, playlists, and counters of every write."""

    def __init__(self) -> None:
        self.playlists: dict[str, dict[str, Any]] = {}
        self.created: list[tuple[str, bool]] = []
        self.replaced: list[tuple[str, list[str]]] = []
        self.detail_updates: list[tuple[str, str, str]] = []
        self.unfollowed: list[str] = []
        self.fail_replace_once = False
        self.fail_create_titled: str | None = None
        self.last_artist_query = ""
        self.isrc_overrides: dict[str, str] = {}
        self.track_searches: list[str] = []

    @property
    def writes(self) -> int:
        return len(self.created) + len(self.replaced) + len(self.detail_updates)

    # --- catalogue ---
    def search_artists(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        self.last_artist_query = query
        return [
            {
                "id": f"id-{_slug(query)}",
                "name": query,
                "genres": ["jazz"],
                "popularity": 30,
                "followers": {"total": 5000},
            }
        ]

    def search_tracks(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        self.track_searches.append(query)
        name = query.removeprefix('artist:"').removesuffix('"')
        artist_id = f"id-{_slug(name)}"
        return [
            {
                "id": f"{artist_id}-t{n}",
                "name": f"{name} Song {n}",
                "album": {"id": f"{artist_id}-alb{n % 2}"},
                "artists": [{"id": artist_id}],
                "external_ids": {"isrc": f"ISRC-{artist_id}-{n}"},
            }
            for n in (1, 2, 3)
        ]

    def get_artist_albums(self, spotify_artist_id: str) -> list[dict[str, Any]]:
        return []

    def get_album_tracks(self, spotify_album_id: str) -> list[dict[str, Any]]:
        return []

    # --- playlists ---
    def create_playlist(self, title: str, description: str, public: bool = False) -> dict[str, Any]:
        if self.fail_create_titled == title:
            raise RuntimeError("Spotify 500 creating playlist")
        playlist_id = f"pl-{len(self.playlists) + 1}"
        self.playlists[playlist_id] = {"title": title, "description": description, "items": []}
        self.created.append((title, public))
        return {"id": playlist_id, "external_urls": {"spotify": f"https://sp/{playlist_id}"}}

    def get_playlist(self, spotify_playlist_id: str) -> dict[str, Any] | None:
        return self.playlists.get(spotify_playlist_id)

    def get_playlist_items(self, spotify_playlist_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": t,
                "isrc": self.isrc_overrides.get(t, f"ISRC-{t.replace('-t', '-')}"),
                "name": t,
            }
            for t in self.playlists[spotify_playlist_id]["items"]
        ]

    def replace_playlist_items(
        self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]
    ) -> None:
        if self.fail_replace_once:
            self.fail_replace_once = False
            raise RuntimeError("Spotify 503")
        self.playlists[spotify_playlist_id]["items"] = list(spotify_track_ids)
        self.replaced.append((spotify_playlist_id, list(spotify_track_ids)))

    def update_playlist_details(
        self, spotify_playlist_id: str, title: str, description: str
    ) -> None:
        self.playlists[spotify_playlist_id].update(title=title, description=description)
        self.detail_updates.append((spotify_playlist_id, title, description))

    def unfollow_playlist(self, spotify_playlist_id: str) -> None:
        self.unfollowed.append(spotify_playlist_id)

    def add_tracks(self, *_: Any) -> None:
        raise AssertionError("DC never uses add_tracks")

    def remove_tracks(self, *_: Any) -> None:
        raise AssertionError("DC never uses remove_tracks")

    def currently_playing(self) -> None:
        return None

    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]:
        return []


@dataclass
class _ToolUse:
    input: dict[str, Any]
    name: str = "adjudicate_match"
    type: str = "tool_use"


@dataclass
class _Message:
    content: list[_ToolUse]


class FakeAdjudicator:
    def __init__(self, music: FakeSpotify) -> None:
        self.messages = self
        self._music = music

    def create(self, **_: Any) -> _Message:
        name = self._music.last_artist_query
        return _Message(
            [_ToolUse({"artist_id": f"id-{_slug(name)}", "confidence": 0.95, "reasoning": "stub"})]
        )


class FakeExtractor:
    def __init__(self) -> None:
        self.calls = 0

    def extract_personnel(self, act_name: str, description: str) -> PersonnelExtraction:
        self.calls += 1
        return PersonnelExtraction(leaders=(act_name,))

    def extract(self, *_: Any, **__: Any) -> list[Any]:
        raise NotImplementedError


class FakeGraph:
    def resolve_mbid(self, name: str) -> None:
        return None

    def spotify_url_for(self, mbid: str) -> None:
        return None

    def edges_for(self, mbid: str) -> list[Any]:
        return []


@dataclass
class World:
    db: ConnectionPool
    calendar: StubCalendar
    music: FakeSpotify = field(default_factory=FakeSpotify)
    extractor: FakeExtractor = field(default_factory=FakeExtractor)
    notifier: _Notifier = field(default_factory=lambda: _Notifier())

    def dc_deps(self, **overrides: Any) -> DcDependencies:
        return DcDependencies(
            calendar=self.calendar,  # type: ignore[arg-type]
            venue_repo=PgVenueRepo(self.db),
            capitalbop_repo=PgCapitalbopRepo(self.db),
            rolling_repo=PgRollingPlaylistRepo(self.db),
            group_repo=PgVenueGroupRepo(self.db),
            feedback_repo=PgFeedbackRepo(self.db),
            novelty_liked_days=182,
            **overrides,
        )

    def run(self, now: datetime = RUN1, today: date = TODAY) -> DcRunResult:
        return run_dc(
            self.dc_deps(),
            PgShowRepo(self.db),
            PgArtistRepo(self.db),
            self.extractor,  # type: ignore[arg-type]
            FakeGraph(),  # type: ignore[arg-type]
            FakeAdjudicator(self.music),  # type: ignore[arg-type]
            "claude-haiku-4-5",
            "claude-haiku-4-5",
            self.music,  # type: ignore[arg-type]
            PgRollingPlaylistRepo(self.db),
            self.notifier,
            today,
            now,
        )

    def daily_deps(self) -> Dependencies:
        return Dependencies(
            club_repo=PgClubRepo(self.db),
            show_repo=PgShowRepo(self.db),
            artist_repo=PgArtistRepo(self.db),
            playlist_repo=PgPlaylistRepo(self.db),
            run_repo=PgRunRepo(self.db),
            http_fetcher=None,  # type: ignore[arg-type]
            pw_fetcher=None,  # type: ignore[arg-type]
            extractor=self.extractor,  # type: ignore[arg-type]
            music=self.music,  # type: ignore[arg-type]
            graph=FakeGraph(),  # type: ignore[arg-type]
            notifier=self.notifier,
            llm_client=FakeAdjudicator(self.music),  # type: ignore[arg-type]
            extraction_model="claude-haiku-4-5",
            adjudication_model="claude-haiku-4-5",
            dc=self.dc_deps(),
            run_lock=PgRunLock(self.db),
        )


class _Notifier:
    def __init__(self) -> None:
        self.alerts: list[str] = []
        self.pushes: list[tuple[str, str | None, tuple[str, ...]]] = []
        self.raise_error = False

    def alert(self, msg: str, title: str | None = None, tags: Sequence[str] = ()) -> None:
        if self.raise_error:
            raise RuntimeError("ntfy is down")
        self.alerts.append(msg)
        self.pushes.append((msg, title, tuple(tags)))


def standard_feed() -> list[CalendarEvent]:
    return [
        ev(1, 101, "Maria Grand", date(2026, 10, 16)),  # dc, slot 1
        ev(2, 102, "Aaron Myers", date(2026, 10, 23)),  # dc, slot 2
        ev(3, 103, "Blues Singer", date(2026, 10, 14), venue=BLUES_ALLEY, pick=False),
        ev(4, 104, "Ignored Jam", date(2026, 10, 15), pick=False),  # not a pick, not a member
    ]


@pytest.fixture
def world(db: ConnectionPool) -> World:
    _seed(db)
    return World(db, StubCalendar(standard_feed()))


def snapshot(db: ConnectionPool) -> dict[str, list[tuple[Any, ...]]]:
    queries = {
        "playlists": "SELECT id, region, slot_key, group_key, spotify_playlist_id, spotify_url, "
        "title, description, is_public, sync_pending FROM rolling_playlists ORDER BY id",
        "stints": "SELECT * FROM rolling_playlist_tracks ORDER BY id",
        "events": "SELECT rolling_playlist_id, event_type, spotify_artist_id, reason, detail "
        "FROM playlist_events ORDER BY id",
    }
    with db.connection() as conn:
        return {k: conn.execute(q).fetchall() for k, q in queries.items()}


def held(world: World, slot_key: str) -> list[str]:
    with world.db.connection() as conn:
        rows = conn.execute(
            "SELECT t.spotify_track_id FROM rolling_playlist_tracks t "
            "JOIN rolling_playlists p ON p.id = t.rolling_playlist_id "
            "WHERE p.slot_key = %s AND t.removed_at IS NULL ORDER BY t.position",
            (slot_key,),
        ).fetchall()
    return [r[0] for r in rows]


def test_first_run_builds_all_three_playlists_with_two_tracks_per_artist(world: World) -> None:
    result = world.run()

    assert result.outcome == "success"
    assert sorted(t for t, _ in world.music.created) == sorted(
        [
            "DC Jazz · Next 6 Weeks",
            "Blues Alley · Next 6 Weeks",
            "AlxJazz · Next 6 Weeks",
        ]
    )
    # Public per core/dc_groups.py: AlxJazz is the one private list.
    assert dict(world.music.created)["AlxJazz · Next 6 Weeks"] is False
    assert dict(world.music.created)["Blues Alley · Next 6 Weeks"] is True
    # One six-week dc playlist: Maria Grand (10/16) then Aaron Myers (10/23), two tracks each.
    assert [t.split("-t")[0] for t in held(world, "next-6-weeks")] == [
        "id-maria-grand",
        "id-maria-grand",
        "id-aaron-myers",
        "id-aaron-myers",
    ]
    assert len(held(world, "blues-alley")) == 2
    assert "Ignored Jam" not in str(world.music.track_searches)


def test_description_lists_artists_with_day_and_venue(world: World) -> None:
    world.run()

    with world.db.connection() as conn:
        description = conn.execute(
            "SELECT description FROM rolling_playlists WHERE slot_key = 'next-6-weeks'"
        ).fetchone()
    assert description is not None
    assert description[0].startswith("Maria Grand (Fri 10/16, Rhizome) · Aaron Myers (")
    assert len(description[0]) <= 300


def test_idempotency_two_runs_leave_the_database_identical_and_issue_no_second_write(
    world: World,
) -> None:
    first = world.run(RUN1)
    after_first = snapshot(world.db)
    writes_after_first = world.music.writes
    haiku_after_first = world.extractor.calls

    second = world.run(RUN2)

    assert (first.outcome, second.outcome) == ("success", "success")
    assert snapshot(world.db) == after_first  # rolling_playlists, stints and events identical
    assert world.music.writes == writes_after_first  # no second create, replace or details PUT
    assert world.extractor.calls == haiku_after_first
    assert len(world.music.created) == 3  # never a second playlist on the same key


def test_a_new_show_changes_only_the_affected_playlist(world: World) -> None:
    world.run(RUN1)
    replaced_before = len(world.music.replaced)
    world.calendar.event_list.append(ev(5, 105, "Late Addition", date(2026, 10, 13)))

    world.run(RUN2)

    assert len(world.music.replaced) == replaced_before + 1
    assert len(held(world, "next-6-weeks")) == 6
    # The new act sorts first (10/13 is before 10/16): order follows earliest show.
    assert held(world, "next-6-weeks")[0].startswith("id-late-addition")


def test_an_artist_whose_show_date_moves_within_the_window_keeps_their_stint(
    world: World,
) -> None:
    world.run(RUN1)
    before = held(world, "next-6-weeks")
    replaced_before = len(world.music.replaced)
    # The 10/23 date is replaced by a 10/14 one for the same series: with one six-week playlist
    # it is the same act on the same list, so nothing moves and nothing is rewritten.
    world.calendar.event_list[1] = ev(22, 102, "Aaron Myers", date(2026, 10, 14))

    world.run(RUN2)

    assert sorted(held(world, "next-6-weeks")) == sorted(before)
    with world.db.connection() as conn:
        closed = conn.execute(
            "SELECT count(*) FROM rolling_playlist_tracks WHERE removed_at IS NOT NULL"
        ).fetchone()
    assert closed is not None and closed[0] == 0
    assert replaced_before >= 1


def test_rolled_off_and_ineligible_reasons(world: World) -> None:
    world.run(RUN1)
    # Maria Grand's only date passes; Blues Singer is cancelled (absent from the feed) but its
    # date is still ahead.
    world.calendar.event_list = [
        e for e in world.calendar.event_list if e.event_id not in (101, 103)
    ]

    world.run(RUN2, today=date(2026, 10, 17))

    with world.db.connection() as conn:
        reasons = dict(
            conn.execute(
                "SELECT DISTINCT spotify_artist_id, removal_reason FROM rolling_playlist_tracks "
                "WHERE removed_at IS NOT NULL"
            ).fetchall()
        )
    assert reasons["id-maria-grand"] == "rolled_off"  # 10/16 has passed (today is 10/17)
    assert reasons["id-blues-singer"] == "rolled_off"  # 10/14 has passed too
    # A cancelled future show is 'ineligible': still booked in the window, no longer on the feed.


def test_cancelled_future_show_closes_stints_as_ineligible(world: World) -> None:
    world.run(RUN1)
    world.calendar.event_list = [e for e in world.calendar.event_list if e.event_id != 102]

    world.run(RUN2)

    with world.db.connection() as conn:
        reasons = conn.execute(
            "SELECT DISTINCT removal_reason FROM rolling_playlist_tracks "
            "WHERE spotify_artist_id = 'id-aaron-myers' AND removed_at IS NOT NULL"
        ).fetchall()
    assert reasons == [("ineligible",)]
    assert not any(t.startswith("id-aaron-myers") for t in held(world, "next-6-weeks"))


def test_spotify_failure_after_the_database_write_is_partial_and_the_next_run_converges(
    world: World,
) -> None:
    world.music.fail_replace_once = True

    first = world.run(RUN1)

    assert first.outcome == "partial"
    failed = [p for p in first.playlists if p.status == "partial"]
    assert len(failed) == 1
    # The database already holds the stints, and flags that Spotify has not caught up.
    with world.db.connection() as conn:
        pending = conn.execute(
            "SELECT slot_key FROM rolling_playlists WHERE sync_pending"
        ).fetchall()
    assert [r[0] for r in pending] == [failed[0].slot_key]
    assert world.music.playlists[_pid(world, failed[0].slot_key)]["items"] == []

    second = world.run(RUN2)

    assert second.outcome == "success"
    for definition_slot in ("next-6-weeks", "blues-alley"):
        assert world.music.playlists[_pid(world, definition_slot)]["items"] == held(
            world, definition_slot
        )
    with world.db.connection() as conn:
        assert conn.execute(
            "SELECT count(*) FROM rolling_playlists WHERE sync_pending"
        ).fetchone() == (0,)
        dupes = conn.execute(
            "SELECT count(*) FROM (SELECT 1 FROM rolling_playlist_tracks WHERE removed_at IS NULL "
            "GROUP BY rolling_playlist_id, spotify_track_id HAVING count(*) > 1) d"
        ).fetchone()
    assert dupes == (0,)


def _pid(world: World, slot_key: str) -> str:
    with world.db.connection() as conn:
        row = conn.execute(
            "SELECT spotify_playlist_id FROM rolling_playlists WHERE slot_key = %s", (slot_key,)
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_one_playlist_raising_leaves_the_others_correctly_built(world: World) -> None:
    world.music.fail_create_titled = "Blues Alley · Next 6 Weeks"

    result = world.run()

    assert result.outcome == "partial"
    by_slot = {p.slot_key: p for p in result.playlists}
    assert by_slot["blues-alley"].status == "partial"
    assert len(held(world, "next-6-weeks")) == 4
    assert len(world.music.created) == 2
    assert held(world, "blues-alley") == []  # nothing recorded for the playlist that failed


def test_a_failed_fetch_touches_no_dc_playlist_and_logs_fetch_fail(world: World) -> None:
    world.run(RUN1)
    before = snapshot(world.db)
    writes = world.music.writes
    world.calendar.fail = True

    run_daily(
        world.daily_deps(),
        today=TODAY,
        horizon_weeks_ahead=4,
        retain_weeks_past=1,
        dry_run=False,
        now=RUN2,
    )

    assert snapshot(world.db) == before
    assert world.music.writes == writes
    runs = PgRunRepo(world.db).recent_runs(club_id="capitalbop", days=3650)
    assert [r.outcome for r in runs] == ["fetch_fail"]


def test_daily_logs_one_capitalbop_row_and_retention_never_unfollows_a_rolling_playlist(
    world: World,
) -> None:
    run_daily(
        world.daily_deps(),
        today=TODAY,
        horizon_weeks_ahead=4,
        retain_weeks_past=1,
        dry_run=False,
        now=RUN1,
    )
    # Far in the future every NYC-style retention window has long passed.
    run_daily(
        world.daily_deps(),
        today=TODAY + timedelta(days=400),
        horizon_weeks_ahead=4,
        retain_weeks_past=1,
        dry_run=False,
        now=RUN2,
    )

    assert world.music.unfollowed == []
    runs = PgRunRepo(world.db).recent_runs(club_id="capitalbop", days=3650)
    assert len(runs) == 2 and runs[-1].outcome in ("success", "no_shows", "partial")


def test_dry_run_issues_zero_spotify_writes_and_leaves_no_state_behind(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    run_daily(
        world.daily_deps(),
        today=TODAY,
        horizon_weeks_ahead=4,
        retain_weeks_past=1,
        dry_run=True,
        now=RUN1,
    )

    assert world.music.writes == 0 and world.music.unfollowed == []
    with world.db.connection() as conn:
        assert conn.execute("SELECT count(*) FROM rolling_playlist_tracks").fetchone() == (0,)
        assert conn.execute("SELECT count(*) FROM rolling_playlists").fetchone() == (0,)
        assert conn.execute(
            "SELECT count(*) FROM playlist_events WHERE rolling_playlist_id IS NOT NULL"
        ).fetchone() == (0,)
    assert "would hold" in caplog.text  # the planned lists are shown


def test_dry_run_then_real_run_builds_everything(world: World) -> None:
    run_daily(
        world.daily_deps(),
        today=TODAY,
        horizon_weeks_ahead=4,
        retain_weeks_past=1,
        dry_run=True,
        now=RUN1,
    )

    result = world.run(RUN2)

    assert result.outcome == "success" and len(world.music.created) == 3
    assert len(held(world, "next-6-weeks")) == 4


def test_a_disliked_artist_is_left_off_every_playlist(world: World) -> None:
    PgArtistRepo(world.db)  # artists are created by the run; feedback needs the row first
    world.run(RUN1)
    PgFeedbackRepo(world.db).record_feedback(
        Feedback(target_type="artist", target_id="id-maria-grand", sentiment="disliked")
    )

    world.run(RUN2)

    assert [t.split("-t")[0] for t in held(world, "next-6-weeks")] == [
        "id-aaron-myers",
        "id-aaron-myers",
    ]
    with world.db.connection() as conn:
        reason = conn.execute(
            "SELECT DISTINCT removal_reason FROM rolling_playlist_tracks "
            "WHERE spotify_artist_id = 'id-maria-grand'"
        ).fetchall()
    assert reason == [("ineligible",)]


def test_a_user_removed_track_is_never_readded(world: World) -> None:
    world.run(RUN1)
    repo = PgRollingPlaylistRepo(world.db)
    playlist = repo.get_rolling_playlist("dc", "next-6-weeks")
    assert playlist is not None and playlist.id is not None
    stint = repo.open_stints(playlist.id)[0]
    assert stint.id is not None
    repo.close_stint(stint.id, "user", RUN1)  # what #29's detection will record...
    spotify_items = world.music.playlists[_pid(world, "next-6-weeks")]["items"]
    spotify_items.remove(stint.spotify_track_id)  # ...after Brian deleted it in Spotify
    replaced_before = len(world.music.replaced)

    world.run(RUN2)

    assert stint.spotify_track_id not in held(world, "next-6-weeks")
    assert len(world.music.replaced) == replaced_before  # nothing to put back
    assert world.music.playlists[_pid(world, "next-6-weeks")]["items"] == held(
        world, "next-6-weeks"
    )


def test_a_departing_artist_with_the_same_track_returning_gets_a_new_stint(world: World) -> None:
    world.run(RUN1)
    feed = world.calendar.event_list
    world.calendar.event_list = [e for e in feed if e.event_id != 101]
    world.run(RUN2, today=TODAY)
    world.calendar.event_list = feed

    world.run(RUN2 + timedelta(days=1))

    with world.db.connection() as conn:
        rows = conn.execute(
            "SELECT count(*), count(removed_at) FROM rolling_playlist_tracks "
            "WHERE spotify_artist_id = 'id-maria-grand'"
        ).fetchone()
    assert rows == (4, 2)  # two tracks, each with one closed stint and one fresh one


def test_overlapping_runs_are_refused_by_the_lock(world: World) -> None:
    lock = PgRunLock(world.db)

    with lock.hold("jazz_agent.dc") as first, lock.hold("jazz_agent.dc") as second:
        assert (first, second) == (True, False)
    with lock.hold("jazz_agent.dc") as again:
        assert again is True  # released on exit


# --- user removals from AlxJazz (DC section 11, issue #29) ---------------------------------------


def with_alx_shows(world: World) -> None:
    world.calendar.event_list += [
        ev(10, 110, "Esther Haynes", date(2026, 10, 21), venue=LAPORTAS, hour=18, pick=False),
        ev(11, 111, "Birch Artist", date(2026, 10, 22), venue=BIRCHMERE, pick=False),
    ]


def spotify_items(world: World, slot_key: str) -> list[str]:
    return world.music.playlists[_pid(world, slot_key)]["items"]  # type: ignore[no-any-return]


def user_closures(world: World) -> list[tuple[Any, ...]]:
    with world.db.connection() as conn:
        return conn.execute(
            "SELECT spotify_track_id FROM rolling_playlist_tracks "
            "WHERE removal_reason = 'user' ORDER BY id"
        ).fetchall()


def spotify_feedback(world: World) -> list[tuple[Any, ...]]:
    with world.db.connection() as conn:
        return conn.execute(
            "SELECT target_type, target_id, sentiment, source, show_id IS NOT NULL, "
            "rolling_playlist_id IS NOT NULL FROM feedback ORDER BY id"
        ).fetchall()


ESTHER = "id-esther-haynes"
BIRCH = "id-birch-artist"


def test_a_removed_laportas_track_is_closed_once_and_recorded_as_one_dislike(
    world: World,
) -> None:
    with_alx_shows(world)
    world.run(RUN1)
    assert held(world, "alxjazz") == [f"{ESTHER}-t1", f"{ESTHER}-t2", f"{BIRCH}-t1", f"{BIRCH}-t2"]
    spotify_items(world, "alxjazz").remove(f"{ESTHER}-t1")  # Brian deletes it in the app

    world.run(RUN2)
    world.run(RUN2 + timedelta(days=1))  # idempotency: a third run adds nothing

    assert user_closures(world) == [(f"{ESTHER}-t1",)]
    assert spotify_feedback(world) == [("track", f"{ESTHER}-t1", "disliked", "spotify", True, True)]
    assert f"{ESTHER}-t1" not in held(world, "alxjazz")
    assert spotify_items(world, "alxjazz") == held(world, "alxjazz")  # nothing put back
    assert len(held(world, "alxjazz")) == 3  # no replacement track was chosen


def test_removing_both_of_an_artists_laportas_tracks_writes_one_artist_dislike(
    world: World,
) -> None:
    with_alx_shows(world)
    world.run(RUN1)
    for track in (f"{ESTHER}-t1", f"{ESTHER}-t2"):
        spotify_items(world, "alxjazz").remove(track)

    world.run(RUN2)
    world.run(RUN2 + timedelta(days=1))

    assert [f[:2] for f in spotify_feedback(world)] == [
        ("track", f"{ESTHER}-t1"),
        ("track", f"{ESTHER}-t2"),
        ("artist", ESTHER),
    ]
    # Novelty honours the artist dislike, and booking her again does not bring her back.
    world.calendar.event_list.append(
        ev(12, 112, "Esther Haynes", date(2026, 10, 28), venue=LAPORTAS, hour=18, pick=False)
    )
    world.run(RUN2 + timedelta(days=2))
    assert not any(t.startswith(ESTHER) for t in held(world, "alxjazz"))
    assert len(user_closures(world)) == 2


def test_a_removed_birchmere_track_stays_removed_and_writes_no_feedback(world: World) -> None:
    with_alx_shows(world)
    world.run(RUN1)
    spotify_items(world, "alxjazz").remove(f"{BIRCH}-t1")

    world.run(RUN2)
    world.run(RUN2 + timedelta(days=1))

    assert user_closures(world) == [(f"{BIRCH}-t1",)]
    assert spotify_feedback(world) == []
    assert f"{BIRCH}-t1" not in held(world, "alxjazz")


def test_a_removed_track_is_not_re_added_even_after_the_artist_is_booked_again(
    world: World,
) -> None:
    with_alx_shows(world)
    world.run(RUN1)
    spotify_items(world, "alxjazz").remove(f"{BIRCH}-t1")
    world.run(RUN2)
    world.calendar.event_list.append(
        ev(13, 111, "Birch Artist", date(2026, 11, 5), venue=BIRCHMERE)
    )  # a later date of the same series

    world.run(RUN2 + timedelta(days=1))

    assert f"{BIRCH}-t1" not in held(world, "alxjazz")
    assert f"{BIRCH}-t2" in held(world, "alxjazz")
    assert len(user_closures(world)) == 1


def test_a_failed_page_read_records_nothing(world: World) -> None:
    with_alx_shows(world)
    world.run(RUN1)
    spotify_items(world, "alxjazz").remove(f"{ESTHER}-t1")
    real = world.music.get_playlist_items

    def broken(_: str) -> list[dict[str, Any]]:
        raise RuntimeError("page 2 returned 502")

    world.music.get_playlist_items = broken  # type: ignore[method-assign]
    world.run(RUN2)

    assert user_closures(world) == [] and spotify_feedback(world) == []
    world.music.get_playlist_items = real  # type: ignore[method-assign]
    world.run(RUN2 + timedelta(days=1))
    assert user_closures(world) == [(f"{ESTHER}-t1",)]  # caught once the read works


def test_a_relinked_track_with_the_same_isrc_is_not_a_removal(world: World) -> None:
    with_alx_shows(world)
    world.run(RUN1)
    items = spotify_items(world, "alxjazz")
    items[items.index(f"{ESTHER}-t1")] = "relinked-id"  # same recording, new track id
    world.music.isrc_overrides = {"relinked-id": f"ISRC-{ESTHER}-1"}

    world.run(RUN2)

    assert user_closures(world) == [] and spotify_feedback(world) == []
    assert len(held(world, "alxjazz")) == 4


def test_a_mass_disappearance_records_nothing_and_alerts(world: World) -> None:
    with_alx_shows(world)
    world.run(RUN1)
    spotify_items(world, "alxjazz").clear()

    world.run(RUN2)

    assert user_closures(world) == [] and spotify_feedback(world) == []
    assert len(held(world, "alxjazz")) == 4  # stints stay open
    assert len(world.notifier.alerts) == 1


def test_removals_are_not_inferred_while_the_playlist_write_is_pending(world: World) -> None:
    with_alx_shows(world)
    world.run(RUN1)
    spotify_items(world, "alxjazz").remove(f"{ESTHER}-t1")
    PgRollingPlaylistRepo(world.db).set_sync_pending(_playlist_row_id(world, "alxjazz"), True)

    world.run(RUN2)

    assert user_closures(world) == []
    assert f"{ESTHER}-t1" in spotify_items(world, "alxjazz")  # the full list was re-sent


def _playlist_row_id(world: World, slot_key: str) -> int:
    with world.db.connection() as conn:
        row = conn.execute(
            "SELECT id FROM rolling_playlists WHERE slot_key = %s", (slot_key,)
        ).fetchone()
    assert row is not None
    return int(row[0])


def test_removals_from_the_other_playlists_are_not_inferred(world: World) -> None:
    world.run(RUN1)
    spotify_items(world, "blues-alley").pop(0)
    spotify_items(world, "next-6-weeks").pop(0)

    world.run(RUN2)

    assert user_closures(world) == [] and spotify_feedback(world) == []


def test_dry_run_records_no_removal_and_no_feedback(world: World) -> None:
    with_alx_shows(world)
    world.run(RUN1)
    spotify_items(world, "alxjazz").remove(f"{ESTHER}-t1")

    run_daily(
        world.daily_deps(),
        today=TODAY,
        horizon_weeks_ahead=4,
        retain_weeks_past=1,
        dry_run=True,
        now=RUN2,
    )

    assert user_closures(world) == [] and spotify_feedback(world) == []


# --- new-artist push (DC section 12, issue #30) ----------------------------------------------


def laportas(n: int, name: str, day: date, hour: int = 18, minute: int = 30) -> CalendarEvent:
    event = ev(200 + n, 200 + n, name, day, venue=LAPORTAS, hour=hour, pick=False)
    return replace(event, start_time=time(hour, minute))


def test_the_seeding_run_sends_nothing(world: World) -> None:
    world.calendar.event_list.append(laportas(1, "Esther Haynes", date(2026, 10, 21)))

    world.run(RUN1)

    assert world.notifier.pushes == []


def test_two_new_artists_in_one_run_produce_one_push_naming_both(world: World) -> None:
    world.calendar.event_list.append(
        ev(11, 111, "Birch Artist", date(2026, 10, 22), venue=BIRCHMERE, pick=False)
    )
    world.run(RUN1)  # seeds AlxJazz
    world.calendar.event_list += [
        laportas(1, "Esther Haynes", date(2026, 10, 21)),
        laportas(2, "Kai Newman", date(2026, 10, 14), hour=19, minute=0),
    ]

    world.run(RUN2)

    assert len(world.notifier.pushes) == 1
    body, title, tags = world.notifier.pushes[0]
    assert title == "AlxJazz \u00b7 new at LaPortas" and tags == ("new-artist",)
    assert body.splitlines() == [
        "Kai Newman \u2014 Wed 10/14, 7:00pm",
        "Esther Haynes \u2014 Wed 10/21, 6:30pm",
    ]


def test_an_artist_already_announced_does_not_push_again(world: World) -> None:
    world.calendar.event_list.append(
        ev(11, 111, "Birch Artist", date(2026, 10, 22), venue=BIRCHMERE, pick=False)
    )
    world.run(RUN1)
    world.calendar.event_list.append(laportas(1, "Esther Haynes", date(2026, 10, 21)))
    world.run(RUN2)
    world.run(RUN2 + timedelta(days=1))

    assert len(world.notifier.pushes) == 1


def test_a_returning_artist_with_a_closed_stint_does_not_push(world: World) -> None:
    world.calendar.event_list.append(
        ev(11, 111, "Birch Artist", date(2026, 10, 22), venue=BIRCHMERE, pick=False)
    )
    esther = laportas(1, "Esther Haynes", date(2026, 10, 21))
    world.calendar.event_list.append(esther)
    world.run(RUN1)
    world.calendar.event_list.remove(esther)
    world.run(RUN2)  # her stints close
    world.notifier.pushes.clear()
    world.calendar.event_list.append(esther)

    world.run(RUN2 + timedelta(days=1))

    assert len(held(world, "alxjazz")) == 4  # she is back on the playlist
    assert world.notifier.pushes == []


def test_an_artist_excluded_by_novelty_never_pushes(world: World) -> None:
    world.calendar.event_list.append(
        ev(11, 111, "Birch Artist", date(2026, 10, 22), venue=BIRCHMERE, pick=False)
    )
    world.run(RUN1)
    PgFeedbackRepo(world.db).record_feedback(
        Feedback(target_type="artist", target_id="id-esther-haynes", sentiment="disliked")
    )
    world.calendar.event_list.append(laportas(1, "Esther Haynes", date(2026, 10, 21)))

    world.run(RUN2)

    assert world.notifier.pushes == []
    assert not any(t.startswith("id-esther") for t in held(world, "alxjazz"))


def test_a_raising_notifier_leaves_the_runs_outcome_unchanged(world: World) -> None:
    world.calendar.event_list.append(
        ev(11, 111, "Birch Artist", date(2026, 10, 22), venue=BIRCHMERE, pick=False)
    )
    world.run(RUN1)
    world.calendar.event_list.append(laportas(1, "Esther Haynes", date(2026, 10, 21)))
    world.notifier.raise_error = True

    result = world.run(RUN2)

    assert result.outcome == "success"
    assert len(held(world, "alxjazz")) == 4


def test_a_birchmere_artist_new_to_alxjazz_does_not_push(world: World) -> None:
    world.calendar.event_list.append(laportas(1, "Esther Haynes", date(2026, 10, 21)))
    world.run(RUN1)
    world.calendar.event_list.append(
        ev(11, 111, "Birch Artist", date(2026, 10, 22), venue=BIRCHMERE, pick=False)
    )

    world.run(RUN2)

    assert world.notifier.pushes == []


def test_dry_run_sends_no_push(world: World) -> None:
    world.calendar.event_list.append(
        ev(11, 111, "Birch Artist", date(2026, 10, 22), venue=BIRCHMERE, pick=False)
    )
    world.run(RUN1)
    world.calendar.event_list.append(laportas(1, "Esther Haynes", date(2026, 10, 21)))

    run_daily(
        world.daily_deps(),
        today=TODAY,
        horizon_weeks_ahead=4,
        retain_weeks_past=1,
        dry_run=True,
        now=RUN2,
    )

    assert world.notifier.pushes == []
