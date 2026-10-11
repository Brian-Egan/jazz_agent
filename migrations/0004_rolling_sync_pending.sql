-- 0004_rolling_sync_pending.sql -- DC reconciler convergence marker (docs/DC.md section 10).
--
-- The reconciler writes the database first and Spotify second (invariant 9). If the Spotify
-- call then fails, the database says tracks are live that Spotify does not hold -- exactly
-- the signature of a user removal. This flag marks that window: it is set before the Spotify
-- write and cleared after it succeeds. While set, the next run re-sends the full desired list
-- instead of trusting the diff, and user-removal detection (DC section 11) must not infer
-- anything from the playlist.

BEGIN;

ALTER TABLE rolling_playlists
    ADD COLUMN sync_pending boolean NOT NULL DEFAULT false;

COMMENT ON COLUMN rolling_playlists.sync_pending IS
    'True between writing rolling_playlist_tracks and a successful Spotify replace. Never
     infer user removals from a playlist while this is set.';

COMMIT;
