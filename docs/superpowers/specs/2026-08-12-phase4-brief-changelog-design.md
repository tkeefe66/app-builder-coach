# Phase 4 design: LLM brief + changelog watcher

**Status:** approved design, not yet planned or built.
**Supersedes nothing.** Fills in the Phase 4 bullet of
`docs/superpowers/specs/2026-08-02-coach-web-dashboard-design.md` (§Backend, §Frontend).

## Why

The dashboard reports. It does not coach. Two gaps:

1. **No brief.** `Goals.tsx` is a placeholder that says weekly briefs land in Phase 4.
   Every gap list on Overview is computed and displayed, but nothing reads them back to
   Tom as "here is what to build next."
2. **The checklist cannot grow.** `feature_catalog` has a `source` column
   (`checklist` | `changelog`) and `Adoption.tsx` already filters `source === "changelog"`
   into a "New since last check" strip. Nothing has ever written a `changelog` row, so the
   strip has never rendered. New Claude Code features are invisible to the coach.

Correcting the handoff: `docs/HANDOFF.md` claims the briefs table exists. **It does not** —
`models.py` has no such class and no brief code exists anywhere in the repo.

## Decisions taken (owner, 2026-08-12)

| Question | Decision |
|---|---|
| Brief cadence | Generate on **every ingest**. Sweep is daily, so briefs are daily. |
| Where the LLM call runs | **Server-side**; set `ANTHROPIC_API_KEY` on Railway. |
| Changelog parsing | **Conservative heuristic only.** No LLM extraction. |
| Watcher trigger | Same ingest-triggered background task as the brief, behind a 7-day guard. |

The cadence and watcher answers interact: the brief loses its guard, the watcher keeps one.
One background task, two different cadences inside it.

## Architecture

```
POST /api/ingest
  └─ transactional upsert (unchanged)
  └─ respond 200                        ← never waits on the LLM
  └─ BackgroundTask(post_ingest)        ← own DB session; request's is closed
       ├─ brief.generate(db)            every ingest
       └─ changelog.check(db)           only if last_checked_at is >= 7 days old
```

Two modules under `apps/coach_web/`, each a set of pure functions plus a thin driver, so
the parsing and context-building logic is testable without a database or an API key:

- `brief.py` — `build_context(db, today) -> dict`, `render_prompt(ctx) -> str`,
  `generate(db, client_factory=..., now=...) -> Brief`
- `changelog.py` — `parse(markdown) -> list[Entry]`, `newer_than(entries, watermark)`,
  `check(db, fetch=..., now=...) -> dict`

Both take their side effects (`client_factory`, `fetch`, `now`) as injected parameters,
following the `sweep.py::_client_factory` pattern already in the repo.

## Data model

One Alembic revision, two tables.

```python
class Brief(Base):
    __tablename__ = "briefs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[str] = mapped_column(String(32), index=True)  # ISO 8601 UTC
    body: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="ok")   # ok | failed
    error: Mapped[str] = mapped_column(String(500), default="")


class WatcherState(Base):
    __tablename__ = "watcher_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(200), default="")
    updated_at: Mapped[str] = mapped_column(String(32), default="")
```

`watcher_state` holds exactly two keys today — `changelog.version_watermark` and
`changelog.last_checked_at`. A two-column KV table beats bolting two nullable columns onto
an unrelated table, and gives the watcher somewhere to grow without another migration.

`briefs` grows one row per day (~365/year, each a few hundred bytes). No pruning needed.

## The changelog watcher

**Source:** `https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md`,
fetched with `httpx` (already a dependency). Verified reachable, HTTP 200.

**Format** is highly regular — `## <version>` headings, then `- <Verb> <text>` bullets.
Measured distribution across the current file (361 versions, ~3,500 bullets):

| Leading verb | Count |
|---|---|
| Fixed | 2281 |
| **Added** | **487** |
| Improved | 374 |
| Changed | 108 |
| Removed | 29 |

