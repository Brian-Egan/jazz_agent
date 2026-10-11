"""Daily orchestrator: the single cron entry point (ARCHITECTURE.md section 10, ADR-015).

Each club runs inside its own try/except and writes its own run_log row --
one club being down, redesigned, or 403-ing must never prevent the others
from being built. Chronic failure (three consecutive failures for the same
club) fires exactly one ntfy push, then suppresses until that club recovers.
Everything else is queryable via run_log; only that one case pushes (ADR-015
resolves the PRD's "failures visible" vs "no notifications" contradiction).
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
import time
import uuid
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import anthropic

from jazz_agent.adapters.anthropic_extractor import AnthropicExtractor, ExtractionFailed
from jazz_agent.core.models import (
    Club,
    Feedback,
    PlaylistEvent,
    PlaylistTrack,
    RollingPlaylist,
    RollingPlaylistEvent,
    RollingPlaylistTrack,
    RunLogEntry,
    Show,
    WeekPlaylist,
)
from jazz_agent.core.normalize import normalize_act_name
from jazz_agent.core.novelty import FeedbackEntry
from jazz_agent.core.weeks import week_start_date
from jazz_agent.pipeline.adjudicate import adjudicate
from jazz_agent.pipeline.dc import FEED_CLUB_ID, FEED_CLUB_NAME, DcDependencies, run_dc
from jazz_agent.pipeline.prune import prune_club
from jazz_agent.pipeline.reconcile import WeekBooking, reconcile_week
from jazz_agent.pipeline.verify import verify_and_correct
from jazz_agent.ports.calendar import CalendarFetchError
from jazz_agent.ports.extractor import Extractor
from jazz_agent.ports.fetcher import Fetcher
from jazz_agent.ports.graph import GraphService
from jazz_agent.ports.music import MusicService
from jazz_agent.ports.notifier import Notifier
from jazz_agent.ports.repository import (
    ArtistRepo,
    ClubRepo,
    FeedbackRepo,
    PlaylistRepo,
    RollingPlaylistRepo,
    RunLock,
    RunRepo,
    ShowRepo,
)

logger = logging.getLogger(__name__)

CHRONIC_FAILURE_THRESHOLD_DEFAULT = 3


class _FetchFailed(Exception):
    pass


class _ExtractFailed(Exception):
    pass


@dataclass
class Dependencies:
    club_repo: ClubRepo
    show_repo: ShowRepo
    artist_repo: ArtistRepo
    playlist_repo: PlaylistRepo
    run_repo: RunRepo
    http_fetcher: Fetcher
    pw_fetcher: Fetcher
    extractor: Extractor
    music: MusicService
    graph: GraphService
    notifier: Notifier
    llm_client: anthropic.Anthropic
    extraction_model: str
    adjudication_model: str
    # Washington, D.C. (docs/DC.md). None leaves the run NYC-only.
    dc: DcDependencies | None = None
    run_lock: RunLock | None = None


class DryRunMusicService:
    """Wraps a MusicService, no-opping every mutating call and logging what
    would have happened instead. Reads pass through untouched, so the whole
    pipeline (adjudication, album selection) runs exactly as it would for
    real, computing and logging the playlist it would have built."""

    def __init__(self, inner: MusicService) -> None:
        self._inner = inner
        self._next_fake_id = 1

    def search_artists(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self._inner.search_artists(query, limit)

    def get_artist_albums(self, spotify_artist_id: str) -> list[dict[str, Any]]:
        return self._inner.get_artist_albums(spotify_artist_id)

    def get_album_tracks(self, spotify_album_id: str) -> list[dict[str, Any]]:
        return self._inner.get_album_tracks(spotify_album_id)

    def create_playlist(self, title: str, description: str, public: bool = False) -> dict[str, Any]:
        fake_id = f"dry-run-playlist-{self._next_fake_id}"
        self._next_fake_id += 1
        logger.info("DRY_RUN: would create playlist %r (%r)", title, description)
        return {"id": fake_id, "external_urls": {"spotify": f"https://dry-run.invalid/{fake_id}"}}

    def search_tracks(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self._inner.search_tracks(query, limit)

    def get_playlist_items(self, spotify_playlist_id: str) -> list[dict[str, Any]]:
        if spotify_playlist_id.startswith("dry-run-"):
            return []
        return self._inner.get_playlist_items(spotify_playlist_id)

    def replace_playlist_items(
        self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]
    ) -> None:
        logger.info(
            "DRY_RUN: would replace %s with %d tracks", spotify_playlist_id, len(spotify_track_ids)
        )

    def update_playlist_details(
        self, spotify_playlist_id: str, title: str, description: str
    ) -> None:
        logger.info("DRY_RUN: would retitle %s to %r", spotify_playlist_id, title)

    def get_playlist(self, spotify_playlist_id: str) -> dict[str, Any] | None:
        if spotify_playlist_id.startswith("dry-run-"):
            return None
        return self._inner.get_playlist(spotify_playlist_id)

    def add_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        logger.info(
            "DRY_RUN: would add %d tracks to %s", len(spotify_track_ids), spotify_playlist_id
        )

    def remove_tracks(self, spotify_playlist_id: str, spotify_track_ids: Sequence[str]) -> None:
        logger.info(
            "DRY_RUN: would remove %d tracks from %s", len(spotify_track_ids), spotify_playlist_id
        )

    def unfollow_playlist(self, spotify_playlist_id: str) -> None:
        logger.info("DRY_RUN: would unfollow %s", spotify_playlist_id)

    def currently_playing(self) -> dict[str, Any] | None:
        return self._inner.currently_playing()

    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._inner.recently_played(limit)


class DryRunPlaylistRepo:
    """Wraps a PlaylistRepo, no-opping every write that would claim a real
    Spotify side effect happened, logging what would have happened instead.

    Reads and upsert_week_playlist (our own bookkeeping row, no Spotify claim)
    pass through untouched -- the rest (link_spotify_playlist, add_tracks,
    remove_track, record_event, mark_removed) would otherwise persist state a
    later real run trusts as "this already exists in Spotify"
    ((club_id, week_start_date) is reconcile_week's whole idempotency key,
    AGENTS.md invariant 4) -- confirmed live: a dry run's fake
    spotify_playlist_id made a subsequent real run try to add tracks to a
    playlist ID that was never actually created."""

    def __init__(self, inner: PlaylistRepo) -> None:
        self._inner = inner

    def upsert_week_playlist(self, playlist: WeekPlaylist) -> int:
        return self._inner.upsert_week_playlist(playlist)

    def link_spotify_playlist(
        self, week_playlist_id: int, spotify_playlist_id: str, spotify_url: str
    ) -> None:
        logger.info(
            "DRY_RUN: would link week_playlist %d to spotify playlist %s",
            week_playlist_id,
            spotify_playlist_id,
        )

    def get_week_playlist(self, club_id: str, week_start_date: date) -> WeekPlaylist | None:
        return self._inner.get_week_playlist(club_id, week_start_date)

    def playlists_for_club(self, club_id: str) -> list[WeekPlaylist]:
        return self._inner.playlists_for_club(club_id)

    def add_tracks(self, week_playlist_id: int, tracks: Sequence[PlaylistTrack]) -> None:
        logger.info(
            "DRY_RUN: would add %d tracks to week_playlist %d", len(tracks), week_playlist_id
        )

    def remove_track(self, week_playlist_id: int, spotify_track_id: str) -> None:
        logger.info(
            "DRY_RUN: would remove track %s from week_playlist %d",
            spotify_track_id,
            week_playlist_id,
        )

    def tracks_for(self, week_playlist_id: int) -> list[PlaylistTrack]:
        return self._inner.tracks_for(week_playlist_id)

    def record_event(self, event: PlaylistEvent) -> None:
        logger.info("DRY_RUN: would record playlist event %r", event)

    def events_for(self, week_playlist_id: int) -> list[PlaylistEvent]:
        return self._inner.events_for(week_playlist_id)

    def mark_removed(self, week_playlist_id: int, removed_at: datetime) -> None:
        logger.info("DRY_RUN: would mark week_playlist %d removed", week_playlist_id)


class DryRunFeedbackRepo:
    """DRY_RUN: removal-derived feedback is logged, not written. Reads pass through."""

    def __init__(self, inner: FeedbackRepo) -> None:
        self._inner = inner

    def record_feedback(self, feedback: Feedback) -> int:
        logger.info("DRY_RUN: would record feedback %r", feedback)
        return -1

    def feedback_for_target(self, target_type: str, target_id: str) -> list[Feedback]:
        return self._inner.feedback_for_target(target_type, target_id)

    def novelty_entries(self) -> list[FeedbackEntry]:
        return self._inner.novelty_entries()


class DryRunNotifier:
    """DRY_RUN: DC's own alerts are logged, never pushed."""

    def alert(self, msg: str, title: str | None = None, tags: Sequence[str] = ()) -> None:
        logger.info("DRY_RUN: would send ntfy %r: %s", title or "alert", msg)


