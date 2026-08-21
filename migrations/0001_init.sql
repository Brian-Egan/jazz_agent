-- 0001_init.sql — Jazz Agent initial schema
--
-- Postgres 17. See docs/DATA_MODEL.md for rationale and example queries.
--
-- Design notes that matter:
--   * The log is permanent. Nothing here is ever pruned, including rows whose Spotify
--     playlist has been unfollowed. Spotify holds audio; this holds history.
--   * Taste-graph capture tables (performers, show_performers, mb_artist_edges) are
--     populated from day one even though v1 only reads them simply. Capture cannot be
--     backfilled; analysis can be added any time. See ADR-013.
--   * pgvector is installed and artists.style_embedding stays NULL in v1, so similarity
--     search is a later feature rather than a later migration.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------------------
-- Configuration
-- ---------------------------------------------------------------------------

-- Edited directly by the user. Adding a club must never require a code change,
-- which is only possible because extraction is LLM-based (ADR-003).
CREATE TABLE clubs (
    club_id        text PRIMARY KEY,
    name           text        NOT NULL,
    schedule_url   text        NOT NULL,
    render_mode    text        NOT NULL DEFAULT 'http'
                       CHECK (render_mode IN ('http', 'js')),
    -- ISO weekday the booking week starts on. 2 = Tuesday (ADR-008).
    week_start_dow smallint    NOT NULL DEFAULT 2 CHECK (week_start_dow BETWEEN 1 AND 7),
    timezone       text        NOT NULL DEFAULT 'America/New_York',
    active         boolean     NOT NULL DEFAULT true,
    notes          text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN clubs.render_mode IS
    'http = httpx with browser headers (default). js = Playwright. No club needs js today.';
COMMENT ON COLUMN clubs.notes IS
    'Site quirks worth remembering, e.g. "403s without a realistic User-Agent".';

-- ---------------------------------------------------------------------------
-- Shows and personnel
-- ---------------------------------------------------------------------------

-- One row per (club, night, act). Multiple acts on one night are multiple rows;
-- they are collapsed into a single weekly playlist by the reconciler.
CREATE TABLE shows (
    show_id         bigserial PRIMARY KEY,
    club_id         text        NOT NULL REFERENCES clubs(club_id),
    show_date       date        NOT NULL,
    set_times       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    act_name_raw    text        NOT NULL,
    act_name_norm   text        NOT NULL,
    album_mentioned text,
    raw_text        text,
    scrape_status   text        NOT NULL DEFAULT 'ok'
                        CHECK (scrape_status IN ('ok', 'partial', 'no_shows')),
    first_seen_at   timestamptz NOT NULL DEFAULT now(),
    last_seen_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT shows_club_date_act_key UNIQUE (club_id, show_date, act_name_norm)
);

COMMENT ON COLUMN shows.show_date IS
    'The date as the club labels it. A 00:30 set listed under Friday stays Friday.';
COMMENT ON COLUMN shows.raw_text IS
    'Listing text the extraction came from. Kept for audit and free-text search.';
COMMENT ON CONSTRAINT shows_club_date_act_key ON shows IS
    'Makes re-running a scrape an upsert rather than a duplicate.';

CREATE INDEX shows_club_date_idx ON shows (club_id, show_date DESC);
CREATE INDEX shows_date_idx      ON shows (show_date DESC);
CREATE INDEX shows_raw_text_fts_idx
    ON shows USING gin (to_tsvector('english', coalesce(raw_text, '')));

-- Every musician named in a listing, not just the headline act. This is the graph
-- substrate that cannot be backfilled (ADR-013), and it covers exactly the players
-- MusicBrainz is thin on: young sidemen with no discography.
CREATE TABLE performers (
    performer_id      bigserial PRIMARY KEY,
    name_norm         text NOT NULL UNIQUE,
    name_display      text NOT NULL,
    mbid              uuid,
    spotify_artist_id text,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX performers_name_trgm_idx ON performers USING gin (name_norm gin_trgm_ops);
CREATE INDEX performers_mbid_idx      ON performers (mbid) WHERE mbid IS NOT NULL;

CREATE TABLE show_performers (
    show_id      bigint  NOT NULL REFERENCES shows(show_id) ON DELETE CASCADE,
    performer_id bigint  NOT NULL REFERENCES performers(performer_id),
    instrument   text,
    is_leader    boolean NOT NULL DEFAULT false,
    PRIMARY KEY (show_id, performer_id)
);

COMMENT ON TABLE show_performers IS
    'Self-join on show_id yields the co-performance graph: who shared a bandstand.';

CREATE INDEX show_performers_performer_idx ON show_performers (performer_id);
CREATE INDEX show_performers_instrument_idx
    ON show_performers (instrument) WHERE instrument IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Matched artists
-- ---------------------------------------------------------------------------

CREATE TABLE artists (
    spotify_artist_id  text PRIMARY KEY,
    name               text        NOT NULL,
    genres             text[]      NOT NULL DEFAULT '{}',
    popularity         smallint,
    followers          integer,
    mbid               uuid,
    match_method       text        NOT NULL
                           CHECK (match_method IN ('llm_adjudicated',
                                                   'dispute_resolved',
                                                   'exact_name')),
    match_confidence   numeric(3,2) NOT NULL CHECK (match_confidence BETWEEN 0 AND 1),
    plausibility_score numeric(3,2),
    needs_review       boolean     NOT NULL DEFAULT false,
    -- Set by MusicBrainz verification, which never blocks the run (ADR-006).
    verification_state text        NOT NULL DEFAULT 'unverified'
                           CHECK (verification_state IN ('verified',
                                                         'disputed',
                                                         'unverifiable',
                                                         'unverified')),
    verified_at        timestamptz,
    match_notes        text,
    style_embedding    vector(1536),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

COMMENT ON COLUMN artists.verification_state IS
    'unverified includes "MusicBrainz was down". Never implies the match is wrong.';
COMMENT ON COLUMN artists.plausibility_score IS
    'From core/plausibility.py. Persisted so thresholds can be tuned on evidence (ADR-007).';
COMMENT ON COLUMN artists.match_notes IS
    'LLM reasoning, so a match or correction is auditable months later.';
COMMENT ON COLUMN artists.style_embedding IS
    'NULL in v1. Reserved so similarity search is a feature, not a migration (ADR-013).';

CREATE INDEX artists_name_trgm_idx  ON artists USING gin (name gin_trgm_ops);
CREATE INDEX artists_genres_idx     ON artists USING gin (genres);
CREATE INDEX artists_review_idx     ON artists (needs_review) WHERE needs_review;
CREATE INDEX artists_verification_idx ON artists (verification_state);
CREATE INDEX artists_mbid_idx       ON artists (mbid) WHERE mbid IS NOT NULL;

-- Links a night's listing to the artist it resolved to.
CREATE TABLE show_artists (
    show_id           bigint NOT NULL REFERENCES shows(show_id) ON DELETE CASCADE,
    spotify_artist_id text   NOT NULL REFERENCES artists(spotify_artist_id),
    PRIMARY KEY (show_id, spotify_artist_id)
);

-- Acts that could not be resolved. Deliberately kept: a logged miss is a useful
-- signal for manual investigation, and silently skipping loses it (PRD FR4).
CREATE TABLE match_misses (
    id            bigserial PRIMARY KEY,
    show_id       bigint      NOT NULL REFERENCES shows(show_id) ON DELETE CASCADE,
    act_name_raw  text        NOT NULL,
    reason        text        NOT NULL,
    best_guess_id text,
    best_guess_confidence numeric(3,2),
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- MusicBrainz graph
-- ---------------------------------------------------------------------------

CREATE TABLE mb_artists (
    mbid           uuid PRIMARY KEY,
    name           text NOT NULL,
    entity_type    text,               -- 'Person' | 'Group' | NULL
    disambiguation text,
    tags           text[] NOT NULL DEFAULT '{}',
    spotify_url    text,               -- the independent cross-check (ADR-006)
    fetched_at     timestamptz NOT NULL DEFAULT now(),
    raw            jsonb
);

COMMENT ON COLUMN mb_artists.entity_type IS
    'Person vs Group distinguishes a bandleader from the band named after them.';
COMMENT ON COLUMN mb_artists.spotify_url IS
    'Human-curated in MusicBrainz. Compared against our match; can be stale.';

-- Negative cache. Hits are permanent (the data barely changes); misses expire so a
-- newly added artist is eventually picked up without daily re-querying.
CREATE TABLE mb_lookup_misses (
    name_norm  text PRIMARY KEY,
    miss_until timestamptz NOT NULL,
    attempts   integer     NOT NULL DEFAULT 1,
    last_error text
);

CREATE TABLE mb_artist_edges (
    src_mbid    uuid   NOT NULL REFERENCES mb_artists(mbid) ON DELETE CASCADE,
    dst_mbid    uuid   NOT NULL,
    dst_name    text   NOT NULL,
    edge_type   text   NOT NULL,       -- 'member of band' | 'collaboration' | 'parent' | ...
    instruments text[] NOT NULL DEFAULT '{}',
    begin_date  date,
    end_date    date,
    PRIMARY KEY (src_mbid, dst_mbid, edge_type)
);

COMMENT ON TABLE mb_artist_edges IS
    'Replacement for Spotify''s withdrawn related-artists. dst_mbid is intentionally not a
     foreign key: edges are discovered before the target artist is fetched.';
COMMENT ON COLUMN mb_artist_edges.begin_date IS
    'Membership period, so "who was in that band while he was" is answerable.';

CREATE INDEX mb_artist_edges_dst_idx  ON mb_artist_edges (dst_mbid);
CREATE INDEX mb_artist_edges_type_idx ON mb_artist_edges (edge_type);

-- ---------------------------------------------------------------------------
-- Playlists
-- ---------------------------------------------------------------------------

CREATE TABLE week_playlists (
    id                  bigserial PRIMARY KEY,
    club_id             text NOT NULL REFERENCES clubs(club_id),
    week_start_date     date NOT NULL,          -- the Tuesday (ADR-008)
    spotify_playlist_id text,
    spotify_url         text,
    title               text NOT NULL,
    description         text,
    spotify_removed_at  timestamptz,            -- unfollowed; the row survives
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT week_playlists_club_week_key UNIQUE (club_id, week_start_date)
);

COMMENT ON CONSTRAINT week_playlists_club_week_key ON week_playlists IS
    'The idempotency key. Deterministic key + diff-based reconcile means re-running
     converges instead of duplicating (PRD 8.3, satisfied structurally).';
COMMENT ON COLUMN week_playlists.spotify_removed_at IS
    'Set when retention unfollows it. Spotify has no delete endpoint; the playlist still
     exists at its URI. This row is never deleted.';

CREATE INDEX week_playlists_week_idx ON week_playlists (week_start_date DESC);

CREATE TABLE playlist_tracks (
    week_playlist_id  bigint  NOT NULL REFERENCES week_playlists(id) ON DELETE CASCADE,
    spotify_track_id  text    NOT NULL,
    spotify_album_id  text    NOT NULL,
    spotify_artist_id text    NOT NULL REFERENCES artists(spotify_artist_id),
    track_name        text,
    position          integer NOT NULL,
    show_id           bigint  REFERENCES shows(show_id),
    removed_at        timestamptz,
    PRIMARY KEY (week_playlist_id, spotify_track_id)
);

COMMENT ON COLUMN playlist_tracks.show_id IS
    'Provenance: which night surfaced this track. Powers feedback attribution.';
COMMENT ON COLUMN playlist_tracks.removed_at IS
    'Set when a correction removes a wrongly matched album (ADR-011). Row is retained.';

CREATE INDEX playlist_tracks_artist_idx ON playlist_tracks (spotify_artist_id);
CREATE INDEX playlist_tracks_track_idx  ON playlist_tracks (spotify_track_id);

-- Audit trail for playlist mutations. The reconciler is additions-only except for
-- match corrections, which must be attributable.
CREATE TABLE playlist_events (
    id                bigserial PRIMARY KEY,
    week_playlist_id  bigint NOT NULL REFERENCES week_playlists(id) ON DELETE CASCADE,
    event_type        text   NOT NULL
                          CHECK (event_type IN ('created', 'tracks_added',
                                                'tracks_removed', 'correction',
                                                'unfollowed')),
    spotify_artist_id text,
    reason            text,
    detail            jsonb,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX playlist_events_playlist_idx ON playlist_events (week_playlist_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- Feedback — the compounding asset
-- ---------------------------------------------------------------------------

-- Targets an artist, track, or album; never a weekly playlist, which holds several
-- bands and many hours of music (ADR-014).
CREATE TABLE feedback (
    id               bigserial PRIMARY KEY,
    target_type      text NOT NULL CHECK (target_type IN ('artist', 'track', 'album')),
    target_id        text NOT NULL,
    sentiment        text CHECK (sentiment IN ('liked', 'disliked', 'neutral')),
    note_text        text,
    show_id          bigint REFERENCES shows(show_id),
    week_playlist_id bigint REFERENCES week_playlists(id),
    source           text NOT NULL DEFAULT 'mcp'
                         CHECK (source IN ('mcp', 'manual', 'implicit')),
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT feedback_has_content
        CHECK (sentiment IS NOT NULL OR note_text IS NOT NULL)
);

COMMENT ON TABLE feedback IS
    'Append-only in practice. The only thing the MCP server may write.';
COMMENT ON COLUMN feedback.show_id IS
    'Context, not target. Gives provenance: "liked X, heard via the Vanguard, week of Aug 18".';
COMMENT ON COLUMN feedback.source IS
    'implicit is reserved for signal derived from recently-played rather than stated.';
COMMENT ON CONSTRAINT feedback_has_content ON feedback IS
    'A feedback row with neither sentiment nor note carries no information.';

CREATE INDEX feedback_target_idx   ON feedback (target_type, target_id);
CREATE INDEX feedback_sentiment_idx ON feedback (sentiment, created_at DESC);
CREATE INDEX feedback_notes_fts_idx
    ON feedback USING gin (to_tsvector('english', coalesce(note_text, '')));
CREATE INDEX feedback_notes_trgm_idx
    ON feedback USING gin (note_text gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- Run log
-- ---------------------------------------------------------------------------

CREATE TABLE run_log (
    id          bigserial PRIMARY KEY,
    run_id      uuid NOT NULL,
    run_at      timestamptz NOT NULL DEFAULT now(),
    club_id     text REFERENCES clubs(club_id),
    outcome     text NOT NULL
                    CHECK (outcome IN ('success', 'no_shows', 'fetch_fail',
                                       'extract_fail', 'no_match', 'partial')),
    shows_found integer,
    detail      text,
    duration_ms integer
);

COMMENT ON COLUMN run_log.club_id IS
    'NULL for run-level rows. Per-club rows are written independently so one club
     failing never prevents the others from being built.';

CREATE INDEX run_log_club_time_idx ON run_log (club_id, run_at DESC);
CREATE INDEX run_log_run_idx       ON run_log (run_id);

COMMIT;