**Only bullets beginning `Added ` become `feature_catalog` rows.** Fixed/Improved/Changed/
Removed are refinements to things that already exist, not new capabilities to adopt — and
the Adoption board is a list of capabilities to adopt. Anything else (a `## ` heading that
is not `N.N.N`, a bullet with an unrecognized shape, a version section with no `Added`
bullets) is logged at debug and skipped. This is the spec's stated posture — *conservative
parsing: unrecognized formats log and skip* — and it matters more than usual right now
because **Phase 5 owns dismissals, so a false positive today is stuck in the catalog with
no way to wave it off.**

### The bootstrap trap

The changelog holds **361 versions and 487 `Added` bullets** of history. A first run that
simply inserts every `Added` bullet floods the Adoption board with 487 undismissable
newcomer rows and destroys the page. This is the single most important behavior in the
watcher:

- **First run** (no `changelog.version_watermark` row): record the newest parsed version as
  the watermark, insert **zero** rows, set `last_checked_at`.
- **Later runs**: insert `Added` bullets only from versions **strictly above** the
  watermark, then advance the watermark to the newest parsed version.
- Version comparison is a **tuple of ints** parsed from the `N.N.N` heading, so `2.1.99`
  sorts below `2.1.228`. A heading that does not parse is skipped and is **never** treated
  as newer — a malformed heading must not be able to advance the watermark past real
  entries.

### Row shape

| Column | Value |
|---|---|
| `name` | `Added ` stripped; cut at the first `;`; leading article (`a`/`an`/`the`) dropped; truncated to 120 chars (the column width) |
| `lesson` | `""` — changelog entries have no lesson id |
| `source` | `"changelog"` |
| `discovered_at` | date first seen (drives the existing "New since last check" strip) |

`name` is the `feature_catalog` primary key, so re-inserting an existing name is skipped
rather than erroring. Inserts are idempotent by construction.

**No new column for the version.** `discovered_at` already drives the UI strip, and nothing
consumes a version today — adding one would be the speculative surface this repo has been
trimming.

## The brief

**Model:** `claude-haiku-4-5`, overridable via `COACH_BRIEF_MODEL`.

The general default for new Claude code is `claude-opus-5`; Haiku is a deliberate owner
choice for a ~200-word daily summarization over a small context, and one env var away from
being changed if the briefs read thin. Haiku 4.5 is $1/$5 per MTok, 200K context.

Three parameters are wrong for this model or this payload and must **not** be added:

- **No `effort`** — errors on Haiku 4.5; it is an Opus/Sonnet-tier parameter.
- **No adaptive thinking** — Haiku 4.5's form is the older `budget_tokens`. A ~200-word
  summary needs no thinking at all; omit the parameter entirely.
- **No `cache_control`** — Haiku 4.5's minimum cacheable prefix is **4096 tokens** and the
  brief context is far below that. A breakpoint would silently never cache while still
  costing a write premium.

`max_tokens=1024`, non-streaming (well under the ~16K streaming threshold).

**Context** — computed from the DB by a pure function, no API call:

- this week vs last week: feature units, sessions, cost — week boundary from the existing
  `aggregate.week_start`, and unit counts exclude `kind == "commits"` for the same reason
  `units_this_week` does (commit clusters are month-resolution and inflate a week figure)
- `never_built` tags and `stale` tags (same derivation `/api/overview` already uses)
- adoption gaps (`status == "never-touched"` on the latest snapshot)

Goals and dismissals are named in the original spec's brief context but are **Phase 5
tables that do not exist**; they are simply absent from the context and the prompt does not
mention them.

**Output:** ~200 words with 2–3 concrete build-next suggestions tied to actual gaps.

### Cost honesty

The server's brief calls are real Anthropic spend. Left unreported they resurface as drift
in the "Untracked spend" tile that the true-cost work just built.

So the brief writes its own usage into `llm_daily` as app `app-builder-coach`, through the
same path `/api/usage` uses. `record_usage` currently inlines its upsert; extract that into

