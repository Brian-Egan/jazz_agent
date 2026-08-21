# Product Requirements Document: NYC Jazz Discovery Engine

**Author:** Brian (Product Owner)
**Prepared for:** Architecture review (Claude Opus 5) and downstream implementation (Claude Code)
**Status:** Draft v1
**Date:** 2026-08-20

---

## 1. Summary

A single-user application that scrapes the event schedule pages of a configurable list of NYC jazz clubs, identifies which artist is playing each club each night, and automatically builds a Spotify playlist of that artist's music. The goal is passive discovery: surface artists the user wouldn't otherwise encounter, let them audition the music at home on their main system, and flag opportunities to see acts live when schedule and interest overlap.

Spotify holds only a rolling window of recently created playlists (7 days). The durable record of everything the app has ever surfaced, club, date, artist, matched tracks, lives in the app's own database, not in Spotify. That record is exposed through an MCP server so the user can query their discovery history conversationally through Claude (e.g., "who was that solo pianist I heard last week?"), and so the user can log feedback in the moment, in conversation, while actually listening ("I really liked that one") without opening any app. Building up that liked/disliked signal over time is itself a core goal, not just a nice-to-have: the point isn't only to surface new artists, it's to learn what the user actually responds to.

This is a personal tool for one user, not a multi-tenant product. Build for correctness and low maintenance overhead, not for scale.

**Portability note:** the working hosting plan is a small VPS (see Section 8.1), with the eventual goal of adding a simple web UI and moving the whole system to run locally on a Raspberry Pi. The architect has final say on the hosting decision, but nothing in this version should make the Pi migration hard regardless of which option is chosen. Section 8.6 covers the specific architectural constraint.

---

## 2. Problem & Goal

The user actively builds a curated jazz vinyl and streaming collection but currently discovers new artists through algorithmic recommendations and personal research. NYC's jazz clubs book working musicians, many of whom aren't well represented in mainstream streaming recommendation engines. Manually checking club calendars and cross-referencing artists against Spotify is a chore the user doesn't do consistently, so a lot of potentially compelling music never reaches them. Separately, even when the user does encounter something they like, there's currently no easy way to capture that reaction in the moment, so it's easy to lose track of what actually landed.

**Goal:** automate the discovery loop. Every day, without user intervention, a fresh playlist appears per club showing that night's lineup, ready to listen to that evening. And make it effortless to capture what the user actually liked, in conversation, at the moment they're listening, so the system builds a real picture of taste over time, not just a feed of new names.

---

## 3. Success Criteria

- The app runs unattended, daily, with no manual triggering required.
- A user checking Spotify each morning sees one playlist per configured club reflecting that night's show.
- The user can ask Claude conversational questions about anything the app has ever surfaced ("who was that solo pianist last week," "what played at Smalls on the 12th") and get a correct answer sourced from the app's own log, not from Spotify.
- The user can tell Claude, in conversation, that they liked or disliked something they're listening to, and that reaction is durably logged against the correct show/artist without the user having to leave the conversation or open another app.
- Playlists contain real, relevant tracks from the correct artist at least the large majority of the time (exact accuracy target TBD after v1 — see Open Questions).
- Adding or removing a club from the scrape list requires only a data change, not a code change or deploy.
- Failures (site down, artist not found, no show that night) degrade gracefully and are visible to the user, not silent.

---

## 4. Users

Single user (Brian). No auth/multi-tenancy requirements beyond securing the user's own Spotify credentials and the MCP access token. No traditional browser-based UI is required for v1; the MCP server is the one component that does need to be reachable from outside, see Section 8.2.

---

## 5. Scope

