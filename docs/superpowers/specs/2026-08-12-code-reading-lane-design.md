# Code-reading lane — design

Status: approved in conversation by Tom 2026-08-12. Extends the coach assessment loop
(`2026-08-11-coach-assessment-loop-design.md`), which must ship first. Nothing here
modifies that spec; this lane appends one section to its corpus builder and one term to
its fingerprint.

## Problem

The coach has never read a line of code.

`collector.py` parses `git log` — commit messages, changed paths, file extensions.
`detect_languages` counts suffixes. `classifier.py` tags units by substring-matching
paths and messages against `taxonomy.yaml`, then asks Haiku to label the remainder from
that same metadata. The coach knows *that* a `Dockerfile` was touched. It has never
opened one.

The assessment loop fixes the brief's *breadth* problem — it feeds the model everything
in Postgres instead of six numbers. But the richest field in that corpus is
`feature_units.summary`, an LLM paraphrase of a commit message. So the deepest evidence
an assessment can offer is an argument from absence:

> *"5 repos, 14 units, zero container work."*

That is a claim about what is missing. The coach structurally cannot say anything about
what was actually built — that `purchase-inventory` funnels fifteen Anthropic calls
through one `lib/anthropic-retry.ts` chokepoint, that `b2b-ai-news-source` had four call
sites across three files with only two behind its wrapper, that a `tsc` build with
`allowJs: false` silently drops a copied `.js` from `dist/`. Every one of those findings
came from an agent reading code. None is derivable from git metadata, and none can ever
appear in a brief as currently designed.

## Shape of the fix

A lane that reads each repo with a bounded agent and stores **structured findings**, on
the same change-gated, no-change-no-cost discipline the assessment loop already uses.

1. **Gate** — resolve each repo's HEAD via one cheap API call. Unchanged, already
   scanned: stop. No fetch, no model call, no cost.
2. **Fetch** — download the tarball at that commit, extract to a temp dir.
3. **Scan** — a bounded tool loop reads the tree and returns findings against fixed
   dimensions.
4. **Feed** — findings become a Code section in the assessment corpus, and the scan set
   joins the brief fingerprint so a fresh scan produces a delta.

### Scope

"Code reading" is three sub-projects on one substrate. **This spec covers the substrate
and the first consumer only.**

| | In this spec |
|---|---|
| Substrate — fetch, scan, store | yes |
| (1) Ground the assessment's recommendations | yes |
| (2) Code-quality depth axis on the grade | **no** — own spec; changes what the grade means |
| (3) Ask-on-demand queries from the dashboard | **no** — own spec; larger surface |

## Placement

**The lane runs in coach-web, not the local sweep.** Seven of the nine apps already had a
live GitHub repo before this work; `app-builder-coach` had no remote and `gtm-job-search`'s
fork had been deleted. Both were pushed 2026-08-12, bringing all nine within reach. The
privacy contract's load-bearing half is `~/.claude` transcript content,
which exists only on the Mac — not application source, which was already hosted.

Consequences, accepted:

- The scanner sees **GitHub HEAD**, not the working tree. Uncommitted work is invisible.
  For a coach that measures *shipped* work this is the more correct view.
- No findings-shipping path, no snapshot scrub, no new privacy guard — the code is
  already where the scanner is.

## Registry

Each scanned app gains a `github: tkeefe66/<repo>` key in `apps.yaml`. The slug↔repo
mapping is **not derivable** (`gtm-job-search` ← `chad-job-search`, `tomkeefe-ai` ←
`my-website`) and must be declared. The current mapping lives in `docs/HANDOFF.md`.

**Exclusion is expressed by omitting the key**, not by adding a flag. `parental-stories`
is deliberately not scanned; it simply has no `github:`. One fact then drives both the
lane's skip and the corpus's "not scanned" list, with no second allowlist entry and no
hardcoded app name anywhere.

