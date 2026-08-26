# Handoff — coach-web (all phases complete, 2026-08-12)

For the next agent picking this up. Read this, the spec, and the project skill before
writing any code.

## Where things stand

**All five phases are shipped, merged to main, and live.** Phases 1–3 (ingest,
dashboard, usage/cost), the true-cost lanes with per-service infra breakdown
(schema v4), Phase 4 (LLM brief + changelog watcher), and Phase 5 (interactive
layer + write-path hardening). There is no remaining planned work — see
"What's actually left" at the end of this document.

> **This repo now has a remote** (`github.com/tkeefe66/app-builder-coach`, private,
> added 2026-08-12) — but **deploys still do not go through it**. `railway up`
> uploads the working directory, so `deployment list`'s `meta.commitHash` remains
> null and `origin/main` tells you nothing about what is running. To check what is
> actually deployed, grep the served JS bundle for an API field name (see the global
> `railway-cli` skill) — component names minify away and server-supplied strings
> never reach the bundle.

- Dashboard: https://coach-web-production-1f04.up.railway.app (password login;
  hash in Railway vars). **All six pages are live with real data** — Overview,
  Capabilities, Activity, Cost, Adoption, and Goals & Coach. (Cost and Goals &
  Coach were empty states through Phase 3; both are now populated.)
- Local pipeline: launchd runs `src/sweep.py` daily at 7:30 (collect → classify →
  profile → **ship snapshot to the cloud**). Ships automatically; failed ships
  queue in `data/outbox/` and self-heal; terminally rejected payloads quarantine
  as `*.rejected`.
- Everything is verified: 139 pytest + 8 vitest green at merge; the Docker image
  was exercised end to end (migrations, auth, SPA, traversal attempts) by the
  Phase 2 final review; the live site was browser-walked page by page.

## Canonical documents

| What | Where |
|---|---|
| Design spec (all 5 phases, approved by Tom) | `docs/superpowers/specs/2026-08-02-coach-web-dashboard-design.md` |
| Phase 1 plan (executed) | `docs/superpowers/plans/2026-08-02-coach-web-phase1-ingest.md` |
| Phase 2 plan (executed) | `docs/superpowers/plans/2026-08-02-coach-web-phase2-dashboard.md` |
| Deploy topology + procedures (KEEP CURRENT) | `.claude/skills/deploy-coach-web/SKILL.md` |

## Architecture in one paragraph

Local Mac collects (repos + `~/.claude` only exist there), cloud renders. The
sweep builds a **derived-aggregates-only** snapshot (`src/shipper.py`, contract in
`shared/snapshot.py`, `SCHEMA_VERSION = 1`) and POSTs it bearer-authed to
`/api/ingest` on the FastAPI app (`apps/coach_web/`), which upserts idempotently
(by content hash) into Postgres. Read endpoints aggregate server-side
(`api.py` + `aggregate.py` + `taxonomy.py` — taxonomy is read from repo-root
`taxonomy.yaml`, same repo, same Docker image, never duplicated). The React SPA
(`apps/coach_web/frontend/`, Vite + TS strict, Recharts, dataviz token palette in
`src/tokens.css`) is built in the Dockerfile's node stage and served by FastAPI
with an SPA fallback. Raw git history and prompt content never leave the Mac.

## What remains (spec §Build phases)

**Phase 3 — sessions + cost collector lanes: ✅ SHIPPED 2026-08-11.**
`src/usage.py` parses `~/.claude` transcripts (per-file mtime/size cursors,
whole-file re-parse on change, per-file aggregates cached in
`data/usage_by_file.jsonl`). Snapshots are **schema v2** and carry `cost_daily`
plus `sessions`/`prompts` on activity rows; **v1 payloads are still accepted and
must stay that way forever** — the outbox can hold pre-v2 payloads and a 400
quarantines them permanently. Server has `cost_daily` + `/api/cost`; Cost page
and both formerly-dimmed tiles are live.

Phase 3 notes for whoever touches it next:
- Pricing lives in `src/usage.py::PRICES` (prefix match, opus-priced fallback),
  in $/MTok. It is an **estimate of API-equivalent value**, not a bill — Tom is
  on a subscription. All 5 model ids in the real store map to a real tier;
  `<synthetic>` hits the fallback but carries zero tokens. `claude-opus-5[1m]`
  premium long-context pricing is NOT modelled — if 1M-context sessions become
  common, add a tier.
- Sessions/prompts count main-chain user prompts only; token usage counts every
  assistant row including sidechains (subagents cost money).
- `sweep.main()` imports the lane as `usage_lane` — it already binds a local
  `usage` for history commands.
- Tiles return `null` (not 0) until the lane has ever shipped, so the UI can say
  "no data yet" instead of showing a real-looking zero.
- `units_this_week` now excludes `kind == "commits"` units (commit clusters are
  month-resolution and were inflating the week tile).

