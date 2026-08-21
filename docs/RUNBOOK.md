# Runbook

Rebuilding the host from scratch, and what to do when something breaks. Written for
whoever is doing this next, including a future version of whoever wrote it.

For what the system is and why it's built this way, see [ARCHITECTURE.md](ARCHITECTURE.md)
and [DECISIONS.md](DECISIONS.md). This document is operational, not architectural.

---

## 1. VPS provisioning

**Target:** one small always-on Linux VPS (ADR-001). Nothing here is resource-hungry --
roughly 5 shows/day across 6 clubs, a handful of API calls, one long-running MCP process.
A 1-2 vCPU / 2GB RAM box is comfortable headroom.

1. Provision a VPS running a current Ubuntu LTS. Point a DNS `A` record at it for the
   subdomain the MCP server will live on (e.g. `mcp.yourdomain.com`) -- Caddy needs this to
   issue a TLS certificate.
2. SSH in, apply OS updates, create a non-root deploy user with `sudo` and add it to the
   `docker` group once Docker is installed (step 3).
3. Install Docker Engine + Docker Compose plugin (the official convenience script or your
   distro's packages both work):
   ```bash
   curl -fsSL https://get.docker.com | sh
   sudo usermod -aG docker "$USER"
   # log out and back in for the group change to take effect
   ```
4. Install `git`, and `uv` (https://docs.astral.sh/uv/getting-started/installation/) for
   running the batch pipeline and one-off scripts outside Docker (the pipeline and MCP
   server run as host processes against a Dockerized Postgres -- see section 2).
5. Clone the repository:
   ```bash
   git clone <this repo's URL> /opt/jazz_agent
   cd /opt/jazz_agent
   uv sync --all-groups
   ```
6. Copy `.env.example` to `.env` (mode `600`, never committed) and fill it in. Sections 4-6
   below walk through the pieces that need real values: Google OAuth, Spotify, MusicBrainz,
   ntfy.

---

## 2. Docker Compose: Postgres

Postgres runs in Docker (`docker-compose.yml`); the pipeline and MCP server run as host
processes via `uv run`, connecting to Postgres on `localhost:5432`. This split exists
because the pipeline needs `cron`, and the MCP server needs to sit behind Caddy on the host
network -- containerizing either buys nothing here.

```bash
make up        # docker compose up -d db
make migrate   # applies migrations/*.sql in order, including migrations/seed_clubs.sql
```

Verify:

```bash
docker compose ps                    # db should show "healthy"
psql "$DATABASE_URL" -c '\dt'        # 15 tables
psql "$DATABASE_URL" -c 'SELECT club_id, schedule_url FROM clubs;'   # 6 seeded clubs
```

If a club's URL has gone stale by the time you're reading this (sites redesign, domains
change), re-verify before trusting it -- see section 8's "a club starts failing" entry.
`migrations/seed_clubs.sql` documents which two candidate domains were tried and rejected
during the original seeding, so the next person doesn't re-discover the same dead ends.

---

## 3. Caddy and TLS

Caddy terminates TLS for the MCP server and reverse-proxies to the local process
(`MCP_HOST`/`MCP_PORT` in `.env`, default `0.0.0.0:8080`). Install Caddy from the official
repo (https://caddyserver.com/docs/install#debian-ubuntu-raspbian), then:

```
# /etc/caddy/Caddyfile
mcp.yourdomain.com {
    reverse_proxy localhost:8080
}
```

```bash
sudo systemctl reload caddy
```

Caddy requests and renews the Let's Encrypt certificate automatically on first request to
that hostname -- nothing else to configure, provided the DNS `A` record from section 1 is
already live and port 443 is open in whatever firewall the VPS provider fronts.

---

## 4. Google OAuth client (for MCP auth)

The MCP server uses FastMCP's `GoogleProvider` (`mcp/server.py`) to delegate login to
Google; claude.ai and Claude Desktop require OAuth 2.1 with dynamic client registration and
reject a static bearer token (ADR-012).

1. In the [Google Cloud Console](https://console.cloud.google.com/), create a project (or
   reuse one), then **APIs & Services -> Credentials -> Create Credentials -> OAuth client
   ID**, application type **Web application**.
2. Authorized redirect URI: `https://mcp.yourdomain.com/auth/callback` (GoogleProvider's
   default `redirect_path`; confirm against the installed fastmcp version if this has
   changed).
3. Copy the client ID and secret into `.env`:
   ```
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   MCP_PUBLIC_URL=https://mcp.yourdomain.com
   MCP_ALLOWED_EMAILS=you@example.com
   ```
   `MCP_ALLOWED_EMAILS` is enforced by this project's own code
   (`mcp/server.py::allowed_emails_check`), not by Google -- Google only proves who signed
   in, not whether they're allowed to use this server.
4. Start the server (`uv run python -m jazz_agent.mcp.server`, or under a process
   supervisor -- see section 7) and connect from claude.ai: **Settings -> Connectors -> Add
   custom connector**, URL `https://mcp.yourdomain.com`. This triggers the OAuth flow; sign
   in with the allow-listed Google account.

---

## 5. Spotify token bootstrap

The pipeline and MCP server both need a Spotify refresh token, obtained once, interactively,
from a machine with a browser (not necessarily the VPS -- the redirect URI defaults to
`http://127.0.0.1:8888/callback`, i.e. your laptop).

1. In the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard), create an
   app. Add `http://127.0.0.1:8888/callback` as a redirect URI.
2. Fill in `.env`: `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`.
3. Run the one-off auth script (`scripts/spotify_auth.py`), which opens a browser, catches
   the redirect, exchanges the code, and prints a refresh token:
   ```bash
   uv run python scripts/spotify_auth.py
   ```
4. Paste the printed value into `.env` as `SPOTIFY_REFRESH_TOKEN` on the VPS. It is reused
   forever unless Spotify revokes it (e.g. the app's grant is manually removed from the
   account's connected-apps list).

---

## 6. MusicBrainz and ntfy

- `MUSICBRAINZ_USER_AGENT`: a descriptive string with real contact info
  (`jazz_agent/1.0 ( you@example.com )`), or MusicBrainz rate-limits aggressively. No
  account or key needed.
- `NTFY_TOPIC`: pick an unguessable string (ntfy.sh topics are public by name -- anyone who
  knows the topic can read the alerts, per `.env.example`'s own warning). Subscribe to it in
  the [ntfy app](https://ntfy.sh/) or via the web UI to actually receive the chronic-failure
  push (ADR-015).

---

## 7. Cron installation

The batch pipeline is `pipeline/daily.py`'s `main()`, run once a day at `RUN_HOUR_ET`
(default 13:00 ET). Cron runs in whatever timezone the host is set to, so either set the
host to `America/New_York` or convert:

```bash
crontab -e
```

```cron
# 13:00 ET daily. If the host is UTC, that's 17:00 (18:00 during EST) --
# recompute for the host's actual timezone and DST, or set TZ= on the line.
0 13 * * * cd /opt/jazz_agent && /root/.local/bin/uv run python -m jazz_agent.pipeline.daily >> /var/log/jazz_agent/daily.log 2>&1
```

```bash
sudo mkdir -p /var/log/jazz_agent
```

The MCP server is long-running, not cron-driven. Run it under a process supervisor so it
restarts on crash and on boot -- a minimal systemd unit:

```ini
# /etc/systemd/system/jazz-agent-mcp.service
[Unit]
Description=Jazz Agent MCP server
After=docker.service

[Service]
WorkingDirectory=/opt/jazz_agent
ExecStart=/root/.local/bin/uv run python -m jazz_agent.mcp.server
Restart=always
User=deploy

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now jazz-agent-mcp
```

---

## 8. Backups: nightly pg_dump, 30-day retention, and restore

**The log is the irreplaceable asset.** Playlists can be rebuilt from club sites; a year of
accumulated taste signal cannot (DATA_MODEL.md section 5).

```bash
sudo mkdir -p /var/backups/jazz_agent
```

```cron
# 30 minutes after the daily run, so a backup captures that day's work.
30 13 * * * pg_dump -Fc "$DATABASE_URL" -f /var/backups/jazz_agent/$(date +\%Y-\%m-\%d).dump
30 4 * * *  find /var/backups/jazz_agent -name '*.dump' -mtime +30 -delete
```

(Cron doesn't read `.env` -- either export `DATABASE_URL` in the crontab directly, or wrap
the pg_dump line in a small script that sources `.env` first.)

### Restore, verified

**Restore into a scratch database first. Never restore directly over the live database** --
if the dump is bad, you want to find out before you've destroyed the only copy.

```bash
createdb jazz_agent_restore_test
pg_restore -d postgresql://jazz_agent:PASSWORD@localhost:5432/jazz_agent_restore_test \
    /var/backups/jazz_agent/2026-08-20.dump
psql postgresql://jazz_agent:PASSWORD@localhost:5432/jazz_agent_restore_test -c '\dt'
psql postgresql://jazz_agent:PASSWORD@localhost:5432/jazz_agent_restore_test \
    -c 'SELECT count(*) FROM clubs;'
```

This exact sequence was run once against a real dump while writing this runbook (issue #15)
-- 15 tables, all 6 seeded clubs, both extensions, restored cleanly. If the scratch restore
looks right, only then point the application at the restored data (rename databases, or
restore into the real `jazz_agent` database after dropping and recreating it) -- and back up
whatever's currently live before you do, in case you need to go back.

```bash
dropdb jazz_agent_restore_test   # once you're done verifying
```

---

## 9. Failure playbook

### A club starts failing (`fetch_fail` or `extract_fail` in `run_log`)

1. Ask `get_run_health` (via the MCP server, or `SELECT * FROM run_log WHERE club_id = '...'
   ORDER BY run_at DESC LIMIT 10;`) how long it's been failing. Three consecutive technical
   failures fire one ntfy push (ADR-015) -- if you're reading this because of that push, the
   answer is "at least three days."
2. Fetch the club's `schedule_url` by hand with the same header set
   `adapters/http_fetcher.py` uses, to see what's actually happening: a redesign (structure
   changed but the LLM extractor usually tolerates this fine, since it isn't selector-based
   -- ADR-003), a new bot-protection layer (a 403 that a browser-like header set no longer
   clears), or the domain itself changed.
3. If the URL moved or a subdomain change is needed, update `clubs.schedule_url` directly
   (`psql`, hand-edited -- ARCHITECTURE.md section 3, no code change or deploy required).
4. If the site now requires JavaScript rendering it didn't before, set `render_mode = 'js'`
   for that club -- this routes it through `adapters/pw_fetcher.py` (Playwright), which
   needs `playwright install chromium` run once on the host if it hasn't been already.
5. If the site is blocking more aggressively than headers can fix (Cloudflare challenge,
   etc.), there's no code fix here -- ADR-004 already covers standard header-based blocking;
   anything beyond that is out of scope for a personal, low-volume scraper. Mark the club
   `active = false` until/unless it's worth revisiting.

### A match looks wrong

1. Find the artist's row: `SELECT * FROM artists WHERE name = '...';`. `match_notes` holds
   the LLM's reasoning for the original match, and -- if MusicBrainz ever disputed it --
   the dispute-resolution reasoning too (`pipeline/verify.py`).
2. Check `verification_state`. `unverified`/`unverifiable` don't imply the match is wrong,
   only that MusicBrainz never confirmed it either way (ADR-006). `disputed` means
   MusicBrainz's stored link disagreed and the correction pipeline already ran -- check
   `playlist_events` for a `correction` row explaining what changed.
3. If the match is genuinely wrong and nothing corrected it (e.g. both plausibility and the
   LLM were fooled by a well-known same-named act), there's no admin UI for this by design
   (ARCHITECTURE.md section 9). Options: wait for the next MusicBrainz-verification pass to
   catch it if a dispute is plausible, or fix it directly in Postgres (`UPDATE artists ...`,
   remove/re-add the affected `playlist_tracks` rows and Spotify tracks by hand, log a
   `playlist_events` row for the record) -- rare enough in practice that hand-fixing beats
   building tooling for it.
4. If it keeps happening for the same act, that's a `core/plausibility.py` tuning signal
   (ADR-007) -- `artists.plausibility_score` is persisted for exactly this: pull a few
   recent mismatches and see whether the thresholds need adjusting on real evidence rather
   than guesswork.

### MusicBrainz is unreachable for days

By design, this **does not affect playlist creation** -- MusicBrainz is verification, never
a precondition (AGENTS.md invariant 3, ADR-006). What you'll actually see:

1. `artists.verification_state` stays `unverified` for new matches during the outage --
   query `SELECT count(*) FROM artists WHERE verification_state = 'unverified' AND
   created_at > now() - interval '3 days';` to see the backlog.
2. Nothing needs manual intervention. `mb_lookup_misses` negative-caches genuine "no hit"
   results for 30 days, but a request *failure* (timeout/5xx) is never cached
   (`adapters/musicbrainz.py`) -- so verification retries automatically on the next run, no
   backlog cleanup required once MusicBrainz recovers.
3. If the outage stretches into weeks, MusicBrainz publishes full database dumps and a
   `musicbrainz-docker` self-hosting path (documented as an escape hatch in ADR-005,
   deliberately not built -- overkill at this volume unless it actually becomes a recurring
   problem).

---

## 10. Quick reference

| Task | Command |
|---|---|
| Bring up Postgres | `make up` |
| Apply migrations + seed clubs | `make migrate` |
| Run the daily pipeline once, by hand | `uv run python -m jazz_agent.pipeline.daily` |
| Run it without writing to Spotify | `DRY_RUN=1 uv run python -m jazz_agent.pipeline.daily` |
| Start the MCP server | `uv run python -m jazz_agent.mcp.server` |
| Regenerate the Claude Project tool inventory | `make project-instructions` |
| Full test suite | `make test` (needs `make up` first) |
| Lint / typecheck | `make lint typecheck` |
