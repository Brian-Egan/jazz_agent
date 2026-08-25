# Research: `POST /v1/playlists/{playlist_id}/tracks` returns bare 403 while other playlist writes succeed

**Date researched:** 2026-08-24
**Related:** [`docs/DECISIONS.md` ADR-005](../DECISIONS.md#adr-005) documents a *different* Spotify restriction (loss of
`audio-features`, `audio-analysis`, `related-artists`, `recommendations` for apps registered after the
27 November 2024 policy change). This document is about a separate, newer restriction that surfaced in
2026 and affects playlist *item writes* specifically. The two are not the same change; ADR-005 is not
edited by this document.

---

## Summary verdict

**The 403 is caused by Spotify's February 2026 Web API migration, not by a Development Mode /
Extended Quota Mode permissions gap.** `POST /v1/playlists/{playlist_id}/tracks` (and its sibling
GET/PUT/DELETE `.../tracks` endpoints) were **removed** and replaced by `POST/GET/PUT/DELETE
/v1/playlists/{playlist_id}/items`, with enforcement rolling out to *existing* Development Mode apps
on **9 March 2026**. Because today is 24 August 2026, any Development Mode app — regardless of when it
was originally registered — has already had this enforced. Playlist creation (`POST /me/playlists`)
and playlist-details editing (`PUT /playlists/{playlist_id}`) were **not** renamed or removed, which is
exactly why they keep working while only the tracks-add call fails. This is confirmed directly by
Spotify's own official changelog and migration guide (see Evidence, source 1), and independently
corroborated by a named real-world fix report dated 4 August 2026 ("switching from `/playlists/{id}/tracks`
to `/playlists/{id}/items` resolved the 403 Forbidden error for me"). **Confidence: high.** This is not
a case of "no legitimate path exists" (unlike the Extended Quota Mode business-tier gate, which is a
real and separate obstacle for a different set of restrictions) — it is a straightforward URL-path
migration that requires a one-line code change and no dashboard setting, app review, or waiting period.

---

## Evidence

### 1. Official Spotify documentation (developer.spotify.com) — CONFIRMED, primary source

Fetched directly (raw HTML, not just search snippets) on 2026-08-24:

- **[Web API Changelog — February 2026](https://developer.spotify.com/documentation/web-api/references/changes/february-2026)**
  explicitly lists, under "Changes to endpoints":
  > `[REMOVED] Add Items to Playlist (POST /playlists/{id}/tracks) – Adds tracks or episodes to a
  > playlist. Use POST /playlists/{id}/items instead`

  and under "Endpoints still available" (i.e. unaffected by this migration):
  > `Change Playlist Details (PUT /playlists/{id})` — *Updates a playlist's name, description, or visibility.*
  > `Create Playlist (POST /me/playlists)` — *Creates a new playlist for logged in users.*

  This is the direct, official explanation for the asymmetry in the bug report: the create and
  rename/edit-details calls were never touched by the migration, while the tracks-add call was hard
  replaced.

- **[February 2026 Web API Migration Guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide)**
  gives the enforcement timeline explicitly:
  > `February 11, 2026` — New Development Mode apps are created with new restrictions
  > `March 9, 2026` — Existing Development Mode apps are migrated to new restrictions

  And states plainly who is/isn't affected:
  > "Extended Quota Mode apps: No migration required. Apps in extended quota mode are not affected by
  > any of the changes described in this guide — all existing endpoints, fields, and behaviors remain
  > unchanged... Development Mode apps: This guide is for you."

  This directly confirms the developer's hypothesis that there's a Development-Mode-vs-Extended-Quota
  distinction — but the distinction is *this specific endpoint migration*, not a missing approval
  step that can be requested for playlist writes specifically.

- **[Add Items to Playlist reference page](https://developer.spotify.com/documentation/web-api/reference/add-tracks-to-playlist)**
  (the old `/tracks` path) now carries a page title of "Add Items to Playlist **[DEPRECATED]**" and
  the in-page banner: *"Deprecated: Use Add Items to Playlist instead."* — pointing at the new
  `/items`-path version of the same reference page.

- **[Quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)** doc
  (CONFIRMED, official): describes Development Mode (5-user allowlist cap, app-owner-Premium
  requirement, quota-bucket rate limiting → `429`, not `403`, when exceeded) versus Extended Quota
  Mode (unlimited users, higher rate limits, no allowlist). It does **not** document a per-endpoint
  write-permission toggle, and does not itself mention the tracks→items rename — that's covered by the
  changelog/migration-guide pages above, which is why the developer's dashboard search for a
  "playlist-item-write toggle" turned up nothing: **there is no such toggle; it's a URL change, not a
  permission gate.**

- **Extended Quota Mode eligibility** (from the same quota-modes documentation, and corroborated by
  the GitHub issue below): since 15 May 2025, Spotify only accepts Extended Quota applications from
  organizations — legally registered business entity, an active launched commercial service, ≥250k
  monthly active users, company email for the application, review up to six weeks. **Confirmed
  official policy.** This is real and does genuinely lock personal/hobbyist apps out of Extended Quota
  Mode — but per the migration guide above, Extended Quota Mode is *not* required to fix this specific
  403; it's a red herring for this particular symptom (it matters for the *unrelated* restrictions
  ADR-005 already documents, and for the 5-user/allowlist/rate-limit behaviors of Development Mode
  generally).

### 2. GitHub — CONFIRMED, primary source (fetched directly via `gh api`, not paraphrased)

- **[spotify/spotify-web-api-ts-sdk#159](https://github.com/spotify/spotify-web-api-ts-sdk/issues/159)**
  — "Restore playlist write access for Development mode apps — personal/hobbyist use is completely
  blocked," opened 2026-05-07 by a developer building an MCP-server-based personal playlist tool (same
  category of project as jazz_agent). Reports the identical symptom: `GET/POST/DELETE
  /playlists/{id}/tracks` and `POST /users/{id}/playlists` (an even older, already-removed endpoint;
  current equivalent is `POST /me/playlists`) return 403 for the authenticated app owner on their own
  data, while read-only endpoints work. The issue itself argues (before the fix was known) that
  Extended Quota Mode is now enterprise-only and therefore no legitimate path exists.

  **The resolving comment**, posted 2026-08-04 by user `krets`:
  > "Switching from `/playlists/{id}/tracks` to `/playlists/{id}/items` resolved the `403 Forbidden`
  > error for me. [February 2026 Web API Migration Guide]"

  This is a **verified, dated, independent real-world confirmation** that the fix is exactly what the
  official changelog implies: swap the endpoint path, nothing else. No Spotify staff account has
  commented on the issue as of this research date, but the community-sourced fix matches the official
  docs exactly, so it doesn't need staff confirmation to be credible.

### 3. Spotify Community developer forum (community.spotify.com) — NOT independently verified, secondary/indirect only

Direct `WebFetch`/`curl` access to community.spotify.com was blocked with HTTP 403 by the forum's own
bot protection (unrelated to the Spotify API 403 under investigation — this was Claude's tooling being
blocked from reading the page, not evidence about the API). I could not read these threads directly or
confirm whether any reply came from a Spotify staff/moderator account. Titles found via search, listed
for the developer's own follow-up, **not verified beyond the title**:
- "Web API playlist creation returns 403 in Development Mode" — `community.spotify.com/t5/Spotify-for-Developers/Web-API-playlist-creation-returns-403-in-Development-Mode-no/td-p/7482480`
- "Write API (playlist tracks + user library) returning 403 for all..." — `community.spotify.com/t5/Spotify-for-Developers/Write-API-playlist-tracks-user-library-returning-403-for-all/td-p/7432545`
- "403 on playlist track addition" — `community.spotify.com/t5/Spotify-for-Developers/403-on-playlist-track-addition/td-p/7350134`
- "403 Forbidden on all playlist track requests, even with new app" — `community.spotify.com/t5/Spotify-for-Developers/403-Forbidden-on-all-playlist-track-requests-even-with-new-app/td-p/7367439`

These titles are consistent with, and corroborate the *existence* of widespread reports matching, the
GitHub-confirmed symptom, but I'm flagging explicitly that I could not read thread bodies or replies
myself — treat their content as unverified until someone with forum access reads them directly.

### 4. Stack Overflow — not consulted directly

I did not find Stack Overflow answers surfaced in search results that added information beyond what
the official changelog/migration guide and the GitHub issue already confirm. Given the GitHub issue is
dated (May–Aug 2026) and directly on-point, I prioritized verifying the primary docs over chasing
Stack Overflow secondary summaries per the source-priority instructions. Not consulted; no claim is
made either way about what's there.

### 5. A genuine open discrepancy worth flagging (speculative explanation only)

The official Feb 2026 changelog **also** lists `Follow Playlist (PUT /playlists/{id}/followers)` and
`Unfollow Playlist (DELETE /playlists/{id}/followers)` under `[REMOVED]`, saying to use
`PUT`/`DELETE /me/library` instead — and the live reference page for `Follow Playlist` does carry a
"Deprecated" badge and the note "This endpoint is deprecated. Use Save Items to Library instead."
**Yet the bug report says follow/unfollow currently return 200.** I fetched the live reference pages
directly and confirmed: the four playlist-*items* endpoints (get/add/update/remove) carry an explicit
`[DEPRECATED]` tag in the page title/nav, whereas Follow/Unfollow Playlist do not carry that page-title
tag despite having an in-body deprecation notice. **My read (speculative, not confirmed by any
Spotify statement I could find): Spotify's documentation marks both categories as deprecated/replaced,
but as of this research date has only actually *enforced* (hard-blocked with 403) the tracks→items
rename, not the followers→library rename.** This is consistent with, though not proven by, the
`external_ids` field removal that the March 2026 changelog explicitly reverted — i.e., Spotify has a
track record of documenting removals ahead of, or inconsistently with, actual enforcement during this
migration. Treat this paragraph as an explanation of an observed inconsistency, not an official
Spotify claim — flagging it mainly so the developer isn't surprised if follow/unfollow *also* starts
403ing on `/playlists/{id}/followers` at some future date, since Spotify's own changelog already says
it's deprecated in favor of `/me/library`.

### On the other candidate root causes named in the brief

- **Missing OAuth scope**: ruled out by the task's own token-inspection evidence; also ruled out
  because the official docs still list `playlist-modify-public`/`playlist-modify-private` as the
  correct scopes for the *new* `/items` endpoint too — no scope change accompanied the path rename.
- **Free vs. Premium account requirement**: CONFIRMED as a real Development Mode requirement ("the app
  owner must have an active Spotify Premium subscription... app will stop working" if it lapses) but
  this would block *all* writes uniformly, not just the tracks endpoint, so it does not explain the
  asymmetry described. Worth a sanity check regardless (see Next steps).
  Source: [February 2026 Migration Guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide), CONFIRMED official.
- **Regional/market restrictions, playlist-ownership subtleties**: no evidence found tying these to
  this symptom; not needed to explain it given the endpoint-migration finding above.
- **Rate limiting manifesting as 403 instead of 429**: ruled out — the official quota-modes doc is
  explicit that quota/rate-limit exhaustion returns `429` with reason `QUOTA_EXCEEDED`, not `403`.
- **The app's declared "API/SDKs" checkbox**: no documentation found tying this checkbox to
  endpoint-level write permissions; the dashboard checkbox appears to be informational/analytics only,
  not an enforcement mechanism, based on the absence of any reference to it in the quota-modes or
  migration-guide docs.

---

## Next steps (concrete, actionable)

1. **Change the endpoint path.** Replace `POST /v1/playlists/{playlist_id}/tracks` with
   `POST /v1/playlists/{playlist_id}/items` in whatever code/client makes this call. Same OAuth
   scopes (`playlist-modify-public`, `playlist-modify-private`), same request body shape
   (`uris`, `position`), same auth flow — this is a pure path change per the official reference docs.
   If a third-party Spotify client library is in use (e.g. `spotipy`, `spotify-web-api-ts-sdk`, or a
   hand-rolled `httpx` wrapper), check whether that library has already shipped a version targeting
   `/items` — if not, update the raw call directly rather than waiting on the library.
   **Applied in jazz_agent**: `adapters/spotify.py::add_tracks`, now posts to `.../items`.
2. **Do the same for the sibling endpoints** while touching this code: `GET .../tracks` → `GET
   .../items`, `PUT .../tracks` → `PUT .../items`, `DELETE .../tracks` → `DELETE .../items`. Note one
   added restriction on the new `GET .../items`: per the official changelog, it's "only available for
   playlists the user owns or collaborates on" — not a concern here since jazz_agent only touches
   playlists it created, but worth knowing if any read-path code touches other users' playlists.
   Also note the response field rename: `tracks` → `items` in the playlist object — anything parsing
   the JSON response needs the same rename.
   **Applied in jazz_agent**: `adapters/spotify.py::remove_tracks`, now `DELETE .../items` -- and the
   request body key also renamed, `"tracks"` → `"items"` (verified directly against the current
   Remove Playlist Items reference page, not just inferred from the URL rename). `get_album_tracks`
   (`/albums/{id}/tracks`) is a different resource, unaffected by this migration, left unchanged.
3. **Do not pursue Extended Quota Mode for this.** It requires a registered business entity and
   250k+ MAU as of May 2025 — genuinely unreachable for a personal single-user app, and per the
   migration guide, not needed to fix this 403 anyway. Save that path only if a *different*,
   genuinely EQM-gated restriction is hit later (rate limits, user cap, etc.).
4. **Sanity-check the Premium requirement** as cheap due diligence: confirm the Spotify account that
   owns this Development Mode app currently has an active Premium subscription, since a lapsed
   subscription is documented to silently break Development Mode apps. Unlikely to be the actual
   cause here (it would block all writes, not just this one), but a 30-second check.
5. **Proactively migrate `PUT`/`DELETE /playlists/{playlist_id}/followers` to `PUT`/`DELETE
   /me/library`** even though they currently still return 200. Spotify's own Feb 2026 changelog
   already lists them as removed/replaced; given the tracks→items removal was enforced roughly a month
   after being documented, the same could happen to these calls without further notice. This is
   optional hardening, not required to fix the current bug. **Not applied in jazz_agent yet** -- left
   as a follow-up, since `unfollow_playlist` still works today and this isn't blocking anything.
6. **No dashboard setting, form, or support ticket is needed for the reported 403 itself.** The
   developer dashboard's lack of a granular per-scope toggle was a correct observation — no such
   toggle exists because this was never a permissions-tier problem, it's a deprecated-URL problem.
