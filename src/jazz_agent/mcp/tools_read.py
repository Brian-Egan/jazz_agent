"""Read tools: search, lookup, and profile queries over the log (ARCHITECTURE.md
section 11). SELECT-only -- test_mcp_write_surface.py mechanically proves
nothing in this module writes anything.

Known limitation: co-performance in artist_connections() matches performers
by normalized name (performers.name_norm), not performers.spotify_artist_id.
That column exists in the schema for exactly this join but nothing in the
pipeline populates it yet -- record_performers (adapters/pg/show_repo.py)
only ever sets name_norm/name_display. Name matching is what
DATA_MODEL.md's own example query uses, so this isn't a regression, but
wiring up spotify_artist_id on performers would make it more precise.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def _normalize_name(name: str) -> str:
    # Must match adapters/pg/show_repo.py's _normalize_performer_name exactly,
    # since this is how we look up the row it wrote.
    return " ".join(name.strip().lower().split())


def search_shows(
    pool: ConnectionPool,
    query: str | None = None,
    club: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    conditions = []
    params: dict[str, Any] = {"limit": limit}
    if query:
        conditions.append(
            "(to_tsvector('english', coalesce(s.raw_text, ''))"
            " @@ websearch_to_tsquery('english', %(query)s)"
            " OR s.act_name_raw %% %(query)s)"
        )
        params["query"] = query
    if club:
        conditions.append("s.club_id = %(club)s")
        params["club"] = club
    if date_from:
        conditions.append("s.show_date >= %(date_from)s")
        params["date_from"] = date_from
    if date_to:
        conditions.append("s.show_date <= %(date_to)s")
        params["date_to"] = date_to

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT s.show_id, s.club_id, c.name AS club_name, s.show_date,
                   s.act_name_raw, s.act_name_norm
            FROM shows s
            JOIN clubs c ON c.club_id = s.club_id
            {where}
            ORDER BY s.show_date DESC
            LIMIT %(limit)s
            """,
            params,
        )
        return cur.fetchall()


