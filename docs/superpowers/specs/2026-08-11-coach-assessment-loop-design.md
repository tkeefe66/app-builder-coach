# Coach assessment loop — design

Status: approved by Tom 2026-08-11. Supersedes the Phase 4 brief design
(`2026-08-12-phase4-brief-changelog-design.md`) for everything under
"Brief generation"; the changelog watcher in that spec is unchanged.

## Problem

The Goals & Coach page renders ten near-identical 200-word essays, all dated the
same day, all recommending Docker / websockets-sse / background tasks. Two causes,
one of which is the real one.

**Surface cause — cadence.** `ingest.post_ingest` generates a brief on *every*
`POST /api/ingest`. Nothing dedupes.

**Root cause — the model has almost nothing to condition on.** The entire input to
`brief.render_prompt` is six numbers and three lists of bare names:

```
units 14 vs 6 · sessions 22 vs 41 · spend $X vs $Y
never_built: <taxonomy tags minus tags ever used>
stale:       <tags untouched 180d>
adoption_gaps: <Claude Code features never touched>
```

`never_built` moves only when a whole capability category is picked up for the
first time — call it monthly. So consecutive runs receive a near-identical prompt
and produce a near-identical essay. Capping the cadence would hide this, not fix
it.

Everything with substance is in the database and never reaches the model:

| Table | Contents | Reaches the brief today |
|---|---|---|
| `feature_units` | every unit of work: repo, date, title, prose summary, complexity 1–5, tags | no |
| `activity_daily` | commits by repo, sessions, prompts, full history | two week-sums only |
| `cost_daily` / `llm_daily` / `infra_usage` | spend by app, model, service | two week-sums only |
| `grade.compute_grade` | tiered rubric grade, gaps, **best-fit repo per missing tag** | no |
| `goals` / `feature_checkoffs` / `notes` | what Tom actually committed to | no |
| `adoption_history` | adoption status over time | current snapshot only |

Third problem, stated by Tom: a brief that fires on a schedule is a newsletter.
Recommendations should earn their place — one re-issued eight days running and
never acted on is noise, and the system should notice.

## Shape of the fix

The brief stops being a daily newsletter and becomes **a standing assessment plus
a change log**.

1. **Assessment** — one deep pass over the entire ingested corpus. Durable output.
2. **Delta** — a short amendment, generated only when material facts actually move.
3. **Change gate** — deterministic Python, not a model judgement. No change, no
   model call, no row, no cost.
4. **Outcome tracking** — every recommendation is marked converted / dismissed /
   ignored, and that history feeds the next prompt.

## Data model

### `briefs` (existing table, additive changes only)

| Column | Change |
|---|---|
| `kind` | new, `String(16)`, `"assessment"` \| `"delta"`. Existing rows backfill to `"delta"`. |
| `day` | new, `String(10)`, indexed, **not unique**. Backfilled from `created_at[:10]`. |
| `fingerprint` | new, `String(64)`, the gate hash this brief was generated at. |

`body` keeps its current meaning — the prose summary. No migration on it, so the
ten existing rows keep rendering as prose with zero recommendations.

**`day` is deliberately not unique.** An earlier draft of this design enforced one
brief per calendar day. That rule existed only to compensate for the missing change
gate; with the gate in place it would wrongly block a legitimate second delta on a
day when something changed twice. Dropping it also removes a destructive migration
(delete-all-but-newest-per-day) that would have been irreversible — this repo has
no database backups.

### `brief_recommendations` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `brief_id` | FK → `briefs.id`, indexed | |
| `ord` | int | rank within the brief, 0-based |
| `title` | `String(200)` | imperative, e.g. "Containerize purchase-inventory" |
| `kind` | `String(16)` | `"tag"` \| `"feature"` — matches `writes.Kind` |
| `target` | `String(120)`, indexed | exact taxonomy tag or Claude Code feature name |
| `why` | `Text` | the argument |
| `evidence` | `Text` | the specific shipped work it is grounded in |
| `outcome` | `String(16)` | `open` \| `converted` \| `dismissed` \| `superseded` |
| `outcome_at` | `String(32)` | ISO, empty while `open` |

A table rather than a JSON column on `briefs`: the recurring rollup is a
`GROUP BY target`, and goal conversion needs a stable row to mark.

### `watcher_state` (existing k/v store)

- `brief.fingerprint` — hash at the last generated brief.
- `brief.deltas_since_assessment` — integer counter.

## Context builder

`brief.build_corpus_context(db, today)` replaces `build_context` for assessments.
Pure function over DB rows, as today, so it is testable without a key or network.

Sections:

- **Repos** — per repo: first/last unit date, unit count, distinct tags, mean
  complexity.
- **Work** — feature units with `title`, `summary`, `complexity`, `tags`, `date`,
  `repo`.
