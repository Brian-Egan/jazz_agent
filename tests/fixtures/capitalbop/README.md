# CapitalBop fixtures

Real responses from `https://www.capitalbop.com/wp-json/capitalbop/v1`, saved 2026-10-11
(one request at a time, >= 1 s apart, standard browser User-Agent). Event objects are
**unmodified**; the files are curated subsets so tests stay small:

- `calendar.json` – `/calendar`, `venues` reduced to the venues used here plus the seeded ones,
  `modal_content` dropped.
- `events_page1.json`, `events_page2.json` – `/events` objects picked from the live
  2026-10-11..2026-11-15 window (page size and `max_pages` rewritten to 2 so pagination is
  exercised). Covers: a two-date residency (LaPortas, event 25683; Green Island, 5988),
  `***SOLD OUT***` (Kofi B) and `**SOLD OUT**` with HTML entities (John Scofield), a double
  bill (`/`), a venue with `geolocation: null` (The Golden Age), a jam session, a venue with
  an empty address but a geolocation (Bonne Vie, Bourbon Boulevard), and three days of
  Alexandria Jazz Fest (June 2026, pulled from a separate one-off window).
- `event_with_article.json` – `/event?instance=37233`; `article.post_content` and
  `article.acf` emptied (large, and not used: DC section 2 stores only title, URL, date).
- `eligibility_snapshot.json` – every event of the live 2026-10-11..2026-11-15 window (349),
  reduced to the fields DC section 5 reads (values copied unmodified from `/events`), for the
  eligibility count test. DC section 5's own counts were measured on 2026-10-10; that snapshot
  is not recoverable, see `tests/test_dc_eligibility.py`.
