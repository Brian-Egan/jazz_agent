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
-- www.mezzrow.com also fails TLS the same way zincbar.com does; the bare
-- mezzrow.com (no www) works and is what's seeded below.
--
-- Named without a numeric prefix deliberately: clubs are hand-edited
-- config (ARCHITECTURE.md section 3), not schema, so this isn't part of
-- the numbered 0001_*, 0002_*, ... migration sequence -- it's just seed
-- data applied once. ON CONFLICT DO NOTHING makes re-running it safe.

INSERT INTO clubs (club_id, name, schedule_url, render_mode, notes) VALUES
    ('village-vanguard', 'Village Vanguard', 'https://villagevanguard.com',
     'http', 'Full personnel with instruments present in static HTML (ADR-004).'),
    ('smalls-live', 'SmallsLIVE', 'https://smallslive.com',
     'http', NULL),
    ('blue-note', 'Blue Note', 'https://bluenotejazz.com/nyc',
     'http', NULL),
    ('birdland', 'Birdland', 'https://birdlandjazz.com',
     'http', '403s without a realistic User-Agent (ADR-004) -- the header set in adapters/http_fetcher.py is not optional here.'),
    ('mezzrow', 'Mezzrow', 'https://mezzrow.com',
     'http', 'Use the bare domain, not www -- www.mezzrow.com fails TLS outright.'),
    ('smoke', 'Smoke Jazz Club', 'https://smokejazz.com',
     'http', NULL)
ON CONFLICT (club_id) DO NOTHING;
