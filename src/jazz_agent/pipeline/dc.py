"""The DC run: mirror, eligibility, extraction and matching, novelty, selection, reconcile
(DC sections 5-10, 14).

DC is one unit for failure purposes: the feed is one source, and a failed fetch means no DC
playlist can be trusted that day, so none is touched. Within a successful fetch each of the
five playlists reconciles inside its own try/except, so one playlist's failure never blocks
the others.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any

import anthropic

from jazz_agent.core import dc_groups
from jazz_agent.core.dc_description import DescriptionEntry, build_description
from jazz_agent.core.dc_geo import resolve_city
from jazz_agent.core.dc_notify import TAGS, TITLE, NewArtist, format_message
from jazz_agent.core.eligibility import (
    DcEvent,
    Membership,
    PlannedAct,
    plan_playlists,
    planned_instance_ids,
)
from jazz_agent.core.models import Artist, RollingPlaylist, RollingPlaylistTrack, StoredEvent
from jazz_agent.core.novelty import excluded_artist_ids, excluded_track_ids
from jazz_agent.core.selection import order_tracks, select_album, select_tracks
from jazz_agent.core.weeks import dc_window
from jazz_agent.pipeline.dc_extract import ExtractMatchResult, extract_and_match
from jazz_agent.pipeline.dc_mirror import MirrorResult, mirror_calendar
from jazz_agent.pipeline.detect_removals import make_removal_detector
from jazz_agent.pipeline.reconcile_rolling import (
    DesiredTrack,
    RollingOutcome,
    reconcile_rolling,
)
from jazz_agent.ports.calendar import CalendarSource
from jazz_agent.ports.extractor import Extractor
from jazz_agent.ports.graph import GraphService
from jazz_agent.ports.music import MusicService
from jazz_agent.ports.notifier import Notifier
from jazz_agent.ports.repository import (
    ArtistRepo,
    CapitalbopRepo,
    FeedbackRepo,
    RollingPlaylistRepo,
    ShowRepo,
    VenueGroupRepo,
    VenueMirrorRepo,
)

logger = logging.getLogger(__name__)

# The feed's own clubs row: run_log and chronic failure key on it (DC section 14).
FEED_CLUB_ID = "capitalbop"
FEED_CLUB_NAME = "CapitalBop"
LAPORTAS_CLUB_ID = "cb-laportas"


@dataclass
class DcDependencies:
    calendar: CalendarSource
    venue_repo: VenueMirrorRepo
    capitalbop_repo: CapitalbopRepo
    rolling_repo: RollingPlaylistRepo
    group_repo: VenueGroupRepo
    feedback_repo: FeedbackRepo
    novelty_liked_days: int


@dataclass
class DcRunResult:
    outcome: str  # a run_log outcome
    shows_found: int = 0
    detail: str = ""
    playlists: list[RollingOutcome] = field(default_factory=list)
    # slot_key -> the track names each playlist would hold; what a DRY_RUN shows.
    planned: dict[str, list[str]] = field(default_factory=dict)


def run_dc(
    dc: DcDependencies,
    show_repo: ShowRepo,
    artist_repo: ArtistRepo,
    extractor: Extractor,
    graph: GraphService,
    llm_client: anthropic.Anthropic,
    extraction_model: str,
    adjudication_model: str,
    music: MusicService,
    rolling_repo: RollingPlaylistRepo,
    notifier: Notifier,
    today: date,
    now: datetime,
) -> DcRunResult:
    """Raises ``CalendarFetchError`` when the feed cannot be read completely, before anything
    is written or reconciled. ``rolling_repo`` is passed separately so DRY_RUN can hand in a
    wrapper that blocks the writes recording Spotify state."""
    start, end = dc_window(today)
    mirror = mirror_calendar(
        dc.calendar, dc.venue_repo, show_repo, dc.capitalbop_repo, start, end, now
    )
    stored = dc.capitalbop_repo.window_events(start, end)
    if not stored:
        return DcRunResult(outcome="no_shows", detail=mirror.detail())

    by_instance = {s.event.instance_id: s for s in stored}
    dc_events = [_to_dc_event(s, mirror.cancelled_instance_ids) for s in stored]
    memberships = [
        Membership(m.club_id, m.group_key, m.filter_profile) for m in dc.group_repo.active_members()
    ]
    plan = plan_playlists(dc_events, memberships, today)
    eligible_ids = planned_instance_ids(dc_events, memberships, today)

    matched = extract_and_match(
        [by_instance[i].event for i in sorted(eligible_ids)],
        dc.capitalbop_repo,
        show_repo,
        artist_repo,
        extractor,
        graph,
        music,
        llm_client,
        extraction_model,
        adjudication_model,
        now,
    )
    _capture_articles(dc, mirror, [by_instance[i] for i in eligible_ids])

    result = DcRunResult(outcome="success", shows_found=len(stored))
    selector = _TrackSelector(music)
    feedback = dc.feedback_repo.novelty_entries()
    excluded_artists = excluded_artist_ids(feedback, now, dc.novelty_liked_days)
    excluded_tracks = excluded_track_ids(feedback)

    detector = make_removal_detector(
        music, rolling_repo, dc.feedback_repo, show_repo, notifier, now
    )

    # Phase 1: what every playlist wants, so phase 2 can tell a slot move from a departure.
    rows: dict[str, RollingPlaylist] = {}
    desired_by_slot: dict[str, tuple[list[DesiredTrack], list[DescriptionEntry]]] = {}
    for definition in dc_groups.PLAYLISTS:
        try:
            rows[definition.slot_key] = _ensure_row(rolling_repo, definition)
            assert rows[definition.slot_key].id is not None
            desired_by_slot[definition.slot_key] = _desired_for(
                plan[definition.slot_key],
                by_instance,
                artist_repo,
                selector,
                excluded_artists,
                excluded_tracks,
                rolling_repo.open_stints(rows[definition.slot_key].id or 0),
            )
        except Exception as e:
            logger.exception(
                "DC playlist %s: building the desired list failed", definition.slot_key
            )
            result.playlists.append(
                RollingOutcome(definition.slot_key, "partial", detail=f"selection failed: {e}")
            )
    wanted_on = {
        slot: {t.spotify_artist_id for t in tracks} for slot, (tracks, _) in desired_by_slot.items()
    }

    # Phase 2: each playlist reconciles in its own try/except.
    for definition in dc_groups.PLAYLISTS:
        if definition.slot_key not in desired_by_slot:
            continue
        tracks, entries = desired_by_slot[definition.slot_key]
        row = rows[definition.slot_key]
        # Before reconcile writes stints: who has ever been on AlxJazz (DC section 12).
        had_stints = (
            rolling_repo.stinted_artist_ids(row.id or 0)
            if definition.group_key == "alxjazz"
            else set()
        )
        reconciled = False
        try:
            outcome = reconcile_rolling(
                definition,
                rows[definition.slot_key],
                tracks,
                definition.title,
                build_description(entries),
                _departure_reasons(definition.slot_key, wanted_on, dc.capitalbop_repo, today, end),
                music,
                rolling_repo,
                now,
                detector,
            )
            reconciled = True
        except Exception as e:
            logger.exception("DC playlist %s failed", definition.slot_key)
            outcome = RollingOutcome(definition.slot_key, "partial", detail=f"failed: {e}")
        result.playlists.append(outcome)
        result.planned[definition.slot_key] = [t.track_name or t.spotify_track_id for t in tracks]
        if definition.group_key == "alxjazz" and reconciled:
            _notify_new_laportas(tracks, had_stints, by_instance, artist_repo, notifier)

    failed = [p for p in result.playlists if p.status == "partial"]
    if failed or matched.failures:
        result.outcome = "partial"
    result.detail = _detail(mirror, matched, result)
    return result


def _detail(mirror: MirrorResult, matched: ExtractMatchResult, result: DcRunResult) -> str:
    changed = sum(1 for p in result.playlists if p.status == "updated")
    parts = [mirror.detail(), matched.detail(), f"{changed} playlists changed"]
    parts += [f"{p.slot_key}: {p.detail}" for p in result.playlists if p.status == "partial"]
    return "; ".join(parts)


def _to_dc_event(stored: StoredEvent, cancelled: frozenset[int]) -> DcEvent:
    e = stored.event
    return DcEvent(
        instance_id=e.instance_id,
        act_name=stored.act_name,
        act_name_norm=stored.act_name_norm,
        show_date=stored.show_date,
        start_time=e.start_time,
        club_id=stored.club_id,
        cb_pick=e.cb_pick,
        tags=e.tags,
        price=e.price_text or "",
        city=resolve_city(stored.city_override, e.geocoded_city, e.neighborhoods),
        cancelled=e.instance_id in cancelled,
    )


def _capture_articles(
    dc: DcDependencies, mirror: MirrorResult, eligible: list[StoredEvent]
) -> None:
    """One article request per eligible, never-before-seen series (DC section 2). MCP context
    only; a failure here never touches a playlist."""
    first_instance: dict[int, StoredEvent] = {}
    for stored in sorted(eligible, key=lambda s: (s.show_date, s.event.instance_id)):
        first_instance.setdefault(stored.event.event_id, stored)
    for series_id in sorted(mirror.new_series_ids & first_instance.keys()):
        try:
            article = dc.calendar.article(first_instance[series_id].event.instance_id)
            if article is not None:
                dc.capitalbop_repo.set_series_article(
                    series_id, article.title, article.url, article.published
                )
        except Exception:
            logger.exception("article capture failed for series %s", series_id)


def _ensure_row(repo: RollingPlaylistRepo, definition: dc_groups.PlaylistDef) -> RollingPlaylist:
    """The rolling_playlists row exists before any Spotify call (DC section 10). An existing
    row is left alone: its title and description are what Spotify was last given, and the
    reconciler compares against them."""
    existing = repo.get_rolling_playlist(definition.region, definition.slot_key)
    if existing is not None:
        return existing
    row = RollingPlaylist(
        region=definition.region,
        slot_key=definition.slot_key,
        group_key=definition.group_key,
        title=definition.title,
        is_public=definition.is_public,
    )
    row_id = repo.upsert_rolling_playlist(row)
    # A DRY_RUN repository writes nothing, so the row may not be readable back.
    return repo.get_rolling_playlist(definition.region, definition.slot_key) or replace(
        row, id=row_id
    )


def _departure_reasons(
    slot_key: str,
    wanted_on: dict[str, set[str]],
    capitalbop_repo: CapitalbopRepo,
    today: date,
    window_end: date,
) -> Callable[[str], str]:
    """Why a departing artist's stints close (DC section 10): ``moved`` when another playlist
    now wants them, ``ineligible`` when they are still booked in the window but no longer
    qualify (cancelled, a filter or membership change, novelty, cap overflow), otherwise
    ``rolled_off`` because their shows passed or left the window."""
    elsewhere = set().union(*(a for k, a in wanted_on.items() if k != slot_key))

    def reason(artist_id: str) -> str:
        if artist_id in elsewhere:
            return "moved"
        if capitalbop_repo.artist_has_shows(artist_id, today, window_end):
            return "ineligible"
        return "rolled_off"

    return reason


class _TrackSelector:
    """Two tracks per artist, once per run however many playlists want them."""

    def __init__(self, music: MusicService) -> None:
        self._music = music
        self._memo: dict[str, list[dict[str, Any]]] = {}

    def tracks_for(self, artist: Artist) -> list[dict[str, Any]]:
        if artist.spotify_artist_id not in self._memo:
            try:
                found = self._music.search_tracks(f'artist:"{artist.name}"', limit=10)
                self._memo[artist.spotify_artist_id] = select_tracks(
                    artist.spotify_artist_id,
                    found,
                    lambda: self._album_fallback(artist.spotify_artist_id),
                )
            except Exception:
                # A failed lookup (a Spotify 429 on the album fallback, say) skips this artist
                # for the run rather than failing the whole playlist or settling for one
                # track: an artist already on a list is never topped up, so a half-selection
                # would stick. They have no stints yet, so the next run picks them up whole.
                logger.exception("track selection failed for %s; skipping this run", artist.name)
                self._memo[artist.spotify_artist_id] = []
        return self._memo[artist.spotify_artist_id]

    def _album_fallback(self, spotify_artist_id: str) -> list[dict[str, Any]]:
        selection = select_album(self._music.get_artist_albums(spotify_artist_id))
        if selection.album is None:
            return []
        album_id = selection.album["id"]
        tracks = order_tracks(self._music.get_album_tracks(album_id))
        return [{**t, "album": {"id": album_id}} for t in tracks]


def _desired_for(
    acts: tuple[PlannedAct, ...],
    by_instance: dict[int, StoredEvent],
    artist_repo: ArtistRepo,
    selector: _TrackSelector,
    excluded_artists: frozenset[str],
    excluded_tracks: frozenset[str],
    open_stints: list[RollingPlaylistTrack],
) -> tuple[list[DesiredTrack], list[DescriptionEntry]]:
    # An artist already on this playlist keeps the tracks it has: search ranking drifts, and a
    # track swapped for no reason is churn on a list other people follow.
    pinned: dict[str, list[RollingPlaylistTrack]] = {}
    for stint in open_stints:
        pinned.setdefault(stint.spotify_artist_id, []).append(stint)
    tracks: list[DesiredTrack] = []
    entries: list[DescriptionEntry] = []
    seen_artists: set[str] = set()
    for act in acts:
        first = by_instance[act.first_instance_id]
        contributed = False
        for artist in artist_repo.artists_for_show(first.event.show_id):
            if (
                artist.spotify_artist_id in excluded_artists
                or artist.spotify_artist_id in seen_artists
            ):
                continue
            seen_artists.add(artist.spotify_artist_id)
            keep = [
                p
                for p in pinned.get(artist.spotify_artist_id, [])
                if p.spotify_track_id not in excluded_tracks
            ]
            if keep:
                chosen = [
                    DesiredTrack(
                        p.spotify_track_id,
                        p.spotify_album_id,
                        p.spotify_artist_id,
                        p.track_name,
                        p.isrc,
                        first.event.show_id,
                    )
                    for p in keep
                ]
            else:
                chosen = [
                    DesiredTrack(
                        spotify_track_id=t["id"],
                        spotify_album_id=(t.get("album") or {}).get("id", ""),
                        spotify_artist_id=artist.spotify_artist_id,
                        track_name=t.get("name"),
                        isrc=(t.get("external_ids") or {}).get("isrc"),
                        show_id=first.event.show_id,
                    )
                    for t in selector.tracks_for(artist)
                    if t["id"] not in excluded_tracks
                ]
            tracks.extend(chosen)
            contributed = contributed or bool(chosen)
        if contributed:
            entries.append(DescriptionEntry(act.act_name, act.earliest, first.venue_name))
    return tracks, entries


def _notify_new_laportas(
    tracks: list[DesiredTrack],
    had_stints: set[str],
    by_instance: dict[int, StoredEvent],
    artist_repo: ArtistRepo,
    notifier: Notifier,
) -> None:
    """One push per run naming every artist new to AlxJazz from a LaPortas show (DC section
    12, ADR-023). Silent on the seeding run (the playlist never held anything), and ``tracks``
    is already post-novelty, so a rated resident never pushes. A push failure is logged and
    never fails the run."""
    if not had_stints:
        return
    by_show = {s.event.show_id: s for s in by_instance.values()}
    new: dict[str, NewArtist] = {}
    for track in tracks:
        artist_id = track.spotify_artist_id
        stored = by_show.get(track.show_id) if track.show_id is not None else None
        if artist_id in had_stints or artist_id in new or stored is None:
            continue
        if stored.club_id != LAPORTAS_CLUB_ID:
            continue
        artist = artist_repo.get_artist(artist_id)
        if artist is None:
            continue
        new[artist_id] = NewArtist(artist.name, stored.show_date, stored.event.start_time)
    if not new:
        return
    try:
        notifier.alert(format_message(list(new.values())), title=TITLE, tags=TAGS)
    except Exception:
        logger.exception("new-artist ntfy push failed")