**True cost — SHIPPED 2026-08-11 (schema v3), per-service breakdown 2026-08-12 (schema v4).**
Separate from the phase numbering. Two lanes answering "what does each app actually cost
to run":
- **Infra:** the sweep shells out to `railway usage projects --json` (`src/railway_cost.py`),
  maps projects to apps via repo-root `apps.yaml`, and ships **cumulative** period-to-date
  dollars. The server derives daily deltas (`apps/coach_web/truecost.py`) because the CLI
  only reports period-to-date. A *decreasing* cumulative clamps to 0.0 — never re-book the
  cumulative as one day's spend, that double-counts a whole billing period.
- **Infra, per service (v4):** memory is ~97% of the entire Railway bill, so "reduce memory"
  was unactionable while "downsize this Postgres" is. The sweep therefore makes **10 Railway
  CLI calls, not 1**: one `railway usage projects --json` for the app-level lane, plus one
  `railway usage projects --project <id> --json` per registry entry
  (`railway_cost.collect_service_rows`). Those rows ship as `infra_usage_services`, land in
  `infra_service_usage`, and surface as a `services` array on each `/api/truecost` app entry
  and a disclosure row on the Cost page's "By app" table.
  - **`infra_services=` in the sweep summary line:** `ok` = every project returned a usable
    payload; `partial(k/n)` = k of n did — the k that succeeded still ship, by design, since
    with 10 calls partial failure is the expected case; `failed` = none did.
  - `service_rows` returns **`None` for an unusable payload and `[]` for a valid project with
    zero services**, and `collect_service_rows` counts with `if got is not None`. Collapsing
    those two would make a legitimately empty Railway project report a permanent false
    `partial(k/n)`. Pinned by `test_collect_service_rows_counts_empty_project_as_ok`.
  - Railway reports unused components as **`-0.0`**; `railway_cost._dollars` normalizes to
    `0.0` before rounding to 6 decimals.
  - `/api/truecost`'s app list is built from the cost lanes (`railway_by_app | llm_by_app`);
    `services` is a drill-down on a row that already exists, never a reason to add one. An
    app with service rows but no app-level infra row is absent rather than listed at $0.00
    beside a non-zero services array.
  - **v1, v2, and v3 payloads must never be rejected** — the outbox can hold them and a 400
    quarantines them permanently as `.rejected`. `shared/snapshot.py` dispatches on
    `schema_version`; each version keeps exactly its old rules, and a payload below v4
    carrying `infra_usage_services` is rejected.
- **LLM:** deployed apps POST their Anthropic `usage` block to `/api/usage` (bearer
  `COACH_USAGE_TOKEN`, separate from the ingest token). Priced server-side into `llm_daily`.
  Reporters live in `reporters/` — copy into each app; they never raise and never block.
- **Which services actually call Anthropic** (built 2026-08-11 by checking service envs for
  `ANTHROPIC_API_KEY`, corrected 2026-08-12 — do NOT infer this from Console key names,
  they mislead):

  | Project | Service | slug | |
  |---|---|---|---|
  | B2B AI News | `B2B AI News` | `b2b-ai-news` | DONE |
  | public-dynasty | `API` (not `Web`) | `public-dynasty` | DONE |
  | Purchase-Inventory | `Web`, `BOT`, `CRON`, `Camping-Cron` | `purchase-inventory` | DONE |
  | Life-Tracker | `web` | `life-tracker` | DONE |
  | gtm-job-search | `web` | `gtm-job-search` | DONE |

  **The rollout is complete — every instrumented app reports.** Parental-Stories was the one
  remaining app; **it is being retired (Tom, 2026-08-13)** and was removed from `apps.yaml`
  rather than instrumented. Its reporter code was written and reviewed but never deployed,
  and `docs/reporter-rollout-prompt.md` is deleted along with it.
  ⚠️ Historical `parental-stories` rows remain in `llm_daily` and `infra_usage`. They still
  render on the Cost page, but as the raw slug rather than "Parental Stories", because
  `display_map` falls back to the name when an app leaves the registry. Expect the
  "Untracked spend" figure to *drop* once the Railway project is deleted — that is the app's
  uninstrumented spend leaving the denominator, not an improvement in tracking.

  DONE = a row for that slug is in `llm_daily` (confirmed 2026-08-12). **The service column
  has been wrong once:** Purchase-Inventory was listed as `Web` and `BOT` and is actually
  four services — `CRON` runs the ingest classifier, `Camping-Cron` the amenity parser
  (corrected 2026-08-12). Re-verify per service with
  `railway variables list -p <id> -e production -s "<svc>" --kv | grep ANTHROPIC`
  rather than trusting this table; a missed service is silently unreported spend.

  `tomkeefe-ai` and `family-tree` hold no Anthropic key — nothing to instrument.
  `coach-web` holds `COACH_USAGE_TOKEN` because it is the *receiver*, not a reporter;
  the coach's own LLM calls happen locally in `src/classifier.py` — **DONE 2026-08-12**,
  reporting as `app-builder-coach`. Because the canonical reporter already lives in this
  repo, `classifier.py` **imports** `reporters.usage` instead of copying it — copying
  would fork the source of truth against itself. Its two variables live in the local
  `.env` (the sweep runs under launchd, not on Railway), and the Dockerfile does not copy
  `src/`, so the deployed server never loads this path.
  Life-Tracker calls Anthropic but has no eponymous Console key — likely the consumer
  of the orphan "Weekly Updates" key. Unconfirmed.
