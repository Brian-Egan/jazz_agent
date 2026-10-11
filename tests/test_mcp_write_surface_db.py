"""The MCP server's writable tables are exactly ``feedback`` and ``venue_group_members``
(AGENTS.md invariant 7, ADR-021, issue #31).

Dynamic proof against a real Postgres: call every tool function the server registers, with
arguments that make the write tools actually write, and compare a fingerprint of every table
before and after. The set of tables that changed must be exactly those two.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.artist_repo import PgArtistRepo
from jazz_agent.adapters.pg.club_repo import PgClubRepo
from jazz_agent.adapters.pg.show_repo import PgShowRepo
from jazz_agent.adapters.pg.venue_repo import PgVenueRepo
from jazz_agent.core.models import Artist, Show
from jazz_agent.mcp import tools_feedback, tools_read, tools_venues

WRITABLE = {"feedback", "venue_group_members"}


class _Music:
    def currently_playing(self) -> None:
        return None

    def recently_played(self, limit: int = 10) -> list[dict[str, Any]]:
        return []


def _fingerprints(db: ConnectionPool) -> dict[str, str]:
    with db.connection() as conn:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"
            ).fetchall()
        ]
        return {
            t: conn.execute(
                f"SELECT coalesce(md5(string_agg(x::text, ',' ORDER BY x::text)), '') FROM {t} x"  # noqa: S608
            ).fetchone()[0]  # type: ignore[index]
            for t in tables
        }


def test_calling_every_mcp_tool_changes_only_feedback_and_venue_group_members(
    db: ConnectionPool,
) -> None:
    PgVenueRepo(db).upsert_capitalbop_venue("rhizome", 7, "Rhizome")
    PgClubRepo(db)  # clubs exist; nothing else is seeded
    show_id = PgShowRepo(db).upsert_show(
        Show(club_id="cb-rhizome", show_date=date(2026, 12, 1), act_name_raw="A", act_name_norm="A")
    )
    PgArtistRepo(db).upsert_artist(
        Artist(
            spotify_artist_id="a1", name="A", match_method="llm_adjudicated", match_confidence=0.9
        )
    )
    PgArtistRepo(db).link_show_artist(show_id, "a1")
    before = _fingerprints(db)

    # Reads.
    tools_read.search_shows(db, query="A")
    tools_read.whats_playing_at(db, "cb-rhizome", date(2026, 12, 1))
    tools_read.search_notes(db, "x")
    tools_read.recent_feedback(db)
    tools_read.artist_profile(db, "A")
    tools_read.artist_connections(db, "A")
    tools_read.when_is_playing(db, "A", today=date(2026, 11, 1))
    tools_venues.list_venue_groups(db)
    tools_feedback.get_listening_candidates(db, _Music())  # type: ignore[arg-type]
    assert _fingerprints(db) == before  # reads change nothing at all

    # Writes.
    tools_feedback.record_feedback(db, "artist", "a1", "liked", None)
    added = tools_venues.add_venue_to_group(db, "Rhizome", "dc")
    tools_venues.deactivate_venue(db, "Rhizome", "dc")

    after = _fingerprints(db)
    changed = {t for t in after if after[t] != before[t]}
    assert added["status"] == "added"
    assert changed == WRITABLE