```python
def upsert_llm_daily(db, date: str, app: str, model: str, usage: dict) -> None
```

in `usage_api.py`, and have both the endpoint and `brief.py` call it. Pricing stays in the
existing `cost_for` / `shared.pricing.price_for`. Forking either would put two prices on the
same call.

## API and frontend

**`GET /api/briefs?limit=10`** (authenticated, like every other read endpoint) returns
newest first:

```json
{"latest": {"created_at": "...", "body": "...", "status": "ok", "stale": false},
 "archive": [{"created_at": "...", "body": "...", "status": "ok"}]}
```

`latest` resolves the spec's *"failures non-fatal: show previous brief flagged"* rule: if
the newest row is `failed`, `latest` carries the most recent `ok` row with `stale: true`,
plus the failed row's `error`. If there has never been an `ok` brief, `latest` is `null`.

**Overview** renders the current brief, flagged when `stale`. **Goals.tsx**'s placeholder
becomes the brief archive.

**Adoption needs no frontend change** — it already filters `source === "changelog"` and
renders the "New since last check" strip. The watcher just has to write rows.

## Error handling

| Failure | Behavior |
|---|---|
| Brief generation raises (auth, rate limit, timeout, network) | Write a `failed` brief row with the error, log it. Ingest already returned 200 — never blocks. |
| `ANTHROPIC_API_KEY` unset | Same path: a `failed` row reading "ANTHROPIC_API_KEY is not set", visible on Overview. **Deliberately not added to `REQUIRED_PROD_SECRETS`** — see below. |
| Changelog fetch fails (network, non-200, non-text) | Log, leave watermark and `last_checked_at` untouched, retry next week. |
| Changelog format drifts | Unparseable headings/bullets skipped; a run that parses zero versions leaves the watermark untouched. |
| Background task raises anything | Caught and logged at the task boundary; ingest is already complete. |

**On `_check_prod_secrets`:** `ANTHROPIC_API_KEY` is deliberately **not** added to the
boot-time required-secrets check. Adding it re-runs the `apps.yaml` trap — deploy before
setting the variable and production crash-loops. A missing key instead surfaces as a failed
brief with a clear message on the Overview page, which is visible without being fatal. The
variable still gets set on Railway *before* the deploy either way.

## Testing

- `changelog.parse` against a fixture of real changelog text: `Added` taken,
  Fixed/Improved/Changed/Removed skipped, malformed heading skipped, name derivation
  (article stripped, cut at `;`, truncated at 120).
- Watermark: **first run inserts zero rows and records a watermark** (the bootstrap trap —
  this is the load-bearing test); a later run inserts only above the watermark; a run with
  nothing new inserts nothing; an unparseable heading never advances the watermark.
- 7-day guard: watcher skipped when `last_checked_at` is recent, runs when it is old or absent.
- `brief.build_context` as a pure function over seeded rows — no API key, no network.
- `brief.generate` with an injected fake client: happy path writes an `ok` row and an
  `llm_daily` row; a raising client writes a `failed` row and does not raise.
- `/api/briefs`: newest-first, `stale` flag when the newest is `failed`, `null` when no
  `ok` brief has ever existed, 401 unauthenticated.
- Ingest still returns 200 when brief generation fails.
- Alembic revision inspected (only the two `create_table`s) and cycled up/down/up.

## Out of scope

- Goals, check-offs, notes, dismissals — Phase 5.
- Editing or regenerating a brief on demand.
- LLM-based changelog extraction.
- Recording which changelog version introduced an entry.
- Any change to the Adoption page's frontend.
- Notifications of any kind when a brief lands.

## Deploy note

`ANTHROPIC_API_KEY` must be set on the `coach-web` Railway service **before** the deploy
that ships this. It is the only new environment variable. `COACH_BRIEF_MODEL` is optional
and defaults to `claude-haiku-4-5`. No new repo-root file, so the Dockerfile `COPY` line
needs no change — but do not remove anything from it.
