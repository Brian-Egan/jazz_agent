"""Feedback tools: the only write surface this server has (ARCHITECTURE.md
section 11, ADR-014). get_listening_candidates is read-only; record_feedback
is the one place this server writes anything, and it writes only to the
feedback table via FeedbackRepo -- there is no path from here to club
config, playlists, matches, or the pipeline (test_mcp_write_surface.py).
"""

from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.feedback_repo import PgFeedbackRepo
from jazz_agent.core.models import Feedback
from jazz_agent.ports.music import MusicService

_VALID_TARGET_TYPES = {"artist", "track", "album"}

# target_type -> (table, column) to check existence against. Fixed internal
# map, not user input, so building SQL from it below is not an injection risk.
_RESOLUTION_TARGETS = {
    "artist": ("artists", "spotify_artist_id"),
    "track": ("playlist_tracks", "spotify_track_id"),
    "album": ("playlist_tracks", "spotify_album_id"),
}


def get_listening_candidates(pool: ConnectionPool, music: MusicService) -> dict[str, Any]:
    """Currently-playing plus the last ~10 recently-played, each joined to the
    log with club/week provenance. Anything not found in the log is returned
    and flagged, not dropped -- the model never has to guess a reference."""
    candidates = []

    current = music.currently_playing()
    current_item = (current or {}).get("item")
    if current_item:
        candidates.append(_with_provenance(pool, current_item, currently_playing=True))

    for item in music.recently_played(limit=10):
        track = item.get("track", item)
        candidates.append(_with_provenance(pool, track, currently_playing=False))

    return {"candidates": candidates}


def _with_provenance(
    pool: ConnectionPool, track: dict[str, Any], currently_playing: bool
) -> dict[str, Any]:
    track_id = track.get("id")
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT pt.week_playlist_id, pt.spotify_artist_id, wp.club_id,
                   wp.week_start_date, wp.title
            FROM playlist_tracks pt
            JOIN week_playlists wp ON wp.id = pt.week_playlist_id
            WHERE pt.spotify_track_id = %s
            ORDER BY pt.week_playlist_id DESC
            LIMIT 1
            """,
            (track_id,),
        )
        provenance = cur.fetchone()

    return {
        "spotify_track_id": track_id,
        "name": track.get("name"),
        "artists": [a.get("name") for a in track.get("artists", [])],
        "currently_playing": currently_playing,
        "in_log": provenance is not None,
        "provenance": provenance,
    }


def record_feedback(
    pool: ConnectionPool,
    target_type: str,
    target_id: str,
    sentiment: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Rejects anything but an explicit resolved target -- the model never
    guesses what "I liked that one" refers to (ADR-014)."""
    if target_type not in _VALID_TARGET_TYPES:
        raise ValueError(
            f"target_type must be one of {sorted(_VALID_TARGET_TYPES)}, got {target_type!r}"
        )
    if sentiment is None and note is None:
        raise ValueError("feedback needs a sentiment, a note, or both")
    if not _target_resolves(pool, target_type, target_id):
        raise ValueError(f"{target_type} {target_id!r} does not resolve to anything in the log")

    feedback_id = PgFeedbackRepo(pool).record_feedback(
        Feedback(target_type=target_type, target_id=target_id, sentiment=sentiment, note_text=note)
    )
    return {"feedback_id": feedback_id}


def _target_resolves(pool: ConnectionPool, target_type: str, target_id: str) -> bool:
    table, column = _RESOLUTION_TARGETS[target_type]
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT 1 FROM {table} WHERE {column} = %s LIMIT 1", (target_id,))  # noqa: S608
        return cur.fetchone() is not None