def whats_playing_at(pool: ConnectionPool, club: str, on_date: date) -> list[dict[str, Any]]:
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT s.show_id, s.act_name_raw, s.act_name_norm, s.set_times,
                   a.spotify_artist_id, a.name AS matched_artist_name,
                   a.verification_state
            FROM shows s
            LEFT JOIN show_artists sa ON sa.show_id = s.show_id
            LEFT JOIN artists a ON a.spotify_artist_id = sa.spotify_artist_id
            WHERE s.club_id = %s AND s.show_date = %s
            ORDER BY s.act_name_raw
            """,
            (club, on_date),
        )
        return cur.fetchall()


def search_notes(pool: ConnectionPool, query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Stemming (tsvector) catches inflectional variants (piano/pianos); word
    trigram similarity catches both misspellings (guitarest/guitarist) and
    close-but-not-stemmed related words (pianist/piano -- Postgres's English
    stemmer does not relate these two, verified directly against a running
    Postgres before writing this query). The default word_similarity
    threshold (0.6) is too strict for pianist/piano (0.5); 0.3 was verified
    to still score a genuinely unrelated query at 0.0, so it stays selective.
    Both tsvector and pg_trgm are indexed on feedback.note_text
    (migrations/0001_init.sql).
    """
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, target_type, target_id, sentiment, note_text, created_at,
                   word_similarity(%(query)s, coalesce(note_text, '')) AS trgm_score
            FROM feedback
            WHERE to_tsvector('english', coalesce(note_text, ''))
                      @@ websearch_to_tsquery('english', %(query)s)
               OR word_similarity(%(query)s, coalesce(note_text, '')) > 0.3
            ORDER BY trgm_score DESC NULLS LAST, created_at DESC
            LIMIT %(limit)s
            """,
            {"query": query, "limit": limit},
        )
        return cur.fetchall()


def recent_feedback(
    pool: ConnectionPool, sentiment: str | None = None, limit: int = 20
) -> list[dict[str, Any]]:
    conditions = []
    params: dict[str, Any] = {"limit": limit}
    if sentiment:
        conditions.append("sentiment = %(sentiment)s")
        params["sentiment"] = sentiment
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"""
            SELECT id, target_type, target_id, sentiment, note_text, created_at
            FROM feedback
            {where}
            ORDER BY created_at DESC
            LIMIT %(limit)s
            """,
            params,
        )
        return cur.fetchall()


def _resolve_artist_by_name(cur: Any, artist_name: str) -> dict[str, Any] | None:
    cur.execute(
        """
        SELECT spotify_artist_id, name, genres, mbid, verification_state
        FROM artists
        WHERE lower(name) = lower(%(name)s)
        ORDER BY match_confidence DESC
        LIMIT 1
        """,
        {"name": artist_name},
    )
    row = cur.fetchone()
    if row:
        return row  # type: ignore[no-any-return]
    cur.execute(
        """
        SELECT spotify_artist_id, name, genres, mbid, verification_state
        FROM artists
        WHERE name %% %(name)s
        ORDER BY similarity(name, %(name)s) DESC
        LIMIT 1
        """,
        {"name": artist_name},
    )
    return cur.fetchone()  # type: ignore[no-any-return]


def artist_profile(pool: ConnectionPool, artist_name: str) -> dict[str, Any] | None:
    """Genres, MB tags, instruments, groups, collaborators, and feedback --
    one call, so "tell me about this pianist" isn't six round-trips."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        artist = _resolve_artist_by_name(cur, artist_name)
        if artist is None:
            return None

        mb_tags: list[str] = []
        groups_and_collaborators: list[dict[str, Any]] = []
        if artist["mbid"]:
            cur.execute("SELECT tags FROM mb_artists WHERE mbid = %s", (artist["mbid"],))
            mb_row = cur.fetchone()
            if mb_row:
                mb_tags = mb_row["tags"]
            cur.execute(
                "SELECT dst_name, edge_type FROM mb_artist_edges WHERE src_mbid = %s",
                (artist["mbid"],),
            )
            groups_and_collaborators = cur.fetchall()

        cur.execute(
            """
            SELECT DISTINCT sp.instrument
            FROM performers p
            JOIN show_performers sp ON sp.performer_id = p.performer_id
            WHERE p.name_norm = %s AND sp.instrument IS NOT NULL
            """,
            (_normalize_name(artist["name"]),),
        )
        instruments = [r["instrument"] for r in cur.fetchall()]

        cur.execute(
            """
            SELECT sentiment, note_text, created_at FROM feedback
            WHERE target_type = 'artist' AND target_id = %s
            ORDER BY created_at DESC
            """,
            (artist["spotify_artist_id"],),
        )
        feedback = cur.fetchall()

        return {
            "spotify_artist_id": artist["spotify_artist_id"],
            "name": artist["name"],
            "genres": artist["genres"],
            "verification_state": artist["verification_state"],
            "mb_tags": mb_tags,
            "instruments": instruments,
            "groups_and_collaborators": groups_and_collaborators,
            "feedback": feedback,
        }


def artist_connections(
    pool: ConnectionPool, artist_name: str, depth: int = 1
) -> list[dict[str, Any]]:
    """Connections from both mb_artist_edges and co-performance -- each result
    says which source it came from, since coverage differs sharply."""
    with pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        artist = _resolve_artist_by_name(cur, artist_name)
        if artist is None:
            return []

        connections: list[dict[str, Any]] = []

        if artist["mbid"]:
            cur.execute(
                """
                SELECT dst_name, edge_type, instruments, begin_date, end_date
                FROM mb_artist_edges WHERE src_mbid = %s
                """,
                (artist["mbid"],),
            )
            for row in cur.fetchall():
                connections.append({**row, "source": "musicbrainz"})

        cur.execute(
            """
            SELECT DISTINCT p2.name_display AS dst_name, sp2.instrument
            FROM show_performers sp1
            JOIN performers p1 ON p1.performer_id = sp1.performer_id
            JOIN show_performers sp2
                ON sp2.show_id = sp1.show_id AND sp2.performer_id <> sp1.performer_id
            JOIN performers p2 ON p2.performer_id = sp2.performer_id
            WHERE p1.name_norm = %s
            """,
            (_normalize_name(artist["name"]),),
        )
        for row in cur.fetchall():
            connections.append({**row, "source": "co_performance"})

        return connections
