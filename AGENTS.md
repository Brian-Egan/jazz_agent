# AGENTS.md

Instructions for the coding agent implementing this repository.

This file is for the **implementer**. The runtime contract for the Claude Project that
*queries* the finished system is a different document:
[`docs/claude-project-instructions.md`](docs/claude-project-instructions.md). Do not
conflate them.

---

## Read first

1. [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — the specification. Issues reference it
   by section.
2. [`docs/DECISIONS.md`](docs/DECISIONS.md) — why things are the way they are.
3. [`docs/DATA_MODEL.md`](docs/DATA_MODEL.md) — schema and the queries it exists to serve.

[`docs/original-prd.md`](docs/original-prd.md) is the original PRD, kept
verbatim. **It is superseded in several places.** Where the PRD and ARCHITECTURE.md disagree,
ARCHITECTURE.md wins and the relevant ADR names the superseded section. Do not implement
from the PRD directly.

---

## How to pick up work

```bash
gh issue list --state open --milestone v1
```

Take the **lowest-numbered issue whose `Blocked by` dependencies are all closed**. Do not
start a blocked issue. If everything open is blocked, say so rather than guessing at
scaffolding.

One issue per branch, one commit series per issue, reference the issue in the commit
message so it closes on merge. Do not batch multiple issues into one change.

If an issue's acceptance criteria turn out to be wrong or impossible, say so on the issue
and stop. Do not silently redefine the work.

---

## Invariants

These are not style preferences. Breaking any of them breaks a decision recorded in an ADR.

### 1. `core/` and `ports/` must never import `adapters/`

`core/` is pure domain logic and performs **no I/O**. `ports/` holds Protocols only. This is
what makes the system portable per PRD 8.6, and it is enforced by an `import-linter`
contract in CI. If you find yourself wanting an HTTP call inside `core/`, the logic belongs
in `pipeline/` and the data should be passed in.

### 2. Only `config.py` reads the environment

No `os.environ` or `os.getenv` in adapters, pipeline, or core. Adapters receive
configuration as constructor arguments. This keeps secrets handling in one auditable place.

### 3. MusicBrainz must never be able to break a playlist

MusicBrainz is verification and enrichment, never a precondition
([ADR-006](docs/DECISIONS.md#adr-006)). Concretely: if `adapters/musicbrainz.py` raises,
times out, or returns nothing, the pipeline must produce the **same playlist it would
otherwise have produced**, differing only in `artists.verification_state`. There is a test
asserting this. Do not weaken it.

### 4. The daily run is idempotent

Running `pipeline.daily` twice must converge, not accumulate. Reconciliation is a diff
against `(club_id, week_start_date)`. Never create a playlist without first checking for an
existing row on that key.

### 5. The log is never pruned

Retention unfollows playlists in Spotify and stamps `spotify_removed_at`. It must never
delete a database row. Removing a playlist from Spotify and deleting its history are
different operations, and only the first one exists.

### 6. Never delete a Spotify playlist

There is no such endpoint. Use `DELETE /v1/playlists/{id}/followers`. If you find code or a
comment claiming to delete a playlist, it is wrong.

### 7. The MCP write surface is feedback only

The MCP server may write exactly one thing: rows in `feedback`. It must not modify club
config, playlists, matches, or trigger a run. Even with valid credentials there is no path
from that server to anything else. Preserve this.

### 8. A wrong match is worse than no match

With one full album per artist, a mismatch means 45 minutes of irrelevant music. When in
doubt, record a `match_misses` row. Do not lower the confidence thresholds in
ARCHITECTURE.md section 6 to increase coverage.

---

## Stack and tooling

| Concern | Choice |
|---|---|
| Python | 3.12, managed with `uv` |
| Lint / format | `ruff` (lint + format) |
| Types | `mypy --strict`. Public functions are annotated. |
| Tests | `pytest`; HTTP stubbed with `respx` |
| Architecture check | `import-linter` |
| Database | Postgres 17 with `pg_trgm` and `pgvector`, via Docker Compose |
| HTTP | `httpx`; Playwright only behind `render_mode: js` |
| HTML to text | `trafilatura` |
| LLM | `anthropic` SDK, Claude Haiku, strict JSON schema output |
| MCP | `fastmcp` with `OAuthProxy` |

Everything must pass before an issue is closed:

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy src
uv run lint-imports
uv run pytest
```

---

## Testing expectations

**Do not call live external services in tests.** Spotify, Anthropic, and MusicBrainz are
all stubbed. Fixtures live in `tests/fixtures/`.

- **Club HTML fixtures are committed.** Save real pages once, test against the saved copy.
  `tests/fixtures/live_cache/` is gitignored and must not be relied on.
- **Extraction is non-deterministic**, so assert semantics, not exact strings: that the
  right number of shows came back, that dates parse, that personnel and instruments were
  captured. Village Vanguard's page publishes full personnel, so that fixture must assert
  performers are populated.
- **The idempotency test is the most important one in the suite.** Run the pipeline twice
  against a seeded database and assert `week_playlists` and `playlist_tracks` are identical
  and that no duplicate Spotify write was issued.
- **The MusicBrainz-independence test** asserts invariant 3 directly. Force the adapter to
  raise and compare the resulting playlist byte-for-byte against the healthy case.
- **`core/plausibility.py` is the least testable module** — it encodes judgement as
  thresholds ([ADR-007](docs/DECISIONS.md#adr-007)). Build a fixture set that includes a
  same-named non-jazz act, and expect to tune it. Persist `plausibility_score` so tuning
  is evidence-driven rather than guesswork.

---

## Conventions

- Timezone: all club-facing dates are `America/New_York`. Store `date` for show dates and
  `timestamptz` for events. Take show dates **as the club labels them** — a 00:30 set listed
  under Friday stays Friday.
- Logging: structured (`structlog` or stdlib with a JSON formatter), always including
  `run_id` and `club_id`. `run_log` is the durable record; stdout is for debugging.
- Errors: catch per club, never per run. One club's failure must not affect another's.
- Secrets: never logged, never in fixtures, never in commit messages. `.env` only.
- Comments explain *why*, not *what*. Where a line exists because of an external
  constraint — a deprecated endpoint, a 403, a rate limit — say so and cite it.
- No new dependency without a reason recorded in the issue.

---

## Scraping conduct

Six public schedule pages, one request each per day, personal use, no redistribution.
Respect `robots.txt`. Send a realistic `User-Agent` — Birdland returns `403` without one.
MusicBrainz requires a descriptive `User-Agent` with contact details or it rate-limits
aggressively; it is set in `.env`. Do not parallelise fetches to go faster. There is no
reason to.

---

## Things that are deliberately absent

Do not add these; each was considered and rejected. If you think one is needed, raise it on
an issue first.

- A web UI. Out of scope for v1 (PRD Section 12).
- CSS-selector scraper adapters ([ADR-003](docs/DECISIONS.md#adr-003)).
- Any use of Spotify `related-artists`, `recommendations`, `audio-features`, or
  `audio-analysis`. These are **withdrawn** for apps registered after November 2024 and will
  return `403`. Artist adjacency comes from MusicBrainz and co-performance
  ([ADR-005](docs/DECISIONS.md#adr-005)).
- Multi-user support, auth beyond the single-email allow-list, or sharing.
- Email or SMS notification. Only `ntfy`, only on chronic failure
  ([ADR-015](docs/DECISIONS.md#adr-015)).
- A second database backend. One implementation behind the Protocols, per PRD 8.6's warning
  against speculative multi-backend support.
