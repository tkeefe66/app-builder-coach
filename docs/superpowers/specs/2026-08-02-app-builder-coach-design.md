# App Builder Coach — Design

**Date:** 2026-08-02
**Status:** Approved

## Purpose

A personal coaching loop with two lanes:

1. **Code-craft lane** — automatically log the *types* of code Tom builds across every
   project in `Code Apps`, building a capability profile over time.
2. **Feature-adoption lane** — track which Claude Code features Tom actually uses vs.
   the full feature surface, surfacing what he's never touched.

A `/build-coach` skill reads both profiles and proposes one concrete next challenge that
stretches his skills a notch, paired with unused Claude Code features to apply while
building it.

## Decisions (settled during brainstorming)

| Decision | Choice |
|---|---|
| Repo name / location | `Code Apps/app-builder-coach` (this repo) |
| Collector mechanism | Daily scheduled sweep across `Code Apps` (no per-repo hooks) |
| Coach delivery | On-demand global skill `/build-coach`; no push in v1 |
| Lanes in v1 | Both (code-craft + feature-adoption) |
| Classification | Approach C: heuristics for cheap facts + Haiku for feature tags, cached forever by content hash |
| Adoption lane | LLM-free, pure local parsing |
| Scheduler | macOS launchd LaunchAgent (cloud agents can't reach local disk) |
| Stack | Python + pytest |

## Non-goals (v1)

- No web UI, no dashboard, no Telegram push.
- No cloud storage — all data local to this repo.
- No rewriting of history: the ledger is append-only.
- Not a time tracker or productivity metric — it profiles *capability types*, not hours.

## Architecture

```
Code Apps/*/            ~/.claude/history.jsonl,
 (git repos,             settings.json, skills/
  docs/superpowers/)          │
      │                       │
  collector.py            adoption.py
      │                       │
 data/ledger.jsonl            │
      │                       │
 classifier.py (Haiku,        │
  cached)                     │
      │                       │
 data/classifications.jsonl   │
      └──────┬────────────────┘
         profile.py
             │
       data/profile.md   ◄── read by the global /build-coach skill
```

### 1. Collector (`src/collector.py`)

- Sweeps every git repo directly under `Code Apps/` (configurable root). Skips
  directories prefixed `z` (archive convention) and non-repos.
- Incremental: stores a last-seen commit cursor per repo in `data/cursors.json`;
  each run appends only new commits. First run backfills all history.
- Emits one JSONL row per commit to `data/ledger.jsonl`:
  `{repo, sha, date, message, files: [...], languages: {...}, insertions, deletions}`.
  Languages derived from file extensions.
- Also indexes `docs/superpowers/specs/*.md` in each repo as **features**
  (`{repo, spec_path, date, title}`) — these are the primary classification unit.
- Pure stdlib + `git` subprocess. Never writes to the swept repos.

### 2. Classifier (`src/classifier.py`)

- Assigns capability tags from a **fixed taxonomy** (`taxonomy.yaml`, ~30 tags:
  `auth`, `db-migrations`, `background-jobs`, `llm-integration`, `email-ingestion`,
  `api-client`, `caching`, `charts-svg`, `webhooks`, `testing-depth`, `deploy-docker`,
  `data-modeling`, `scraping`, `cli-tooling`, …). Tags are the vocabulary of the
  profile; adding a tag is a code change, not an LLM improvisation.
- Input per feature: the spec file text (preferred) or, for repos without specs, a
  cluster of commit messages + file paths.
- One Haiku 4.5 call per unclassified feature (`claude-haiku-4-5-20251001`), JSON out:
  `{tags: [...], complexity: 1-5, summary: one line}`.
- **Cache-forever rule:** results keyed by content hash in `data/classifications.jsonl`.
  A run with no new work makes zero API calls (the llm-cost-analysis lesson: churn
  dominates cost — classify once, never re-classify).
- `ANTHROPIC_API_KEY` unset → heuristics-only fallback (path/dependency rules), logged,
  never a crash.

### 3. Feature-adoption lane (`src/adoption.py`)

- **No LLM. No prompt content leaves the machine.** Reads only:
  - `~/.claude/history.jsonl` — which slash commands / skills are invoked (command
    names only, never prompt text);
  - `~/.claude/settings.json` — hooks configured;
  - `~/.claude/skills/` + plugin config — what exists vs. what gets used.
- Diffs against `feature-checklist.yaml` (~40 entries seeded from the claude-howto
  lessons 01–10 plus newer features: worktrees, checkpoints, plan mode, background
  tasks, subagent teams, print mode/CI, MCP resources, …). Each entry: name, lesson
  link, detection rule (command pattern / config key / manual).
- Output per feature: `used` / `configured-but-unused` / `never-touched`, with last-used
  date where detectable.

### 4. Profile builder (`src/profile.py`)

- Renders `data/profile.md`, the coach's single input:
  - **Capability matrix:** tag × feature count × last-done date × complexity trend.
  - **Streaks/gaps:** tags never done, tags not done in 6+ months.
  - **Feature-adoption table** from lane 2.
- Deterministic render from the JSONL files; no LLM.

### 5. Coach (global skill `~/.claude/skills/build-coach/SKILL.md`)

- Invoked as `/build-coach`. Steps: run `make sweep` if data is stale (>24h), read
  `data/profile.md`, then produce:
  1. Snapshot: what you've built, by capability.
  2. Gap analysis: weakest/missing tags, unused Claude Code features.
  3. **One** concrete next challenge — a buildable feature/project one notch beyond
     the profile (not a list; a single recommendation with a reason).
  4. 1–2 unused Claude Code features to apply while building it.
- The skill holds coaching *instructions*; all data comes from the repo. Scope note:
  the skill is global but points at this repo's absolute path.

### 6. Scheduling

- `launchd` LaunchAgent (`com.tomkeefe.app-builder-coach.plist`, installed by
  `make install-schedule`) runs `make sweep` daily. Sweep = collector → classifier →
  profile. The coach also refreshes on invocation, so the timer is belt-and-suspenders.

## Repo layout

```
app-builder-coach/
├── src/            collector.py, classifier.py, adoption.py, profile.py, config.py
├── data/           ledger.jsonl, classifications.jsonl, cursors.json, profile.md  (gitignored except .gitkeep)
├── taxonomy.yaml
├── feature-checklist.yaml
├── launchd/com.tomkeefe.app-builder-coach.plist
├── Makefile        sweep / test / install-schedule
├── tests/          pytest — fixture git repos, parsing, caching, checklist diffing
└── docs/superpowers/  specs + plans
```

`data/` is gitignored: the ledger derives from git history and can always be rebuilt;
classifications are the one semi-precious file (they cost API calls) — backed up by the
cache-forever design, and cheap to regenerate if lost.

## Error handling

- Collector: unreadable repo → log and skip, never abort the sweep.
- Classifier: API failure → leave feature unclassified, retry next run; rate limits
  respected with simple backoff; cost logged per call (writer/tokens/cost row, same
  pattern as public-dynasty's `llm_costs.jsonl`).
- Adoption: missing/corrupt history lines → skip line, count parse failures, surface in
  profile footer.
- All jobs exit 0 on partial failure with a status line; the profile notes staleness.

## Privacy

- Everything stays local. The only data leaving the machine: spec text / commit
  messages + file paths sent to the Anthropic API for tag classification.
- `history.jsonl` prompt *content* is never sent anywhere or copied; only command
  names are parsed out.

## Testing

- pytest; pure functions (ledger parsing, cursor logic, tag caching, checklist
  diffing, history parsing) tested against fixtures, including a synthetic git repo
  built in tmp_path.
- The Haiku call isolated behind one function (`classify_feature`), mocked in tests.
- TDD per global config (superpowers:test-driven-development).

## Build phases

1. Collector + ledger + tests (works with zero LLM).
2. Adoption lane + checklist (also LLM-free).
3. Profile builder rendering both lanes.
4. Classifier with cache + cost logging.
5. `/build-coach` global skill + launchd install.

Each phase leaves the system usable.
