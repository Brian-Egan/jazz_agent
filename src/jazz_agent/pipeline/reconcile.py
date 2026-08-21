"""Week reconciler: converge one playlist per club per booking week (ARCHITECTURE.md
section 8, ADR-008).

This is where idempotency lives. The key is deterministic
((club_id, week_start_date), enforced by a DB constraint) and the operation
is a diff against our own DB's record of the playlist, not a blind re-add --
running this twice with the same bookings must issue zero additional Spotify
writes and leave week_playlists/playlist_tracks unchanged.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from jazz_agent.core.models import PlaylistEvent, PlaylistTrack, WeekPlaylist
from jazz_agent.core.selection import order_tracks, select_album
from jazz_agent.ports.music import MusicService
from jazz_agent.ports.repository import PlaylistRepo


@dataclass(frozen=True, slots=True)
class WeekBooking:
    """One night's act, resolved or not. spotify_artist_id is None for a
    match_miss -- it still belongs in the description (so the week is
    legible even where matching failed) but contributes no tracks. show_id
    carries through onto PlaylistTrack.show_id -- provenance for feedback
    attribution and for match_misses recorded during later correction
    (pipeline/verify.py)."""

    show_date: date
    artist_name: str
    spotify_artist_id: str | None = None
    show_id: int | None = None


def reconcile_week(
    club_id: str,
    club_name: str,
    week_start: date,
    bookings: Sequence[WeekBooking],
    music: MusicService,
    playlist_repo: PlaylistRepo,
) -> WeekPlaylist:
    ordered = _dedupe_chronologically(bookings)
    matched = [b for b in ordered if b.spotify_artist_id is not None]

    desired_track_ids, track_meta = _build_desired_tracks(matched, music)

    week_end = week_start + timedelta(days=6)
    title = (
        f"This Week at {club_name} - {week_start:%b} {week_start.day}-{week_end:%b} {week_end.day}"
    )
    description = ", ".join(dict.fromkeys(b.artist_name for b in ordered))

    playlist_id = playlist_repo.upsert_week_playlist(
        WeekPlaylist(
            club_id=club_id, week_start_date=week_start, title=title, description=description
        )
    )
    playlist = playlist_repo.get_week_playlist(club_id, week_start)
    assert playlist is not None

    spotify_playlist_id = playlist.spotify_playlist_id
    if spotify_playlist_id is None:
        created = music.create_playlist(title, description)
        spotify_playlist_id = created["id"]
        spotify_url = created.get("external_urls", {}).get("spotify", "")
        playlist_repo.link_spotify_playlist(playlist_id, spotify_playlist_id, spotify_url)

    existing_ids = {
        t.spotify_track_id for t in playlist_repo.tracks_for(playlist_id) if t.removed_at is None
    }
    new_track_ids = [tid for tid in desired_track_ids if tid not in existing_ids]

    if new_track_ids:
        music.add_tracks(spotify_playlist_id, new_track_ids)
        position_by_id = {tid: i for i, tid in enumerate(desired_track_ids)}
        playlist_repo.add_tracks(
            playlist_id,
            [
                PlaylistTrack(
                    week_playlist_id=playlist_id,
                    spotify_track_id=track_id,
                    spotify_album_id=track_meta[track_id]["album_id"],
                    spotify_artist_id=track_meta[track_id]["artist_id"],
                    track_name=track_meta[track_id]["name"],
                    position=position_by_id[track_id],
                    show_id=track_meta[track_id]["show_id"],
                )
                for track_id in new_track_ids
            ],
        )
        playlist_repo.record_event(
            PlaylistEvent(
                week_playlist_id=playlist_id,
                event_type="created" if not existing_ids else "tracks_added",
                detail={"track_count": len(new_track_ids)},
            )
        )

    final = playlist_repo.get_week_playlist(club_id, week_start)
    assert final is not None
    return final


def _build_desired_tracks(
    matched: Sequence[WeekBooking], music: MusicService
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """One album per artist (ADR-008's whole point), tracks in album order,
    concatenated in first-appearance order across the week."""
    track_ids: list[str] = []
    track_meta: dict[str, dict[str, Any]] = {}
    for booking in matched:
        assert booking.spotify_artist_id is not None
        albums = music.get_artist_albums(booking.spotify_artist_id)
        selection = select_album(albums)
        if selection.album is None:
            continue  # no eligible album for this artist; contributes nothing

        tracks = order_tracks(music.get_album_tracks(selection.album["id"]))
        for track in tracks:
            if track["id"] in track_meta:
                continue
            track_ids.append(track["id"])
            track_meta[track["id"]] = {
                "album_id": selection.album["id"],
                "artist_id": booking.spotify_artist_id,
                "name": track.get("name"),
                "show_id": booking.show_id,
            }
    return track_ids, track_meta


def _dedupe_chronologically(bookings: Sequence[WeekBooking]) -> list[WeekBooking]:
    """Dedupe by spotify_artist_id, falling back to a normalized name slug for
    unmatched acts, keeping only the first chronological appearance -- an
    artist playing Tuesday through Sunday contributes exactly once."""
    seen: set[str] = set()
    ordered: list[WeekBooking] = []
    for booking in sorted(bookings, key=lambda b: b.show_date):
        key = booking.spotify_artist_id or _slug(booking.artist_name)
        if key not in seen:
            seen.add(key)
            ordered.append(booking)
    return ordered


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
