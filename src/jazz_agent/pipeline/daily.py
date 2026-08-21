"""Daily orchestrator: the single cron entry point (ARCHITECTURE.md section 10, ADR-015).

Each club runs inside its own try/except and writes its own run_log row --
one club being down, redesigned, or 403-ing must never prevent the others
from being built. Chronic failure (three consecutive failures for the same
club) fires exactly one ntfy push, then suppresses until that club recovers.
Everything else is queryable via run_log; only that one case pushes (ADR-015
resolves the PRD's "failures visible" vs "no notifications" contradiction).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import anthropic

from jazz_agent.adapters.anthropic_extractor import AnthropicExtractor, ExtractionFailed
from jazz_agent.core.models import Club, RunLogEntry, Show
from jazz_agent.core.normalize import normalize_act_name
from jazz_agent.core.weeks import week_start_date
from jazz_agent.pipeline.adjudicate import adjudicate
from jazz_agent.pipeline.prune import prune_club
from jazz_agent.pipeline.reconcile import WeekBooking, reconcile_week
from jazz_agent.pipeline.verify import verify_and_correct
from jazz_agent.ports.extractor import Extractor
from jazz_agent.ports.fetcher import Fetcher
from jazz_agent.ports.graph import GraphService
from jazz_agent.ports.music import MusicService
from jazz_agent.ports.notifier import Notifier
from jazz_agent.ports.repository import ArtistRepo, ClubRepo, PlaylistRepo, RunRepo, ShowRepo

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

    def create_playlist(self, title: str, description: str) -> dict[str, Any]:
        fake_id = f"dry-run-playlist-{self._next_fake_id}"
        self._next_fake_id += 1
        logger.info("DRY_RUN: would create playlist %r (%r)", title, description)
        return {"id": fake_id, "external_urls": {"spotify": f"https://dry-run.invalid/{fake_id}"}}

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

    for club in deps.club_repo.get_active_clubs():
        club_logger = logging.LoggerAdapter(logger, {"run_id": run_id, "club_id": club.club_id})
        run_club(club, run_id, today, horizon_weeks_ahead, deps, music, now, club_logger)
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
                deps.playlist_repo,
                now,
            )
        except Exception:
            club_logger.exception("retention prune failed")

    deps.run_repo.log_run_outcome(RunLogEntry(run_id=run_id, outcome="success"))
    return run_id


def run_club(
    club: Club,
    run_id: str,
    today: date,
    horizon_weeks_ahead: int,
    deps: Dependencies,
    music: MusicService,
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
            club, today, horizon_weeks_ahead, deps, music, now, club_logger
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
    now: datetime,
    club_logger: logging.LoggerAdapter[logging.Logger],
) -> tuple[str, int]:
    fetcher = deps.http_fetcher if club.render_mode == "http" else deps.pw_fetcher
    try:
        html = fetcher.get(club.schedule_url, render_mode=club.render_mode)
    except Exception as e:
        raise _FetchFailed(str(e)) from e

    try:
        extracted_shows = deps.extractor.extract(html, horizon_weeks_ahead)
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
            club.club_id, club.name, week_start, bookings, music, deps.playlist_repo
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
                deps.playlist_repo,
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
    from jazz_agent.adapters.http_fetcher import HttpFetcher
    from jazz_agent.adapters.musicbrainz import MusicBrainzClient
    from jazz_agent.adapters.ntfy import NtfyNotifier
    from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
    from jazz_agent.adapters.pg.club_repo import PgClubRepo
    from jazz_agent.adapters.pg.graph_repo import PgGraphRepo
    from jazz_agent.adapters.pg.playlist_repo import PgPlaylistRepo
    from jazz_agent.adapters.pg.pool import make_pool
    from jazz_agent.adapters.pg.run_repo import PgRunRepo
    from jazz_agent.adapters.pg.show_repo import PgShowRepo
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
