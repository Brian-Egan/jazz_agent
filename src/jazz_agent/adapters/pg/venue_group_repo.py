"""Postgres implementation of ports.repository.VenueGroupRepo."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from jazz_agent.core.dc_groups import DEDICATED_GROUP_KEYS, FILTER_PROFILES, GROUP_KEYS
from jazz_agent.core.models import VenueGroupMember
from jazz_agent.ports.repository import DedicatedGroupConflict

_COLUMNS = "id, club_id, group_key, filter_profile, added_via, added_at, deactivated_at"


def _row_to_member(row: dict[str, Any]) -> VenueGroupMember:
    return VenueGroupMember(**row)


class PgVenueGroupRepo:
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def add_member(self, club_id: str, group_key: str, filter_profile: str, added_via: str) -> int:
        if group_key not in GROUP_KEYS:
            raise ValueError(f"unknown group {group_key!r}; valid groups: {', '.join(GROUP_KEYS)}")
        if filter_profile not in FILTER_PROFILES:
            raise ValueError(
                f"unknown filter profile {filter_profile!r}; valid: {', '.join(FILTER_PROFILES)}"
            )

        with self._pool.connection() as conn, conn.cursor() as cur:
            # Serialise concurrent adds for one venue. The partial unique index covers only
            # (club_id, group_key), so "one dedicated group per venue" is a cross-row rule
            # the database cannot express; the row lock on the club closes the
            # check-then-insert race inside this transaction (DATA_MODEL section 7).
            cur.execute("SELECT source FROM clubs WHERE club_id = %s FOR UPDATE", (club_id,))
            club = cur.fetchone()
            if club is None or club[0] != "capitalbop_venue":
                raise ValueError(f"{club_id!r} is not a CapitalBop venue")

            cur.execute(
                """
                SELECT id FROM venue_group_members
                WHERE club_id = %s AND group_key = %s AND deactivated_at IS NULL
                """,
                (club_id, group_key),
            )
            existing = cur.fetchone()
            if existing is not None:
                return existing[0]  # type: ignore[no-any-return]

            if group_key in DEDICATED_GROUP_KEYS:
                cur.execute(
                    """
                    SELECT group_key FROM venue_group_members
                    WHERE club_id = %s AND deactivated_at IS NULL
                      AND group_key = ANY(%s)
                    """,
                    (club_id, list(DEDICATED_GROUP_KEYS)),
                )
                other = cur.fetchone()
                if other is not None:
                    raise DedicatedGroupConflict(club_id, other[0])

            cur.execute(
                """
                INSERT INTO venue_group_members (club_id, group_key, filter_profile, added_via)
                VALUES (%s, %s, %s, %s)
                RETURNING id
                """,
                (club_id, group_key, filter_profile, added_via),
            )
            row = cur.fetchone()
            assert row is not None
            return row[0]  # type: ignore[no-any-return]

    def deactivate_member(self, club_id: str, group_key: str, at: datetime) -> bool:
        # Stamp, never delete (invariant 5).
        with self._pool.connection() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE venue_group_members SET deactivated_at = %s
                WHERE club_id = %s AND group_key = %s AND deactivated_at IS NULL
                """,
                (at, club_id, group_key),
            )
            return cur.rowcount > 0

    def active_members(self, group_key: str | None = None) -> list[VenueGroupMember]:
        with self._pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                f"""
                SELECT {_COLUMNS} FROM venue_group_members
                WHERE deactivated_at IS NULL AND (%s::text IS NULL OR group_key = %s)
                ORDER BY group_key, club_id
                """,
                (group_key, group_key),
            )
            return [_row_to_member(row) for row in cur.fetchall()]
