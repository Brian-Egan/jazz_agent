-- 0006_mcp_dc_reads.sql -- what `when_is_playing` needs (docs/DC.md section 13).
--
-- A venue address on each stored event (the feed carries it as venue_detail.address; until now
-- only its geocoded city was kept), and accent-insensitive matching so "Sharon Clark" finds
-- "Sharon Clark Trio" however the feed spells it. unaccent is a trusted extension, so the
-- database owner can create it.

BEGIN;

CREATE EXTENSION IF NOT EXISTS unaccent;

ALTER TABLE capitalbop_events ADD COLUMN venue_address text;

COMMIT;