> ⚠️ **`shared/apps.py` `ALLOWED` is a strict allowlist and must be edited in the same
> commit.** An unknown key raises `ValueError`, and `load_apps` is called both inside
> `create_app` (`main.py:68`) and by the sweep (`src/sweep.py:67,75`). Adding `github:`
> without widening `ALLOWED` crash-loops the server *and* kills the daily sweep. This is
> the `apps.yaml` trap from the Dockerfile `COPY` incident, reached by a different route.

The existing `active` flag is **not** reused as a scan filter. It is required by the
schema, validated, and read by nothing — overloading a dead flag with new meaning would
be worse than the explicit `github:` key.

## Fetching

`python:3.11-slim` has no `git` binary. Rather than add an apt layer and a
credential-bearing clone URL:

- **HEAD:** `GET /repos/{owner}/{repo}/commits/HEAD` → sha. `HEAD` resolves to the
  default branch server-side, so this costs one request rather than two and works
  whether the repo is on `main` or `master`. This is the entire cost of an unchanged repo.
- **Tree:** `GET /repos/{owner}/{repo}/tarball/{sha}` → extract with `tarfile` into a
  temp dir, deleted after the scan.

`httpx` is already a dependency. No new image layers and no `.git` directory to reason
about.

> **`extractall` must pass `filter="data"`.** Without it, tar members can traverse
> (`../`), use absolute paths, or plant symlinks. Available on 3.11.4+. Pinned by a test.

## The scan gate

A scan is identified by `(app, head_sha, prompt_version)`.

```
for each app with a `github:` key:
    sha = resolve_head(app)
    if an ok scan exists for (app, sha, PROMPT_VERSION):  skip
    if a failed scan exists with attempts >= MAX_ATTEMPTS: skip
    scan
```

**The skip-gate matches `status == "ok"` only.** If it matched any row, a single 401 from
an expired PAT would write a `failed` row that permanently suppresses that commit — the
repo would silently never be scanned again, and the corpus would rot with no symptom.
`MAX_ATTEMPTS` (3) bounds retries so a genuinely broken repo does not burn a call every day.

`PROMPT_VERSION` is an int constant in the module. Bumping it invalidates every scan and
forces a full re-read — the intended way to roll out an improved analysis prompt.

## Trigger and bootstrap

The lane runs inside `ingest.post_ingest`'s existing `BackgroundTask`, **before** brief
generation, so a fresh scan is visible to the same run's assessment. `post_ingest` is a
sync function, so FastAPI runs it in a threadpool and it does not block the event loop.

Steady state is 0–2 changed repos per day: seconds.

> **Bootstrap bound.** The first run has eight unscanned repos. Eight agentic reads in one
> background task is minutes of wall clock and several dollars in one spike. The lane
> therefore scans at most `MAX_SCANS_PER_RUN` (default 3) repos per ingest and lets the
> backlog drain over successive days. This repo already has a bootstrap-trap precedent —
> Phase 4's changelog watcher, which would have inserted 487 undismissable rows on first
> run. Same class of mistake, different units.

## Data model

### `code_scans` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `app` | `String(64)`, indexed | `apps.yaml` slug |
| `repo` | `String(140)` | `owner/name` as scanned |
| `head_sha` | `String(40)` | |
| `prompt_version` | int | |
| `status` | `String(16)` | `ok` \| `failed` |
| `attempts` | int | incremented per try |
| `error` | `Text` | HTTP status + message; empty when ok |
| `scanned_at` | `String(32)` | ISO |
| `summary` | `Text` | what this repo *is*, 2–3 sentences |
| `scores` | `Text` | JSON, per-dimension 1–5 |
| `truncated` | bool | the loop hit its budget |

Indexed on `(app, head_sha, prompt_version)`, **not unique** — a failed attempt and a
later successful one are both legitimate rows for the same key.

### `code_findings` (new)

| Column | Type | Notes |
|---|---|---|
| `id` | int PK | |
| `scan_id` | FK → `code_scans.id`, indexed, cascade delete | |
| `ord` | int | rank within the scan |
| `dimension` | `String(24)` | see below |
| `severity` | `String(8)` | `high` \| `medium` \| `low` |
| `title` | `String(200)` | |
| `detail` | `Text` | the argument |
| `files` | `Text` | comma-separated `path` or `path:line` |