- **A repo may hold several independent Anthropic clients.** b2b-ai-news-source had
  four call sites across three files; only two routed through its `ai-models.ts`
  wrapper. Grep for `messages.create`, `messages.stream`, AND `new Anthropic(` before
  assuming a single chokepoint. This is the known reporters-vs-gateway weakness.
  The converse also holds: `purchase-inventory` matched 23 files but took **one** edit,
  because all 15 runtime calls funnel through `lib/anthropic-retry.ts`. Look for a
  chokepoint before editing N sites; duck-type on `model` + `usage` if the wrapper is
  generic, so non-Anthropic callers passing through it never report.
- **`tsc` with `allowJs: false` drops the copied `usage.js` from `dist/`** — the service
  then crash-loops on import at boot, after a green typecheck and a green build. The build
  script needs an explicit `cp lib/usage.js dist/lib/usage.js`. Bundlers (Next.js) are
  fine; anything deploying raw `tsc` output is not.
- **Streaming calls report after the stream drains** (usage lives on the final
  message), so a client that disconnects mid-response leaves that call unreported —
  real spend that surfaces only as drift.
- **TypeScript repos need a `usage.d.ts`** beside the copied `usage.js` when `allowJs`
  is off. Do not port the reporter to TypeScript — that forks the one source of truth.
- `GET /api/truecost?days=30` joins both over a **trailing 30 days**. Railway bills from the
  27th and Anthropic on calendar months, so those two single-source figures are shown with
  their own window labels and are **never added together**.
- Anthropic's Admin API (real cost endpoint) is unavailable: the account is an *individual*
  org, and `/settings/admin-keys` 404s. Converting to a team org would unlock it.
- Drift check replaces a credit burn-down: `COACH_CONSOLE_FROM/_TO/_SPEND` hold whatever
  Console last said; the UI shows the gap. Tracked spend under-counts by construction
  (un-instrumented apps, Workbench usage, dropped POSTs), so the gap IS the blind spot,
  measured rather than assumed. Re-anchor monthly.
- ⚠️ `COACH_USAGE_TOKEN` is in the prod fail-fast list — deploying without it set
  crash-loops the whole dashboard. Set the variable before `railway up`, never after.
- ⚠️ Any repo-root YAML the server reads at boot must be in the Dockerfile `COPY` line.
  `apps.yaml` was missed once and crash-looped the server;
  `tests/web/test_dockerfile_data_files.py` now guards all three.

**Phase 4 — LLM brief + changelog watcher: ✅ SHIPPED 2026-08-12.** Verified live:
a real `ok` brief row with readable prose, watermark recorded, brief spend in
`llm_daily`. (This section previously claimed a briefs table already existed — it
did not; `briefs` and `watcher_state` were both created by this phase.)

- **Trigger:** `POST /api/ingest` responds, then runs ONE FastAPI `BackgroundTask`
  (`ingest.post_ingest`) with its own DB session — the request's is already closed.
  The changelog watcher runs only if `watcher_state['changelog.last_checked_at']` is
  ≥7 days old. ⚠️ **The brief no longer runs on every ingest** — see the assessment
  loop section below; `post_ingest` now calls `brief.decide_and_generate`.
- **Model:** `claude-haiku-4-5` for **deltas**, override with `COACH_BRIEF_MODEL`.
  Haiku 4.5 **rejects `effort`**, uses the older `budget_tokens` thinking form, and
  has a **4096-token cache minimum** the brief never reaches — so the delta call
  deliberately sends **no `effort`, no `thinking`, no `cache_control`**.
  ⚠️ **The pinning test was renamed and deliberately narrowed** (2026-08-12) to
  `test_delta_sends_no_effort_thinking_or_cache_control`. It used to assert
  `"output_config" not in sent`, which over-stated the constraint: `effort` lives
  *inside* `output_config`, but `output_config.format` (structured outputs) **is**
  supported on Haiku 4.5 and the brief now requires it to return JSON. Banning the
  whole key would block the feature. The test now pins the three genuine
  prohibitions precisely and permits `format`. A reviewer mutation-verified that
  adding `effort`, `thinking`, or `cache_control` to the delta path each turns it
  red — it is **stronger** than what it replaced. Do not widen it back.
- **`ANTHROPIC_API_KEY` is now set on coach-web** (copied from the local `.env`, so
  the sweep's classifier and the server's briefs share one key — rotating it breaks
  both). It is deliberately **NOT** in `REQUIRED_PROD_SECRETS`: a missing key shows
  as a `failed` brief on Overview rather than crash-looping the boot, which is the
  `apps.yaml` trap this repo already hit once.
- **THE BOOTSTRAP TRAP (do not undo).** The changelog holds 361 versions and **487
  `Added` bullets**, and Phase 5 owns dismissals — so a first run that inserted them
  all would put 487 undismissable rows on the Adoption board permanently. The first
  run therefore records a watermark and inserts **zero**. Pinned by
  `test_first_run_records_watermark_and_inserts_nothing`, which was verified to FAIL
  against a build with the guard removed. Production first run: 0 rows, as intended.
