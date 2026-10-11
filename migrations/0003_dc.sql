-- 0003_dc.sql -- Washington, D.C. extension (docs/DC.md, DATA_MODEL.md section 7).
--
-- Non-destructive: new tables and columns, plus two relaxed NOT NULL constraints
-- (clubs.schedule_url, playlist_events.week_playlist_id) guarded by CHECKs. No existing
-- row changes and nothing NYC reads changes meaning.
--
-- The group, filter-profile and removal-reason CHECK lists below mirror
-- core/dc_groups.py and DC section 10; tests/test_dc_schema.py reads both and fails if
-- they drift.

BEGIN;

-- ---------------------------------------------------------------------------
-- clubs: one table for every place a show can happen (DC section 3)
-- ---------------------------------------------------------------------------

ALTER TABLE clubs
    ADD COLUMN source text NOT NULL DEFAULT 'scrape'
        CHECK (source IN ('scrape', 'capitalbop_feed', 'capitalbop_venue')),
    ADD COLUMN capitalbop_term_id integer UNIQUE,
    ADD COLUMN capitalbop_slug    text    UNIQUE,
    ADD COLUMN city_override      text;

-- Venue rows have nothing to fetch.
ALTER TABLE clubs ALTER COLUMN schedule_url DROP NOT NULL;
ALTER TABLE clubs
    ADD CONSTRAINT clubs_schedule_url_required
    CHECK (source = 'capitalbop_venue' OR schedule_url IS NOT NULL);

COMMENT ON COLUMN clubs.source IS
    'scrape = hand-edited NYC club (the scrape loop selects only these). capitalbop_feed =
     the single feed row run_log keys DC on. capitalbop_venue = mirrored from CapitalBop,
     club_id = ''cb-'' || capitalbop_slug.';
COMMENT ON COLUMN clubs.city_override IS
    'Hand-set in psql for venues CapitalBop has no address for; first hit in DC section 3
     city resolution.';

-- ---------------------------------------------------------------------------
-- Rolling playlists: invariant 4's key for DC (DC section 10)
-- ---------------------------------------------------------------------------

CREATE TABLE rolling_playlists (
    id                  bigserial PRIMARY KEY,
    region              text    NOT NULL,
    slot_key            text    NOT NULL,
    group_key           text    NOT NULL
                            CHECK (group_key IN ('dc', 'blues-alley', 'alxjazz')),
    spotify_playlist_id text,
    spotify_url         text,
    title               text    NOT NULL,
    description         text,
    is_public           boolean NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (region, slot_key)
);

COMMENT ON TABLE rolling_playlists IS
    'Permanent Spotify URLs, upserted from core/dc_groups.py at the start of each DC run.
     There is deliberately no spotify_removed_at: rolling playlists are never unfollowed
     (invariant 5), because someone else may be following them.';

-- One row per stint. A track that leaves and later returns gets a new row.
CREATE TABLE rolling_playlist_tracks (
    id                  bigserial PRIMARY KEY,
    rolling_playlist_id bigint NOT NULL REFERENCES rolling_playlists(id),
    spotify_track_id    text   NOT NULL,
    isrc                text,
    spotify_album_id    text   NOT NULL,
    spotify_artist_id   text   NOT NULL REFERENCES artists(spotify_artist_id),
    track_name          text,
    position            integer NOT NULL,
    show_id             bigint REFERENCES shows(show_id),
    added_at            timestamptz NOT NULL DEFAULT now(),
    removed_at          timestamptz,
    removal_reason      text
        CHECK (removal_reason IN ('rolled_off', 'moved', 'ineligible', 'correction', 'user')),
    CHECK ((removed_at IS NULL) = (removal_reason IS NULL))
);

-- At most one open stint per track per playlist.
CREATE UNIQUE INDEX rolling_playlist_tracks_open_uq
    ON rolling_playlist_tracks (rolling_playlist_id, spotify_track_id)
    WHERE removed_at IS NULL;

-- The reconciler's "ever removed by the user" exclusion spans every playlist (invariant 9).
CREATE INDEX rolling_playlist_tracks_reason_idx
    ON rolling_playlist_tracks (spotify_track_id, removal_reason);