### In scope (v1)
- Config-driven list of jazz clubs (venue name, schedule page URL, scraping metadata), stored in a database or config file, editable directly by the user without a UI.
- Daily scheduled scrape of each configured club's schedule page.
- Extraction of the artist(s)/act playing each club that night.
- Matching extracted artist names to Spotify artists and pulling representative tracks.
- Daily creation of one Spotify playlist per club, named and described so the night and venue are identifiable.
- A rolling 7-day retention window for playlists in Spotify: playlists older than 7 days are removed from Spotify (unfollowed/deleted), since Spotify is no longer the archive.
- A persistent, durable log of every show/artist/playlist the app has ever produced, independent of Spotify, that serves as the actual archive.
- An MCP server, secured with an access token, exposing that log for conversational queries, and accepting feedback (liked/disliked, with an optional note) that Claude writes on the user's behalf during conversation.
- Logging/alerting on scrape failures, no-match artists, or nights with no listed show.

### Out of scope (v1, candidates for later)
- Apple Music support (user may revisit the service choice, see Section 11).
- Any traditional web/browser UI for managing clubs or browsing history (conversational query via MCP covers the browsing need for v1; a UI may follow later, see Section 12).
- Cross-referencing show dates against the user's personal calendar to flag "you're free and this artist is playing" opportunities.
- Multi-user support, sharing, or public deployment.
- Notifications (email, push, SMS) when playlists are ready.
- Automatic quality scoring or filtering of scrape results.

---

## 6. System Overview

The system has two halves that run on different rhythms, and the architecture should treat them as two components, not one:

**A. The daily batch pipeline** (once per day):

1. A scheduled trigger invokes an orchestration function.
2. The orchestrator reads the club config.
3. For each club, a scraping step fetches and parses that club's schedule page for tonight's date, extracting artist name(s).
4. Each extracted artist name is passed to a Spotify matching step: search the Spotify catalog, resolve to a specific artist (handling ambiguity, see Section 9), and pull a track set.
5. A playlist step creates that club's "tonight" playlist in the user's Spotify account with the resolved tracks.
6. As part of the same run, any playlist older than 7 days is removed from Spotify (see FR6).
7. The show, matched artist, and resulting playlist are written to the persistent log (the durable archive, independent of Spotify). Run results (success, partial failure, full failure per club) are logged alongside it.

**B. The MCP query & feedback server** (always available, low traffic, invoked whenever the user asks Claude a question about their listening history or tells Claude they liked/disliked something):

1. Claude connects to the MCP server using the user's access token.
2. For queries, the server exposes read-only tools over the persistent log (search by artist, by club, by date range, by free-text description) and returns results for Claude to reason over and answer in conversation.
3. For feedback, the server exposes a narrow write tool that attaches a sentiment (liked/disliked) plus optional note to a specific show/artist/playlist Claude has already resolved from the conversation. This is the only way data flows back into the log outside the daily batch run.
4. This component needs to be reachable over the network whenever the user wants to query it or log feedback, which has implications for hosting (see Section 8.1) regardless of where the batch pipeline runs.

Building blocks (illustrative, architect to confirm or revise regardless of the hosting decision in Section 8.1):
- A scheduler for the daily trigger (e.g., EventBridge Scheduler on AWS, or plain cron elsewhere).
- Scraping, matching, and playlist-management logic as one or more invocable units (Lambda functions, or a single script/service, depending on hosting).
- A database for club config, show history, and run logs (DynamoDB on AWS, or SQLite/Postgres elsewhere).
- A secrets store for Spotify OAuth credentials and the MCP access token (Secrets Manager on AWS, or a local secrets file/vault elsewhere).
- Structured logging and alerting on repeated failures.
- A persistent (or on-demand but reliably reachable) MCP server process, secured by the access token.

The architect should treat this as a starting proposal, not a mandate. Cost and operational simplicity for a single-user, once-a-day-plus-occasional-queries workload should drive the final decision.

Whatever hosting is chosen, the core logic (scraping, matching, playlist building, log writes) should be written as plain, dependency-injected Python (or whatever language is chosen) modules that don't assume a specific hosting environment. Scheduler/trigger code and any cloud SDK calls should be a thin wrapper around that core logic, not woven through it. See Section 8.6.

---

## 7. Functional Requirements