Dimensions: `tests`, `error-handling`, `security`, `structure`, `deployment`,
`duplication`. A fixed vocabulary, so the corpus can group and the future grade axis has
stable columns to score.

## The agent loop

Anthropic SDK directly — **not** the Claude Code CLI. A small tool loop is hermetically
testable with fixtures, adds no binary to the image, and gives explicit control over what
the agent can reach.

**Model:** `claude-sonnet-5`, override `COACH_SCAN_MODEL`. Structured result via
`output_config.format`, the same mechanism the assessment loop chose — one pattern in the
codebase rather than two.

**Tools:** `list_dir(path)`, `read_file(path, offset?, limit?)`, `grep(pattern, glob?)`.

**Sandbox.** Every path is resolved and checked `is_relative_to(root)`, with symlinks
resolved *before* the check. A deny list covers `node_modules`, `.git`, `dist`, `.next`,
`build`, lockfiles and binaries — half containment, half not spending 40k tokens reading
a lockfile.

**Budget.** 40 tool calls or 150k input tokens per repo, whichever trips first. On
exhaustion the model is asked for its findings anyway and the scan is marked `truncated`.

> Truncation is **stated in the corpus**, never hidden — the same rule the assessment
> spec applies to its unit list. A silently truncated scan reads to the model as a
> complete one and produces confident claims about a repo it only half read.

## The analysis prompt

Two jobs.

**Characterize before criticizing.** The `summary` must say what the repo actually does —
stack, entry points, what it deploys as — before any finding is allowed. This is the
anchor that makes findings specific.

**Defeat the generic checklist.** The failure mode is eight scans that all say *"add
tests, handle errors, move secrets to env vars."* Two counters, both enforced in the
prompt and the second in the writer:

- Every finding must name specific files in `files`. A finding with no file reference is
  dropped.
- A finding that would read identically against a different repo is not a finding.

Findings cite **paths and line numbers, not code snippets**. The reason is token economy
and keeping source out of Postgres — it is explicitly *not* a privacy control, since the
scan sends the code to Anthropic regardless. Recording the weaker, true justification
matters: a future reader who believes it is a security boundary will make bad decisions
about the surrounding system.

## Corpus integration

`brief.build_corpus_context` gains a **Code** section:

- Per app with an `ok` scan: `summary`, `scores`, and findings ordered by severity.
- An explicit **coverage line** naming every app without a current `ok` scan.

**Excluded and failed must read differently.** An app with no `github:` key is out of
scope; an app whose scan failed is missing data. Collapsing them tells the model the
corpus is complete when it isn't, which is the same error the truncation rule exists to
prevent. The line is derived — apps lacking `github:` for the first group, apps whose
latest scan is `failed` for the second — so no app name is hardcoded.

`brief.fingerprint` gains the sorted set of `(app, head_sha)` over `ok` scans. A new scan
therefore trips the existing change gate and yields a delta with **no new trigger
mechanism**. This is the whole integration: one corpus section, one fingerprint term.

## Scan health and token expiry

`COACH_GITHUB_TOKEN` is a fine-grained PAT (`contents: read`) over the eight scanned
repos, live since 2026-08-12, **expiring 2027-06-01**. It is deliberately **not** in
`REQUIRED_PROD_SECRETS` — following the `ANTHROPIC_API_KEY` precedent, a missing token
must degrade to visible failed scans rather than crash-loop the boot, which is the
`apps.yaml` trap this repo has already hit once.

On expiry every scan returns 401 and the corpus quietly stops refreshing. There is no
user-visible symptom, which makes it the most dangerous failure here. So:

- `COACH_GITHUB_TOKEN_EXPIRES` (optional, ISO date) holds the known expiry.
- `GET /api/overview` gains `scan_health`: `{scanned, total, failed[], token_expires,
  days_remaining}`, where `total` counts apps carrying a `github:` key — excluded apps are
  not a shortfall and must not read as one.