- Only bullets starting `Added ` become rows (Fixed/Improved/Changed/Removed are
  refinements). Versions compare as **tuples of ints** — `2.1.99` sorts above
  `2.1.228` as a string, which would freeze the watermark forever. The watermark is
  the newest version *carrying an Added bullet* (currently `2.1.225`; 2.1.226–228
  have none), which is conservative: it can never skip an unexamined version.
- **The test suite must never hit the network.** `TestClient` runs `BackgroundTasks`
  synchronously after the response, so wiring this made every `/api/ingest` test
  fetch the real changelog over HTTP and the suite hung past 120s. The autouse
  `background_calls` fixture in `tests/web/conftest.py` neutralizes `post_ingest` and
  **records** its calls, so route tests still prove the task was scheduled instead of
  passing vacuously. Do not replace it with a bare no-op.
- ⚠️ `GET /api/briefs` no longer returns `{latest, archive}` — see below.
- `usage_api.upsert_llm_daily(db, date, app, model, usage)` is shared by `/api/usage`
  and the brief so one call can never be priced two ways. It does **not** commit.

**Assessment loop — ✅ SHIPPED 2026-08-12.** Replaces Phase 4's per-ingest brief.
Spec: `docs/superpowers/specs/2026-08-11-coach-assessment-loop-design.md`.

The page showed ten near-identical 200-word briefs. The cause was not rendering: the
model was handed six numbers and three lists of bare tag names, once per ingest, so it
wrote the same essay every time. Capping the cadence would have hidden that, not fixed it.

- **Two kinds of brief.** `briefs.kind` is `"assessment"` (a deep pass over the whole
  corpus, `claude-sonnet-5`, `COACH_ASSESSMENT_MODEL`) or `"delta"` (a short amendment,
  Haiku 4.5). Both return validated JSON via `output_config.format`; recommendations
  land in the new `brief_recommendations` table.
- **The change gate is the cost control.** `brief.fingerprint(db, today)` hashes five
  material facts (tags ever used, adopted features, goal ids+statuses, unit count,
  changelog watermark). **Spend is deliberately not one of them.** It was until
  2026-08-12, rounded to whole dollars on the theory that cents were the noise floor;
  at $3,000/day it moved the hash on every ingest and defeated the gate entirely. Read
  the full account under "What's actually left" before considering adding it back.
  Decision order in `decide_and_generate` is load-bearing:
  force/no-assessment → **unchanged-fingerprint skip** → 5-delta reassessment → delta.
  The skip must outrank the counter or a quiet stretch triggers a pointless reassessment.
- ⚠️ **A failed generation must never advance the fingerprint.** If it did, the change
  it was meant to report would be marked seen and skipped forever. Both branches guard
  on `row.status == "ok"`, and **each guard has its own test** — they were mutation-
  verified separately, because removing only the `assess()` guard left the whole suite
  green.
- ⚠️ **`briefs.day` is indexed but deliberately NOT unique.** A second delta in one day
  is legitimate when the fingerprint moves twice. The gate replaces the uniqueness rule
  an earlier draft had, and that draft's delete-all-but-newest migration is gone with it.
  (That warning used to end "this repo has no database backups" — it does now, nightly to
  R2, but a destructive migration is still only recoverable back to the last nightly run.)
- **`kind` is derived, never trusted.** `_store` sets `kind=allowed[target]` from the
  vocabulary the target came from (`never_built`/`stale` → tag, `adoption_gaps` →
  feature) and ignores what the model claimed. That is what makes `_store`'s
  supersede-on-target and `writes._mark_recommendations`'s mark-on-`(kind, target)`
  equivalent. Break the derivation and mislabelled rows strand as `open` forever.
- **Outcome loop.** Creating a goal marks every open recommendation for that target
  `converted`; dismissing marks them `dismissed`; re-issuing supersedes. That history is
  rendered into **both** prompts via the shared `_render_history` helper, with a rule
  that something suggested 3+ times and never acted on must be argued differently or
  dropped. It was wired into deltas only at first — the assessment path is the *more*
  frequent route to a fresh recommendation list, so check both if you touch it.
- **Failure handling:** a failed assessment keeps the last good one flagged `stale` with
  the error; a failed delta stores a `failed` row (a run of them is still the only
  visible signal of a bad key); **unparseable JSON is NOT a failure** — it degrades to
  prose with zero recommendations and stays `ok`; an out-of-vocabulary target is dropped
  and its siblings kept; `stop_reason == "max_tokens"` raises so a truncated assessment
  shows as `failed` instead of a silent zero-recommendation brief.
- `GET /api/briefs` returns `{assessment, deltas, history, recurring}`. Legacy prose
  briefs still render — `_brief_json` falls back to `created_at[:10]` when `day` is
  empty, and that fallback is pinned.
- **`POST /api/reassess`** forces an assessment. ⚠️ **Known and accepted:** it runs the
  Sonnet call **synchronously**, holding a DB session for up to the SDK's 600s
  non-streaming timeout. `/api/ingest` avoids this with `BackgroundTasks`; this endpoint
  does not, because returning 202 needs frontend polling that was judged not worth it
  for a single user pressing a button. If it starts timing out at the edge, that is the
  fix.
