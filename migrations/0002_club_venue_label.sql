BEGIN;

ALTER TABLE clubs ADD COLUMN venue_label text;

COMMENT ON COLUMN clubs.venue_label IS
    'Set when schedule_url is a combined page listing multiple venues (e.g.
     smallslive.com covers Smalls, Mezzrow, and Jazz Cultural Theatre on one
     page, each show explicitly labeled "Live at <venue>"). When set,
     extraction only keeps shows labeled for this venue, ignoring the rest of
     the page. NULL for clubs whose schedule_url is already venue-specific.';

COMMIT;
