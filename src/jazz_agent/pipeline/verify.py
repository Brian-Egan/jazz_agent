"""MusicBrainz verification, dispute resolution, and correction-driven playlist
repair (ARCHITECTURE.md section 6 stages 5-7; ADR-006, ADR-011).

MusicBrainz is verification, never a precondition (AGENTS.md invariant 3): if
MusicBrainz raises, times out, or returns nothing, verify_artist() changes
only verification_state -- the artist's match, and any playlist built from
it, are otherwise byte-identical to the MusicBrainz-up case. This module is
always a follow-up pass over an already-built playlist, never something the
initial build waits on.

MusicBrainz is not automatically authoritative: its stored Spotify links are
human-curated but can be stale, or point at a band's page when a member was
meant. Disputes are resolved by one further LLM call, not by trusting
whichever side MusicBrainz is on.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import anthropic

from jazz_agent.core.models import (
    Artist,
    MatchMiss,
    MbArtist,
    MbArtistEdge,
    PlaylistEvent,
    PlaylistTrack,
    WeekPlaylist,
)
from jazz_agent.core.selection import order_tracks, select_album
from jazz_agent.ports.graph import GraphService
from jazz_agent.ports.music import MusicService
from jazz_agent.ports.repository import ArtistRepo, PlaylistRepo, ShowRepo

_DISPUTE_TOOL_NAME = "resolve_dispute"
_DISPUTE_TOOL_SCHEMA: dict[str, Any] = {
    "name": _DISPUTE_TOOL_NAME,
    "description": (
        "Decide which Spotify artist is correct when MusicBrainz's stored link "
        "disagrees with an existing match."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "winner": {
                "type": "string",
                "enum": ["ours", "musicbrainz", "none"],
                "description": "'none' if neither is clearly right",
            },
            "reasoning": {"type": "string"},
        },
        "required": ["winner", "reasoning"],
    },
}

_DISPUTE_SYSTEM_PROMPT = (
    "MusicBrainz's stored Spotify link for an artist disagrees with a match "
    "already made independently. MusicBrainz links are human-curated but can "
    "be stale, or point at a band's page when a specific member was meant -- "
    "it is not automatically authoritative. Decide which Spotify artist is "
    "actually correct given the entity type, tags, and membership context, or "
    "'none' if neither is clearly right."
)


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """verification_state is always set; mb_artist is set whenever MusicBrainz
    returned something (verified/disputed/unverifiable), None for unverified."""

    verification_state: str  # 'verified' | 'disputed' | 'unverifiable' | 'unverified'
    mb_artist: MbArtist | None = None


def verify_artist(artist: Artist, graph: GraphService) -> VerificationOutcome:
    """Never raises. Any MusicBrainz failure here -- an exception, not just a
    None result -- leaves the artist's match untouched; only the returned
    verification_state differs from the healthy case."""
    try:
        mb_artist = graph.resolve_mbid(artist.name)
    except Exception:
        return VerificationOutcome(verification_state="unverified")

    if mb_artist is None:
        return VerificationOutcome(verification_state="unverified")

    if mb_artist.spotify_url is None:
        return VerificationOutcome(verification_state="unverifiable", mb_artist=mb_artist)

    if _spotify_artist_id(mb_artist.spotify_url) == artist.spotify_artist_id:
        return VerificationOutcome(verification_state="verified", mb_artist=mb_artist)

    return VerificationOutcome(verification_state="disputed", mb_artist=mb_artist)


def apply_verification(
    artist: Artist, outcome: VerificationOutcome, verified_at: datetime
) -> Artist:
    confidence = 1.0 if outcome.verification_state == "verified" else artist.match_confidence
    return dataclasses.replace(
        artist,
        mbid=outcome.mb_artist.mbid if outcome.mb_artist else artist.mbid,
        verification_state=outcome.verification_state,
        verified_at=verified_at,
        match_confidence=confidence,
    )


def verify_and_correct(
    artist: Artist,
    playlist: WeekPlaylist,
    graph: GraphService,
    music: MusicService,
    artist_repo: ArtistRepo,
    playlist_repo: PlaylistRepo,
    show_repo: ShowRepo,
    llm_client: anthropic.Anthropic,
    model: str,
    verified_at: datetime,
) -> VerificationOutcome:
    """Verify one artist's match, then correct the given playlist if -- and
    only if -- MusicBrainz raised a genuine dispute that resolution upholds.
    Never raises; a MusicBrainz failure results in verification_state
    'unverified' and no playlist change whatsoever."""
    outcome = verify_artist(artist, graph)
    updated = apply_verification(artist, outcome, verified_at)
    artist_repo.upsert_artist(updated)

    if outcome.verification_state != "disputed":
        return outcome

    assert outcome.mb_artist is not None
    edges = _safe_edges_for(graph, outcome.mb_artist.mbid)
    verdict = _resolve_dispute(updated, outcome.mb_artist, edges, llm_client, model)

    if verdict["winner"] == "ours":
        artist_repo.upsert_artist(dataclasses.replace(updated, match_notes=verdict["reasoning"]))
    elif verdict["winner"] == "musicbrainz":
        _apply_correction(
            updated,
            outcome.mb_artist,
            verdict["reasoning"],
            playlist,
            music,
            artist_repo,
            playlist_repo,
            verified_at,
        )
    else:
        _apply_removal_as_miss(
            updated, verdict["reasoning"], playlist, music, playlist_repo, show_repo
        )

    return outcome


def _safe_edges_for(graph: GraphService, mbid: str) -> list[MbArtistEdge]:
    try:
        return graph.edges_for(mbid)
    except Exception:
        return []


def _resolve_dispute(
    our_artist: Artist,
    mb_artist: MbArtist,
    edges: Sequence[MbArtistEdge],
    llm_client: anthropic.Anthropic,
    model: str,
) -> dict[str, Any]:
    mb_spotify_id = _spotify_artist_id(mb_artist.spotify_url) if mb_artist.spotify_url else None
    prompt = (
        f"Our match: {our_artist.name!r}, spotify_artist_id={our_artist.spotify_artist_id}, "
        f"genres={list(our_artist.genres)}\n\n"
        f"MusicBrainz: {mb_artist.name!r}, entity_type={mb_artist.entity_type}, "
        f"tags={list(mb_artist.tags)}, stored spotify_artist_id={mb_spotify_id}\n\n"
        "Membership/collaboration edges from MusicBrainz:\n"
        + "\n".join(f"- {e.edge_type}: {e.dst_name}" for e in edges)
    )
    response = llm_client.messages.create(
        model=model,
        max_tokens=1024,
        system=_DISPUTE_SYSTEM_PROMPT,
        tools=[_DISPUTE_TOOL_SCHEMA],
        tool_choice={"type": "tool", "name": _DISPUTE_TOOL_NAME},
        messages=[{"role": "user", "content": prompt}],
    )  # type: ignore[call-overload]

    for block in response.content:
        if block.type == "tool_use" and block.name == _DISPUTE_TOOL_NAME:
            result: dict[str, Any] = block.input
            return result
    raise ValueError("Model response contained no resolve_dispute tool call")


def _apply_correction(
    old_artist: Artist,
    mb_artist: MbArtist,
    reasoning: str,
    playlist: WeekPlaylist,
    music: MusicService,
    artist_repo: ArtistRepo,
    playlist_repo: PlaylistRepo,
    verified_at: datetime,
) -> None:
    assert playlist.id is not None
    new_spotify_artist_id = _spotify_artist_id(mb_artist.spotify_url or "")
    if new_spotify_artist_id is None:
        return

    removed = _remove_live_tracks(playlist, old_artist.spotify_artist_id, music, playlist_repo)
    if not removed:
        return  # nothing live to correct

    new_artist = Artist(
        spotify_artist_id=new_spotify_artist_id,
        name=mb_artist.name,
        match_method="dispute_resolved",
        match_confidence=1.0,
        mbid=mb_artist.mbid,
        verification_state="verified",
        verified_at=verified_at,
        match_notes=reasoning,
    )
    artist_repo.upsert_artist(new_artist)

    playlist_repo.record_event(
        PlaylistEvent(
            week_playlist_id=playlist.id,
            event_type="tracks_removed",
            spotify_artist_id=old_artist.spotify_artist_id,
            reason="dispute_resolved",
            detail={"track_count": len(removed)},
        )
    )

    added = _add_new_album_tracks(
        playlist, new_spotify_artist_id, removed[0].show_id, music, playlist_repo
    )
    if added:
        playlist_repo.record_event(
            PlaylistEvent(
                week_playlist_id=playlist.id,
                event_type="tracks_added",
                spotify_artist_id=new_spotify_artist_id,
                reason="dispute_resolved",
                detail={"track_count": added},
            )
        )

    playlist_repo.record_event(
        PlaylistEvent(
            week_playlist_id=playlist.id,
            event_type="correction",
            spotify_artist_id=new_spotify_artist_id,
            reason=reasoning,
        )
    )


def _apply_removal_as_miss(
    old_artist: Artist,
    reasoning: str,
    playlist: WeekPlaylist,
    music: MusicService,
    playlist_repo: PlaylistRepo,
    show_repo: ShowRepo,
) -> None:
    assert playlist.id is not None
    removed = _remove_live_tracks(playlist, old_artist.spotify_artist_id, music, playlist_repo)
    if not removed:
        return

    show_id = next((t.show_id for t in removed if t.show_id is not None), None)
    if show_id is not None:
        show_repo.record_match_miss(
            MatchMiss(
                show_id=show_id,
                act_name_raw=old_artist.name,
                reason="DISPUTE_RESOLVED_NONE",
                best_guess_id=old_artist.spotify_artist_id,
                best_guess_confidence=0.0,
            )
        )

    playlist_repo.record_event(
        PlaylistEvent(
            week_playlist_id=playlist.id,
            event_type="tracks_removed",
            spotify_artist_id=old_artist.spotify_artist_id,
            reason=reasoning,
            detail={"track_count": len(removed)},
        )
    )
    playlist_repo.record_event(
        PlaylistEvent(
            week_playlist_id=playlist.id,
            event_type="correction",
            spotify_artist_id=old_artist.spotify_artist_id,
            reason=reasoning,
        )
    )


def _remove_live_tracks(
    playlist: WeekPlaylist, spotify_artist_id: str, music: MusicService, playlist_repo: PlaylistRepo
) -> list[PlaylistTrack]:
    assert playlist.id is not None
    live = [
        t
        for t in playlist_repo.tracks_for(playlist.id)
        if t.spotify_artist_id == spotify_artist_id and t.removed_at is None
    ]
    if not live:
        return []
    if playlist.spotify_playlist_id is not None:
        music.remove_tracks(playlist.spotify_playlist_id, [t.spotify_track_id for t in live])
    for track in live:
        playlist_repo.remove_track(playlist.id, track.spotify_track_id)
    return live


def _add_new_album_tracks(
    playlist: WeekPlaylist,
    spotify_artist_id: str,
    show_id: int | None,
    music: MusicService,
    playlist_repo: PlaylistRepo,
) -> int:
    assert playlist.id is not None
    albums = music.get_artist_albums(spotify_artist_id)
    selection = select_album(albums)
    if selection.album is None:
        return 0

    tracks = order_tracks(music.get_album_tracks(selection.album["id"]))
    if not tracks:
        return 0

    existing = playlist_repo.tracks_for(playlist.id)
    start_position = max((t.position for t in existing), default=-1) + 1
    if playlist.spotify_playlist_id is not None:
        music.add_tracks(playlist.spotify_playlist_id, [t["id"] for t in tracks])
    playlist_repo.add_tracks(
        playlist.id,
        [
            PlaylistTrack(
                week_playlist_id=playlist.id,
                spotify_track_id=track["id"],
                spotify_album_id=selection.album["id"],
                spotify_artist_id=spotify_artist_id,
                track_name=track.get("name"),
                position=start_position + i,
                show_id=show_id,
            )
            for i, track in enumerate(tracks)
        ],
    )
    return len(tracks)


def _spotify_artist_id(spotify_url: str) -> str | None:
    match = re.search(r"open\.spotify\.com/artist/([A-Za-z0-9]+)", spotify_url)
    return match.group(1) if match else None