- **Activity** — monthly buckets of commits / sessions / prompts across all history.
- **Cost** — monthly buckets, plus by-model and by-app breakdowns.
- **Grade** — `grade.compute_grade()` verbatim, including per-gap `best_fit_repo`.
  This is already computed and has never been shown to the model.
- **Adoption** — per feature: status and `last_used`.
- **Commitments** — goals (all statuses), check-offs, dismissals. Dismissals are
  named explicitly so the model knows they were considered and waved off, not
  merely absent.
- **Recommendation history** — per target: times suggested, date range, outcome.

### Bounding

The work section is the only unbounded one. Budget it at ~40k tokens: keep the
most recent 150 units plus the 50 highest-complexity units not already included,
deduplicated.

**Truncation is always stated in the prompt** (`"showing 200 of 431 units: the 150
most recent and the 50 most complex"`). A silently truncated corpus reads to the
model as a complete one and produces confidently wrong conclusions about coverage.
Pinned by a test.

`build_delta_context(db, today, assessment)` is much smaller: the current
assessment's summary and recommendations, plus a rendered diff of what moved since
its fingerprint.

## The change gate

`brief.fingerprint(db, today) -> str` — sha256 over a canonical JSON dump of:

- sorted set of tags ever used
- sorted set of adopted feature names (`status != "never-touched"`)
- sorted list of `(goal_id, status)`
- `feature_units` count
- trailing-7-day spend, **rounded to the nearest dollar**
- `watcher_state['changelog.last_checked_at']` watermark

Spend is rounded so ordinary cent-level drift does not trip the gate. Everything
else is a set or a count, and moves only on a real event.

Decision procedure in `post_ingest`:

```
fp = fingerprint(db, today)

no assessment row exists         -> generate ASSESSMENT      # the initial deep read
fp == watcher_state[fingerprint] -> return. no call, no row, no cost.
deltas_since_assessment >= 5     -> generate ASSESSMENT, reset counter
otherwise                        -> generate DELTA, increment counter
```

Plus `POST /api/reassess` (cookie + same-origin, on the `writes.py` router) which
forces an assessment on demand.

**No time-based reassessment.** If nothing has changed in thirty days there are no
deltas and nothing to reassess. Change, not clock, throughout.

## Model calls

Structured output via `output_config.format`, not forced tool use —
`claude-haiku-4-5` and `claude-sonnet-5` both support it, and it leaves the
existing Haiku parameter constraints untouched.

```json
{
  "type": "object",
  "properties": {
    "summary": {"type": "string"},
    "recommendations": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "title":    {"type": "string"},
          "kind":     {"type": "string", "enum": ["tag", "feature"]},
          "target":   {"type": "string"},
          "why":      {"type": "string"},
          "evidence": {"type": "string"}
        },
        "required": ["title", "kind", "target", "why", "evidence"],
        "additionalProperties": false
      }
    }
  },
  "required": ["summary", "recommendations"],
  "additionalProperties": false
}
```

`target` must be an exact string from the gap lists supplied in the prompt; the
system prompt already demands this and the writer validates it, dropping any
recommendation whose target is not in the supplied vocabulary rather than storing
a dangling one.

`evidence` is the field that stops the output being generic. It must cite specific
shipped work — *"5 repos, 14 units, zero container work; purchase-inventory runs
four services"* — not restate the recommendation.

### Models

| Path | Model | Env override | Parameters |
|---|---|---|---|
| Assessment | `claude-sonnet-5` | `COACH_ASSESSMENT_MODEL` | `output_config` with `effort: "medium"`, adaptive thinking |
| Delta | `claude-haiku-4-5` | `COACH_BRIEF_MODEL` | **no `effort`, no `thinking`, no `cache_control`** |

The two paths get separate parameter construction. The Haiku constraints and their
pinning test (`test_generate_sends_no_effort_thinking_or_cache_control`) stay
confined to the delta path and are not modified.

The assessment is ~25–40k input tokens and runs roughly twice a month — single-digit
cents. Both calls report through `usage_api.upsert_llm_daily(..., "app-builder-coach", ...)`
as today, so the assessment shows as a visible spike in the coach's own spend.
That is correct, not a defect.

### Prompt rule for recommendation history

The system prompt carries an explicit rule:

> A recommendation you have made three or more times that was never acted on must
> either be argued on materially different grounds, or dropped in favour of
> something else. Do not restate it.

This is Tom's "unless the recommendations provide value" made operational.

## Outcome propagation

In `writes.py`:

- `POST /api/goals` — after creating the goal, mark every `open`
  `brief_recommendations` row with the same `(kind, target)` as `converted`.
- `POST /api/dismissals` — mark every `open` row for that target `dismissed`.
  Idempotent, matching the existing dismissal behaviour.