class DryRunRollingRepo:
    """Wraps a RollingPlaylistRepo for DRY_RUN: every write that records playlist state is
    logged and skipped, so a dry run leaves no stint, event, link or playlist row behind (the
    NYC wrapper's lesson: state recorded without the Spotify call a later run trusts is
    corruption). Reads pass through, so the planned lists are computed from real state."""

    def __init__(self, inner: RollingPlaylistRepo) -> None:
        self._inner = inner

    def upsert_rolling_playlist(self, playlist: RollingPlaylist) -> int:
        existing = self._inner.get_rolling_playlist(playlist.region, playlist.slot_key)
        if existing is not None and existing.id is not None:
            logger.info("DRY_RUN: would update rolling playlist %s", playlist.slot_key)
            return existing.id
        logger.info("DRY_RUN: would create rolling_playlists row %s", playlist.slot_key)
        return -1  # never a real id: nothing downstream may treat it as stored

    def link_spotify_playlist(
        self, rolling_playlist_id: int, spotify_playlist_id: str, spotify_url: str
    ) -> None:
        logger.info("DRY_RUN: would link rolling playlist %d", rolling_playlist_id)

    def get_rolling_playlist(self, region: str, slot_key: str) -> RollingPlaylist | None:
        return self._inner.get_rolling_playlist(region, slot_key)

    def set_sync_pending(self, rolling_playlist_id: int, pending: bool) -> None:
        logger.info("DRY_RUN: would set sync_pending=%s on %d", pending, rolling_playlist_id)

    def set_stint_position(self, stint_id: int, position: int) -> None:
        logger.info("DRY_RUN: would move stint %d to position %d", stint_id, position)

    def open_stints(self, rolling_playlist_id: int) -> list[RollingPlaylistTrack]:
        return self._inner.open_stints(rolling_playlist_id) if rolling_playlist_id > 0 else []

    def stinted_artist_ids(self, rolling_playlist_id: int) -> set[str]:
        return (
            self._inner.stinted_artist_ids(rolling_playlist_id)
            if rolling_playlist_id > 0
            else set()
        )

    def open_stint(self, track: RollingPlaylistTrack) -> int:
        logger.info("DRY_RUN: would open stint for %s", track.spotify_track_id)
        return -1

    def close_stint(self, stint_id: int, reason: str, at: datetime) -> None:
        logger.info("DRY_RUN: would close stint %d (%s)", stint_id, reason)

    def user_removed_track_ids(self) -> set[str]:
        return self._inner.user_removed_track_ids()

    def record_event(self, event: RollingPlaylistEvent) -> None:
        logger.info("DRY_RUN: would record rolling playlist event %r", event)