### FR1: Club Configuration
- A durable, directly-editable store (a config file or a database table, architect's choice given the hosting decision in Section 8.1) holds the list of clubs.
- Each club entry needs at minimum: club name, schedule page URL, and enough scraping metadata (e.g., CSS selectors, date format hints) to support a maintainable scraper for that specific site.
- No UI is required. The user will edit the config directly (via console/CLI, a text editor, or a DB client, whichever fits the chosen hosting) as clubs are added or removed.
- Config changes should take effect on the next scheduled run without a deploy.

### FR2: Schedule Scraping
- For each configured club, fetch that club's schedule page and extract the event(s) for the current date (or the relevant "tonight" date, accounting for the club's timezone, which is always America/New_York).
- Because every club's site has a different structure, scraping logic must be modular per club rather than one generic parser. The config-driven metadata (FR1) should minimize hardcoded logic where possible, but a fully generic scraper across arbitrary jazz club websites is not realistic. Expect a light per-club adapter pattern.
- Handle the case where a club has no listed show that night (dark night, private event, page shows nothing) without erroring the whole run.
- Respect each site's robots.txt and apply reasonable rate limiting/backoff. Scraping should look like a single polite visit per club per day, not a crawl.

### FR3: Artist Identification & Normalization
- Extract the performing artist or act name as listed on the schedule page (e.g., "The Bill Evans Legacy Trio," "Ravi Coltrane Quartet").
- Normalize obvious noise: featured/supporting artist notation, "early/late set" labels, ticket/price text that may be co-located with the artist name in the scraped HTML.
- **Open design question for the architect:** some clubs book multiple distinct acts at one venue on one night (an early set and a late set with different bands, or a multi-act bill). Per the user's decision in Section 11, playlists are organized one-per-club-per-night, so the default behavior should be: if multiple acts are listed for the same club on the same night, include all of them in that club's single playlist for the night, clearly delineated in the playlist description or track ordering. Flag this to the user if it turns out to produce cluttered or low-quality playlists in practice.

### FR4: Spotify Matching
- For each identified artist name, query the Spotify API to resolve a specific Spotify artist entity.
- Handle ambiguity: common names, tribute acts, ensembles named after a bandleader who may also have solo entries, artists with no meaningful Spotify presence.
- Recommended default matching strategy (architect to refine): prefer exact or near-exact name match; among candidates, weight toward artists tagged with jazz-adjacent genres and higher popularity/follower counts as a tiebreaker, since that's more likely to represent the actual touring act than an obscure same-named artist.
- When no reasonable match is found, log it clearly (club, date, raw scraped name) rather than silently skipping or guessing. These logged misses become a useful signal for the user to manually investigate a promising unmatched act.
- Track selection per matched artist: pull a representative set of tracks. A reasonable default is the artist's top tracks plus one or two additional tracks from their most popular or most recent album, aiming for roughly 8-15 tracks per artist so a single-set listen is a reasonable sample, not just three singles. Architect/implementation can tune this.

### FR5: Playlist Creation
- One playlist per club per night, created or updated via the Spotify API against the user's personal Spotify account.
- Playlist naming and description must make the club, date, and artist(s) immediately clear without opening the playlist, since the user will be scanning a growing list over time. Suggested convention: `[Club Name] — [Weekday, Mon DD]` as the title, with the artist name(s) in the description.
- Playlists should be added to the user's library (followed) so they appear in their Spotify account automatically.

### FR6: Playlist Lifecycle & Retention
- Each night's playlist is created fresh (never overwrites a prior night's playlist for that club) and lives in the user's Spotify account for exactly 7 days from creation.
- As part of each daily run, any playlist older than 7 days is unfollowed/deleted from the user's Spotify account. Spotify is a listening surface for what's current, not the archive; retention cleanup keeps it from accumulating hundreds of playlists over time.
- Deleting a playlist from Spotify must never delete or affect its corresponding record in the persistent log (FR9). The two are independent; Spotify holds the audio, the log holds the history.

### FR7: Scheduling & Orchestration
- Runs once daily, timed early enough that the playlist is ready well before that evening's show (late morning or early afternoon Eastern is reasonable; exact time TBD, should be configurable).
- Each club's scrape/match/playlist pipeline should fail independently. One club's site being down or restructured should not block the other clubs from getting their playlists that day.

