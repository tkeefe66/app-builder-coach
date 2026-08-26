# Overall Grade — design spec (2026-08-03)

Approved direction from brainstorming with Tom. Adds an "overall grade" hero card to the
Overview page answering: **how close am I to a typical engineer?** Tom has zero coding
background (works in GTM Ops); the grade must be honest, explainable, and actionable.

Decisions locked during brainstorming:

- **Form:** career-ladder level label + progress bar (not letter grade / not bare %).
- **Inputs:** capabilities only (breadth × depth × recency over the 25 taxonomy tags).
  Activity habits and Claude Code adoption stay out of the grade.
- **Engine:** deterministic, checked-in rubric. Same data → same grade. No LLM.
- **Presentation:** grade + "path to next level" (top unmet requirements), each with a
  best-fit repo chosen by a deterministic affinity heuristic.
- **Scoring model:** core-gated + score — levels gate on core skills; you cannot reach
  Mid-Level by stacking specialties while fundamentals are thin.

## Scope

- **No DB changes, no migrations, no schema-version bump, no collector changes.** All
  inputs (feature units: repo, date, tags, complexity) are already in Postgres. None of
  the Phase 3 deploy-ordering constraints apply; deploy is a single ordinary release.
- New: `rubric.yaml` (repo root), `apps/coach_web/grade.py`, `grade` field on
  `/api/overview`, `GradeCard` component on Overview.

## 1. User-visible behavior

Hero card at the top of Overview, above the existing tile row:

```
OPERATING AT: JUNIOR ENGINEER
[############------------] 58% to Mid-Level

To reach Mid-Level, build:
  - auth at depth (3 builds — need 5+)          best fit: <repo>
  - deploy-docker (never built)                 best fit: <repo>
  - error-handling (2 builds — need 5+)         best fit: <repo>
```

- Ladder: **Newcomer → Beginner → Junior → Mid-Level → Senior**. "Typical engineer" =
  Mid-Level. The progress bar always targets the *next* level; after reaching Mid-Level
  it retargets to Senior.
- Caption under the card: *"Based on what you've shipped across skill areas — not years
  of experience."* (Frontend copy; keeps the framing honest — this measures demonstrated
  output built with Claude, not hand-coding ability.)
- Top 3 unmet requirements shown; each names the tag, what you have vs. what's needed,
  and the best-fit repo. Empty state (no snapshot ingested yet): card body shows
  "No data yet", same treatment as other cards.
- The grade **can go down**: skills untouched for 180+ days count at half credit (see
  §3), so decay can drop a level. This is intended coaching behavior.

## 2. `rubric.yaml` (repo root)

Sibling of `taxonomy.yaml`; checked in; read at server startup via the same pattern as
`apps/coach_web/taxonomy.py` (repo root, same Docker image, never duplicated).
Tuning thresholds is a YAML edit, no code change.

Three sections:

**`tiers`** — every taxonomy tag assigned exactly one tier:

- `core` (10): auth, api-backend, data-modeling, db-migrations, testing-depth,
  error-handling, frontend-spa, deploy-docker, deploy-infra, privacy-security
- `standard`: api-client, background-jobs, caching, cli-tooling, state-machines,
  llm-integration, webhooks, frontend-ssr, agents-automation
- `specialty`: charts-svg, email-ingestion, llm-cost-control, payments-money,
  scraping, websockets-sse

