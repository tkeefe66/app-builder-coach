# True Cost: per-app infra + LLM spend (design)

**Status:** approved 2026-08-11. Supersedes nothing; extends the Phase 3 Cost page.

**Goal.** Show what each app actually costs to run — Railway infra plus Anthropic API — with
enough detail to spot waste, and enough warning to not run out of API credits.

**Non-goal.** Replacing the Claude Code API-equivalent estimate. That number answers a
different question ("what would my subscription usage cost at API rates") and stays in its
own labelled section.

## Why this shape

Actual API cost does not require Anthropic's Admin API. Every Messages API response carries an
exact `usage` block; exact tokens × published price is exact cost, not an estimate. This repo
already proves the pattern — `src/classifier.py:84` reads `resp.usage.input_tokens` and writes
priced rows to `data/llm_costs.jsonl`.

The Admin API (`/v1/organizations/cost_report`) is **unavailable** here regardless: the account
is "Tom's Individual Org", and `platform.claude.com/settings/admin-keys` 404s. Converting to a
team organization would unlock it; Tom has chosen to stay individual.

Railway needs no instrumentation at all — `railway usage projects --json` returns per-project
period-to-date dollars.

**Measured baseline (2026-08-11):** Railway $12.06/period across 10 projects (memory is
$11.65 of it — the real optimization target). Anthropic $8.90 month-to-date across 5 keys.
Combined run rate ≈ $21/month.

## Architecture

Two lanes, because the two costs arrive by different routes.

| Lane | Source | Path | App changes |
|---|---|---|---|
| Infra | `railway usage projects --json` | local sweep → snapshot → server | none |
| LLM | `resp.usage` in each app | app → `POST /api/usage` → server | reporter snippet |

The infra lane fits the existing "local Mac collects, cloud renders" model. The LLM lane cannot:
the apps run on Railway, so they report to the server directly.

**Rejected: a gateway proxy.** Routing every app's Anthropic traffic through a recording proxy
would catch 100% of calls with only an env-var change per app, and is the better system at scale.
It was rejected because it sits in the request path — a gateway outage breaks all ten apps — which
is disproportionate risk to protect ~$9/month. Its one real advantage over the chosen approach is
that it cannot miss a call site (see Known blind spot).

## The join: `apps.yaml`

Names do not line up across systems (Railway `public-dynasty` vs Anthropic key
`Public Dynasty App`; `Purchase-Inventory` vs `Purchase Inventory`). One registry at repo root
is the join key for both lanes:

```yaml
apps:
  - name: b2b-ai-news              # canonical slug, used as the wire value
    display: "B2B AI News"
    railway_project_id: 5fd75529-08bf-4c58-8923-788dbc12b475
    anthropic_key_name: "B2B AI News"
    active: true
```

Loaded by the sweep (to map Railway project ids) and by the server (to validate `/api/usage`
payloads). Same stance as `taxonomy.yaml`: read from the repo root, one copy, never duplicated.

## Data model

### Infra: ship cumulative, derive daily

`railway usage projects --json` returns **billing-period-to-date** totals, not a daily series.
The sweep ships raw cumulative rows; the **server** derives daily deltas.

Snapshot section `infra_usage`, one row per app per capture:

```json
{"capture_date": "2026-08-11", "period_start": "2026-07-27", "app": "b2b-ai-news",
 "cumulative_usd": 2.885146}
```

`period_start` is `billingPeriod.start` from the Railway JSON **normalized to a date** — the raw
value is a timestamp (`2026-07-27T16:07:00+00:00`) and must be truncated to `2026-07-27` before
shipping, so it groups stably across captures.

Server stores by `(period_start, app, capture_date)` and computes `daily = today − previous
within the same period_start`. Deriving server-side keeps re-shipping a day idempotent, and
handles the period rollover on the 27th — where cumulative resets near zero and a naive delta
would go negative. On the first capture of a new `period_start`, daily = cumulative.

### LLM: aggregate on write

`POST /api/usage`, bearer-authed, carries the `usage` block verbatim:

```json
{"app": "b2b-ai-news", "model": "claude-sonnet-4-6", "ts": "2026-08-11T14:00:00Z",
 "input_tokens": 1234, "output_tokens": 56,
 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
```

The server validates `app` against the registry, prices it, and rolls it into
`llm_daily(date, app, model)` — token sums, `cost_usd`, `call_count`, and both cache fields.

No per-call table: growth stays bounded and cache-hit rate is still derivable. An unknown `app`
is a 400, so a typo surfaces immediately instead of silently vanishing.

Pricing reuses `src/usage.py::PRICES` (prefix match, opus-priced fallback). Unlike the Claude
Code lane, applying it to API traffic yields **actual billed cost**, not an estimate.

### Drift check (replaces a computed burn-down)