COMMENT ON COLUMN rolling_playlist_tracks.isrc IS
    'Powers the relinking guard (DC section 11): Spotify can serve the same recording under
     a different track ID, and linked_from was removed in February 2026.';

-- ---------------------------------------------------------------------------
-- Existing tables that point at rolling playlists
-- ---------------------------------------------------------------------------

ALTER TABLE feedback DROP CONSTRAINT feedback_source_check;
ALTER TABLE feedback
    ADD CONSTRAINT feedback_source_check
    CHECK (source IN ('mcp', 'manual', 'implicit', 'spotify'));
ALTER TABLE feedback ADD COLUMN rolling_playlist_id bigint REFERENCES rolling_playlists(id);

ALTER TABLE playlist_events ALTER COLUMN week_playlist_id DROP NOT NULL;
ALTER TABLE playlist_events
    ADD COLUMN rolling_playlist_id bigint REFERENCES rolling_playlists(id);
ALTER TABLE playlist_events
    ADD CONSTRAINT playlist_events_exactly_one_playlist
    CHECK ((week_playlist_id IS NULL) <> (rolling_playlist_id IS NULL));

CREATE INDEX playlist_events_rolling_idx
    ON playlist_events (rolling_playlist_id, created_at DESC)
    WHERE rolling_playlist_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Venue groups: what the MCP server may write (ADR-021)
-- ---------------------------------------------------------------------------

CREATE TABLE venue_group_members (
    id             bigserial PRIMARY KEY,
    club_id        text NOT NULL REFERENCES clubs(club_id),
    group_key      text NOT NULL CHECK (group_key IN ('dc', 'blues-alley', 'alxjazz')),
    filter_profile text NOT NULL
        CHECK (filter_profile IN ('all', 'standard', 'standard_no_pick', 'early_evening')),
    added_via      text NOT NULL CHECK (added_via IN ('seed', 'mcp', 'manual')),
    added_at       timestamptz NOT NULL DEFAULT now(),
    deactivated_at timestamptz
);

CREATE UNIQUE INDEX venue_group_members_active_uq
    ON venue_group_members (club_id, group_key)
    WHERE deactivated_at IS NULL;

COMMENT ON TABLE venue_group_members IS
    'Deactivation stamps, never deletes; re-adding opens a new row. "At most one dedicated
     group per venue" (DC section 4) spans rows, so the repository enforces it inside the
     insert transaction; the club must be a capitalbop_venue row, also enforced there.';

-- ---------------------------------------------------------------------------
-- CapitalBop mirror and extraction cache (DC sections 2 and 6)
-- ---------------------------------------------------------------------------

-- One row per CapitalBop instance (one date of one event).
CREATE TABLE capitalbop_events (
    instance_id      integer PRIMARY KEY,
    event_id         integer NOT NULL,
    show_id          bigint  NOT NULL REFERENCES shows(show_id),
    start_time       time,
    price_text       text,
    ticket_link      text,
    cb_pick          boolean NOT NULL DEFAULT false,
    tags             text[]  NOT NULL DEFAULT '{}',
    repeats          boolean NOT NULL DEFAULT false,
    neighborhoods    text[]  NOT NULL DEFAULT '{}',
    geocoded_city    text,
    description_html text,
    description_hash text,
    article_title    text,
    article_url      text,
    article_date     date,
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX capitalbop_events_last_seen_idx ON capitalbop_events (last_seen_at);
CREATE INDEX capitalbop_events_event_idx     ON capitalbop_events (event_id);

COMMENT ON COLUMN capitalbop_events.show_id IS
    'If CapitalBop renames an act this repoints to the new shows row; the old row is kept,
     stale.';
COMMENT ON COLUMN capitalbop_events.last_seen_at IS
    'Older than the latest complete fetch = cancelled. Never deleted.';

-- The Haiku personnel cache. A residency's dates share one row.
CREATE TABLE capitalbop_extractions (
    event_id         integer NOT NULL,
    description_hash text    NOT NULL,
    result           jsonb   NOT NULL,
    model            text    NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (event_id, description_hash)
);

COMMIT;