### FR8: Error Handling & Visibility
- Every run writes a structured result per club (success / no show tonight / scrape failure / no artist match / partial match) to a persistent log.
- Repeated failures for the same club (e.g., three consecutive days of scrape failure, suggesting the site structure changed) should raise an alert so the user knows a scraper adapter needs attention, rather than discovering silently that a club has been producing empty playlists for a week.
- No user-facing notification system is required in v1 (see Out of Scope), but the logging should be detailed enough to support adding one later without rearchitecting.

### FR9: Persistent Discovery Log
- Independent of Spotify and independent of the 7-day playlist retention window, the app maintains a permanent record of every club/date/artist/playlist it has ever produced, plus any feedback the user has given on it. This is the actual archive, and it must never be pruned or expire on its own.
- Each log entry should carry enough to answer both structured questions ("what played at the Vanguard on Feb 3rd") and fuzzier ones ("who was that solo pianist last week"): club, date, matched artist, ensemble type/instrumentation if derivable from the scraped listing (e.g., "solo piano," "quartet"), genre tags from Spotify, and the original Spotify playlist URI (even after the playlist itself has been deleted from Spotify, its ID and track listing should remain in the log).
- **Feedback is a first-class part of the log, not an afterthought.** When the user tells Claude they liked or disliked something they're listening to, that reaction should be attachable to the specific show/artist/playlist it refers to: a simple sentiment (liked/disliked/neutral) plus optional free text on why. This is the same field referred to below as the notes mechanism, it now serves two purposes: descriptive recall ("stirring rhythm") and an explicit like/dislike signal the user is deliberately building up over time to track what they respond to.
- See Section 10 for an important limitation: Spotify no longer provides audio-characteristic data (tempo, mood, energy) for new API integrations, so mood/vibe-style recall depends on this notes/feedback field rather than anything pulled automatically from Spotify. The field is optional and empty by default; the log is still useful without it, just less capable of answering "vibe" or "what have I liked" questions until entries accumulate.

### FR10: MCP Query & Feedback Interface
- The app exposes an MCP server so the user can query the discovery log conversationally through Claude, and so Claude can write feedback into the log on the user's behalf during a normal conversation, rather than the user having to open an app or a database to do either.
- Access is controlled by a token issued to the user; the server should not be reachable without it. This matters regardless of hosting choice, since the server needs to be reachable over the network for Claude to connect to it.
- **Read tools (minimum viable set):** search shows/artists by name, look up what played at a given club on a given date (or date range), free-text search over notes/feedback and other log fields for descriptive queries, and a way to list recent liked (or disliked) artists so the user can ask things like "what have I liked lately."
- **Write tools (minimum viable set):** attach a feedback entry (sentiment plus optional note) to a specific show/artist/playlist the user is referring to in conversation. This is the only write surface the MCP server needs for v1; it should not be able to modify club config, trigger scrapes, or alter playlists. Keeping the write surface narrow limits what a compromised token could do and keeps the server's job simple: read the log, and append feedback to it.
- Because feedback arrives via natural language in conversation ("I really liked that last one"), Claude is responsible for resolving "that last one" to a specific log entry before calling the write tool; the tool itself should expect an unambiguous show/artist/playlist reference, not do that resolution itself.
- Response data for read queries should be small and conversational (a handful of matching shows with enough detail to answer the question), not a dump of the full log.

---

## 8. Non-Functional Requirements

### 8.1 Hosting & Cost

**Working plan: a small always-on Linux VPS**, with the final decision left to the architect. AWS is not a hard requirement; it's what the user already knows, which has real value, but a VPS is the better structural fit once the MCP server is factored in, and it's the closest match to the eventual Raspberry Pi target, so that later migration is closer to "move the process" than "reinterpret managed services." The full comparison below should still be reviewed with the architect before locking this in, since implementation-level tradeoffs (their own operational preferences, existing tooling, etc.) may change the call.

