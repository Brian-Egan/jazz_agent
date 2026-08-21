# Jazz Agent

A single-user tool that reads the schedules of NYC jazz clubs, works out who is playing,
and builds a Spotify playlist per club per week so the music arrives without being
asked for. It keeps a permanent log of everything it has ever surfaced, and exposes that
log to Claude over MCP so you can ask about it and record what you liked while listening.

Two halves, deliberately separate:

- **A daily batch pipeline** (cron) that scrapes, matches, and reconciles playlists.
- **An always-on MCP server** that answers questions about the log and records feedback.

## Why it is built this way

Three findings drove the design, and all three contradict the obvious approach:

1. **Spotify deleted the endpoints you would reach for.** The November 2024 API changes
   removed `related-artists`, `recommendations`, `audio-features`, and `audio-analysis`
   for any app registered afterwards. There is no acoustic data and no artist-similarity
   data available. Artist connections come from MusicBrainz and from the club listings
   themselves; "style" comes from genre tags and your own notes.
2. **CSS selectors are the wrong tool for six unmaintained websites.** Extraction is done
   by an LLM over page text, so adding a club is a row in a table, and a venue redesign
   does not break anything.
3. **Weekly playlists, not nightly.** One playlist per club per booking week
   (Tuesday–Monday) removes an entire class of problem: residencies spanning several
   nights, the same act playing two sets, and playlists expiring mid-run. The daily run
   becomes an idempotent convergence loop rather than a creation event.

## Documentation

| Document | What it is for |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | The technical specification. Every issue references a section of it. |
| [docs/DECISIONS.md](docs/DECISIONS.md) | ADRs. Why each choice was made, what was rejected, and the evidence. |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Schema, indexes, and the queries that make the taste graph useful. |
| [docs/claude-project-instructions.md](docs/claude-project-instructions.md) | Paste into a Claude Project. Runtime contract for the chatbot. |
| [AGENTS.md](AGENTS.md) | Conventions and invariants for the agent implementing this. |
| [docs/original-prd.md](docs/original-prd.md) | The original PRD, verbatim. Superseded in places; see ADRs. |

The PRD is kept unedited as the record of original intent. Where the architecture departs
from it, the ADR says so explicitly and names the PRD section it supersedes.

## Status

Not yet implemented. The build is tracked as issues under the `v1` milestone, each with
acceptance criteria and explicit `Blocked by` links. Start with the lowest-numbered
unblocked issue.

## Stack

Python 3.12 with `uv`, Postgres 17 (`tsvector`, `pg_trgm`, `pgvector`), `httpx` with
Playwright as a per-club opt-in, Anthropic Claude Haiku for extraction and adjudication,
FastMCP with Google-delegated OAuth, Docker Compose on a small Linux VPS.

## Licence

MIT.
