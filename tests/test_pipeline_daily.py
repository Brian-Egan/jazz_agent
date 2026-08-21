"""pipeline.daily tests against a real Postgres (see conftest.py). Fetcher,
extractor, MusicService, GraphService, notifier, and the Anthropic client are
all fakes/stubs -- no live calls anywhere.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pytest
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.adapters.pg.club_repo import PgClubRepo
from jazz_agent.adapters.pg.playlist_repo import PgPlaylistRepo
from jazz_agent.adapters.pg.run_repo import PgRunRepo
from jazz_agent.adapters.pg.show_repo import PgShowRepo
from jazz_agent.core.models import ExtractedShow, MbArtist
from jazz_agent.pipeline.daily import Dependencies, run_daily

TODAY = date(2026, 8, 18)
NOW = datetime(2026, 8, 18, 13, 0, tzinfo=UTC)

VANGUARD_URL = "https://villagevanguard.example/schedule"
SMALLS_URL = "https://smalls.example/schedule"

FRISELL_ID = "3JsHnjpbhX4SnySpvpa9DK"


class FakeFetcher:
    def __init__(self, responses: dict[str, str | Exception]) -> None:
        self._responses = responses
        self.calls: list[str] = []

    def get(self, url: str, render_mode: str = "http") -> str:
        self.calls.append(url)
        response = self._responses.get(url)
        if response is None:
            raise RuntimeError(f"no fake response registered for {url}")
        if isinstance(response, Exception):
            raise response
        return response


class FakeExtractor:
    def __init__(self, responses: dict[str, list[ExtractedShow] | Exception]) -> None:
        self._responses = responses

    def extract(self, text: str, window: int) -> list[ExtractedShow]:
        response = self._responses.get(text, [])
        if isinstance(response, Exception):
            raise response
        return response


class FakeMusicService:
    def __init__(self) -> None:
        self._albums: dict[str, list[dict[str, Any]]] = {}
        self._tracks: dict[str, list[dict[str, Any]]] = {}
        self.create_playlist_calls = 0
        self.add_tracks_calls: list[tuple[str, list[str]]] = []
        self.remove_tracks_calls: list[tuple[str, list[str]]] = []
        self.unfollow_calls: list[str] = []

    def register_artist(
        self, spotify_artist_id: str, name: str, album_id: str, tracks: list[dict[str, Any]]
    ) -> None:
        self._albums[spotify_artist_id] = [
            {"id": album_id, "name": f"Album {album_id}", "popularity": 50}
        ]
        self._tracks[album_id] = tracks
        self._search_result = {
            "id": spotify_artist_id,
            "name": name,
            "genres": ["contemporary jazz", "jazz"],
            "popularity": 50,
            "followers": {"total": 10000},
        }

    def search_artists(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return [self._search_result] if hasattr(self, "_search_result") else []

    def get_artist_albums(self, spotify_artist_id: str) -> list[dict[str, Any]]:
        return self._albums.get(spotify_artist_id, [])

    def get_album_tracks(self, spotify_album_id: str) -> list[dict[str, Any]]:
        return self._tracks.get(spotify_album_id, [])

    def create_playlist(self, title: str, description: str) -> dict[str, Any]:
        self.create_playlist_calls += 1
        return {"id": "spotify-playlist-1", "external_urls": {"spotify": "https://x/1"}}

    def get_playlist(self, spotify_playlist_id: str) -> dict[str, Any] | None:
        return None

    def add_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        self.add_tracks_calls.append((spotify_playlist_id, list(spotify_track_ids)))

    def remove_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        self.remove_tracks_calls.append((spotify_playlist_id, list(spotify_track_ids)))

    def unfollow_playlist(self, spotify_playlist_id: str) -> None:
        self.unfollow_calls.append(spotify_playlist_id)

    def currently_playing(self) -> dict[str, Any] | None:
        return None

    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]:
        return []


class FakeGraphService:
    """Always a clean 'no hit' -- verification becomes unverified, never
    disputed, so dispute resolution (already covered by test_pipeline_verify.py)
    is never exercised by these orchestrator-level tests."""

    def resolve_mbid(self, name: str) -> MbArtist | None:
        return None

    def spotify_url_for(self, mbid: str) -> str | None:
        return None

    def edges_for(self, mbid: str) -> list[Any]:
        return []


@dataclass
class _FakeToolUseBlock:
    input: dict[str, Any]
    name: str = "adjudicate_match"
    type: str = "tool_use"


@dataclass
class _FakeMessage:
    content: list[_FakeToolUseBlock]


class _FakeMessages:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def create(self, **kwargs: Any) -> _FakeMessage:
        return _FakeMessage(content=[_FakeToolUseBlock(input=self._payload)])


@dataclass
class FakeAnthropicClient:
    payload: dict[str, Any] = field(default_factory=dict)
    messages: _FakeMessages = field(init=False)

    def __post_init__(self) -> None:
        self.messages = _FakeMessages(self.payload)


class FakeNotifier:
    def __init__(self, raise_error: bool = False) -> None:
        self.alert_calls = 0
        self._raise_error = raise_error

    def alert(self, msg: str) -> None:
        self.alert_calls += 1
        if self._raise_error:
            raise RuntimeError("ntfy is down")


def _insert_club(
    db: ConnectionPool, club_id: str, name: str, schedule_url: str, render_mode: str = "http"
) -> None:
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO clubs (club_id, name, schedule_url, render_mode) VALUES (%s, %s, %s, %s)",
            (club_id, name, schedule_url, render_mode),
        )


def _deps(
    db: ConnectionPool,
    fetcher: FakeFetcher,
    extractor: FakeExtractor,
    music: FakeMusicService,
    graph: FakeGraphService,
    notifier: FakeNotifier,
    llm_payload: dict[str, Any] | None = None,
) -> Dependencies:
    return Dependencies(
        club_repo=PgClubRepo(db),
        show_repo=PgShowRepo(db),
        artist_repo=PgArtistRepo(db),
        playlist_repo=PgPlaylistRepo(db),
        run_repo=PgRunRepo(db),
        http_fetcher=fetcher,  # type: ignore[arg-type]
        pw_fetcher=fetcher,  # type: ignore[arg-type]
        extractor=extractor,  # type: ignore[arg-type]
        music=music,  # type: ignore[arg-type]
        graph=graph,  # type: ignore[arg-type]
        notifier=notifier,  # type: ignore[arg-type]
        llm_client=FakeAnthropicClient(  # type: ignore[arg-type]
            llm_payload
            or {"artist_id": FRISELL_ID, "confidence": 0.95, "reasoning": "exact name match"}
        ),
        extraction_model="claude-haiku-4-5",
        adjudication_model="claude-haiku-4-5",
    )


def _frisell_shows(show_date: date = TODAY) -> list[ExtractedShow]:
    return [
        ExtractedShow(
            show_date=show_date,
            act_name="Bill Frisell Trio",
            raw_text="Bill Frisell Trio tonight",
        )
    ]


def test_one_club_raising_leaves_the_others_playlists_correctly_built(db: ConnectionPool) -> None:
    _insert_club(db, "village-vanguard", "Village Vanguard", VANGUARD_URL)
    _insert_club(db, "smalls", "SmallsLIVE", SMALLS_URL)

    fetcher = FakeFetcher(
        {VANGUARD_URL: RuntimeError("403 Forbidden"), SMALLS_URL: "<html>ok</html>"}
    )
    music = FakeMusicService()
    music.register_artist(FRISELL_ID, "Bill Frisell", "album1", [{"id": "t1", "track_number": 1}])
    extractor = FakeExtractor({"<html>ok</html>": _frisell_shows()})
    deps = _deps(db, fetcher, extractor, music, FakeGraphService(), FakeNotifier())

    run_id = run_daily(
        deps, TODAY, horizon_weeks_ahead=4, retain_weeks_past=1, dry_run=False, now=NOW
    )

    run_repo = PgRunRepo(db)
    outcomes = {r.club_id: r.outcome for r in run_repo.recent_runs(days=1) if r.club_id}
    assert outcomes["village-vanguard"] == "fetch_fail"
    assert outcomes["smalls"] == "success"

    playlist = PgPlaylistRepo(db).get_week_playlist("smalls", TODAY)
    assert playlist is not None
    tracks = PgPlaylistRepo(db).tracks_for(playlist.id)  # type: ignore[arg-type]
    assert len(tracks) == 1
    assert run_id  # sanity: a run_id was produced


def test_three_consecutive_failures_fire_one_alert_and_recovery_resets_it(
    db: ConnectionPool,
) -> None:
    _insert_club(db, "village-vanguard", "Village Vanguard", VANGUARD_URL)
    failing_fetcher = FakeFetcher({VANGUARD_URL: RuntimeError("403 Forbidden")})
    notifier = FakeNotifier()
    deps = _deps(
        db, failing_fetcher, FakeExtractor({}), FakeMusicService(), FakeGraphService(), notifier
    )

    run_daily(deps, TODAY, 4, 1, dry_run=False, now=NOW)
    assert notifier.alert_calls == 0
    run_daily(deps, TODAY, 4, 1, dry_run=False, now=NOW)
    assert notifier.alert_calls == 0
    run_daily(deps, TODAY, 4, 1, dry_run=False, now=NOW)
    assert notifier.alert_calls == 1  # fires exactly on the third

    run_daily(deps, TODAY, 4, 1, dry_run=False, now=NOW)  # a fourth consecutive failure
    assert notifier.alert_calls == 1  # suppressed, not fired again

    # Recovery: the club's page is reachable again (nothing booked this week).
    healthy_fetcher = FakeFetcher({VANGUARD_URL: "<html>dark this week</html>"})
    deps.http_fetcher = healthy_fetcher  # type: ignore[assignment]
    deps.pw_fetcher = healthy_fetcher  # type: ignore[assignment]
    run_daily(deps, TODAY, 4, 1, dry_run=False, now=NOW)
    assert notifier.alert_calls == 1  # a success/no_shows run resets the streak

    # Break it again for three more runs -- must alert again, not stay suppressed.
    deps.http_fetcher = failing_fetcher  # type: ignore[assignment]
    deps.pw_fetcher = failing_fetcher  # type: ignore[assignment]
    run_daily(deps, TODAY, 4, 1, dry_run=False, now=NOW)
    run_daily(deps, TODAY, 4, 1, dry_run=False, now=NOW)
    assert notifier.alert_calls == 1
    run_daily(deps, TODAY, 4, 1, dry_run=False, now=NOW)
    assert notifier.alert_calls == 2


def test_ntfy_failure_is_logged_and_does_not_fail_the_run(
    db: ConnectionPool, caplog: pytest.LogCaptureFixture
) -> None:
    _insert_club(db, "village-vanguard", "Village Vanguard", VANGUARD_URL)
    fetcher = FakeFetcher({VANGUARD_URL: RuntimeError("403 Forbidden")})
    notifier = FakeNotifier(raise_error=True)
    deps = _deps(db, fetcher, FakeExtractor({}), FakeMusicService(), FakeGraphService(), notifier)

    run_id = ""
    with caplog.at_level("ERROR"):
        for _ in range(3):
            run_id = run_daily(deps, TODAY, 4, 1, dry_run=False, now=NOW)  # must not raise

    assert notifier.alert_calls == 1  # it did try, and did fail
    assert any("ntfy alert failed" in record.message for record in caplog.records)
    run_repo = PgRunRepo(db)
    assert any(r.run_id == run_id for r in run_repo.recent_runs(days=1))


def test_dry_run_issues_zero_spotify_writes(db: ConnectionPool) -> None:
    _insert_club(db, "village-vanguard", "Village Vanguard", VANGUARD_URL)
    fetcher = FakeFetcher({VANGUARD_URL: "<html>ok</html>"})
    music = FakeMusicService()
    music.register_artist(FRISELL_ID, "Bill Frisell", "album1", [{"id": "t1", "track_number": 1}])
    extractor = FakeExtractor({"<html>ok</html>": _frisell_shows()})
    deps = _deps(db, fetcher, extractor, music, FakeGraphService(), FakeNotifier())

    run_daily(deps, TODAY, horizon_weeks_ahead=4, retain_weeks_past=1, dry_run=True, now=NOW)

    assert music.create_playlist_calls == 0
    assert music.add_tracks_calls == []
    assert music.remove_tracks_calls == []
    assert music.unfollow_calls == []

    # But the pipeline still ran and computed what it would have built:
    # the DB-side playlist and tracks exist, just never pushed to Spotify.
    playlist = PgPlaylistRepo(db).get_week_playlist("village-vanguard", TODAY)
    assert playlist is not None
    assert len(PgPlaylistRepo(db).tracks_for(playlist.id)) == 1  # type: ignore[arg-type]


def test_every_run_writes_a_run_log_row_per_club_plus_a_run_level_row(db: ConnectionPool) -> None:
    _insert_club(db, "village-vanguard", "Village Vanguard", VANGUARD_URL)
    _insert_club(db, "smalls", "SmallsLIVE", SMALLS_URL)
    fetcher = FakeFetcher({VANGUARD_URL: "<html>a</html>", SMALLS_URL: "<html>b</html>"})
    extractor = FakeExtractor({})
    deps = _deps(db, fetcher, extractor, FakeMusicService(), FakeGraphService(), FakeNotifier())

    run_id = run_daily(
        deps, TODAY, horizon_weeks_ahead=4, retain_weeks_past=1, dry_run=False, now=NOW
    )

    with db.connection() as conn:
        rows = conn.execute(
            "SELECT club_id, outcome FROM run_log WHERE run_id = %s", (run_id,)
        ).fetchall()

    club_ids = {r[0] for r in rows}
    assert club_ids == {"village-vanguard", "smalls", None}  # two club rows + one run-level row
    assert len(rows) == 3