- `gaps.py` is the one source of truth for never-built / stale / never-adopted, shared by
  `/api/overview` and the coach. `exclude_dismissed` is the single deliberate difference:
  Overview keeps showing dismissed items so a dismissal never becomes invisible; the coach
  must stop re-suggesting them.
- **Goals are no longer free text.** The blank field is gone; `GoalPicker` sources every
  option from the system's own gap lists. A goal that is neither a taxonomy tag nor a
  Claude Code feature can no longer be created — deliberate, and consistent with
  `writes.GoalIn`, which always required `kind ∈ {tag, feature}` plus a target.
- `create_goal` is now idempotent on an active `(kind, target)`, matching
  `create_dismissal` and `create_checkoff`.

**Phase 5 — interactive layer: ✅ SHIPPED 2026-08-12. ALL PHASES NOW COMPLETE.**
Four app-owned tables (`goals`, `notes`, `dismissals`, `feature_checkoffs`), CRUD in
`apps/coach_web/writes.py`, plus the write-path hardening that was deferred to this phase.

- **`require_same_origin` (auth.py) has two carve-outs, and BOTH are load-bearing:**
  1. **Safe methods are skipped.** Browsers do not send `Origin` on same-origin `GET`;
     enforcing it there 403s every read endpoint and blanks the dashboard.
  2. **It is a router-level dependency on `writes.py` and `/api/logout` only — NEVER
     global middleware.** `/api/ingest` and `/api/usage` are bearer-token machine
     clients that send no `Origin` at all; covering them breaks the daily sweep
     silently (payloads just queue). `POST /api/login` is exempt too — no session
     cookie to ride. If you ever move this to `add_middleware`, the sweep dies.
- **Security headers** (main.py middleware): `X-Frame-Options: DENY`,
  `CSP: frame-ancestors 'none'`, `nosniff`, `Referrer-Policy: same-origin`, HSTS.
  Verified 5/5 live. **The CSP is deliberately minimal** — the SPA uses React inline
  `style={{}}` everywhere, so a `style-src` directive breaks every page. A full CSP
  needs the inline styles moved to classes first.
- **`POST /api/logout`** clears the cookie. **Accepted limitation:** the session is a
  stateless signed cookie, so a copied cookie stays valid until its 30-day expiry.
  Server-side revocation was offered and declined as disproportionate here.
- **Integrations — the reason these tables exist.** Dismissals filter `never_built`,
  `stale` AND `adoption_gaps` out of `brief.build_context`, so the coach stops
  re-suggesting waved-off items; Overview deliberately still shows them so a
  dismissal never becomes invisible. Check-offs override the Adoption board
  (`status: "checked-off"`) while `detected_status` preserves the sweep's opinion.
  `/api/overview` carries `active_goals`.
- **Measured security baseline 2026-08-12** (against the live app, not source):
  FastAPI auto-docs already correctly disabled (`/docs` is the SPA fallback);
  login throttle holds at 40-way concurrency (5 through, 35 blocked — a
  `threading.Lock` was added anyway to remove the residual race); **no public
  Postgres proxy** (`DATABASE_PUBLIC_URL` is an unresolved template, no
  `RAILWAY_TCP_PROXY_*`).
- **✅ This section's old "no database backups" gap was closed 2026-08-13** by the
  `coach-backup` nightly cron service (encrypted `pg_dump` → Cloudflare R2, restore
  verified). A bad migration is no longer permanent — it costs you back to the last
  nightly run.

Original Phase 5 scope, for reference: goals / feature_checkoffs / notes / dismissals
tables (spec §Data model, app-owned family) + CRUD + UI. First write endpoints:
revisit CSRF posture (cookie is SameSite=Lax) and add the logout route.

## Process that worked (repeat it)

**Mutation-test load-bearing guards; a green suite is not evidence.** The
assessment loop's reviews kept surfacing the same shape of defect: correct code
with an unpinned invariant. Five separate times a reviewer deleted a guard and
the entire suite stayed green — the `assess()` branch's fingerprint guard, the
`!=` in the adopted query, the legacy `day` fallback, the `/api/briefs` refetch
after a write, and the delta path's `effort` prohibition. Every one of those had
a test that *looked* like it covered the behaviour. When you change something
this document calls load-bearing, break it on purpose first and confirm the
suite goes red; if it stays green, the test is decorative and the next person
will delete the guard in good faith.

Two corollaries worth the same care:
- **An inequality assertion is symmetric and cannot pin an operator.** Asserting
  "the hash changed after a status flip" stays green when `!=` becomes `==`,
  because flipping just swaps which set is populated. Pin the asymmetric case
  instead — a never-touched feature must move the hash *not at all*.
- **Test fixtures must span the production magnitude.** The spend-in-fingerprint
  bug survived a full suite because the tests used $2.10 and $9.90 against a
  reality of $3,000/day.

**The "deferred minors" list in this document is a decision, not a backlog.** A
whole-branch review triaged each one and ruled *defer*, with reasons. Working
through them as if they were tickets converts a deliberate "don't" into work.
Pick one up only if you are already in that file for another reason.