**Option A: AWS serverless** (Lambda, EventBridge, DynamoDB, Secrets Manager). Familiar, scales to zero, costs close to nothing for the batch pipeline alone. The friction is the MCP server: MCP is naturally a persistent, session-oriented process, and while it's possible to run it behind API Gateway + Lambda, that adds real plumbing (cold starts affecting query latency, session/state handling) for a workload that's fundamentally "a small always-available service," which Lambda isn't really designed to be. Workable, but not the most natural fit for half the system.

**Option B: a small always-on Linux VPS** (e.g., a low-cost box from a provider like Hetzner, DigitalOcean, or similar). **This is the working plan.** The batch job runs as a cron entry; the MCP server runs as a normal long-running process; both are plain code with no cloud-specific plumbing. This is a more natural home for an always-reachable MCP server, costs a small flat monthly fee instead of AWS's per-service pricing, and, notably, is structurally identical to the eventual Raspberry Pi target: moving from a VPS to a Pi later is "move the same process to different hardware," not "reinterpret serverless-managed services as long-running processes." Given the Pi migration is already a stated goal, this option arguably makes that migration close to a non-event. The tradeoff: the user owns OS patching, security updates, uptime monitoring, and backups, and the batch job and query server share a single point of failure since they're on one box.

**Option C: skip straight to the Raspberry Pi now.** Only sensible if the user already has a Pi that can be reliably always-on (stable power, network, and a way to expose the MCP server securely to the internet, e.g., a Cloudflare Tunnel or Tailscale Funnel, since a home network has no public IP by default). If that's true today, there's an argument for skipping the interim step entirely rather than building it twice.

**Recommendation, pending the architect's final call:** proceed on the assumption of Option B (small VPS), since it's the best structural match for a system with both a batch job and an always-reachable query server, and it de-risks the eventual Pi move more than AWS serverless does. Section 8.6's decoupling requirements keep this reversible either way, so if the architect has a good reason to prefer AWS or to go straight to the Pi, that's a low-cost pivot, not a rewrite.

### 8.2 Security
- Spotify OAuth credentials (client ID, client secret, refresh token) stored in a proper secrets store (Secrets Manager on AWS, or an equivalent local secrets mechanism elsewhere), never in code or plain config.
- The MCP server is a public-facing endpoint by necessity (Claude needs to reach it), and it must require the user's access token on every request, for both reads and the feedback write path. There is no "trusted network" assumption to lean on here; treat it as an internet-facing credentialed API even if it's hosted on a home Pi behind a tunnel.
- The write surface is intentionally narrow (feedback only, see FR10); even with a valid token, there's no path from this server to modifying club config, playlists, or anything outside the feedback log. That containment is itself a security property worth preserving as the system evolves.
- Beyond the MCP server, no other public-facing endpoints are required for v1. If a web UI is added later, it should sit behind its own authentication.

### 8.3 Reliability
- The daily run should be idempotent for a given club/date: if it's manually re-triggered (e.g., after fixing a scraper bug), it shouldn't create duplicate playlists for the same club/night.
- Transient failures (network blips, rate limits) should retry with backoff before being logged as a hard failure.

### 8.4 Maintainability
- Because each club's website is a separate, unmaintained-by-us integration point that can change layout at any time, scraper adapters should be isolated from each other and easy to update independently (one club breaking shouldn't require touching shared code).
- Config (club list) and code should be cleanly separated per FR1, so the common maintenance action (adding/removing a club) never requires a code change.

### 8.5 Observability
- Dashboards/alarms (CloudWatch on AWS, or an equivalent lightweight option elsewhere) sufficient to answer, at a glance: did last night's run succeed, which clubs failed or had no match, and is any single club chronically failing.

### 8.6 Portability (self-hosted / Raspberry Pi migration path)
The user has flagged intent to later add a simple web UI and potentially run this whole system locally on a Raspberry Pi. Combined with Section 8.1's point that AWS isn't a hard requirement even for v1, this means the codebase should never be tightly coupled to any one hosting environment. Concretely:

