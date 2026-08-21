"""Environment configuration.

This is the only module permitted to read ``os.environ`` (see AGENTS.md invariant 2).
Adapters receive their configuration as constructor arguments, sourced from a
:class:`Config` built here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Loads .env into the process environment on import, once. Searches the
# current directory and its parents for .env, so this works whether you run
# `uv run python scripts/spotify_auth.py` or `uv run python -m
# jazz_agent.pipeline.daily` from the repo root. Never overrides a variable
# that's already set in the real environment (override=False, the default)
# -- a production deployment setting secrets via systemd Environment= or a
# container's env, rather than a committed-nowhere .env file, still wins.
load_dotenv()


@dataclass(frozen=True)
class Config:
    # ---------- Database ----------
    database_url: str
    postgres_user: str
    postgres_password: str
    postgres_db: str

    # ---------- Spotify ----------
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    spotify_refresh_token: str

    # ---------- Anthropic (extraction + adjudication) ----------
    anthropic_api_key: str
    extraction_model: str
    adjudication_model: str

    # ---------- MusicBrainz ----------
    musicbrainz_user_agent: str
    musicbrainz_timeout_seconds: float
    musicbrainz_miss_ttl_days: int

    # ---------- Pipeline ----------
    run_hour_et: int
    horizon_weeks_ahead: int
    retain_weeks_past: int
    tracks_match_accept: float
    tracks_match_review: float
    dry_run: bool

    # ---------- MCP server ----------
    mcp_host: str
    mcp_port: int
    mcp_public_url: str
    google_client_id: str
    google_client_secret: str
    mcp_allowed_emails: tuple[str, ...]

    # ---------- Alerting ----------
    ntfy_topic: str
    ntfy_server: str
    chronic_failure_threshold: int


def _bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false")


def load_config() -> Config:
    """Read every variable documented in ``.env.example`` from the process environment.

    Values with a real default in ``.env.example`` (ports, model names, thresholds)
    fall back to that default here. Values that are blank in ``.env.example``
    (credentials, API keys) fall back to an empty string or tuple rather than
    raising, so tooling that only needs a subset of config (e.g. the migration
    runner, which only needs ``database_url``) can call this without a full
    production ``.env``. Adapters that require a given field are responsible for
    failing when it is actually missing.
    """
    env = os.environ
    return Config(
        database_url=env.get(
            "DATABASE_URL", "postgresql://jazz_agent:CHANGEME@localhost:5432/jazz_agent"
        ),
        postgres_user=env.get("POSTGRES_USER", "jazz_agent"),
        postgres_password=env.get("POSTGRES_PASSWORD", "CHANGEME"),
        postgres_db=env.get("POSTGRES_DB", "jazz_agent"),
        spotify_client_id=env.get("SPOTIFY_CLIENT_ID", ""),
        spotify_client_secret=env.get("SPOTIFY_CLIENT_SECRET", ""),
        spotify_redirect_uri=env.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback"),
        spotify_refresh_token=env.get("SPOTIFY_REFRESH_TOKEN", ""),
        anthropic_api_key=env.get("ANTHROPIC_API_KEY", ""),
        extraction_model=env.get("EXTRACTION_MODEL", "claude-haiku-4-5"),
        adjudication_model=env.get("ADJUDICATION_MODEL", "claude-haiku-4-5"),
        musicbrainz_user_agent=env.get(
            "MUSICBRAINZ_USER_AGENT", "jazz_agent/1.0 ( you@example.com )"
        ),
        musicbrainz_timeout_seconds=float(env.get("MUSICBRAINZ_TIMEOUT_SECONDS", "2")),
        musicbrainz_miss_ttl_days=int(env.get("MUSICBRAINZ_MISS_TTL_DAYS", "30")),
        run_hour_et=int(env.get("RUN_HOUR_ET", "13")),
        horizon_weeks_ahead=int(env.get("HORIZON_WEEKS_AHEAD", "4")),
        retain_weeks_past=int(env.get("RETAIN_WEEKS_PAST", "1")),
        tracks_match_accept=float(env.get("TRACKS_MATCH_ACCEPT", "0.80")),
        tracks_match_review=float(env.get("TRACKS_MATCH_REVIEW", "0.50")),
        dry_run=_bool(env.get("DRY_RUN"), False),
        mcp_host=env.get("MCP_HOST", "0.0.0.0"),
        mcp_port=int(env.get("MCP_PORT", "8080")),
        mcp_public_url=env.get("MCP_PUBLIC_URL", ""),
        google_client_id=env.get("GOOGLE_CLIENT_ID", ""),
        google_client_secret=env.get("GOOGLE_CLIENT_SECRET", ""),
        mcp_allowed_emails=tuple(
            email.strip() for email in env.get("MCP_ALLOWED_EMAILS", "").split(",") if email.strip()
        ),
        ntfy_topic=env.get("NTFY_TOPIC", ""),
        ntfy_server=env.get("NTFY_SERVER", "https://ntfy.sh"),
        chronic_failure_threshold=int(env.get("CHRONIC_FAILURE_THRESHOLD", "3")),
    )