Phase 3 was executed inline (no subagents) straight from the plan, which carried
complete code; that worked fine at this size. Two plan bugs surfaced during
execution and are worth expecting again: a plan-supplied import that collided
with an existing local name, and a plan test whose expected error string didn't
match the implementation's error (missing-key errors now list ALL missing keys).
Also, tasks 3 and 4 must land in one commit — bumping `SCHEMA_VERSION` without
also emitting `cost_daily` leaves the suite red in between.

Per phase: `superpowers:writing-plans` from the spec → subagent-driven-development
(fresh implementer per task on cheap models — the plan carries complete code, so
haiku transcribes; sonnet for multi-file/integration tasks; sonnet reviewers;
**opus final whole-branch review before merge — it caught deploy-blocking bugs
both phases**) → finishing-a-development-branch → merge to main → deploy from
main via the deploy-coach-web skill → verify live (curl + browser walk).

Worktree gotchas: create via EnterWorktree; build the venv with **python3.11
explicitly** (`python3 -m venv` grabs system 3.9 and `str | None` syntax
explodes); `rm -rf` is permission-blocked — use `python3.11 -m venv --clear`.
Frontend needs its own `npm --prefix apps/coach_web/frontend install` per
worktree.

## Deferred findings (from the Phase 2 final review — the SDD ledgers died with
their worktrees, this list is the survivor)

Worth fixing opportunistically; none blocking:
- 614 kB single JS chunk (code-split when the app grows), stock Vite README left
  in `frontend/`, missing empty states on Capabilities/Overview lists, login page
  renders inside the app shell (logged-out users see nav links).
- `HEAD` requests 405 on SPA routes (uptime monitors); bare `/api` serves
  index.html; no security headers (CSP/HSTS/X-Frame-Options) — add with Phase 5
  writes; login rate limiter is global (attacker can lock Tom out; mitigated by
  the 30-day cookie) and in-process (breaks if replicas > 1).
- UTC-vs-local "this week" skew (~6h early on Sunday evenings);
  `aggregate.streak()` has a dead `today` param; stale cutoff off-by-one at
  exactly 180 days; `adoption_board` ships unbounded history the UI doesn't
  render yet (add LIMIT in Phase 4); `cssVar()` reads colors at render time so an
  OS theme flip mid-session leaves chart marks stale until re-render;
  `units_this_week` relies on the classifier's exclude-current-month invariant —
  add a regression test when touching either side.

## Secrets / env (locations only — values live in the places named)

- Local `.env` (gitignored, chmod 600): `ANTHROPIC_API_KEY`, `COACH_INGEST_URL`,
  `COACH_INGEST_TOKEN`. Loaded by `config.load_env()` at sweep start. Add
  `COACH_USAGE_URL` + `COACH_USAGE_TOKEN` when the coach's own classifier starts
  reporting via `reporters/usage.py`.
- Railway service coach-web: `DATABASE_URL` (reference `${{Postgres.DATABASE_URL}}`),
  `COACH_INGEST_TOKEN`, `COACH_SECRET_KEY`, `COACH_PASSWORD_HASH`,
  **`COACH_USAGE_TOKEN`**, **`ANTHROPIC_API_KEY`** (added Phase 4; the *same* key as
  the local `.env` one, so rotating it breaks the sweep's classifier and the server's
  briefs together). The app fail-fasts at startup if any of the first five is missing;
  `ANTHROPIC_API_KEY` deliberately is **not** in that check.
  **`COACH_GITHUB_TOKEN`** (set 2026-08-12) — fine-grained GitHub PAT, `contents: read`,
  granted to the eight scanned repos, **expires 2027-06-01**. Read by the planned
  code-reading lane; nothing consumes it yet. Deliberately **not** in
  `REQUIRED_PROD_SECRETS`, same reasoning as `ANTHROPIC_API_KEY`. ⚠️ On expiry every scan
  returns 401 and the corpus silently stops refreshing, so the dashboard must warn ahead
  of the date rather than waiting for failures. A predecessor token was exposed and
  revoked the day this was set — never pass the value on a shell command line; set it in
  the Railway dashboard.
  Optional: `COACH_CONSOLE_FROM`, `COACH_CONSOLE_TO`, `COACH_CONSOLE_SPEND` (drift check;
  all three or none — a partial set silently no-ops); `COACH_ASSESSMENT_MODEL`
  (defaults `claude-sonnet-5`; the assessment path only, ~25–40k input tokens a
  couple of times a month — deliberately NOT in `REQUIRED_PROD_SECRETS`, a missing
  value just falls back to the default); `COACH_BRIEF_MODEL`
  (defaults `claude-haiku-4-5`); `COACH_ALLOWED_ORIGINS` (unset falls back to the
  request `Host`, correct for both current domains).
- Each reporting app's Railway service: `COACH_USAGE_URL` (the coach-web `/api/usage` URL)
  and `COACH_USAGE_TOKEN` (same value as on coach-web).
- Dashboard password: Tom knows it; to rotate, see the skill's
  "Change the login password" section.

## GitHub repo mapping (added 2026-08-12, for the planned code-reading lane)

