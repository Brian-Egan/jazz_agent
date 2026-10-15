# Decision Log

One ADR per significant choice: context, decision, rationale, rejected alternatives,
consequences. Where a decision supersedes the [PRD](original-prd.md), the PRD
section is named. The PRD is kept unedited as the record of original intent.

Evidence gathered during architecture review is reproduced verbatim in ADR-004 and ADR-005,
because it is load-bearing and a future reader would otherwise have to re-derive it.

| ADR | Decision | Status |
|---|---|---|
| [001](#adr-001) | Single always-on Linux VPS | Accepted |
| [002](#adr-002) | Postgres 17, used properly | Accepted |
| [003](#adr-003) | LLM extraction instead of CSS-selector adapters | Accepted, supersedes FR2 |
| [004](#adr-004) | httpx by default, Playwright opt-in per club | Accepted |
| [005](#adr-005) | MusicBrainz as a first-class component | Accepted |
| [006](#adr-006) | Spotify-first matching, MusicBrainz verifies | Accepted, supersedes ADR-006a |
| [006a](#adr-006a) | MusicBrainz-first matching | **Superseded by ADR-006** |
| [007](#adr-007) | Plausibility guard as primary mismatch defence | Accepted |
| [008](#adr-008) | Weekly playlists, Tuesday–Monday | Accepted, supersedes Section 11, FR5, FR6. NYC only; DC per ADR-018 |
| [009](#adr-009) | Horizon scrape, not "tonight" scrape | Accepted |
| [010](#adr-010) | One full album per artist, in track order | Accepted, refines FR4. NYC only; DC per ADR-020 |
| [011](#adr-011) | Corrections may remove tracks | Accepted |
| [012](#adr-012) | OAuth 2.1 via Google, not a static token | Accepted, supersedes 8.2 |
| [013](#adr-013) | Capture the taste graph in v1, analyse later | Accepted, extends FR9 |
| [014](#adr-014) | Feedback targets artist/track, not playlist | Accepted, supersedes FR9. Write surface amended by ADR-021 |
| [015](#adr-015) | Run-health tool plus ntfy for chronic failure only | Accepted, resolves SC7 vs Out-of-Scope. Amended by ADR-023 |
| [016](#adr-016) | Two files for two agent audiences | Accepted |
| [017](#adr-017) | DC from the CapitalBop JSON API, not scraped pages | Accepted |
| [018](#adr-018) | DC: five rolling playlists, Monday weeks, hardcoded groups | Accepted, departs from ADR-008 for DC. Slot structure amended by ADR-025 |
| [019](#adr-019) | DC eligibility filters and novelty | Accepted |
| [020](#adr-020) | DC: two tracks per artist, search as popularity proxy | Accepted, departs from ADR-010 for DC |
| [021](#adr-021) | MCP may write venue group membership | Accepted, amends ADR-014 and invariant 7 |
| [022](#adr-022) | Removing a track in Spotify is feedback (LaPortas) | Accepted |
| [023](#adr-023) | ntfy for new artists at LaPortas | Accepted, amends ADR-015 |
| [024](#adr-024) | DC and Blues Alley playlists are public | Accepted, amends "no sharing" |
| [025](#adr-025) | DC: one six-week list instead of three slots | Accepted, amends ADR-018 |

---

## ADR-001 {#adr-001}
### Single always-on Linux VPS

**Context.** The PRD (8.1) offers AWS serverless, a VPS, or a Raspberry Pi, leaving the call
to the architect. No hardware exists today; a VPS would be purchased for this.

**Decision.** One small always-on Linux VPS. Docker Compose for Postgres and the MCP
server; host cron for the batch job.

**Rationale.** Half the system is a persistent, session-oriented HTTP server, which is a
poor fit for function-as-a-service: cold starts affect query latency and session handling
becomes plumbing. A flat monthly fee is easier to reason about than per-service pricing for
a workload this small.

**Rejected.** *AWS serverless* — familiar, but the MCP half fights the model. *Raspberry Pi
now* — no reliably always-on Pi exists yet, and it would need tunnelling for public
reachability. *Mac* — laptop sleep means missed runs.

**Consequences.** OS patching, uptime, and backups are owned. Batch and server share one
point of failure. Mitigated by the section 2 dependency rule, which keeps the move to other
hardware a deployment change.

---

## ADR-002 {#adr-002}
### Postgres 17, and use it properly

**Context.** Roughly 5 shows a day, ~2k rows a year, two writers. SQLite with FTS5 would
be sufficient on volume alone and carries no operational burden.

**Decision.** Postgres 17 in Docker Compose, pinned. Use `tsvector` for note search,
`pg_trgm` for fuzzy artist recall, `JSONB` for set times, and install `pgvector` against
future use.

**Rationale.** The system's distinguishing read pattern is imprecise recall of
half-remembered artists — "who was that solo pianist last week". `tsvector` stemming and
`pg_trgm` typo tolerance are materially better at that than FTS5, and `pgvector` is the
natural home for the eventual taste-similarity work. The operational cost is a fixed
one-time setup rather than ongoing drag.

**Rejected.** *SQLite + FTS5* — the better engineering fit on volume, weaker on the search
that matters most. *Managed Postgres* — adds a network dependency to a local-first system.
*Dual implementations* — speculative multi-backend support that PRD 8.6 explicitly warns
against.

**Consequences.** A daemon to patch, a connection string to manage, `pg_dump` on a cron,
and slower tests than in-memory SQLite. Repository Protocols keep a later swap contained.

---

## ADR-003 {#adr-003}
### LLM extraction instead of CSS-selector adapters

**Supersedes** PRD FR2 (per-club adapters), Section 13 (`selector_hints`), and answers
Open Question 3.

**Context.** The PRD assumes one hand-written adapter per club with CSS selectors, and
admits (10.3) they will silently break when venues redesign. For six venues that is a few
breakages a year of unpaid maintenance, each discovered only after a week of empty
playlists.

**Decision.** Fetch the page, reduce it to text, and make one Haiku call with a strict JSON
schema. A club is `{name, url, render_mode}` and nothing more.

**Rationale.** There are no selectors to break, so a redesign is a non-event. It makes
FR1's "adding a club requires only a data change" literally true rather than aspirational —
under the adapter model, adding a club means writing, testing, and deploying code. At six
calls a day the cost is cents a month.

**Rejected.** *Hand-written adapters* — deterministic and free, reliably broken. *Selectors
with LLM fallback* — cheapest steady state, two code paths and two test suites to maintain
for a workload where the LLM cost is already negligible. *Structured feeds only* — JSON-LD
and `.ics` coverage across small jazz clubs is spotty; used opportunistically where present.

**Consequences.** Extraction is non-deterministic, so tests use golden files over saved HTML
and assert semantics rather than exact output. A model that hallucinates a show would create
a phantom entry — mitigated by strict schemas and by keeping `raw_text` for every row.

---

## ADR-004 {#adr-004}
### httpx by default, Playwright opt-in per club

**Context.** Whether heavyweight browser automation is needed was unknown. It was measured
rather than assumed.

**Evidence** (live probes during architecture review):

| Site | Result |
|---|---|
| villagevanguard.com | 200, 61 KB HTML, 5,011 chars text, 1 JSON-LD block. Artist names **and full personnel with instruments** present in static HTML |
| smallslive.com | 200, 83 KB HTML. "Quartet" and "Trio" both present in raw HTML |
| bluenotejazz.com/nyc | 200, 119 KB HTML. "Quartet" and "Trio" both present in raw HTML |
| birdlandjazz.com | **403 Forbidden** to a bare `urllib` request |
| two further guessed domains | DNS resolution failure |

**Decision.** `httpx` with a full browser-like header set as the default. A per-club
`render_mode: js` flag routes a club through Playwright/Chromium.

**Rationale.** Three of four real venues are server-rendered, so browser automation would
add roughly 1 GB of container and several hundred MB of RAM for no benefit on the common
path. One venue already blocks naive requests, so realistic headers are mandatory rather
than polite.

**Rejected.** *Playwright for everything* — uniform but wasteful. *httpx only* — leaves a
known unhandled gap. *Auto-escalate on empty result* — self-healing, but masks a club's true
state and doubles worst-case runtime.

**Consequences.** Two fetch paths exist, only one normally exercised. The DNS failures are
why verifying a club URL is an explicit acceptance criterion for seeding, not an assumption.

---

## ADR-005 {#adr-005}
### MusicBrainz as a first-class component

**Context.** A stated long-term goal is finding connections between artists and identifying
patterns in what the user likes. The PRD (10.5) notes that `audio-features` and
`audio-analysis` are gone for apps registered after November 2024.

**The PRD understates the problem.** The same 27 November 2024 change also removed
**`related-artists`** and **`recommendations`**. There is therefore *no Spotify path at all*
to artist adjacency, and no acoustic data of any kind. Any design assuming Spotify could
supply artist similarity is dead on arrival.

**Evidence** (live MusicBrainz probe for "Bill Frisell"):

```
SEARCH: Bill Frisell           Person  score=100  mbid=a21318db-f228-4a4d-8bce-6947a62985a5
        The Bill Frisell Band  Group   score=70
        Bill Frisell Trio      -       score=69

TAGS:   contemporary jazz, folk, jazz, jazz and blues, jazz fusion

15 artist-artist edges: {member of band: 13, collaboration: 1, parent: 1}
   member of band  -> Paul Motian Trio
   member of band  -> Naked City
   member of band  -> Andrew Cyrille Quartet    ['guitar']
   member of band  -> Paul Motian Quintet       ['electric guitar']
   collaboration   -> The Paul Bley Quartet

SPOTIFY LINK STORED IN MB: https://open.spotify.com/artist/3SONlwqLIP2GtaMh9pLYe5
```

A `503 Service Temporarily Unavailable` was also returned on the first attempt.

**Decision.** MusicBrainz is a first-class component supplying the artist graph and
independent match verification. It is never on the critical path (see ADR-006).

**Rationale.** It is the only available replacement for the adjacency Spotify withdrew, and
arguably better: "played in Paul Motian's trio" is more meaningful than an opaque similarity
score. Person/Group typing addresses the ensemble-versus-bandleader problem. The stored
Spotify URL enables genuine cross-verification rather than a second fuzzy name match. Free,
no API key.

**Rejected.** *No MusicBrainz* — leaves adjacency limited to artists who happened to play
the user's six clubs. *Defer to v1.1* — acceptable, since MBIDs are backfillable, but it
delays the component the user considers most important long-term.

**Consequences.** 1 req/s, flaky, community-coverage gaps (a young sideman with no
discography simply is not there — which is precisely the gap listing-derived co-performance
fills). Requires a permanent cache and backoff. Escape hatch if reliability becomes a real
problem: MusicBrainz publishes full database dumps and a `musicbrainz-docker` self-host
path. Recorded, not built.

---

## ADR-006 {#adr-006}
### Spotify-first matching; MusicBrainz verifies, never blocks

**Supersedes ADR-006a.**

**Context.** ADR-006a put MusicBrainz first, which placed a flaky 1 req/s service on the
critical path to producing a playlist — a live `503` was observed during the very session
that specified it.

**Decision.** Spotify search resolves the match (with the ADR-007 guard and LLM
adjudication). MusicBrainz then verifies, off the critical path, with a hard 2s timeout.
Verification outcomes: `verified`, `disputed`, `unverifiable`, `unverified`. A dispute is
resolved by one further LLM call given both candidates and MusicBrainz context, which may
also return "neither" and downgrade to a logged miss.

**Rationale.** Three reasons, in order of weight:

1. **MusicBrainz cannot supply tracks, so Spotify must be called regardless.**
   MusicBrainz-first did not replace a call, it *added* one ahead of a call that still had
   to happen — pure added latency and an added failure mode for no reduction in work.
2. **It converts downtime from a correctness problem into a confidence problem.** Under
   ADR-006a an outage changed *which artist matched*, requiring later re-adjudication. Here
   the match is identical either way; only `verification_state` differs. Nothing needs
   redoing.
3. **What MusicBrainz was uniquely buying in matching turned out to be replaceable.**
   Person/Group typing was to resolve "The Bill Frisell Four" to Bill Frisell —
   ensemble-suffix stripping plus an LLM that knows what a jazz bandleader is handles that
   with no network call. MusicBrainz's irreplaceable contributions, the graph and the
   cross-check, are post-match by nature.

Disputes are resolved by LLM rather than by a fixed precedence rule because disputes are by
definition the ambiguous cases, where a heuristic is least trustworthy. MusicBrainz is not
automatically authoritative: its links are human-curated but can be stale, and can point at
a band where the person was wanted.

**Rejected.** *MusicBrainz wins disputes* — simple, no extra call, but trusts a possibly
stale link. *Spotify wins, log for review* — fully predictable, leaves wrong albums in place
until manual intervention. *Dispute means no match* — never wrong, discards valid matches
whenever a MusicBrainz link is stale.

**Consequences.** Testable as a hard invariant: with MusicBrainz forced to fail, the
produced playlist must be **byte-identical** to the MusicBrainz-up case, differing only in
`verification_state`. ADR-006a could not have asserted this. Some matches remain unverified
indefinitely, which is visible and queryable rather than hidden.

---

## ADR-006a {#adr-006a}
### MusicBrainz-first matching — **SUPERSEDED**

Original decision: resolve the act through MusicBrainz (Person/Group typing, membership
edges, stored Spotify URL) before falling back to Spotify search, recording `match_method`
so weaker matches could be re-adjudicated later.

Superseded by ADR-006 during review. Retained because the reasoning for the inversion only
makes sense against what it replaced, and because "MusicBrainz is important, therefore query
it first" is an intuitive mistake worth documenting as a mistake.

---

## ADR-007 {#adr-007}
### Plausibility guard is the primary defence against mismatches

**Context.** Matching the wrong same-named artist is worse than not matching at all (PRD
10.2), and the cost rose sharply once a match yields a full album rather than a few tracks.
The obvious defence, MusicBrainz cross-check, is unavailable exactly when MusicBrainz is
down.

**Decision.** `core/plausibility.py` scores each Spotify candidate on whether it could
plausibly be a working jazz act — jazz-adjacent genres present, follower and album counts
mutually consistent, not an obvious same-named act from an unrelated genre. Pure local logic
over the search response, ahead of LLM adjudication.

**Rationale.** Catches the ugliest failure mode (a same-named metal band's album playing for
45 minutes) using data already in hand, at zero cost, with no external dependency. It works
during a MusicBrainz outage, which is when a guard matters most.

**Consequences.** The least testable component in the system: "does this look like a working
jazz act" is judgement encoded as thresholds. Mitigated with a hand-built fixture set
including a same-named non-jazz act, and `plausibility_score` is persisted so tuning can be
evidence-driven. Expect adjustment over the first few weeks.

---

## ADR-008 {#adr-008}
### Weekly playlists, Tuesday–Monday

**Supersedes** PRD Section 11, FR5, FR6. Resolves Open Question 1 (multi-act nights).

**Context.** The PRD specifies one playlist per club per night. Review of real calendars
showed Village Vanguard books a single act for a whole week while SmallsLIVE runs distinct
early and late bands nightly. Nightly playlists therefore produce six near-identical
playlists for one residency, and require an "engagement" concept with contiguity rules, gap
tolerance, per-act identity, and a retention clock that must not expire mid-residency.

**Decision.** One playlist per club per booking week, Tuesday–Monday, keyed on
`(club_id, week_start_date)`. Artists deduped within the week by `spotify_artist_id`.

**Rationale.** It removes that entire class of accidental complexity rather than solving it.
Idempotency becomes structural: the key is deterministic and the operation is a diff, so
re-running converges instead of duplicating — PRD 8.3 is satisfied by construction. Clutter
drops roughly fourfold. Late-published lineups self-heal, where under nightly playlists a
late post meant a permanently missed night.

Tuesday–Monday over ISO Monday–Sunday because clubs open new bookings on Tuesday; a
Monday-start week would split a Tuesday–Sunday residency across two playlists.

**Rejected.** *Nightly, one per club* — the PRD's model, all the complexity above.
*Nightly, one per act* — cleanest attribution, worst clutter. *ISO week* — simpler
arithmetic, splits real bookings.

**Consequences.** **Success Criterion 2 is no longer literally satisfiable**: Spotify alone
no longer tells you who is on *tonight*, only who is on this week. Accepted knowingly;
mitigated by chronological track order, an artist-by-night description, and the MCP server.
Playlists are long (4–6 hours per club), which suits background listening.

---

## ADR-009 {#adr-009}
### Horizon scrape, not "tonight" scrape

**Context.** The PRD models the daily run as scraping tonight's show. But clubs publish
weeks ahead, so a fetch that reads only today discards most of what it just downloaded.

**Decision.** Each run extracts every event from today through `HORIZON_WEEKS_AHEAD` weeks,
groups by week, and reconciles playlists for the current week plus four ahead, retaining
one past week — six live playlists per club.

**Rationale.** The data is already on the page. Forward playlists give runway to plan going
out, and the daily run becomes a convergence loop rather than a creation event, so genuine
lineup changes are corrected and nothing depends on a single well-timed run.

**Consequences.** More Spotify writes per run and a larger blast radius per bug, bounded by
idempotency. Far-out weeks are often sparse until clubs fill them in, which is expected
rather than a fault.

---

## ADR-010 {#adr-010}
### One full album per artist, in track order

**Refines** PRD FR4 (8–15 representative tracks).

**Context.** The user listens to these playlists in the background across a whole week, so
sampling is not the goal.

**Decision.** One full album per artist, added in `track_number` order. Selection: highest
`album.popularity` among `album_group=album`, excluding compilations, greatest-hits, and
obvious reissues; fall back to most recent `release_date` when the top album scores below 5.

**Rationale.** Jazz albums are composed sequences; scattering top tracks across them is the
worse listen. `include_groups='album'` matters more than it looks — without it you get
greatest-hits packages and `appears_on` sideman credits, which for working jazz musicians is
the common case rather than an edge case.

**Correction to the PRD's framing:** Spotify exposes **no album ratings**. "Highest rated"
is implementable only as `popularity` (0–100) on the album object. The recency fallback
exists because working musicians frequently have no album with meaningful popularity.

**Consequences.** Playlists run 4–6 hours per club-week. A wrong match now costs a full
album, which is why ADR-007 exists and why the confidence bar is conservative.

**Known issue (found October 2026, not yet resolved).** Spotify's February 2026 migration
removed `popularity` from album, artist, and track objects for Development Mode apps, and the
March 2026 partial reversion did not restore it. `core/selection.py` reads a field that is
now always absent, so every artist falls through to the recency fallback: NYC has been
selecting each artist's **most recent** album since enforcement on 9 March 2026.
`core/plausibility.py` also scores on `popularity`. Both need a decision in V2; see ADR-020
for the proxy DC uses instead.

---

## ADR-011 {#adr-011}
### Corrections may remove tracks from a live playlist

**Context.** Dispute resolution can change a match after tracks are live. The reconciler is
otherwise additions-only.

**Decision.** A match correction may remove the wrong album and add the right one. Both
events are written to `playlist_events` with a reason. This is the only circumstance in
which a live playlist shrinks.

**Rationale.** A corrected match is worthless if the wrong album stays for the rest of the
week. The alternative leaves 45 minutes of known-wrong music in place.

**Rejected.** *Additions only, correct future weeks* — perfectly stable, knowingly wrong.
*Verify before adding* — attractive, but it puts MusicBrainz back on the critical path,
which ADR-006 exists to prevent.

**Consequences.** Something queued can disappear mid-week. Rare, always logged, always
attributable to a recorded reason.

---

## ADR-012 {#adr-012}
### OAuth 2.1 delegated to Google, not a static access token

**Supersedes** PRD 8.2 and FR10 (token-secured MCP endpoint).

**Context.** The PRD specifies a static access token. The user connects from claude.ai and
Claude Desktop, whose custom connectors require OAuth 2.1 with dynamic client registration
and **reject static bearer tokens**. The PRD's model does not survive contact with the
actual client.

**Decision.** FastMCP with `OAuthProxy`, presenting the DCR and metadata endpoints
claude.ai requires while delegating authentication to Google. A single email is
allow-listed. Caddy terminates TLS.

**Rationale.** Satisfies the client without writing an authorization server. Access is
enforced by Google plus an allow-list rather than by a shared secret in a config file, so
there is no long-lived token to leak. No hand-written PKCE or token endpoint means no
hand-written crypto bugs.

**Rejected.** *Hand-rolled OAuth 2.1 + DCR* — the most security-critical code in the
project, written by us. *Cloudflare Access* — strong and free, binds deployment to a
vendor dashboard. *Bearer token for Claude Code only* — faster, but not the client the user
actually uses.

**Consequences.** A Google OAuth client must be registered, and login now depends on
Google's availability. The write surface stays narrow regardless (ADR-014).

---

## ADR-013 {#adr-013}
### Capture the taste graph in v1, analyse in v2

**Extends** PRD FR9.

**Context.** The user's longer-term aim is identifying artists they like, connections
between them, and recurring patterns of style. Not all of that is needed in v1, but the
architecture must support it.

**Decision.** v1 *captures* everything the graph will need — full personnel with instruments
per show, Spotify genres, MusicBrainz edges with date ranges, artist- and track-level
feedback, playback provenance — while *exposing* only straightforward reads plus
`artist_profile` and `artist_connections`. `artists.style_embedding` (`pgvector`) exists and
stays null.

**Rationale.** Capture and analysis have opposite economics. Analysis can be added whenever
there is enough signal to be meaningful; capture cannot be backfilled, because an unrecorded
week is gone permanently. Personnel extraction in particular costs nothing on a call already
being made. Deferring it would silently forfeit graph data for every week until it shipped.

Extraction of personnel also fills MusicBrainz's coverage gap: young sidemen with no
discography appear in club listings and nowhere else.

**Consequences.** v2 analysis is queries against existing data, not a migration. Some
captured fields go unused in v1, which is deliberate.

**Honest limitation.** With `audio-features` gone there is no acoustic description of
anything. Style questions draw on genre tags (coarse), the user's notes (precise, sparse),
and the model's own knowledge of the musician (rich, not grounded in this data). The graph
contributes disambiguation and provenance, not description.

---

## ADR-014 {#adr-014}
### Feedback targets artist, track, or album — never a playlist

**Supersedes** PRD FR9's `ListeningNote.playlist_record_id`.

**Context.** The PRD attaches sentiment to a playlist record, which made sense for nightly
single-act playlists. Under ADR-008 a playlist holds several bands and many hours of music.

**Decision.** `feedback.target_type` is `artist`, `track`, or `album`, with `show_id` and
`week_playlist_id` carried as context. Two MCP tools:
`get_listening_candidates()` (read) then `record_feedback()` (write), which rejects anything
but an explicit resolved target.

**Rationale.** Playlist-scoped sentiment on a 40-hour container says nothing. Splitting
resolution from writing means the model never guesses what "I like this" refers to — it
offers candidates drawn from currently-playing and recently-played, each already joined to
the log, and the user picks. This also satisfies FR10's requirement that the write tool
receive an unambiguous reference, without asking the model to infer one.

**Consequences.** Two round-trips instead of one, in exchange for feedback that is never
misattributed. Feedback lands with provenance — "liked Gerald Clayton, heard via Village
Vanguard, week of 18 Aug" — rather than as a bare thumbs-up.

---

## ADR-015 {#adr-015}
### Run-health tool, plus ntfy for chronic failure only

**Resolves a contradiction** between PRD SC7/FR8 (failures must be visible, chronic
breakage must raise an alert) and Out of Scope (no notifications). An alert nobody is told
about is a log entry.

**Decision.** All run outcomes are queryable via `get_run_health`. Three consecutive
failures for the same club fires one `ntfy` push, then suppresses until that club recovers.
Ordinary single-day failures do not push.

**Rationale.** Honours both requirements where they can be honoured. `ntfy` is a single HTTP
POST with no account and no SMTP deliverability problems. Chronic failure is the one case
that cannot be allowed to pass unnoticed, because it means a club has quietly stopped
producing music.

**Rejected.** *Query-only* — strictly in scope, but a club could be broken for a fortnight.
*Failures surfaced inside Spotify* — no new dependency, pollutes the listening surface.
*Email* — needs credentials and fights VPS IP reputation.

**Consequences.** One outbound dependency. Anyone knowing the ntfy topic can read the
alerts, so the topic must be unguessable; alerts carry no secrets.

---

## ADR-016 {#adr-016}
### Two instruction files, for two different agents

**Context.** Two distinct agents interact with this system: the coding agent that builds it,
and the Claude Project that queries it at runtime. Conflating their instructions would
confuse both.

**Decision.** [`AGENTS.md`](../AGENTS.md) at the repo root instructs the implementer —
conventions, invariants, issue pickup. `docs/claude-project-instructions.md` is pasted into
a Claude Project and holds the runtime contract: connection, tool policy, feedback
confirmation flow, goals.

The tool inventory inside the runtime file sits between generated-content markers, rewritten
by `make project-instructions` from the FastMCP registry.

**Rationale.** `AGENTS.md` conventionally addresses agents working *inside* a repository;
the runtime contract is a different audience entirely. Generating the tool inventory removes
the predictable failure where a tool is added in month three, the pasted instructions still
describe the old set, and the new tool is never called. Claude Projects also cap instruction
length, so the runtime file stays tight and verbose reference stays in the repo.

**Consequences.** Two files to keep current, with the drift-prone half generated. Policy
prose outside the markers is hand-maintained and must be re-pasted into the Project after
meaningful changes.

---

## ADR-017 {#adr-017}
### DC from the CapitalBop JSON API, not scraped pages

**Context.** DC has far fewer dedicated jazz rooms than New York, and its scene is covered by
one consolidated calendar, CapitalBop. The calendar page is a Vue widget whose HTML carries
no events; the widget reads a public WordPress REST API
(`/wp-json/capitalbop/v1/events`, `/calendar`, `/event`) returning structured fields: date,
start time, venue with geocoded address, price, ticket link, tags, an editorial "CB Pick"
flag, a series id, and an HTML description. `robots.txt` allows it.

**Decision.** Read the JSON API directly, one paged pass per daily run. Store **every** event
in the window as a `shows` row, eligible for a playlist or not. Run Haiku only on
playlist-eligible events, only for personnel, over the **description** text — never the
linked magazine article — cached per `(event_id, description hash)`.

**Rationale.** The fragile half of NYC's pipeline is fetch-and-extract: HTML drift,
`trafilatura` failures on widget pages, dates parsed by a model. The API removes all three.
What is left for the model is the one thing the feed does not structure: who is in the band.
Storing every event costs nothing — the same pages are fetched regardless — and makes "when
is X playing?" answerable for acts the filters keep out of playlists. Articles were measured
on all 69 eligible events: present on 18, often a decade old (2010, 2013) or about a different
musician (Lenny Robinson's event links a Donvonte McCoy feature), so they would attach the
wrong sidemen.

**Rejected.** *Scrape the page with Playwright* — renders what the API already returns, with
more failure modes. *Haiku over description plus article* — more text, worse personnel.
*Haiku over every event* — ~$0.10–0.20 a week more for personnel on acts deliberately
filtered out; deferred, not refused (DC §16).

**Consequences.** DC depends on an undocumented API that CapitalBop could change without
notice; chronic-failure alerting (ADR-015) is the safety net. Cancellations must be inferred
from absence, which is only safe after a complete fetch (DC §2). DC's extraction departs from
ARCHITECTURE §5 (no HTML, no date extraction) while keeping its rule that `performers[]` is
captured.

---

## ADR-018 {#adr-018}
### DC: five rolling playlists, Monday weeks, hardcoded groups

**Departs from** ADR-008 for DC only. NYC is unchanged.

**Context.** The user wanted DC consolidated across venues, covering a rolling six weeks from
the current week, and shareable with a friend. Measurement then reshaped the request: the
first cut was 392 events and 147 acts in six weeks, far beyond a 1–2 hour weekly listening
budget; Blues Alley proved high-value enough to stand alone; LaPortas and the Birchmere are
walkable from home and wanted as their own list. Venue lists turned out sparse in their far
weeks (Blues Alley weeks 5–6: zero artists).

**Decision.** Five playlists with permanent Spotify URLs: three DC **slots** (weeks 1–2,
3–4, 5–6) that slide weekly, plus single six-week lists for Blues Alley and AlxJazz. Weeks
start **Monday**. Keyed `UNIQUE (region, slot_key)` in `rolling_playlists`; track history in
`rolling_playlist_tracks`, one row per stint, closed with a reason and never deleted. Each
artist sits in the slot of their earliest show. DC slots cap at 12 artists. The three
**groups** and their playlist definitions are hardcoded in `core/dc_groups.py`; venue
membership of a group is data.

**Rationale.** Fixed slots make sharing work: a friend follows once and never chases a new
dated playlist. Monday weeks because ADR-008's Tuesday start encodes NYC club booking cycles,
which a citywide calendar does not have. Venue lists are small enough that one list covers
the window. Hardcoding groups keeps playlist creation out of reach of the MCP server
(ADR-021).

**Rejected.** *Dated blocks like NYC* ("DC · Oct 12–25") — reuses the NYC reconciler, but a
new playlist to follow every two weeks. *One six-week playlist for DC* — ~40 albums. *Three
slots per venue list* — nine playlists, most slots empty. *Groups as a table, creatable over
MCP* — fully self-serve, but lets the MCP server cause Spotify playlist creation.

**Consequences.** A second reconciler with replace-items semantics alongside NYC's
additions-only one. Retention never touches DC playlists, by construction (it operates on
`week_playlists`). A fourth group is a code change and a deploy. ADR-008's Consequence — "who
is on tonight" is not answerable from Spotify — holds for DC too; `when_is_playing` answers
it.

---

## ADR-019 {#adr-019}
### DC eligibility filters and novelty

**Context.** CapitalBop lists every gig in the region: restaurant residencies, brunch duos,
jam sessions, Maryland and outer-Virginia rooms, non-jazz bookings. Most of it is not what
the user wants to discover, and the listening budget is small.

**Decision.** Eligibility is computed from feed fields before extraction: CB Pick for the
citywide list; ticketed; evening (weekdays from 19:00, Saturday and Sunday from 17:00); at most
three dates in the window; within Washington, Alexandria, or Arlington; never a jam session.
Venues in a group carry a **filter profile** (`all`, `standard`, `standard_no_pick`,
`early_evening`) so a venue can be looser or stricter than the citywide rule. **Novelty**:
an artist whose latest artist-level feedback is `disliked` is excluded permanently; `liked`
excludes for six months; unrated artists keep appearing.

**Rationale.** Each filter was measured against the live feed before being chosen. CB Pick
borrows editorial judgement already made. "Ticketed" and "evening" remove background
restaurant sets; the resident cap removes weekly house acts. Per-venue profiles exist because
venues fail the citywide rule for different reasons: LaPortas publishes no prices at all, so
"ticketed" would reject every LaPortas show; the Birchmere books mostly non-jazz, so it keeps
every filter except CB Pick. Novelty driven only by *stated* feedback means nothing leaves a
playlist unless the user rated it; with an eight-act resident roster at LaPortas, about eight
ratings turn that venue's list into new bookings only.

**Rejected.** *Everything, matching sorts it out* — 60–100 acts per two-week list. *Novelty
by exposure* (anything already played is excluded) — an artist skipped unheard would vanish
silently. *No novelty* — residents never leave. *Tribute and genre gating* — the user chose
to rely on feedback; designed and deferred (DC §16).

**Consequences.** Filters run on CapitalBop's tagging, which is human and inconsistent; a
blank price passes "ticketed" because blank means unknown, not free. Some venues have no
address in the feed and need a hand-set `city_override`. Filter thresholds are product
decisions, recorded here so they are changed deliberately.

---

## ADR-020 {#adr-020}
### DC: two tracks per artist, search as popularity proxy

**Departs from** ADR-010 for DC only.

**Context.** ADR-010's one-album-per-artist rule assumes background listening of four to six
hours a week. DC is sized for *discovery* in 1–2 hours a week, and the user asked for each
artist's top two songs. Spotify's February 2026 migration removed
`GET /artists/{id}/top-tracks` and the `popularity` field on every object; neither came back in
March. There is no first-party notion of "top songs".

**Decision.** Two tracks per artist. Candidates come from one track search
(`artist:"<name>"`), filtered to tracks whose first-listed artist is the matched artist,
deduplicated by normalized title, two taken from different albums where possible. Fewer than
two survivors fall back to the opening tracks of the album ARCHITECTURE §7 would select.

**Rationale.** Search ranking is relevance-ordered and correlates closely with popularity,
and it is an endpoint the system already depends on. The first-artist filter does for tracks
what `include_groups='album'` does for albums: it keeps sideman credits out.

**Rejected.** *First two tracks of the selected album* — deterministic, but album openers are
not the best tracks, and it is one record. *Last.fm `artist.getTopTracks`* — real play counts,
but a new dependency and API key, plus a second name match per track mapped back to Spotify:
another chance for a wrong match.

**Consequences.** Spotify search ranking is opaque and can shift. A wrong match costs two
stray tracks instead of an album; invariant 8's thresholds are unchanged regardless.

---

## ADR-021 {#adr-021}
### MCP may write venue group membership

**Amends** ADR-014's containment statement and `AGENTS.md` invariant 7.

**Context.** The user will add DC venues over time, from the same chat surface used for
feedback, and asked for that to work over MCP. Invariant 7 limits the MCP server to writing
`feedback` rows.

**Decision.** The MCP server may also add a venue to one of the three hardcoded DC groups
(with a filter profile), or deactivate it, through `venue_group_members`. The venue must
resolve to exactly one CapitalBop venue; zero or several returns candidates and writes
nothing. Changes take effect on the next daily run. The MCP server still cannot create a
group or a playlist, touch tracks or matches, edit NYC clubs, or trigger a run.

**Rationale.** Membership is the narrowest write that serves the need. Everything downstream
still flows through the daily reconciler, so the MCP server never spends a Spotify write or
alters a match directly. Resolving against CapitalBop's own venue list means a typo cannot
create a venue that never matches. Deactivation, not deletion, matches how the rest of the
log treats retirement.

**Rejected.** *Edit any source config, NYC clubs included* — a much wider surface for little
gain. *A proposal queue approved from the CLI* — preserves invariant 7 almost exactly, but
loses the one-step convenience that motivated the change.

**Consequences.** Two writable tables instead of one. The runtime instructions require
confirmation before a venue write, as they do for feedback.

---

## ADR-022 {#adr-022}
### Removing a track in Spotify is feedback (LaPortas)

**Context.** Spotify's Web API exposes no dislike or "hide song" signal. But the user owns the
DC playlists and can delete a track from one in the Spotify app, which saves opening a chat to
record a reaction. NYC's reconciler never reads a playlist back from Spotify, and would
re-add a deleted track the next day.

**Decision.** Before reconciling, read each AlxJazz playlist back from Spotify. A track the
database believes is live but Spotify lacks was removed by the user: close its stint with
`removal_reason = 'user'` and never re-add it, in any playlist. If the track came from a
LaPortas show, also write `feedback` with `sentiment = 'disliked'` and `source = 'spotify'`
— for the track; and, when it was the artist's last remaining track, for the artist.
Removals elsewhere in AlxJazz are respected but are not feedback.

**Rationale.** LaPortas is a short list of residents the user will tidy by hand, which is
where the signal is real; scoping there proves the guards on one venue before they cover
everything. Respecting every removal in the playlist costs nothing extra — detection runs per
playlist anyway — and means the user never fights the reconciler. Two removals to dislike an
artist keeps one stray tap from banning them permanently.

**Guards.** Inference only from a complete read of every page. ISRC comparison before calling
a track missing, because Spotify relinks recordings across IDs and `linked_from` was removed
in February 2026. More than half a playlist missing at once is an anomaly: record nothing,
alert. The reconciler writes the database before Spotify, so its own removals can never look
like the user's.

**Rejected.** *All DC playlists* — beyond the scope the user set. *Give LaPortas its own
playlist so the feature applies cleanly* — six playlists, and a thinner AlxJazz. *Skips from
recently-played* — 50 tracks of history and no skip flag.

**Consequences.** The pipeline now writes `feedback`, which was previously MCP-only; this is
not an MCP write, so invariant 7 is unaffected. Playlists must stay non-collaborative, or
someone else's removal becomes the user's dislike.

---

## ADR-023 {#adr-023}
### ntfy for new artists at LaPortas

**Amends** ADR-015, which reserved `ntfy` for chronic failure.

**Context.** Once novelty retires the LaPortas residents, that list mostly stays short, and
the event worth knowing about is a new name booked within walking distance.

**Decision.** When a run adds to AlxJazz an artist from a LaPortas show who has never been in
AlxJazz before, send one `ntfy` push for the run, listing every such artist with day and time.
The first run, which seeds the list, sends nothing. Same topic as failure alerts,
distinguished by title and tag.

**Rationale.** It is the one DC change that is time-sensitive and that the user would
otherwise only notice by opening Spotify. Batching keeps it to at most one push a day.
Counting only post-novelty additions means rated residents never push.

**Rejected.** *A separate topic* — more configuration for one low-volume message type.
*Pushes for every playlist* — noise; the user asked for LaPortas.

**Consequences.** `ntfy` carries two message types. Alerts now reveal which venue near the
user's home they follow, so the topic's unguessability (ADR-015) matters slightly more.

---

## ADR-024 {#adr-024}
### DC and Blues Alley playlists are public

**Amends** `AGENTS.md`'s "deliberately absent" entry for sharing, which this narrows rather
than removes.

**Context.** The user will share DC playlists with a friend. Every playlist is created with
`public: false`, and Spotify does not reliably let other people open a private playlist from
a link.

**Decision.** Visibility is set per playlist in `core/dc_groups.py`. The DC list and
Blues Alley are public; AlxJazz is private. "Sharing" means only that others can view and
follow a playlist. Multi-user support — other people logging in, recording feedback, or using
MCP — remains absent.

**Rationale.** AlxJazz is built around two venues within walking distance of home; public, it
would sit on the user's profile and narrow down where they live. Per-playlist visibility in
the same hardcoded definitions as the groups makes changing this a one-line edit.

**Consequences.** The friend's reactions do not reach the system. The user's removals curate
what the friend hears. Public playlists appear on the user's Spotify profile unless hidden
there by hand.

---

## ADR-025 {#adr-025}
### DC: one six-week list instead of three slots

**Amends** ADR-018. Its three-slot structure is replaced; Monday weeks, hardcoded groups,
stints and the `(region, slot_key)` key stand.

**Context.** ADR-018 split the `dc` group into three two-week slots on the expectation of
dozens of artists. Measured on the live feed (2026-10-11) the `dc` group yields about ten
artists in the whole six-week window, five and five in the first two slots and none in the
third. Three playlists for ten artists means two thin lists and an empty one, and the friend
the lists are shared with has three things to follow.

**Decision.** One public playlist, `DC Jazz · Next 6 Weeks` (`('dc', 'next-6-weeks')`),
ordered by each act's earliest show. The 12-artist cap now applies to the whole list. Three
playlists in total: the DC list, Blues Alley, AlxJazz.

**Rationale.** The list is short enough to be read as a whole, and a six-week view is what
the venue lists already give. Fewer playlists means fewer permanent URLs.

**Consequences.** The cap is a ceiling on the whole window rather than on each slot (12
artists, down from 36 in total capacity); at the measured ten it does not bind, and it is
`DC_CAP` in `core/dc_groups.py` if the feed grows. `removal_reason = 'moved'` is no longer
produced by dc slot changes; it stays in the schema for an artist moving between playlists.
No migration: no production DC run had created a playlist under the old slot keys.
