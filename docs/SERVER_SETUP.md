# Server Setup

Turning a freshly provisioned cloud VPS into a box that's ready to run Jazz Agent: SSH
access locked down, Docker and `uv` installed, Postgres up and migrated, and Caddy
terminating TLS. Written generically for any Ubuntu LTS VPS; notes below call out where
a specific provider's console makes a step easier.

Once you're through this, go to [SETUP.md](SETUP.md) to register Spotify and Google OAuth
credentials and actually run the pipeline and MCP server. For cron, the systemd unit,
backups, and what to do when something breaks later, see [RUNBOOK.md](RUNBOOK.md).

## Prerequisites

- A VPS already provisioned, running Ubuntu 22.04 or 24.04 LTS, with SSH access (root or a
  sudo-capable user). 1-2 vCPU / 2GB RAM is comfortable headroom for this workload
  (`RUNBOOK.md` section 1) -- nothing here needs more.
- A domain or subdomain you control, so you can point a DNS `A` record at the box (needed in
  step 4, used by Caddy in step 10).
- Your SSH public key. Most providers, including Hetzner Cloud, let you attach it at server
  creation so you never touch a password. If yours doesn't, add it now with
  `ssh-copy-id root@<server-ip>` before continuing.

---

## 1. First login and updates

```bash
ssh root@<server-ip>
apt update && apt upgrade -y
```

Reboot if the kernel was updated (`apt list --upgradable` will mention `linux-image-*` if
so):

```bash
reboot
```

---

## 2. Lock down access

Running everything as `root` over SSH forever is the kind of thing that's fine until it
isn't. Create a deploy user now, while it's cheap:

```bash
adduser deploy
usermod -aG sudo deploy
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy
```

Log out and back in as `deploy` to confirm it works *before* touching root access:

```bash
ssh deploy@<server-ip>
```

Then disable root SSH login and password authentication (key-only from here on):

```bash
sudo sed -i 's/^#\?PermitRootLogin .*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/^#\?PasswordAuthentication .*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh
```

Keep your current root session open until you've confirmed `ssh deploy@<server-ip>` and
`sudo` both work -- if something's wrong with the new user, you don't want to have already
locked yourself out of the only other way in.

---

## 3. Firewall

This app needs exactly three inbound ports: 22 (SSH), 80 and 443 (Caddy/TLS). Everything
else -- Postgres, the MCP server's raw port -- should never be reachable from outside the
box; Postgres is only ever reached from `localhost`, and Caddy is what fronts the MCP port.

If your provider has a firewall product that filters traffic before it reaches the VM
(Hetzner Cloud's **Firewalls**, DigitalOcean's **Cloud Firewalls**, etc.), prefer it over a
host-level firewall for the coarse allow-list -- a bad `iptables`/`ufw` rule can lock you out
of the box entirely, where a provider-side misconfiguration just fails to apply. In Hetzner's
console: **Firewalls → Create Firewall**, allow inbound TCP 22/80/443, attach it to the
server.

Add `ufw` on the host too, as defense in depth (redundant with a provider firewall, but free
and worth having if you ever move providers):

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

`ufw enable` will warn it may disrupt existing SSH connections -- it won't, since OpenSSH is
already allow-listed, but confirm you can open a *second* SSH session successfully before
closing your first one, same caution as step 2.

---

## 4. DNS

Point an `A` record at the server's public IP for the subdomain the MCP server will live on,
e.g. `mcp.yourdomain.com`. Caddy (step 10) needs this live and propagated before it can issue
a TLS certificate -- `dig +short mcp.yourdomain.com` should return the server's IP before you
move on.

---

## 5. Install Docker

Postgres runs in Docker; the pipeline and MCP server run as host processes against it
(`RUNBOOK.md` section 2 explains why: the pipeline needs `cron`, the MCP server needs to sit
behind Caddy on the host network, and containerizing either buys nothing here).

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker "$USER"
```

Log out and back in for the group change to take effect, then confirm:

```bash
docker run hello-world
```

---

## 6. Install git and uv

```bash
sudo apt install -y git
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
```

---

## 7. Clone the repo

```bash
git clone <this repo's URL> /opt/jazz_agent
cd /opt/jazz_agent
uv sync --all-groups
```

If `/opt` isn't writable by `deploy`, `sudo mkdir -p /opt/jazz_agent && sudo chown deploy:deploy /opt/jazz_agent` first.

---

## 8. `.env`

```bash
cp .env.example .env
chmod 600 .env
```

Leave it with placeholder/blank values for now -- `SETUP.md` walks through filling in the
Spotify and Google OAuth pieces next. `DATABASE_URL` and the Postgres credentials can be set
now, or left as `.env.example`'s defaults if you're fine with them for a single-user box.

---

## 9. Bring up Postgres and migrate

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

If `psql` isn't installed on the host, `sudo apt install -y postgresql-client` -- it's only
needed for this kind of manual check, not for the application itself (which talks to
Postgres via `psycopg`, already pulled in by `uv sync`).

---

## 10. Caddy and TLS

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

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
that hostname -- nothing else to configure, provided the DNS record from step 4 is live and
ports 80/443 are open (steps 3 and this one). There's nothing listening on port 8080 yet
(that's the MCP server, started in `SETUP.md` section 3), so a request to
`https://mcp.yourdomain.com` right now will get a TLS handshake but a 502 -- that's expected
and confirms TLS itself is working.

---

## What's next

The box is ready. From here:

1. [SETUP.md](SETUP.md) -- register the Spotify app and Google OAuth client, fill in the
   rest of `.env`, and do a first run of the pipeline and MCP server.
2. [RUNBOOK.md](RUNBOOK.md) sections 7-8 -- install the cron entry for the daily pipeline,
   the systemd unit so the MCP server survives reboots and crashes, and nightly backups.

---

## Troubleshooting

- **Locked out after step 2 or 3.** Most providers offer a browser-based console (Hetzner
  Cloud: server → **Console**) that works even when SSH doesn't -- use it to fix
  `sshd_config` or `ufw` rules directly. This is exactly why steps 2 and 3 say to keep your
  existing session open until you've confirmed the new one works.
- **`docker run hello-world` fails with a permission error.** The group change from step 5
  needs a fresh login to take effect -- `exit` and `ssh` back in, don't just open a new
  terminal tab in the same session.
- **Caddy won't get a certificate.** Almost always DNS hasn't propagated yet, or port 80/443
  isn't actually open -- re-check `dig +short mcp.yourdomain.com` and
  `curl -I http://mcp.yourdomain.com` (should reach Caddy, not time out) before suspecting
  Caddy itself. `sudo journalctl -u caddy -n 50` has the actual ACME error if it's something
  else.
- **`make migrate` can't connect.** Confirm `docker compose ps` shows the `db` service as
  `healthy` (not just `running` -- it takes Postgres a few seconds to become ready after
  container start), and that `DATABASE_URL` in `.env` matches the credentials in
  `docker-compose.yml`.