Every app in `apps.yaml` can optionally carry a `github:` key pointing at its repo. The
slug↔repo mapping is **not derivable** and must be declared explicitly. Not every app is
in scope for code scanning, and at least one repo carries non-standard history (a
squashed snapshot rather than full history) — check each one before assuming `git log`
there tells the whole story.

- **Exclusion from code scanning is expressed by omitting the `github:` key**, not by adding
  a flag — the scan lane skips any app without one, and the brief's Code section derives its
  "not scanned" list the same way, so there is no hardcoded name and no second allowlist
  entry to maintain.
- ⚠️ **Adding a `github:` key to `apps.yaml` requires editing `shared/apps.py` in the same
  commit.** `ALLOWED` is a strict allowlist and an unknown key raises — which crash-loops
  the server at boot (`main.py:68` calls `load_apps` inside `create_app`) *and* kills the
  local sweep (`src/sweep.py:67,75`). Distinct from the Dockerfile `COPY` trap above; same
  blast radius.

> The full slug↔repo↔visibility table and per-repo notes were removed from this public
> copy (2026-08-26) — they named other private repositories and their visibility. See
> local history for the complete version if you need to reconstruct it.

## What's actually left

> **✅ DEPLOYED AND VERIFIED LIVE (2026-08-13).** Plan and spec:
> `docs/superpowers/plans/2026-08-12-remaining-work.md` and
> `docs/superpowers/specs/2026-08-12-remaining-work-design.md`.
>
> **What shipped:**
> - A real CSP (nine directives, replacing `frame-ancestors` alone).
> - `age_days` + `stale` on `/api/truecost`'s `drift` object (stale when `> 35` days old, or
>   when the anchor is future-dated — a typo that used to hide itself forever).
> - The Cost page's "Untracked spend" tile flags itself when stale.
>
> **The required browser walk was performed and passed.** All six pages were loaded as full
> navigations — not just client-side routing — so initial load was exercised under the new CSP,
> with console capture active: **zero console messages of any kind, zero CSP violations.**
> Pages rendered with real data, which is what makes the clean console mean "clean" rather than
> "nothing loaded". Header checks: nine-directive CSP live, `/api/health` ok, 5/5 security
> headers, and `make sweep` returned `shipped=1 queued=0`.
> The drift tile renders `vs Console 2026-08-01→2026-08-12` with no warning treatment —
> correct, since that anchor was one day old against a 35-day threshold.
>
> ⚠️ **The walk did NOT exercise the interactive write paths** (create a goal, dismiss an item,
> Reassess) — each mutates real coaching state and Reassess costs a Sonnet call, so they were
> skipped deliberately rather than forgotten. Residual CSP risk is low: `connect-src 'self'` is
> already exercised by every page fetching `/api/*`, and `form-action` never applies because
> the SPA posts with `fetch`, not form submissions. Exercise one write next time you are in the
> UI anyway.
>
> ⚠️ **Do not "simplify" `--warn-text` back to `--status-warn`.** The first implementation used
> `--status-warn` as a text colour at ~1.8:1 contrast on the light surface — *less* readable
> than the normal text it replaced, inverting the feature. Dark mode was fine, so a
> dark-mode-only check would have missed it. `--warn-text` is 5.77:1 light / 9.5:1 dark.
> `--status-*` tokens are indicator colours, not text colours; `--good-text` exists for the
> same reason.
>
> **✅ BACKUPS ARE LIVE AND VERIFIED (2026-08-13).** `coach-backup` is a Railway service in
> this project running `Dockerfile.backup` → `python -m src.backup_nightly`: `pg_dump
> --format=custom` → AES-256-GCM → Cloudflare R2 bucket `coach-web-backups`, key
> `backups/nightly/<YYYY-MM-DD>.dump.enc`, 30-day prune. Ported from `family-tree`.
>
> **Railway's own volume backups and PITR are Pro-plan only and are NOT available here.**
> Do not plan around them. The R2 logical dump is the only layer — and it is the only one
> that would survive losing the Railway project anyway.
>
> **Measured 2026-08-13, end to end, not inferred:** dump 84,446 bytes plain / 84,474
> encrypted (exactly +28 = 12-byte IV + 16-byte GCM tag). Downloaded from R2, decrypted with
> `BACKUP_ENCRYPTION_KEY`, confirmed `PGDMP` magic, and **fully restored into a real
> Postgres 18 with `pg_restore --exit-on-error`, which exited 0 in ~0.1s**. Restored
> `feature_units` = 120, matching production's live `unit_count` of 120; `briefs` 27,
> `brief_recommendations` 26, `goals` 1, `adoption_history` 2130. `feature_checkoffs`,
> `dismissals` and `notes` restored empty because they are genuinely empty.
> **Recovery time ≈ seconds. Recovery point = the last nightly run.**
>
> ⚠️ **`BACKUP_ENCRYPTION_KEY` is the single point of failure.** Lose it and every object in
> R2 is permanently unreadable. It must exist somewhere other than Railway.
>
> **Still outstanding on backups:**
> - ~~The cron schedule is NOT set~~ — **confirmed live 2026-08-26** via `railway status`:
>   `coach-backup` runs `0 8 * * *` (08:00 UTC), last run `Completed`.
> - The in-repo `src/restore_drill.py` has never been executed against production. The
>   verification above was done by hand, locally, because the service's start command could
>   not be overridden for a one-off run (see the deploy skill). Running the drill on Railway
>   needs either a dashboard start-command override or a mode flag on the entrypoint.
> - A scratch database `restore_drill` exists on the Railway Postgres server, created for
>   that purpose and never used. Drop it or keep it for the drill.
> - ~~Parental-Stories reporter~~ — **dropped 2026-08-13: the application is being retired.**
>   Removed from `apps.yaml`; `docs/reporter-rollout-prompt.md` deleted. The reporter code was
>   written and reviewed but never deployed, and dies with the repo.
>
> **Two lessons from building this, both worth keeping:**
> - *A mutation-check is only as good as the assertion it targets.* The plan specified a test
>   asserting on copy text, and a mutation flipping a `warn` prop — but the copy did not depend
>   on that prop, so the check would have passed against broken code. Verify the assertion
>   actually observes the thing being mutated.
> - *A fresh git worktree has no virtualenv* (`.venv` is git-ignored). Instructions that say
>   "use `.venv/bin/python`" are false there; an agent built a Python 3.9 venv, which cannot
>   import this codebase, and the resulting 8 collection errors look exactly like a broken
>   branch. Use `"<main checkout>/.venv/bin/python"` from a worktree.

