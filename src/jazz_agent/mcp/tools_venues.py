"""Venue-group tools: the one widening of the MCP write surface (DC section 13, ADR-021).

The server may add and deactivate rows in ``venue_group_members`` for the three groups
hardcoded in ``core/dc_groups.py``, and nothing else here: no tool creates a group or a
playlist, touches tracks or matches, or triggers a run. Venue changes take effect on the next
daily run. Writes go through ``PgVenueGroupRepo``, which only ever touches that table.

A venue name is resolved before anything is written, the same discipline as feedback: exactly
one match writes, zero or several returns the candidates and writes nothing.
"""

from __future__ import annotations

import difflib
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from jazz_agent.adapters.pg.venue_group_repo import PgVenueGroupRepo
from jazz_agent.core.dc_groups import FILTER_PROFILES, GROUP_KEYS, GROUPS, is_dedicated
from jazz_agent.ports.repository import DedicatedGroupConflict

_TAKES_EFFECT = "Takes effect on the next daily run."
_MAX_CANDIDATES = 10
_TYPO_RATIO = 0.82


def _fold(text: str) -> str:
    """Accent-insensitive, case-insensitive, punctuation-insensitive form of a name."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c)
    )
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.casefold()).split())


def _venues(pool: ConnectionPool, club_ids: set[str] | None = None) -> list[dict[str, Any]]:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT club_id, name, capitalbop_slug AS slug, city_override FROM clubs "
            "WHERE source = 'capitalbop_venue' ORDER BY name"
        )
        rows = cur.fetchall()
    return [r for r in rows if club_ids is None or r["club_id"] in club_ids]


def resolve_venue(venues: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Venues matching ``query``, best first. An exact name or slug match wins outright, so
    "Hotel Heron" is not made ambiguous by "Good Fortune Rooftop Bar @ Hotel Heron"."""
    wanted = _fold(query)
    if not wanted:
        return []
    exact = [v for v in venues if wanted in (_fold(v["name"]), _fold(v["slug"] or ""))]
    if exact:
        return exact

    def score(venue: dict[str, Any]) -> float:
        names = [_fold(venue["name"]), _fold(venue["slug"] or "")]
        if any(wanted in n or all(t in n.split() for t in wanted.split()) for n in names):
            return 1.0
        return max(difflib.SequenceMatcher(None, wanted, n).ratio() for n in names)

    scored = sorted(((score(v), v) for v in venues), key=lambda sv: (-sv[0], sv[1]["name"]))
    return [v for s, v in scored if s >= _TYPO_RATIO][:_MAX_CANDIDATES]


def _candidates(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"club_id": v["club_id"], "name": v["name"], "slug": v["slug"]} for v in matches]


def list_venue_groups(pool: ConnectionPool) -> dict[str, Any]:
    """The three groups, their active member venues with filter profiles, and each playlist's
    URL (null until the first reconcile creates it)."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT m.group_key, m.club_id, c.name AS venue, m.filter_profile, m.added_via,
                   m.added_at
            FROM venue_group_members m JOIN clubs c ON c.club_id = m.club_id
            WHERE m.deactivated_at IS NULL
            ORDER BY m.group_key, c.name
            """
        )
        members = cur.fetchall()
        cur.execute("SELECT slot_key, spotify_url FROM rolling_playlists")
        urls = {r["slot_key"]: r["spotify_url"] for r in cur.fetchall()}

    return {
        "groups": [
            {
                "group": g.key,
                "dedicated": g.dedicated,
                "venues": [
                    {k: v for k, v in m.items() if k != "group_key"}
                    for m in members
                    if m["group_key"] == g.key
                ],
                "playlists": [
                    {
                        "slot_key": p.slot_key,
                        "title": p.title,
                        "public": p.is_public,
                        "url": urls.get(p.slot_key),
                    }
                    for p in g.playlists
                ],
            }
            for g in GROUPS
        ],
        "profiles": list(FILTER_PROFILES),
    }


def add_venue_to_group(
    pool: ConnectionPool, venue: str, group: str, profile: str = "standard"
) -> dict[str, Any]:
    if group not in GROUP_KEYS:
        raise ValueError(f"group must be one of {list(GROUP_KEYS)}, got {group!r}")
    if profile not in FILTER_PROFILES:
        raise ValueError(f"profile must be one of {list(FILTER_PROFILES)}, got {profile!r}")

    matches = resolve_venue(_venues(pool), venue)
    if len(matches) != 1:
        return {
            "status": "not_found" if not matches else "ambiguous",
            "message": (
                f"No CapitalBop venue matches {venue!r}; nothing was written."
                if not matches
                else f"{len(matches)} venues match {venue!r}; nothing was written. "
                "Call again with one of these exact names."
            ),
            "candidates": _candidates(matches),
        }

    chosen = matches[0]
    repo = PgVenueGroupRepo(pool)
    active = [m for m in repo.active_members() if m.club_id == chosen["club_id"]]
    for member in active:
        if member.group_key == group:
            return {
                "status": "already_member",
                "venue": chosen["name"],
                "group": group,
                "profile": member.filter_profile,
                "message": f"{chosen['name']} is already in {group} with profile "
                f"{member.filter_profile!r}. To change the profile, deactivate it and add it "
                "again.",
            }
    held = next((m.group_key for m in active if is_dedicated(m.group_key)), None)
    if held is not None and group == "dc":
        # A dedicated venue reaches only its own playlist (DC section 4); a dc membership at
        # such a venue would be silently ignored, so say so rather than accept it.
        return {
            "status": "rejected",
            "venue": chosen["name"],
            "message": f"{chosen['name']} is in dedicated group {held!r}, whose venues never "
            "reach the dc playlists. Deactivate it there first.",
        }
    try:
        member_id = repo.add_member(chosen["club_id"], group, profile, "mcp")
    except DedicatedGroupConflict as e:
        return {
            "status": "rejected",
            "venue": chosen["name"],
            "message": f"{chosen['name']} is already in dedicated group "
            f"{e.existing_group!r}; a venue may belong to at most one. Deactivate it there "
            "first.",
        }
    return {
        "status": "added",
        "venue": chosen["name"],
        "group": group,
        "profile": profile,
        "member_id": member_id,
        "message": f"Added {chosen['name']} to {group} ({profile}). {_TAKES_EFFECT}",
    }


def deactivate_venue(pool: ConnectionPool, venue: str, group: str) -> dict[str, Any]:
    if group not in GROUP_KEYS:
        raise ValueError(f"group must be one of {list(GROUP_KEYS)}, got {group!r}")

    repo = PgVenueGroupRepo(pool)
    members = {m.club_id for m in repo.active_members(group)}
    # Resolved among this group's active venues: that is the set the user means.
    matches = resolve_venue(_venues(pool, members), venue)
    if len(matches) != 1:
        return {
            "status": "not_found" if not matches else "ambiguous",
            "message": (
                f"No active member of {group} matches {venue!r}; nothing was changed."
                if not matches
                else f"{len(matches)} members of {group} match {venue!r}; nothing was changed."
            ),
            "candidates": _candidates(matches),
        }
    chosen = matches[0]
    repo.deactivate_member(chosen["club_id"], group, datetime.now(UTC))
    return {
        "status": "deactivated",
        "venue": chosen["name"],
        "group": group,
        "message": f"Deactivated {chosen['name']} in {group}; the history is kept and it can be "
        f"added again. {_TAKES_EFFECT}",
    }