def run_daily(
    deps: Dependencies,
    today: date,
    horizon_weeks_ahead: int,
    retain_weeks_past: int,
    dry_run: bool,
    now: datetime,
    chronic_failure_threshold: int = CHRONIC_FAILURE_THRESHOLD_DEFAULT,
) -> str:
    """Run the full pipeline for every active club. Returns the run_id."""
    run_id = str(uuid.uuid4())
    music = DryRunMusicService(deps.music) if dry_run else deps.music
    playlist_repo = DryRunPlaylistRepo(deps.playlist_repo) if dry_run else deps.playlist_repo

    for club in deps.club_repo.get_active_clubs():
        club_logger = logging.LoggerAdapter(logger, {"run_id": run_id, "club_id": club.club_id})
        run_club(
            club, run_id, today, horizon_weeks_ahead, deps, music, playlist_repo, now, club_logger
        )
        _maybe_alert_chronic_failure(
            club, deps.run_repo, deps.notifier, chronic_failure_threshold, club_logger
        )
        try:
            prune_club(
                club.club_id,
                today,
                retain_weeks_past,
                horizon_weeks_ahead,
                music,
                playlist_repo,
                now,
            )
        except Exception:
            club_logger.exception("retention prune failed")

    if deps.dc is not None:
        _run_dc_unit(deps, deps.dc, run_id, today, music, dry_run, now, chronic_failure_threshold)

    deps.run_repo.log_run_outcome(RunLogEntry(run_id=run_id, outcome="success"))
    return run_id