No planned phases remain. In rough order of value:

0. **✅ Assessment loop DEPLOYED and verified 2026-08-12 06:56Z.** Migration
   `334147163440 → 9c1a4f2b7e30` applied cleanly; `day` backfilled with zero empty
   rows; all 17 legacy briefs labelled `kind=delta`. The first post-deploy ingest
   produced exactly one `assessment` (id=18, sonnet, 14.9k in / 4.1k out, $0.1057) as
   designed, and `brief_recommendations` shows working outcome propagation — two rows
   already `superseded`.
   - **The suppression path could not fire, and the cause was a design bug — fixed
     2026-08-12.** Three back-to-back sweeps each produced a brief. The third added
     no feature units at all (`units` stayed 120), so it should have been suppressed.
     It was not, because `fingerprint()` included **trailing-7-day spend rounded to
     whole dollars**, and that rounding was calibrated for the wrong order of
     magnitude: `cost_daily` is Claude Code session spend, which runs at
     **$300–$3,300 a day** and moves by hundreds between sweeps. The hash therefore
     changed on every single ingest and a delta fired every time — precisely what the
     gate exists to prevent.
     **Spend is no longer a fingerprint component.** The remaining five (tags used,
     adopted features, goals, unit count, changelog watermark) capture material
     change; spend is an *outcome* of building that `units` and `tags` already detect,
     so it was double-counting a signal we had. Pinned by
     `test_spend_never_moves_the_fingerprint_at_any_magnitude`, which asserts across
     cents, a dollar, and a $3,277 day — reintroduce spend with any rounding and it
     goes red. Do not add it back without a bucket coarse enough to survive a
     $3,000 day.
     Note the expensive path was correctly gated throughout: exactly one Sonnet
     assessment ran ($0.1058) across all three sweeps. The runaway was confined to
     ~2¢ Haiku deltas.
   - `SPEND_WINDOW_DAYS` was renamed `RECENT_WINDOW_DAYS` in the same change — it
     never windowed spend, only `describe_change`'s feature units and check-offs.
   - One artifact: brief id=17 (06:57:20) was served by the *old* container during
     rollout overlap, so it is `kind=delta` with an empty fingerprint. Harmless.
1. **✅ Database backups — DONE 2026-08-13.** Was the one real gap. Six app-owned tables
   (`goals`, `notes`, `dismissals`, `feature_checkoffs`, `brief_recommendations`, `briefs`)
   hold data that exists nowhere else; everything else is rebuildable by re-running the
   sweep. Now covered by the `coach-backup` cron service — see the banner at the top of this
   section for the measured restore numbers, and the `deploy-coach-web` skill for how to
   recover.
2. **✅ Drift staleness — DONE 2026-08-13.** The tile now reports its own age and flags
   itself past 35 days, so it can no longer drift out of meaning unnoticed. **Re-anchoring
   `COACH_CONSOLE_*` from the Console figure is still a manual monthly chore** — the app
   tells you when, it cannot do it for you.
3. **✅ A fuller CSP — DONE 2026-08-13.** Nine directives, live and browser-verified.
   ⚠️ The old claim that this was blocked on moving the SPA's inline `style={{}}` to classes
   **was wrong**: Recharts injects inline styles at runtime, so `style-src 'unsafe-inline'`
   is permanent regardless. That refactor buys nothing for CSP — do not do it for this reason.
4. The opportunistic list above (chunk size, login-page shell, empty states, `HEAD`
   on SPA routes, UTC-vs-local week skew).
5. **~~Parental-Stories reporter~~ — DROPPED 2026-08-13. The application is being retired**
   (Tom). Removed from `apps.yaml`, `docs/reporter-rollout-prompt.md` deleted. The reporter
   was written and reviewed but never deployed. **The usage-reporter rollout is therefore
   complete** — every app that remains and calls Anthropic reports its spend.