- When a new brief is written, any `open` row for a target the new brief re-issues
  becomes `superseded`, so "suggested 8×" is a count rather than eight live rows.

Prior rows are marked, not just the current one, because every one of them was
pitching the same thing.

## API

- `GET /api/briefs` returns `{assessment, deltas, history, recurring}`.
  - `assessment` — newest `kind="assessment"`, with recommendations, `stale` flag
    and error when a later regeneration failed.
  - `deltas` — deltas since that assessment, newest first.
  - `history` — older entries, one row per brief, with a summary line.
  - `recurring` — `GROUP BY target`: count, first/last date, outcome.
- `POST /api/reassess` — forces an assessment. On `writes.py`'s router, so it
  inherits `require_user` + `require_same_origin`.

## Frontend

`pages/Goals.tsx` splits into components:

1. **`AssessmentCard`** — summary prose, "updated <date>", Reassess button, ranked
   `RecommendationCard`s.
2. **`RecommendationCard`** — title, why, evidence; `Add as goal` (prefills
   `kind`/`target`/`title` from the model's own output) and `Dismiss` (reuses
   `POST /api/dismissals` — no new endpoint).
3. **`SinceThen`** — deltas beneath the assessment, or *"No material change since
   <date>."*
4. **`GoalPicker`** — active goals plus a collapsed picker over `never_built`,
   `stale` and `adoption_gaps`, all of which `/api/overview` already returns. The
   free-text goal field is **removed**, per Tom's decision.
5. **`BriefHistory`** — the `recurring` rollup (*"deploy-docker — suggested 8×,
   never acted on"*, which is itself coaching) plus one collapsed `<details>` per
   entry. Native `<details>`, so no new dependency and keyboard-accessible.

Page order: Assessment → Since then → Goals → History → Dismissed. Goals moves
above the archive; today it sits under roughly 3000 words of duplicate prose.

**Accepted consequence of removing the free-text field:** a goal that is neither a
taxonomy tag nor a Claude Code feature can no longer be created. This is consistent
with the existing data model — `writes.GoalIn` already requires `kind ∈ {tag,
feature}` plus a `target`, so free text was always half-formed — but it is a real
narrowing, made deliberately.

## Failure handling

| Failure | Behaviour |
|---|---|
| Assessment generation fails | Keep the previous assessment; flag `stale` and surface the error. Mirrors the existing `latest`/`stale` fallback. |
| Delta generation fails | Store a `failed` delta row. A run of `failed` rows remains the only visible signal of a misconfigured key — do not drop it. |
| Response is not parseable JSON despite the schema | Store the raw text as `summary` with zero recommendations. Do **not** mark `failed`: a degraded brief beats no brief, and the failed state is reserved for genuine call failures. |
| A recommendation's `target` is not in the supplied vocabulary | Drop that recommendation, keep the rest, log it. |

## Tests

Python:

- fingerprint is stable for unchanged state and moves when the tag set, adopted
  set, goal set, unit count, dollar-rounded spend, or watermark changes
- an unchanged fingerprint schedules **no** model call
- first run with no assessment generates an assessment, not a delta
- five deltas trigger reassessment and reset the counter
- `POST /api/reassess` forces one regardless of fingerprint
- corpus bounding keeps recent + most-complex and **states the truncation**
- converting a goal marks all prior open rows for that `(kind, target)`
- dismissing marks all open rows for that target
- re-issuing supersedes prior open rows
- an out-of-vocabulary target is dropped, siblings survive
- legacy prose briefs (no `kind`, no recommendations) render without error
- the existing Haiku parameter test still passes against the delta path

Frontend (vitest): `AssessmentCard`, `RecommendationCard`, `GoalPicker`,
`BriefHistory`, including the empty states (no assessment yet, no deltas, no
recurring items).

Existing invariants that must not regress: the autouse `background_calls` fixture
in `tests/web/conftest.py` still neutralizes and records `post_ingest` so the suite
never hits the network; `require_same_origin` stays a router-level dependency and
never becomes global middleware.

## Migration

One Alembic revision, **additive and non-destructive**:

- add `briefs.kind` (default `"delta"`), `briefs.day` (indexed, not unique),
  `briefs.fingerprint`
- backfill `day` from `created_at[:10]` and `kind` to `"delta"`
- create `brief_recommendations`

No rows are deleted, so no pre-migration dump is required. The ten existing prose
briefs remain in the history as legacy entries, which is honest — they happened.

## Out of scope

- Database backups. Still the repo's largest real gap (see `docs/HANDOFF.md`), and
  this design deliberately avoids adding to the exposure by keeping the migration
  non-destructive.
- The changelog watcher, unchanged from Phase 4 — including the bootstrap-trap
  guard, which must not be removed.
- Re-anchoring `COACH_CONSOLE_*` drift.