- Overview renders a warning at **≤30 days remaining** and a distinct expired state.

The warning is driven off the configured date, **not inferred from 401s** — inference only
fires after the damage. Unset means no warning, and no crash.

## Failure handling

| Failure | Behaviour |
|---|---|
| GitHub 401 | `failed`, `"github 401 — COACH_GITHUB_TOKEN expired or lacks access"`. Surfaced in `scan_health`. Retried. |
| GitHub 404 | `failed`, repo deleted or not granted in the PAT. Retried to cap, then dormant until `prompt_version` bumps. |
| Tarball over size cap | `failed` with the measured size; never partially extracted. |
| Anthropic error | `failed`, retried to cap. |
| Output not parseable despite the schema | Store raw text as `summary`, zero findings, status **`ok`**. |
| A finding with no `files` entry | Dropped, siblings kept, logged. |

The unparseable case mirrors the assessment spec exactly: a degraded scan beats no scan,
and `failed` stays reserved for genuine call failures so a run of `failed` rows remains a
trustworthy signal of misconfiguration.

## Cost

A full pass over eight repos is single-digit dollars; steady state is 0–2 repos per day.
Both the scan and the brief report through
`usage_api.upsert_llm_daily(..., "app-builder-coach", ...)`, so scanning appears as a
visible line in the coach's own spend. That is correct, not a defect — the coach measuring
its own cost is the point.

`MAX_SCANS_PER_RUN` is the cost ceiling per ingest. `PROMPT_VERSION` bumps are the one
operation that re-incurs the full pass, and are therefore deliberate.

## Tests

Python:

- path sandbox rejects `../`, absolute paths, and symlink escape
- `extractall` uses `filter="data"`; a crafted traversal member is refused
- the gate skips an app with an `ok` row for `(app, sha, prompt_version)`
- the gate **retries** an app whose only row is `failed`, and stops at `attempts >= 3`
- a `prompt_version` bump forces a rescan of an already-`ok` app
- `MAX_SCANS_PER_RUN` bounds a bootstrap run and the remainder is picked up next ingest
- an app without a `github:` key is never scanned
- the corpus coverage line distinguishes excluded from failed
- truncation is stated in the corpus when `truncated` is set
- `brief.fingerprint` moves when a new `ok` scan lands, and is stable otherwise
- unparseable model output stores `summary`, zero findings, status `ok`
- a finding with no `files` is dropped and its siblings survive
- `scan_health` reports `days_remaining` from `COACH_GITHUB_TOKEN_EXPIRES`, and omits the
  warning when unset
- `shared/apps.py` accepts `github:` and still rejects a genuinely unknown key

Frontend (vitest): the Overview scan-health line — healthy, warning, expired, and the
unset case that renders nothing.

Existing invariants that must not regress: the autouse `background_calls` fixture in
`tests/web/conftest.py` still neutralizes and records `post_ingest`, so the suite never
reaches GitHub or Anthropic through this path — **do not replace it with a bare no-op**;
`require_same_origin` stays a router-level dependency and never becomes global middleware.

## Migration

One Alembic revision, additive and non-destructive: create `code_scans` and
`code_findings`. No existing table is altered and no row is deleted, so no pre-migration
dump is required — which matters, because this repo still has no database backups.

Coupled, and **must land in the same commit** (not a migration, but a deploy-ordering
constraint): the `github:` keys in `apps.yaml` and the widened `ALLOWED` in
`shared/apps.py`. An `apps.yaml` edit that reaches the Mac before the code does also kills
the sweep, so this is not merely a deploy-order concern.

## Out of scope

- The code-quality grade axis (sub-project 2) and ask-on-demand queries (sub-project 3).
- `parental-stories`, excluded by decision, and `tkeefe66/claude-config`, which is
  configuration rather than an app.
- Scanning the working tree. The lane sees GitHub HEAD; uncommitted work is invisible by
  design.
- Database backups. Still the repo's largest real gap, and this design avoids adding to
  the exposure by keeping the migration additive.
