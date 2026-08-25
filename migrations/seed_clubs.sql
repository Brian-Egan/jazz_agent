-- seed_clubs.sql -- the initial six-club seed list (ARCHITECTURE.md section 3).
--
-- Every URL below has been fetched successfully with the real HttpFetcher
-- (adapters/http_fetcher.py's browser-like header set), not assumed --
-- ARCHITECTURE.md section 3: "Seeded URLs must be verified, not assumed.
-- During architecture work two plausible club domains failed DNS resolution
-- outright." Two additional candidates were tried while building this list
-- and rejected for the same reason real infrastructure work always turns
-- up surprises:
--   * jazz.org/dizzys/ (Dizzy's Club) -- genuine 403, confirmed with both
--     the project's own fetcher and a bare curl with realistic headers;
--     looks like bot-protection stronger than a header check.
--   * zincbar.com (Zinc Bar) -- TLS handshake fails outright
--     (SSL: UNEXPECTED_EOF_WHILE_READING), not a fetch-layer problem.
--
-- Re-verified 2026-08-25 after four of six clubs came back no_shows on the
-- first real run -- sites move, and "seeded once" doesn't mean "correct
-- forever" any more than any other scraping target does:
--   * birdland: root domain has no listings in static HTML (its calendar
--     widget literally renders "Calendar Loading..." without JS). The real
--     calendar lives at /calendar/ and needs render_mode='js'.
--   * mezzrow: mezzrow.com now 301-redirects into smallslive.com outright --
--     Mezzrow no longer has its own site. www.mezzrow.com's old TLS failure
--     is moot now for the same reason.
--   * smalls-live: smallslive.com's root is still correct and still fully
--     server-rendered (no JS needed) -- but it now covers three venues on
--     one combined page (Smalls, Mezzrow, and Jazz Cultural Theatre), each
--     show explicitly labeled "Live at <venue>". venue_label filters
--     extraction down to just this club's own shows. Jazz Cultural Theatre
--     is deliberately not seeded as a club here.
--   * smoke: root domain's static HTML has no schedule content at all; the
--     real listings are on a separate ticketing subdomain.
--
-- Named without a numeric prefix deliberately: clubs are hand-edited
-- config (ARCHITECTURE.md section 3), not schema, so this isn't part of
-- the numbered 0001_*, 0002_*, ... migration sequence -- it's just seed
-- data applied once. ON CONFLICT DO NOTHING makes re-running it safe (and,
-- per the above, does not retroactively fix an already-seeded database --
-- see RUNBOOK.md's failure playbook for hand-editing an existing row).

INSERT INTO clubs (club_id, name, schedule_url, render_mode, notes, venue_label) VALUES
    ('village-vanguard', 'Village Vanguard', 'https://villagevanguard.com',
     'http', 'Full personnel with instruments present in static HTML (ADR-004).', NULL),
    ('smalls-live', 'SmallsLIVE', 'https://www.smallslive.com/',
     'http', 'Combined page with Mezzrow and Jazz Cultural Theatre -- venue_label filters it.', 'Smalls'),
    ('blue-note', 'Blue Note', 'https://bluenotejazz.com/nyc',
     'http', NULL, NULL),
    ('birdland', 'Birdland', 'https://www.birdlandjazz.com/calendar/',
     'js', 'Calendar widget renders client-side ("Calendar Loading..." with JS off) -- render_mode=js is required, not optional, here.', NULL),
    ('mezzrow', 'Mezzrow', 'https://www.smallslive.com/',
     'http', 'No longer has its own site -- mezzrow.com 301s into smallslive.com. Combined page with Smalls and Jazz Cultural Theatre; venue_label filters it.', 'Mezzrow'),
    ('smoke', 'Smoke Jazz Club', 'https://tickets.smokejazz.com/',
     'http', 'Root domain has no schedule content; listings live on this separate ticketing subdomain.', NULL)
ON CONFLICT (club_id) DO NOTHING;
