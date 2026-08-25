# Setup: Spotify, Google OAuth, and running the server

Step-by-step instructions for the three things this project needs from the outside world
before it does anything useful: a Spotify app (to search, match, and build playlists), a
Google OAuth client (so the MCP server can authenticate you), and the two long-running
processes actually running somewhere reachable.

This document is deliberately generic -- written for anyone running their own copy of this
project, not tied to a specific host or domain. For full production deployment (VPS
provisioning, Caddy/TLS, systemd, backups, and the failure playbook), see
[RUNBOOK.md](RUNBOOK.md); this document covers the credential setup that runbook's sections
4 and 5 point back to, plus enough about running the server to actually exercise those
credentials while you're setting them up.

Replace every `<placeholder>` below with your own values. Nothing here should be copied
verbatim.

---

## Prerequisites

- The repo cloned, and `uv sync --all-groups` run once.
- Postgres reachable and migrated: `make up && make migrate` (see the main
  [README](../README.md) if this is unfamiliar).
- `.env.example` copied to `.env` (mode `600`, never committed -- `.gitignore` already
  excludes it). Every variable referenced below lives in that file.

---

## 1. Spotify

The pipeline needs a Spotify app to search for artists, look up albums and tracks, and
create/modify playlists. This is a one-time setup per Spotify account.

### 1.1 Create the app

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard) and log
   in with the Spotify account whose library the playlists should live in.
2. **Create app**. Any name/description is fine; this is never shown to anyone else.
3. **Redirect URIs**: add `http://127.0.0.1:8888/callback`. This is only used once, locally,
   to obtain a refresh token (step 1.3) -- it is not the production server's URL.
4. Save. Open the app's **Settings** and copy the **Client ID** and **Client Secret**.

### 1.2 Fill in `.env`

```
SPOTIFY_CLIENT_ID=<client id from 1.1>
SPOTIFY_CLIENT_SECRET=<client secret from 1.1>
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8888/callback
```

### 1.3 Obtain a refresh token

Run this from a machine with a browser (your laptop, not necessarily the server):

```bash
uv run python scripts/spotify_auth.py
```

This opens a browser to Spotify's consent screen, requesting exactly these scopes (no more):

```
playlist-modify-private playlist-modify-public playlist-read-private
user-read-currently-playing user-read-recently-played
```

Approve it. The script catches the redirect locally, exchanges the authorization code for
tokens, and prints a refresh token to the terminal. Paste it into `.env`:

```
SPOTIFY_REFRESH_TOKEN=<the printed value>
```

This is obtained once and reused forever (the code that uses it, `adapters/spotify.py`,
automatically exchanges it for short-lived access tokens as needed). It only stops working
if you revoke the app's access from your Spotify account's connected-apps settings, or if
you re-run `scripts/spotify_auth.py` and don't update `.env` with the new value.

### 1.4 Verify it

```bash
uv run python -c "
from jazz_agent.config import load_config
from jazz_agent.adapters.spotify import SpotifyClient
c = load_config()
client = SpotifyClient(c.spotify_client_id, c.spotify_client_secret, c.spotify_refresh_token)
print(client.search_artists('Bill Frisell', limit=1))
"
```

A non-empty result means the credentials and refresh token both work.

---

## 2. Google OAuth (MCP server authentication)

claude.ai and Claude Desktop's custom connectors require OAuth 2.1 with dynamic client
registration; they reject a static bearer token outright. The MCP server (`mcp/server.py`)
uses fastmcp's `GoogleProvider` to delegate the actual login to Google, then checks the
signed-in email against an allow-list this project owns (`MCP_ALLOWED_EMAILS`) -- Google only
proves *who* signed in, not whether they're allowed to use this server.

### 2.1 Decide the server's public URL first

You need this before creating the Google OAuth client, because the redirect URI has to match
exactly. Two options:

- **Real deployment**: `https://<your-subdomain>.<your-domain>` -- see
  [RUNBOOK.md](RUNBOOK.md) sections 1-3 for provisioning a host and terminating TLS with
  Caddy.