- **Decouple business logic from any cloud provider.** Scraping, artist normalization, Spotify matching, playlist lifecycle, and log-writing logic should live in plain modules with no direct AWS SDK (or any other provider-specific SDK) calls inside them. Scheduler/trigger handlers call into this logic; they don't contain it. This is the single most important constraint in this section, since it's what lets the same code run under a cron job on a VPS or a Pi with minimal changes.
- **Abstract the data layer.** Don't scatter raw DynamoDB (or whatever database is chosen) calls throughout the codebase. Put a narrow interface in front of config storage, the discovery log, and run logs (get club list, record a show, record a playlist, log a run outcome) with one concrete implementation for whatever hosting is chosen first. A future SQLite-backed implementation for local/Pi use should be a matter of writing a second implementation of that same interface, not a rewrite.
- **Abstract secrets/config access.** Spotify credentials, the MCP access token, and any other config values should be read through a small config/secrets interface, not provider SDK calls sprinkled through business logic, so a `.env` file or local secrets store can stand in later.
- **Keep scheduling logic separate from the trigger mechanism.** The "what to do once a day" logic shouldn't assume anything about a specific scheduler; a cron job invoking the same entry point locally should be a drop-in equivalent.
- **Keep the MCP server itself hosting-agnostic.** It should be a normal long-running process speaking MCP over its transport of choice, not something wired specifically to one provider's function-as-a-service model, so it can move between hosting options the same way the batch pipeline does.

This doesn't mean over-engineering v1 with speculative multi-backend support that's never exercised. It means drawing the module boundaries in the right places now, so that swapping what sits behind them later is a contained change instead of a rewrite.

---

## 9. Data Model (indicative, not prescriptive)

**Club**
- club_id, name, schedule_url, scraper_adapter_id/selector config, timezone (default America/New_York), active flag

**Show** (one row per club/date the scraper runs)
- club_id, date, raw_scraped_artist_text, normalized_artist_name(s), scrape_status

**ArtistMatch**
- show_id, matched_spotify_artist_id, match_confidence/method, track_ids_used

**PlaylistRecord** (the permanent log entry; survives even after the Spotify playlist itself is deleted per the 7-day retention window)
- club_id, date, spotify_playlist_id, spotify_playlist_url, track_ids, ensemble_type (e.g., solo/trio/quartet, if derivable from the scraped listing), spotify_removed_at (null until the 7-day cleanup removes it from Spotify), created_at

**ListeningNote** (user- or Claude-authored; the main lever for "vibe"-style recall and the record of what the user liked or disliked, see FR9 and Section 10)
- playlist_record_id, sentiment (liked/disliked/neutral, nullable), note_text (nullable), created_at

**RunLog**
- run_id, date, club_id, outcome (success/no_show/scrape_fail/no_match/partial), detail/error message

---

## 10. Open Questions & Risks for the Architect

These are unresolved enough that they should be addressed explicitly in the technical spec before implementation, not assumed away:

