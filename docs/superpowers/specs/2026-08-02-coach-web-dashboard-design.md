# Coach Web Dashboard — Design

**Date:** 2026-08-02
**Status:** Approved (design review with Tom, this session)

## Problem

The build-coach pipeline collects rich data (capability tags, adoption checklist,
1,700+ classified commits) but delivery depends on remembering to run the
`/build-coach` skill. Tom won't remember. He also wants deeper analytics (he's a
data nerd), weekly Claude Code activity/cost tracking, and to keep discovering
*new* Claude Code features as they ship.

## Decisions made during design review

| Question | Decision |
|---|---|
| Hosting | Railway-hosted web app (phone access matters) |
| Metrics | All four lanes: output, sessions/prompts, cost/tokens, adoption-over-time |
| New-feature discovery | First-class requirement: checklist auto-grows from Claude Code changelog |
| Coaching | Computed gap lists + short LLM weekly brief (~1 cheap call per sweep) |
| Interactivity | Fully interactive: goals, notes, check-offs, dismissals, streaks |
| Architecture | Approach A: local collector, cloud brain-and-face |

## Architecture

```
Mac (launchd, daily 7:30)                    Railway
┌─────────────────────────┐                ┌──────────────────────────────┐
│ sweep (Python, existing)│                │ coach-web                    │
│  collect → classify     │   POST /api/   │  FastAPI ─── Postgres        │
│  + sessions lane (new)  │──  ingest ────▶│    │           │             │
│  + cost lane (new)      │   (bearer tok) │  serves React SPA            │
│  → build snapshot JSON  │                │  cron: changelog watcher     │
└─────────────────────────┘                │  on ingest: LLM weekly brief │
                                           └──────────────────────────────┘
```

- Collection stays local (repos and `~/.claude` only exist on the Mac).
- The snapshot POSTed to the cloud contains **derived aggregates only** —
  classified feature rows, daily counts, cost rollups, adoption statuses. Raw
  git history and prompt content never leave the Mac.
- Railway app owns storage, API, SPA, LLM brief generation, changelog watcher.
- Monorepo: this repo grows `apps/coach-web/`; Railway deploys from that path.
- Shared snapshot schema lives in a `shared/` module with an explicit
  `schema_version`; the server rejects unknown versions with a clear error.

## Data model (Postgres)

**Ingested (sweep-owned, idempotent upsert per snapshot):**

- `snapshots` — one row per sweep run: captured_at, repo count, sweep stats.
- `feature_units` — classified work: repo, date, tags, complexity, short label.
- `activity_daily` — per day: sessions, prompts, commits, per-project breakdown.
  Weekly/monthly views are read-time rollups.
- `cost_daily` — per day: tokens in/out, estimated cost, model mix.
- `adoption_history` — append-only per sweep per feature: status
  (used / configured-but-unused / never-touched), last-used date. Powers
  "watch gaps close" trendlines.
- `feature_catalog` — checklist in the DB: key, name, lesson, `discovered_at`,
  `source` (`checklist` seed | `changelog` watcher).
- `briefs` — every generated weekly brief, browsable history.

**App-owned (UI-written, never touched by ingest):**

- `goals` — references a tag or feature; target date; status
  (active / done / abandoned).
- `feature_checkoffs` — manual "I learned this" marks, independent of detection.
- `notes` — free text attached to a tag, feature, or brief.
- `dismissals` — coach suggestions waved off; excluded from future briefs.

Streaks and weekly targets are computed at read time from `activity_daily` +
`goals`; no stored counters.

## Collector changes (local Python)

All new lanes follow existing conventions: incremental cursors, read-only,
sweep always exits 0.

- **Sessions lane** — parse `~/.claude` transcripts/history for session
  boundaries, timestamps, project paths. Retain counts only, zero prompt
  content (same privacy stance as the adoption lane).
- **Cost lane** — walk new transcript JSONL since cursor; aggregate per-message
  `usage` blocks into tokens per day per model; apply a checked-in pricing
  table to estimate spend.