- **Testing locally, without deploying anything yet**: run a tunnel (e.g.
  [ngrok](https://ngrok.com/) or `cloudflared tunnel`) pointed at the port the MCP server
  will listen on, and use the HTTPS URL it gives you. This is the fastest way to get a real,
  publicly-reachable HTTPS URL for testing the full OAuth + claude.ai flow without
  provisioning a server. Both work; the tunnel's URL is only good for the current session
  unless you're on a paid plan with a fixed subdomain, so expect to re-register the redirect
  URI (step 2.2) if it changes.

Either way, call this value `<public-url>` below -- e.g. `https://mcp.example.com` or
`https://abcd1234.ngrok-free.app`.

### 2.2 Create the OAuth client

In the [Google Cloud Console](https://console.cloud.google.com/), create a project (or reuse
one you control). Google's console for this is the **Google Auth Platform** section (left
nav: Overview / Branding / Audience / Clients / ...) -- it replaced the older single-page
"OAuth consent screen + Credentials" flow, so don't be thrown if this doesn't match an older
screenshot you find elsewhere.

1. **Audience**: User type **External** (Internal only appears for Google Workspace
   accounts). Leave publishing status as **Testing** -- you don't need to submit for
   verification for a personal/single-user server. Add your own Google account under
   **Test users**; this keeps sign-in private to accounts you explicitly list, and is what
   actually has to happen before Google will let you create a client.
2. **Branding**: fill in an app name (anything -- never shown to anyone else), a user support
   email, and a developer contact email. Both can be your own address.
3. **Clients -> Create OAuth client** (also reachable from the Overview page's "Create OAuth
   client" button):
   - Application type: **Web application**.
   - **Authorized redirect URIs**: add `<public-url>/auth/callback` exactly --
     `GoogleProvider`'s default `redirect_path` is `/auth/callback` (verified against the
     installed fastmcp version; if you've pinned a materially different fastmcp release,
     double check this hasn't changed). Any mismatch here is the single most common setup
     failure -- Google will reject the callback with a redirect_uri_mismatch error.
4. Save, then copy the **Client ID** and **Client Secret**.

### 2.3 Fill in `.env`

```
GOOGLE_CLIENT_ID=<client id from 2.2>
GOOGLE_CLIENT_SECRET=<client secret from 2.2>
MCP_PUBLIC_URL=<public-url>
MCP_ALLOWED_EMAILS=<your-email@example.com>
```

`MCP_ALLOWED_EMAILS` is comma-separated if you ever need more than one address, but keeping
it to one is the intent (`ARCHITECTURE.md` section 11) -- this is a single-user system, and
the allow-list is this project's own enforcement, checked on every tool call
(`mcp/server.py::allowed_emails_check`), independent of anything Google does.

---

## 3. Running the server

Two separate long-running/scheduled pieces, both reading the same `.env`:

### 3.1 The daily batch pipeline

Scrapes, matches, and builds playlists. Meant to run once a day (cron; see
[RUNBOOK.md](RUNBOOK.md) section 7), but run it by hand first to confirm everything's wired
up:

```bash
DRY_RUN=1 uv run python -m jazz_agent.pipeline.daily
```

`DRY_RUN=1` runs the entire real pipeline -- fetching, extraction, matching, album
selection -- and logs what it *would* write to Spotify without actually writing anything
(`pipeline/daily.py::DryRunMusicService`). Check `run_log` afterward:

```bash
psql "$DATABASE_URL" -c "SELECT club_id, outcome, shows_found FROM run_log ORDER BY run_at DESC LIMIT 10;"
```

Once that looks right, drop `DRY_RUN` (or set it to `0`) and run it for real, then install
the cron entry from RUNBOOK.md section 7.

### 3.2 The MCP server

```bash
uv run python -m jazz_agent.mcp.server
```

Listens on `MCP_HOST:MCP_PORT` (default `0.0.0.0:8080`) using plain HTTP -- TLS is expected
to be terminated in front of it (Caddy in production, or your tunnel if you're testing
locally per step 2.1). Leave this running (a process supervisor in production -- see
RUNBOOK.md section 7 for a systemd unit; a plain terminal is fine for local testing).

### 3.3 Connect from claude.ai

1. claude.ai -> **Settings -> Connectors -> Add custom connector**.
2. URL: `<public-url>/mcp` -- fastmcp's HTTP transport (`mcp.run(transport="http", ...)`
   in `mcp/server.py`) mounts the actual MCP protocol endpoint at `/mcp`, not at the
   bare domain. OAuth-related routes (`/auth/callback`, `/.well-known/...`) *do* work
   at the bare `<public-url>`, which is why using it without `/mcp` still lets sign-in
   succeed -- claude.ai only reports the problem afterward, as "Your account was
   authorized, but no MCP server was found at the provided URL." Confirm directly with
   `curl -i <public-url>/mcp` (expect `401`, meaning the route exists and just wants a
   token) versus `curl -i <public-url>` (expect `404`) if this happens again.
3. This triggers the OAuth flow from section 2: sign in with the Google account you added to
   `MCP_ALLOWED_EMAILS`.
4. Once connected, ask it something the log can answer -- e.g. "what's played at
   [a club you've seeded] recently" -- to confirm the tools are actually reachable end to
   end, not just that auth succeeded.

### 3.4 If something doesn't work

- **`redirect_uri_mismatch` from Google**: the URI registered in step 2.2 doesn't exactly
  match `MCP_PUBLIC_URL` + `/auth/callback`. Check for a trailing slash mismatch or an `http`
  vs `https` mismatch.
- **Connector adds but every tool call fails as unauthorized**: your signed-in email isn't in
  `MCP_ALLOWED_EMAILS`, or there's a typo -- the check is case-insensitive but not
  whitespace-tolerant beyond a simple strip.
- **Spotify calls fail with 401 after previously working**: the refresh token was revoked
  (check the Spotify account's connected-apps list) -- redo step 1.3.
- **Nothing shows up when you ask about a club**: confirm the pipeline has actually run at
  least once (`run_log`) and that the club is `active = true` in the `clubs` table.
