"""FastMCP server: Google-delegated OAuth, single-email allow-list, Caddy
terminates TLS in front of this (ARCHITECTURE.md section 11, ADR-012).

A static bearer token does not work here: claude.ai and Claude Desktop
custom connectors require OAuth 2.1 with dynamic client registration and
reject bearer tokens (ADR-012, supersedes PRD 8.2). No hand-rolled
authorization server -- fastmcp's GoogleProvider supplies the OAuth 2.1 +
DCR surface claude.ai requires while Google performs the actual
authentication. (ARCHITECTURE.md's "OAuthProxy delegating login to Google"
predates this class name -- GoogleProvider is fastmcp's purpose-built,
OAuthProxy-based Google integration, i.e. exactly that.)

The email allow-list is this project's own check, applied to every tool via
allowed_emails_check(): FastMCP/Google only prove *who* signed in, not
whether that person is allowed to use this server.
"""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.utilities.authorization import AuthCheck, AuthContext
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.run_repo import PgRunRepo
from jazz_agent.config import Config
from jazz_agent.mcp import tools_feedback, tools_read
from jazz_agent.ports.music import MusicService


def allowed_emails_check(allowed_emails: tuple[str, ...]) -> AuthCheck:
    allowed = {e.strip().lower() for e in allowed_emails if e.strip()}

    def check(ctx: AuthContext) -> bool:
        if ctx.token is None:
            return False
        email = (ctx.token.claims or {}).get("email", "")
        return isinstance(email, str) and email.strip().lower() in allowed

    return check


def build_server(config: Config, pool: ConnectionPool, music: MusicService) -> FastMCP:
    auth_provider = GoogleProvider(
        client_id=config.google_client_id,
        client_secret=config.google_client_secret,
        base_url=config.mcp_public_url,
        required_scopes=["openid", "email"],
    )
    mcp: FastMCP = FastMCP("jazz-agent", auth=auth_provider)
    check = allowed_emails_check(config.mcp_allowed_emails)

    @mcp.tool(auth=check)
    def search_shows(
        query: str | None = None,
        club: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict[str, Any]]:
        """Structured and fuzzy show lookup."""
        return tools_read.search_shows(
            pool, query=query, club=club, date_from=date_from, date_to=date_to
        )

    @mcp.tool(auth=check)
    def whats_playing_at(club: str, on_date: date) -> list[dict[str, Any]]:
        """What played at a given club on a given date."""
        return tools_read.whats_playing_at(pool, club, on_date)

    @mcp.tool(auth=check)
    def search_notes(query: str) -> list[dict[str, Any]]:
        """Free text over notes and listings (stemmed and typo-tolerant)."""
        return tools_read.search_notes(pool, query)

    @mcp.tool(auth=check)
    def recent_feedback(sentiment: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Recently recorded feedback, optionally filtered by sentiment."""
        return tools_read.recent_feedback(pool, sentiment=sentiment, limit=limit)

    @mcp.tool(auth=check)
    def artist_profile(artist_name: str) -> dict[str, Any] | None:
        """Genres, MusicBrainz tags, instruments, groups, collaborators, and
        feedback history for one artist, in a single call."""
        return tools_read.artist_profile(pool, artist_name)

    @mcp.tool(auth=check)
    def artist_connections(artist_name: str, depth: int = 1) -> list[dict[str, Any]]:
        """Artists connected to this one via MusicBrainz or co-performance --
        each result says which source it came from."""
        return tools_read.artist_connections(pool, artist_name, depth=depth)

    @mcp.tool(auth=check)
    def get_run_health(days: int = 7) -> list[dict[str, Any]]:
        """Per-club pipeline run outcomes, most recent first."""
        return [dataclasses.asdict(r) for r in PgRunRepo(pool).recent_runs(days=days)]

    @mcp.tool(auth=check)
    def get_listening_candidates() -> dict[str, Any]:
        """Currently-playing plus recently-played, joined to the log. Read-only;
        call record_feedback with a target from here to attach sentiment."""
        return tools_feedback.get_listening_candidates(pool, music)

    @mcp.tool(auth=check)
    def record_feedback(
        target_type: Literal["artist", "track", "album"],
        target_id: str,
        sentiment: Literal["liked", "disliked", "neutral"] | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Attach sentiment and/or a note to an artist, track, or album --
        never a weekly playlist (ADR-014). sentiment must be exactly 'liked',
        'disliked', or 'neutral' -- no other values, synonyms, or intensity
        variants (e.g. not 'dislike', 'negative', 'thumbs_down'). A note alone,
        with no sentiment, is also valid. Rejects a target that doesn't resolve."""
        return tools_feedback.record_feedback(pool, target_type, target_id, sentiment, note)

    return mcp


def main() -> int:
    """Entry point: `uv run python -m jazz_agent.mcp.server`. Long-running,
    behind Caddy for TLS termination. Wires real adapters from config.py --
    the only module that reads the environment (AGENTS.md invariant 2)."""
    from jazz_agent.adapters.pg.pool import make_pool
    from jazz_agent.adapters.spotify import SpotifyClient
    from jazz_agent.config import load_config

    config = load_config()
    pool = make_pool(config.database_url)
    music = SpotifyClient(
        client_id=config.spotify_client_id,
        client_secret=config.spotify_client_secret,
        refresh_token=config.spotify_refresh_token,
    )
    mcp = build_server(config, pool, music)
    mcp.run(transport="http", host=config.mcp_host, port=config.mcp_port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
