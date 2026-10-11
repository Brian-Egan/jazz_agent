-- seed_dc.sql -- DC seed data (docs/DC.md sections 3 and 4). Applied after 0003_dc.sql.
--
-- Hand-edited config like seed_clubs.sql, not schema, so it has no numeric prefix.
-- Every statement is idempotent: re-running changes nothing, and in particular never
-- reactivates a membership that has been deactivated since.
--
-- The venue rows below exist only so memberships and city overrides have something to
-- reference before the first CapitalBop mirror run (#24). They carry the slug as a
-- placeholder name; the mirror fills in the real name and capitalbop_term_id. city_override
-- is hand-set and the mirror never touches it.

-- The feed row keeps a schedule_url (only venue rows may omit it, clubs_schedule_url_required),
-- but nothing fetches it: the events come from /wp-json/capitalbop/v1 (DC section 2).
INSERT INTO clubs (club_id, name, schedule_url, source, notes) VALUES
    ('capitalbop', 'CapitalBop', 'https://www.capitalbop.com/dcjazzcalendar/',
     'capitalbop_feed',
     'The DC calendar feed. run_log and chronic failure key on this row (DC section 14).')
ON CONFLICT (club_id) DO NOTHING;

INSERT INTO clubs (club_id, name, source, capitalbop_slug, city_override, notes) VALUES
    ('cb-blues-alley', 'blues-alley', 'capitalbop_venue', 'blues-alley', NULL, NULL),
    ('cb-laportas', 'laportas', 'capitalbop_venue', 'laportas', NULL, NULL),
    ('cb-the-birchmere', 'the-birchmere', 'capitalbop_venue', 'the-birchmere', NULL, NULL),
    ('cb-hotel-heron', 'hotel-heron', 'capitalbop_venue', 'hotel-heron', 'Alexandria',
     'CapitalBop carries no address for this venue.'),
    ('cb-good-fortune-rooftop-bar-hotel-heron', 'good-fortune-rooftop-bar-hotel-heron',
     'capitalbop_venue', 'good-fortune-rooftop-bar-hotel-heron', 'Alexandria',
     'Second CapitalBop term for Hotel Heron; no address in the feed.'),
    ('cb-old-waterfront-park', 'old-waterfront-park', 'capitalbop_venue',
     'old-waterfront-park', 'Alexandria', 'CapitalBop carries no address for this venue.'),
    ('cb-galaxy-hut', 'galaxy-hut', 'capitalbop_venue', 'galaxy-hut', 'Arlington',
     'CapitalBop carries no address for this venue.')
ON CONFLICT (club_id) DO NOTHING;

-- NOT EXISTS (not ON CONFLICT) because the unique index is partial and covers only active
-- rows: a seeded membership that Brian has since deactivated must stay deactivated.
INSERT INTO venue_group_members (club_id, group_key, filter_profile, added_via)
SELECT v.club_id, v.group_key, v.filter_profile, 'seed'
FROM (VALUES
    ('cb-blues-alley',                          'blues-alley', 'all'),
    ('cb-laportas',                             'alxjazz',     'early_evening'),
    ('cb-the-birchmere',                        'alxjazz',     'standard_no_pick'),
    ('cb-hotel-heron',                          'alxjazz',     'all'),
    ('cb-good-fortune-rooftop-bar-hotel-heron', 'alxjazz',     'all'),
    ('cb-old-waterfront-park',                  'alxjazz',     'all')
) AS v (club_id, group_key, filter_profile)
WHERE NOT EXISTS (
    SELECT 1 FROM venue_group_members m
    WHERE m.club_id = v.club_id AND m.group_key = v.group_key AND m.added_via = 'seed'
);