- **Shipper** — final sweep step: build snapshot JSON from `data/`, stamp with
  content hash + `schema_version`, POST to `COACH_INGEST_URL` with
  `COACH_INGEST_TOKEN` (both from `.env`). On failure, write to
  `data/outbox/`; next run ships all pending, oldest first. Shipping failures
  log loudly but never fail the sweep.

## Backend (FastAPI on Railway)

- `POST /api/ingest` — machine bearer token (distinct from login), validates
  `schema_version`, transactional upsert, then triggers brief generation as a
  FastAPI background task (ingest responds without waiting on the LLM).
- Read endpoints: matrix, tag trendlines, weekly activity, cost rollups,
  adoption history, gaps, briefs.
- Write endpoints: CRUD for goals, check-offs, notes, dismissals.
- **Auth (human):** single-user password login (hash in Railway env var) →
  long-lived signed session cookie. Works on phone. Multi-user is explicitly
  out of scope; the login page is the seam if that ever changes.
- **LLM brief:** on ingest, build compact context (this week vs last: units,
  sessions, cost; never-built/stale lists; adoption gaps minus dismissals;
  active goals) → one Claude call (Haiku default, configurable) → ~200-word
  brief with 2–3 concrete build-next suggestions tied to actual gaps. Stored in
  `briefs`. Failures non-fatal: show previous brief flagged
  "generation failed". Costs logged.
- **Changelog watcher:** weekly Railway cron fetches Claude Code
  changelog/release notes, extracts feature-shaped entries, diffs against
  `feature_catalog`. New entries land as never-touched with
  `source: changelog`. Conservative parsing: unrecognized formats log and skip;
  false positives dismissable from the UI.

## Frontend (React + Vite SPA, mobile-responsive)

- **Overview** — this-week stat tiles (features shipped, sessions, spend,
  streak), current brief, "new Claude Code features since last week" strip,
  data-freshness stamp ("data as of <date>" — staleness visible, never silent).
- **Capabilities** — live matrix: per-tag trendlines, complexity over time,
  per-repo breakdown, never-built + stale lists.
- **Activity** — weekly sessions/prompts/commits charts, per-project split,
  day-of-week patterns, streaks.
- **Cost** — spend/token trendlines, model mix.
- **Adoption** — checklist status board with per-feature history, check-offs,
  changelog newcomers flagged.
- **Goals & Coach** — goal create/track, brief archive, dismissals.

Charts follow the dataviz skill's system. Visual design goes through
frontend-design at build time.

## Error handling

- Sweep POST fails → snapshot to outbox, re-ship next run, sweep still exits 0.
- Ingest with unknown `schema_version` → 400 with clear message, nothing
  partial written.
- Brief generation fails → previous brief shown, flagged; never blocks ingest.
- Changelog parse ambiguity → log and skip, never insert junk.

## Testing

- pytest: new collector lanes (fixture transcripts → expected aggregates),
  snapshot schema round-trip, outbox retry behavior.
- FastAPI TestClient: ingest idempotency, auth (machine + human), CRUD.
- vitest: frontend rollup/streak computation.
- LLM calls mocked via the same client-factory pattern as `sweep.py`.

## Deploy

- Railway project: `coach-web` service (from `apps/coach-web/`) + Postgres.
- Migrations: Alembic.
- Railway env: `DATABASE_URL`, `COACH_INGEST_TOKEN` (same name both sides),
  `COACH_PASSWORD_HASH`, `ANTHROPIC_API_KEY`.
- Local `.env` gains `COACH_INGEST_URL`, `COACH_INGEST_TOKEN`.

## Build phases (each ships something usable)

1. Ingest API + Postgres + auth, fed by existing data lanes (shipper included).
2. Dashboard UI over ingested data.
3. New collector lanes: sessions, cost.
4. Coach brief + changelog watcher.
5. Interactive layer: goals, check-offs, notes, dismissals.

## Out of scope

- Multi-user support.
- Uploading raw git history or prompt content.
- Real-time updates (daily sweep cadence is the refresh rate).
- Editing capability taxonomy from the UI (stays a code change).