def _run_dc_unit(
    deps: Dependencies,
    dc: DcDependencies,
    run_id: str,
    today: date,
    music: MusicService,
    dry_run: bool,
    now: datetime,
    chronic_failure_threshold: int,
) -> None:
    """DC after the NYC clubs, in its own try/except, one run_log row on ``capitalbop``
    (DC section 14). Never raises: DC failing must not mask the NYC run it follows."""
    dc_logger = logging.LoggerAdapter(logger, {"run_id": run_id, "club_id": FEED_CLUB_ID})
    feed = Club(club_id=FEED_CLUB_ID, name=FEED_CLUB_NAME, schedule_url=None)
    rolling_repo = DryRunRollingRepo(dc.rolling_repo) if dry_run else dc.rolling_repo
    if dry_run:
        dc = dataclasses.replace(dc, feedback_repo=DryRunFeedbackRepo(dc.feedback_repo))
    dc_notifier: Notifier = DryRunNotifier() if dry_run else deps.notifier
    start = time.monotonic()
    shows_found: int | None = None
    detail: str | None = None

    try:
        # Runs can overlap (no mutual exclusion in cron or here); two DC runs at once could
        # create a playlist twice, so only one proceeds.
        with _lock(deps.run_lock, "jazz_agent.dc") as acquired:
            if not acquired:
                dc_logger.warning("another DC run holds the lock; skipping")
                return
            result = run_dc(
                dc,
                deps.show_repo,
                deps.artist_repo,
                deps.extractor,
                deps.graph,
                deps.llm_client,
                deps.extraction_model,
                deps.adjudication_model,
                music,
                rolling_repo,
                dc_notifier,
                today,
                now,
            )
        outcome, shows_found, detail = result.outcome, result.shows_found, result.detail
        if dry_run:
            for slot_key, names in result.planned.items():
                dc_logger.info("DRY_RUN: %s would hold %d tracks: %s", slot_key, len(names), names)
    except CalendarFetchError as e:
        outcome, detail = "fetch_fail", str(e)
        dc_logger.exception("DC fetch failed; no DC playlist was touched")
    except Exception as e:
        outcome, detail = "extract_fail", str(e)
        dc_logger.exception("DC run failed")

    deps.run_repo.log_run_outcome(
        RunLogEntry(
            run_id=run_id,
            club_id=FEED_CLUB_ID,
            outcome=outcome,
            shows_found=shows_found,
            detail=detail,
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    )
    _maybe_alert_chronic_failure(
        feed, deps.run_repo, deps.notifier, chronic_failure_threshold, dc_logger
    )


@contextlib.contextmanager
def _lock(lock: RunLock | None, name: str) -> Iterator[bool]:
    if lock is None:
        yield True
        return
    with lock.hold(name) as acquired:
        yield acquired


def run_club(
    club: Club,
    run_id: str,
    today: date,
    horizon_weeks_ahead: int,
    deps: Dependencies,
    music: MusicService,
    playlist_repo: PlaylistRepo,
    now: datetime,
    club_logger: logging.LoggerAdapter[logging.Logger],
) -> str:
    """Never raises. Always writes exactly one run_log row for this club, so
    this club's failure can never prevent the others from being built."""
    start = time.monotonic()
    shows_found: int | None = None
    detail: str | None = None

    try:
        outcome, shows_found = _run_club_pipeline(
            club, today, horizon_weeks_ahead, deps, music, playlist_repo, now, club_logger
        )
    except Exception as e:
        outcome = _outcome_for_exception(e)
        detail = str(e)
        club_logger.exception("club pipeline failed")

    duration_ms = int((time.monotonic() - start) * 1000)
    deps.run_repo.log_run_outcome(
        RunLogEntry(
            run_id=run_id,
            club_id=club.club_id,
            outcome=outcome,
            shows_found=shows_found,
            detail=detail,
            duration_ms=duration_ms,
        )
    )
    club_logger.info("club run finished: outcome=%s shows_found=%s", outcome, shows_found)
    return outcome


def _run_club_pipeline(
    club: Club,
    today: date,
    horizon_weeks_ahead: int,
    deps: Dependencies,
    music: MusicService,
    playlist_repo: PlaylistRepo,
    now: datetime,
    club_logger: logging.LoggerAdapter[logging.Logger],
) -> tuple[str, int]:
    fetcher = deps.http_fetcher if club.render_mode == "http" else deps.pw_fetcher
    if club.schedule_url is None:
        raise _FetchFailed(f"{club.club_id} has no schedule_url")
    try:
        html = fetcher.get(club.schedule_url, render_mode=club.render_mode)
    except Exception as e:
        raise _FetchFailed(str(e)) from e

    try:
        extracted_shows = deps.extractor.extract(
            html, horizon_weeks_ahead, today, club.venue_label, club.render_mode
        )
    except ExtractionFailed as e:
        raise _ExtractFailed(str(e)) from e
    except Exception as e:
        raise _ExtractFailed(str(e)) from e

    if not extracted_shows:
        return "no_shows", 0

    bookings_by_week: dict[date, list[WeekBooking]] = {}
    matched_count = 0
    miss_count = 0

    for extracted in extracted_shows:
        act_name_norm = normalize_act_name(extracted.act_name)
        show_id = deps.show_repo.upsert_show(
            Show(
                club_id=club.club_id,
                show_date=extracted.show_date,
                act_name_raw=extracted.act_name,
                act_name_norm=act_name_norm,
                set_times=extracted.set_times,
                album_mentioned=extracted.album_mentioned,
                raw_text=extracted.raw_text,
            )
        )
        if extracted.performers:
            deps.show_repo.record_performers(show_id, extracted.performers)

        result = adjudicate(
            show_id,
            extracted.act_name,
            extracted.raw_text,
            music,
            deps.llm_client,
            deps.adjudication_model,
        )

        week_start = week_start_date(extracted.show_date, club.week_start_dow)
        bookings_by_week.setdefault(week_start, [])

        if result.matched_artist is not None:
            deps.artist_repo.upsert_artist(result.matched_artist)
            deps.artist_repo.link_show_artist(show_id, result.matched_artist.spotify_artist_id)
            matched_count += 1
            bookings_by_week[week_start].append(
                WeekBooking(
                    extracted.show_date,
                    result.matched_artist.name,
                    result.matched_artist.spotify_artist_id,
                    show_id,
                )
            )
        else:
            assert result.match_miss is not None
            deps.show_repo.record_match_miss(result.match_miss)
            miss_count += 1
            bookings_by_week[week_start].append(
                WeekBooking(extracted.show_date, extracted.act_name, None, show_id)
            )

    for week_start, bookings in bookings_by_week.items():
        playlist = reconcile_week(
            club.club_id, club.name, week_start, bookings, music, playlist_repo
        )
        for booking in bookings:
            if booking.spotify_artist_id is None:
                continue
            artist = deps.artist_repo.get_artist(booking.spotify_artist_id)
            if artist is None:
                continue
            verify_and_correct(
                artist,
                playlist,
                deps.graph,
                music,
                deps.artist_repo,
                playlist_repo,
                deps.show_repo,
                deps.llm_client,
                deps.adjudication_model,
                now,
            )

    if matched_count == 0:
        return "no_match", len(extracted_shows)
    if miss_count > 0:
        return "partial", len(extracted_shows)
    return "success", len(extracted_shows)


def _outcome_for_exception(e: Exception) -> str:
    if isinstance(e, _FetchFailed):
        return "fetch_fail"
    # _ExtractFailed or anything unexpected past the fetch stage: treated as
    # extract_fail, the closest of the six allowed outcomes to "something
    # went wrong processing this club's content."
    return "extract_fail"


def _maybe_alert_chronic_failure(
    club: Club,
    run_repo: RunRepo,
    notifier: Notifier,
    threshold: int,
    club_logger: logging.LoggerAdapter[logging.Logger],
) -> None:
    recent = run_repo.recent_runs(club_id=club.club_id, days=30)
    if not _should_alert_chronic_failure(recent, threshold):
        return
    try:
        notifier.alert(
            f"{club.name} has failed {threshold} runs in a row -- check its schedule page"
        )
    except Exception:
        club_logger.exception("ntfy alert failed")


_TECHNICAL_FAILURE_OUTCOMES = {"fetch_fail", "extract_fail"}


def _should_alert_chronic_failure(
    recent_most_recent_first: Sequence[RunLogEntry], threshold: int
) -> bool:
    """True exactly on the run that crosses the threshold, not on every
    subsequent failing run: fires once per streak, resets on recovery.

    "Failure" here means a technical failure (fetch_fail, extract_fail) --
    the club is down, redesigned, or 403-ing. no_shows is a legitimate dark
    week, and no_match/partial are matching shortfalls on a page that
    fetched and parsed fine; neither means the scraper itself is broken, so
    neither should count against this club or reset its streak.
    """
    if len(recent_most_recent_first) < threshold:
        return False
    if not all(
        r.outcome in _TECHNICAL_FAILURE_OUTCOMES for r in recent_most_recent_first[:threshold]
    ):
        return False
    if len(recent_most_recent_first) > threshold:
        run_before_streak = recent_most_recent_first[threshold]
        if run_before_streak.outcome in _TECHNICAL_FAILURE_OUTCOMES:
            return False  # already alerted earlier in this same streak
    return True


def main() -> int:
    """Cron entry point: `uv run python -m jazz_agent.pipeline.daily`, scheduled
    at RUN_HOUR_ET. Wires real adapters from config.py -- the only module that
    reads the environment (AGENTS.md invariant 2)."""
    from jazz_agent.adapters.capitalbop import CapitalbopClient
    from jazz_agent.adapters.http_fetcher import HttpFetcher
    from jazz_agent.adapters.musicbrainz import MusicBrainzClient
    from jazz_agent.adapters.ntfy import NtfyNotifier
    from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
    from jazz_agent.adapters.pg.capitalbop_repo import PgCapitalbopRepo
    from jazz_agent.adapters.pg.club_repo import PgClubRepo
    from jazz_agent.adapters.pg.feedback_repo import PgFeedbackRepo
    from jazz_agent.adapters.pg.graph_repo import PgGraphRepo
    from jazz_agent.adapters.pg.playlist_repo import PgPlaylistRepo
    from jazz_agent.adapters.pg.pool import make_pool
    from jazz_agent.adapters.pg.rolling_playlist_repo import PgRollingPlaylistRepo
    from jazz_agent.adapters.pg.run_lock import PgRunLock
    from jazz_agent.adapters.pg.run_repo import PgRunRepo
    from jazz_agent.adapters.pg.show_repo import PgShowRepo
    from jazz_agent.adapters.pg.venue_group_repo import PgVenueGroupRepo
    from jazz_agent.adapters.pg.venue_repo import PgVenueRepo
    from jazz_agent.adapters.pw_fetcher import PlaywrightFetcher
    from jazz_agent.adapters.spotify import SpotifyClient
    from jazz_agent.config import load_config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    config = load_config()
    pool = make_pool(config.database_url)
    llm_client = anthropic.Anthropic(api_key=config.anthropic_api_key)

    deps = Dependencies(
        club_repo=PgClubRepo(pool),
        show_repo=PgShowRepo(pool),
        artist_repo=PgArtistRepo(pool),
        playlist_repo=PgPlaylistRepo(pool),
        run_repo=PgRunRepo(pool),
        http_fetcher=HttpFetcher(),
        pw_fetcher=PlaywrightFetcher(),
        extractor=AnthropicExtractor(client=llm_client, model=config.extraction_model),
        music=SpotifyClient(
            client_id=config.spotify_client_id,
            client_secret=config.spotify_client_secret,
            refresh_token=config.spotify_refresh_token,
        ),
        graph=MusicBrainzClient(
            graph_repo=PgGraphRepo(pool),
            user_agent=config.musicbrainz_user_agent,
            timeout_seconds=config.musicbrainz_timeout_seconds,
            miss_ttl_days=config.musicbrainz_miss_ttl_days,
        ),
        notifier=NtfyNotifier(topic=config.ntfy_topic, server=config.ntfy_server),
        llm_client=llm_client,
        extraction_model=config.extraction_model,
        adjudication_model=config.adjudication_model,
        dc=DcDependencies(
            calendar=CapitalbopClient(),
            venue_repo=PgVenueRepo(pool),
            capitalbop_repo=PgCapitalbopRepo(pool),
            rolling_repo=PgRollingPlaylistRepo(pool),
            group_repo=PgVenueGroupRepo(pool),
            feedback_repo=PgFeedbackRepo(pool),
            novelty_liked_days=config.novelty_liked_days,
        ),
        run_lock=PgRunLock(pool),
    )

    run_id = run_daily(
        deps,
        today=date.today(),
        horizon_weeks_ahead=config.horizon_weeks_ahead,
        retain_weeks_past=config.retain_weeks_past,
        dry_run=config.dry_run,
        now=datetime.now(UTC),
        chronic_failure_threshold=config.chronic_failure_threshold,
    )
    logger.info("daily run complete: run_id=%s", run_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