1. **Multi-act nights.** See FR3. Some clubs book two or more distinct acts on the same night. The default behavior (combine into one playlist) is a reasonable starting point but should be validated against a few real club calendars before locking in.
2. **Artist match confidence threshold.** How aggressive should auto-matching be versus how often should it defer to a logged "no confident match" outcome? Being wrong (matching the wrong same-named artist) is arguably worse than not matching at all, since it pollutes the discovery experience with irrelevant music. The spec should define a conservative default and make it tunable.
3. **Scraper fragility and site variety.** A handful of clubs each with a bespoke site structure means a handful of bespoke scraper adapters, each of which can silently break when the venue redesigns its site. The spec should define how new adapters are added (config-only vs. requiring code) and how breakage is detected quickly.
4. **Legal/ToS considerations for scraping.** Not a blocker for a personal-use tool scraping a handful of public schedule pages at low frequency, but worth a brief acknowledgment in the spec that this is respectful, low-volume, personal-use scraping, not a scaled or redistributed data product.
5. **Spotify no longer exposes audio-characteristic data.** Spotify permanently deprecated its audio-features/audio-analysis endpoints (tempo, key, energy, mood-adjacent metrics) for any app registered after November 2024, and there's no official replacement. This means the app cannot pull anything like "stirring rhythm" or "melancholy" directly from Spotify's catalog data. FR9's ListeningNote/feedback field is the intended answer: free text plus sentiment that the user or Claude attaches during or after listening, which the MCP server can search over. This should be validated with the user: is a manual/conversational notes step acceptable, or is there appetite for a heavier alternative (e.g., running independent audio analysis on preview clips)? The PRD assumes the lightweight notes approach is sufficient for v1.
6. **MCP reachability mechanism.** However hosting is decided (Section 8.1), the MCP server needs a stable, internet-reachable, token-authenticated endpoint. On the working-plan VPS (or AWS) this is straightforward (a domain pointed at the box, or an API Gateway URL); on a home-hosted Raspberry Pi, whether now or after a later migration, it requires a tunneling solution (e.g., Cloudflare Tunnel, Tailscale Funnel) since home networks don't have a stable public IP by default. Worth deciding early since it affects whichever hosting option the architect lands on.

---

## 11. Assumptions

- Music service is Spotify for v1. The user may revisit this decision later, so the architecture should keep the "resolve artist to catalog + pull tracks + create playlist" logic behind a reasonably clean boundary rather than hardcoding Spotify calls throughout the system, without over-investing in a multi-provider abstraction that isn't needed yet.
- Playlists are organized one per club per night, live in Spotify for 7 days, and are then removed from Spotify. The permanent record lives in the app's own log, not in Spotify, per the user's explicit direction.
- Club configuration lives in a config file or database and is edited directly by the user; no admin UI is in scope.
- Single user, single Spotify account, no multi-tenancy.
- No user-facing push/email notification is required for v1; the user interacts with the system either by checking Spotify directly or by asking Claude questions through the MCP server.
- Hosting: the working plan is a small always-on VPS (see Section 8.1), with the final decision left to the architect. AWS is not a hard requirement. The codebase should be written so hosting is a contained decision, not baked into the core logic, regardless of which option is ultimately chosen. See Sections 8.1 and 8.6.
- The MCP server is secured with an access token; there is no expectation of a fully public, unauthenticated endpoint.

---

## 12. Future Considerations (explicitly out of scope for v1)

- Migrate to a local/self-hosted deployment (e.g., Raspberry Pi), paired with a simple web UI for browsing clubs, shows, and the discovery log. Section 8.6 defines what v1 needs to do architecturally to keep this option open.
- Revisit Spotify vs. Apple Music, or support both, if the user's preference changes.
- Calendar cross-reference: flag nights where a discovered artist is playing and the user's calendar shows them free, to support the "see them live" half of the original goal.
- A traditional browsing UI over the discovery log, complementary to (not a replacement for) the conversational MCP interface, if that becomes a nicer way to skim history than asking questions.
- Notification (email/push) when the day's playlists are ready.
- Richer audio characterization than the free-text notes field allows, e.g., running independent audio analysis on track previews, if the notes-based approach in FR9 turns out to be too thin for good "vibe" recall.
- Using the accumulated feedback log to actually influence future matching or track selection (e.g., favoring artists/genres similar to what's been liked). FR9/FR10 cover capturing the feedback; acting on it algorithmically is a natural v2, not v1, since it needs enough data to be meaningful first.

---

## 13. Appendix: Example Club Config Entry (illustrative only)

```json
{
  "club_id": "village-vanguard",
  "name": "Village Vanguard",
  "schedule_url": "https://villagevanguard.com/schedule/",
  "timezone": "America/New_York",
  "active": true,
  "scraper_adapter": "village_vanguard_v1",
  "selector_hints": {
    "event_container": ".event-listing",
    "artist_name": ".event-title",
    "date": ".event-date"
  }
}
```

The exact schema is the architect's/implementation's call; this is illustrative of the kind of metadata FR1 expects the config to carry.