**`levels`** — ordered list. Each level has `gates` (per-tag requirements:
`min_count`, optional `min_avg_complexity`, optional `within_days`) and an optional
`breadth` requirement (number of distinct tags with ≥1 build). Initial calibration
(lands today's data at Junior, ~72% to Mid-Level):

| Level | Gates (sketch) | Breadth |
|---|---|---|
| Newcomer | none (default floor) | — |
| Beginner | none (breadth only) | 3 |
| Junior | api-backend ≥3, data-modeling ≥3, frontend-spa ≥2, testing-depth ≥2, db-migrations ≥1, auth ≥1, error-handling ≥1 | 10 |
| Mid-Level | all 10 core tags: auth ≥5@cx3, api-backend ≥10@cx3, data-modeling ≥10@cx3, db-migrations ≥5@cx3, testing-depth ≥8@cx3.5, error-handling ≥5@cx3, frontend-spa ≥10@cx3, deploy-docker ≥2, deploy-infra ≥3@cx3, privacy-security ≥5@cx3 | 18 |
| Senior | all 10 core ≥12@cx3.5 within 365d; ≥8 standard/specialty tags at ≥3 builds | 24 |

Exact YAML numbers are drafted at implementation time from this table; they are
data, not contract — Tom tunes them freely afterward.

**`pairs_with`** — per-tag affinity list used by the best-fit heuristic, e.g.
`websockets-sse: [api-backend, frontend-spa]`, `deploy-docker: [deploy-infra,
api-backend]`. Tags may have an empty list.

**Validation (fail-fast at startup, like existing config):** every tag in `tiers`,
`gates`, and `pairs_with` must exist in `taxonomy.yaml`; every taxonomy tag must have
exactly one tier; levels must be a non-empty ordered list. Invalid rubric → server
refuses to start with an actionable message.

## 3. Scoring (pure functions, `apps/coach_web/grade.py`)

Inputs: feature-unit rows `(repo, date, tags, complexity)`, rubric, `today`.

- **Per-gate fraction** = `min(1, count/min_count)` × `min(1, avg_cx/min_avg_cx)` (when
  specified) × **recency multiplier** (1.0 if the tag's `last_done` is within 180 days,
  else 0.5 — reusing the app's existing stale threshold). A gate is *satisfied* when
  its fraction = 1.0. `within_days` gates additionally require `last_done` within that
  window. The two recency rules **stack** (ruled 2026-08-03): work older than both the
  180-day stale window and a gate's `within_days` is quartered (×0.5 twice) —
  progressively harsher decay for long-abandoned skills is intended coaching.
- **Breadth fraction** = `min(1, distinct_tags_built / required)`.
- **Level attained** = highest level whose gates and breadth are ALL satisfied. Levels
  are checked in order and the climb stops at the first unsatisfied level.
- **percent_to_next** = round(100 × mean of the next level's gate + breadth fractions)
  (capped at 99 until the level's gates are actually satisfied). Displayed as the
  progress bar.
- **Gaps** = the next level's unsatisfied gates, sorted ascending by fraction (worst
  first); API returns all, card shows top 3. Each gap carries
  `{tag, have: {count, avg_complexity, last_done}, need: {min_count,
  min_avg_complexity, within_days}, best_fit_repo}`.
- **Best-fit repo** (deterministic): the repo with the most feature units tagged with
  any of the gap tag's `pairs_with` tags in the last 180 days; ties → most recent such
  unit; no pairs or no match → the repo with the most recent feature unit overall.

## 4. API

`/api/overview` response gains a `grade` object (no new endpoint):

```json
"grade": {
  "level": "junior", "level_label": "Junior Engineer",
  "next_level": "mid", "next_label": "Mid-Level Engineer",
  "percent_to_next": 58,
  "gaps": [ { "tag": "auth", "have": {...}, "need": {...},
              "best_fit_repo": "budget-app" } ]
}
```

`grade` is `null` when the DB has no feature units (frontend shows the empty state).
At Senior (top level), `next_level`/`next_label` are `null`, `percent_to_next` is 100,
`gaps` is `[]`, and the card shows the level without a target.

## 5. Frontend

- New `GradeCard` component (`frontend/src/components/GradeCard.tsx`) rendered at the
  top of `Overview.tsx`, above the tile row. Progress bar and colors from the existing
  `tokens.css` palette; no new dependencies.
- The `Overview` type gains the `grade` field; `null` → empty-state card.

## 6. Error handling

- Rubric problems fail at **startup**, never at request time (§2 validation).
- `grade.py` is total over valid inputs: empty rows → `null` grade; a tag present in
  data but absent from taxonomy is impossible upstream (ingest validates against
  taxonomy) and is ignored defensively if it occurs.

## 7. Testing (TDD)

- **pytest** (`tests/web/test_grade.py`): gate fraction math (count, complexity,
  recency multiplier), level attainment incl. decay demotion, percent_to_next,
  gap ordering, best-fit heuristic (pairs match, tie-break, fallback), rubric
  validation failures (unknown tag, missing tier, empty levels), API shape via the
  existing test client (grade present, null when empty).
- **vitest**: GradeCard render states (normal, top-level, null/empty).
- Existing suites (139 pytest + 8 vitest) stay green.

## Out of scope (deliberately)

- LLM-written per-app suggestions — Phase 4's brief upgrades `best_fit_repo` lines to
  tailored narrative without redesign.
- Grade history/trend over time (would need persisting computed grades; revisit with
  Phase 5 interactive layer).
- Any change to sweep, shipper, snapshot schema, or ingest.