Three coach-web env vars hold whatever Console last reported, as a window plus a figure:
`COACH_CONSOLE_FROM`, `COACH_CONSOLE_TO`, `COACH_CONSOLE_SPEND`. The server sums tracked LLM
spend over exactly that window and shows both numbers with the gap named. Three vars rather than
two because a bare "as of" date leaves the window's *start* implicit, and Console's own range
selector is what defines it.

**Known blind spot.** Tracked spend only counts what the reporter sees. It misses
un-instrumented apps, missed call sites inside instrumented apps, Workbench/Console usage
(which carries no API key at all), dropped fire-and-forget POSTs, and non-token costs such as
web search and code execution. **Every one of these errs the same direction** — tracked spend
reads low, so a naive "remaining credits" number reads high, failing optimistically right up
until apps break.

The drift check exists because of this: rather than trusting the under-count, it **measures**
it. The gap is the blind spot, in dollars. A gap that grows means something is uninstrumented.

A computed burn-down alert was considered and cut for exactly this reason.

## Reporters

Canonical copies live in `reporters/` (`usage.py`, `usage.js`) with tests, and are copied into
each app — one source of truth to fix when pricing or fields change. Two implementations because
the apps are split between Python and Node.

**Contract: never raise, never block.** Short timeout, swallow every exception. A reporting
failure must never take down an app that was otherwise working.

Each app gets `COACH_USAGE_URL` and `COACH_USAGE_TOKEN` as Railway vars. `COACH_USAGE_TOKEN` is
a **new** token, not the existing `COACH_INGEST_TOKEN` — that one currently lives only on Tom's
Mac, and spreading it across ten cloud services widens the blast radius for no benefit.

**Rollout scope:** the active apps only (B2B AI News, public-dynasty, Purchase-Inventory,
Weekly Updates, app-builder-coach). The five dormant keys are skipped; if one wakes up, the
drift check is what surfaces it.

**app-builder-coach is the exception that proves the contract.** Its Anthropic calls happen in
`src/classifier.py` on Tom's Mac, not on Railway — but it uses the same `reporters/usage.py`
POST as every other app rather than riding the snapshot. One code path, exercised locally first,
and it makes coach-web's own spend visible in the same table as everything else. The existing
`data/llm_costs.jsonl` write stays as-is; the reporter is additive.

**This plan covers this repo only.** Copying the reporter into the four external app repos and
setting their Railway vars is per-app work tracked as a rollout checklist, not tasks in this
repo's implementation plan.

## UI

Cost page gains, above the existing Claude Code estimate section:

- **Tiles:** combined run rate, Railway period-to-date, LLM month-to-date, drift.
- **Per-app table:** app | Railway $ | LLM $ | total | share of total.
- **Cache efficiency card:** cache-read share of input tokens — the optimization view.

**The two billing windows do not align.** Railway bills on a period starting the 27th; Anthropic
reports on calendar months. Each single-source tile is labelled with its own window. The
**combined run rate and the per-app table use a trailing 30 days**, summed from daily rows on
both sides, so the two halves are always over the same span. Never add a Railway period total to
an Anthropic month-to-date total — those cover different spans and the sum is meaningless.

The Claude Code estimate keeps its own labelled section so the two are never confused.

## Error handling

| Condition | Behaviour |
|---|---|
| `railway` CLI missing or unauthenticated | Lane logs, ships nothing, sweep still exits 0 |
| `/api/usage` unknown `app` | 400 |
| `/api/usage` bad or missing bearer token | 401 |
| Reporter cannot reach coach-web | Swallowed; data point lost, app unaffected |
| New `period_start` seen | First capture's daily = cumulative |

## Schema v3

The infra lane rides the snapshot, so `SCHEMA_VERSION = 3` with a new required `infra_usage`
section. **v1 and v2 must keep validating forever** — the outbox can hold older payloads and a
400 quarantines them permanently as `.rejected`.

Deploy order is unchanged from Phase 3: merge → deploy server → verify → *then* run the local
sweep. Never ship v3 at a v2-only server.

`/api/usage` is coach-web's first write endpoint, but it is bearer-authed like `/api/ingest`,
not session-authed — so the CSRF question the Phase 2 review raised does not apply here. It
still applies to the Phase 5 UI writes.

## Testing

- **Unit:** Railway JSON parsing; delta derivation including period rollover and reset; pricing
  math; drift calculation; `apps.yaml` loading and validation.
- **Web:** `/api/usage` auth, payload validation, daily aggregation upsert, unknown-app 400.
- **Schema:** v1, v2, and v3 all validate; v3 rejects a missing `infra_usage`.
- **Reporters:** both helpers swallow a connection error without raising.

## Implementation note

`railway usage projects --json` requires an authenticated CLI on the sweep host. The launchd
sweep runs as Tom, so this works today — but a token expiry would surface as a silently empty
infra lane. The lane logs a warning on failure, and the sweep summary line carries an
`infra=ok|failed` field so a broken lane is visible in `data/sweep.log` without reading the
whole file.
