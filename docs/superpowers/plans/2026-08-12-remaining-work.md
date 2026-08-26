# Remaining Work Implementation Plan

> **⚠️ EXECUTED 2026-08-13. Do not re-run this plan.** Tasks 1, 4, 5, 6 and 8 shipped as
> written. Two tasks diverged and the divergence matters more than the plan text:
>
> - **Tasks 2 and 3 (backups) were impossible as specified.** Railway volume backups and PITR
>   are **Pro-plan only** and unavailable on this account. Do not spend time on the Backups
>   tab. What was built instead: a `coach-backup` nightly cron service — encrypted `pg_dump`
>   → Cloudflare R2 — ported from the sibling `family-tree` repo. It is live, scheduled
>   (`0 8 * * *`), and its restore was verified end to end.
> - **Task 7 (Parental-Stories reporter) was DROPPED 2026-08-13 — the application is being
>   retired.** Do not execute it. The code was written and reviewed on branch `usage-reporter`
>   in that repo and never deployed; the app was removed from `apps.yaml` instead. With it
>   gone, the usage-reporter rollout is complete.
>
> Current state is `docs/HANDOFF.md`. This file is kept as the record of what was intended
> and why, not as instructions.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close out every open item on coach-web — database backups, a real CSP, a drift
tile that reports its own staleness, and the last unfinished usage reporter — so
`docs/HANDOFF.md` is left holding accepted limitations only.

**Architecture:** Four independent slices plus two bookend chores. Backups are Railway
configuration plus a restore drill, not code. The CSP and drift changes are small,
test-first edits to `apps/coach_web/main.py`, `api.py`, and the SPA. The reporter is a
copy-in of the existing `reporters/usage.py` into a different repo at its single
Anthropic chokepoint. **No schema migrations anywhere** — deliberate, so nothing in this
plan can put the database into a state the new backups don't yet cover.

**Tech Stack:** FastAPI + SQLAlchemy + Alembic, pytest; React 18 + TypeScript strict +
Vite + Recharts, vitest + Testing Library; Railway CLI; Postgres 16.

**Spec:** `docs/superpowers/specs/2026-08-12-remaining-work-design.md`

## Global Constraints

- **Mutation-check every new guard.** Break it deliberately, confirm the suite goes red,
  restore it. A green suite is not evidence — five guards in this repo were deleted by
  reviewers with the suite staying green.
- **An inequality assertion cannot pin an operator.** Test asymmetric cases and both
  sides of every boundary.
- **Test fixtures must span production magnitude.** Real anchor ages, not 1 and 2 days.
- **No schema migrations in this plan.** If a task appears to need one, stop and escalate.
- **Python is `.venv/bin/python`** — bare `python` is not on PATH in this environment.
- **Run all commands from the repo root.** The app imports `shared/` and `apps/` from there.
- **Deploy is `railway up --service coach-web --detach`**, then verify per the
  `deploy-coach-web` skill. Deploys upload the working directory, so `meta.commitHash` is
  always null — never infer what is live from git.
- **`make sweep` is part of deploy verification**, not an optional extra: it is the only
  place a `require_same_origin` regression on the ingest route shows up, and it shows up
  silently as `queued=1` instead of `shipped=1`.
- Full suite before any deploy: `.venv/bin/python -m pytest -q` (419 passing at plan
  time) and `npm run test -- --run` in `apps/coach_web/frontend/` (46 passing).

---

### Task 1: Clear the decks

**Files:** none — git and branch state only.

**Interfaces:**
- Consumes: nothing.
- Produces: `main` carries the spec and this plan; no stale branches remain.

⚠️ **Run this task from the main checkout** (`/Users/tomkeefe/Code Apps/app-builder-coach`),
not from a worktree. `main` is checked out there, and git refuses to check out the same
branch in two working trees. The spec and this plan were authored on
`worktree-handoff-fingerprint-doc-fix`, so they only reach `main` via this merge.

- [ ] **Step 1: Merge the doc/spec branch into main**

```bash
git checkout main
git merge --ff-only worktree-handoff-fingerprint-doc-fix
git push origin main
```

If the fast-forward is refused, `main` has moved — rebase the branch onto `main` and
retry rather than forcing.

- [ ] **Step 2: Verify the merge landed**

```bash
git log --oneline -3
ls docs/superpowers/specs/2026-08-12-remaining-work-design.md
```

Expected: the spec and plan commits are on `main`, and the file exists.

- [ ] **Step 3: Delete the three merged branches**

```bash
git branch -d phase4-coach phase5-interactive infra-services
git branch -d worktree-handoff-fingerprint-doc-fix
git push origin --delete worktree-handoff-fingerprint-doc-fix
```

`-d` (not `-D`) is deliberate: it refuses to delete anything not fully merged, which is
the safety check. `phase4-coach`, `phase5-interactive` and `infra-services` are local-only.

- [ ] **Step 4: Confirm the end state**

```bash
git branch --merged main
git status --short
```

Expected: only `main` listed, working tree clean.

---

### Task 2: Enable Railway backups and PITR

**Files:** none — Railway configuration.

**Interfaces:**
- Consumes: nothing.
- Produces: a Postgres service with daily + weekly volume snapshots and PITR archiving.
  Task 3 verifies it.

⚠️ **These are dashboard actions.** The Railway CLI has no backup subcommand — do not
burn time looking for one. Project `app-builder-coach`
(`9a0fc543-5688-4b67-be19-4ac7f09650f4`), service **Postgres**.

⚠️ **Enabling PITR redeploys the Postgres service**, so the dashboard drops connections
briefly. Do it deliberately now rather than during an incident. Tell the user before
clicking if they are actively using the dashboard.

- [ ] **Step 1: Enable the volume backup schedules**

Open the Postgres service → **Backups** tab → enable:
- **Daily** (retained 6 days)
- **Weekly** (retained 1 month)

Leave **Monthly** off. Everything except the six app-owned tables is reconstructible by
re-running the sweep, and PITR covers the recent window where real mistakes happen.

- [ ] **Step 2: Take one manual backup immediately**

Same tab → trigger a manual backup. This gives a restore point that exists before
anything else in this plan touches the database.

Note the limit: manual backups are capped at 50% of the volume's total size. If it is
refused, grow the volume first and say so — do not skip the step.

- [ ] **Step 3: Enable PITR**

Same tab → **Enable PITR** → confirm. Railway creates a `Postgres-PITR` bucket, sets
`WAL_ARCHIVE_*` variables and redeploys.

**The restore window starts at the first post-enable base backup.** Enabling it today
does not let you restore to yesterday — which is exactly why this task is not deferred.

- [ ] **Step 4: Confirm archiving is healthy**

Wait for the datetime picker to appear on the Backups tab; it only appears once archiving
is healthy and the first base backup completes. Then confirm the app came back:

```bash
curl -s https://coach-web-production-1f04.up.railway.app/api/health
```

Expected: `{"status":"ok"}`.

- [ ] **Step 5: Record what is enabled**

Add to `.claude/skills/deploy-coach-web/SKILL.md` under a new `## Backups` heading:

```markdown
## Backups

Postgres has **daily (6d) + weekly (1mo) volume snapshots** and **PITR** (~4-week
window), both enabled 2026-08-12 from the service's Backups tab. There is no CLI for
either — they are dashboard-only.

⚠️ **Wiping a volume deletes all of its backups.** Snapshots and PITR protect against
mistakes *in* the database, not against losing the volume or project. The logical
`pg_dump` in `data/backups/` is the only layer that survives that.

Restoring a volume backup removes any backups newer than it, and restores only into the
same project and environment. A PITR restore instead creates a *new sibling service*
named `Postgres-restored-<stamp>` and leaves the original serving traffic.
```

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/deploy-coach-web/SKILL.md
git commit -m "docs: record the backup layers now enabled on Postgres"
```

---

### Task 3: Restore drill

**Files:**
- Create: `data/backups/` (git-ignored via `.gitignore`'s `data/*`, excluded from Railway
  uploads via `.railwayignore` — both confirmed 2026-08-12)
- Modify: `docs/HANDOFF.md`, `.claude/skills/deploy-coach-web/SKILL.md`

**Interfaces:**
- Consumes: the backups enabled in Task 2.
- Produces: two documented numbers — real recovery time and real recovery point.

A backup that has never been restored is unverified. This task is what converts "we have
backups" from an assumption into a fact.

- [ ] **Step 1: Confirm the Postgres client tools exist**

```bash
which pg_dump pg_restore psql
```

If missing: `brew install libpq` and add its `bin` to PATH. Do this before starting, not
halfway through a tunnel session.

- [ ] **Step 2: Open a tunnel to production Postgres**

```bash
railway connect postgres --tunnel-only
```

Leave it running; it prints host, port, user, password and database. Use a second
terminal for everything below.

The public TCP proxy is deliberately not enabled on this project
(`DATABASE_PUBLIC_URL` is an unresolved template — confirmed in the Phase 5 security
baseline), so the tunnel is the way in.

- [ ] **Step 3: Take a logical dump, timing it**

```bash
mkdir -p data/backups
time pg_dump "postgresql://postgres:<password>@localhost:<port>/railway" \
  --format=custom --no-owner \
  --file=data/backups/coach-$(date +%Y%m%d-%H%M%S).dump
```

`--format=custom` is compressed and supports selective and parallel restore; a plain SQL
dump does not.

- [ ] **Step 4: Record production row counts for the six app-owned tables**

```bash
psql "postgresql://postgres:<password>@localhost:<port>/railway" -c \
"SELECT relname, n_live_tup FROM pg_stat_user_tables \
 WHERE relname IN ('goals','notes','dismissals','feature_checkoffs','brief_recommendations','briefs') \
 ORDER BY relname;"
```

Save the output — it is the comparison baseline for Step 6.

- [ ] **Step 5: Restore into a scratch database, timing it**

```bash
psql "postgresql://postgres:<password>@localhost:<port>/railway" -c 'CREATE DATABASE restore_drill;'
time pg_restore --dbname="postgresql://postgres:<password>@localhost:<port>/restore_drill" \
  --no-owner --exit-on-error \
  data/backups/coach-<stamp>.dump
```

`--exit-on-error` matters: without it `pg_restore` reports success while having skipped
failing objects, which is precisely the false confidence this drill exists to prevent.

- [ ] **Step 6: Verify the restored data matches**

```bash
psql "postgresql://postgres:<password>@localhost:<port>/restore_drill" -c \
"SELECT relname, n_live_tup FROM pg_stat_user_tables \
 WHERE relname IN ('goals','notes','dismissals','feature_checkoffs','brief_recommendations','briefs') \
 ORDER BY relname;"
```

Expected: identical counts to Step 4 for all six tables. A mismatch is a **failed drill**
— stop and investigate rather than documenting a number that is not true.

Then spot-check the newest rows actually carry content:

```bash
psql "postgresql://postgres:<password>@localhost:<port>/restore_drill" -c \
"SELECT id, kind, day, status FROM briefs ORDER BY created_at DESC LIMIT 3;"
```

- [ ] **Step 7: Drop the scratch database**

```bash
psql "postgresql://postgres:<password>@localhost:<port>/railway" -c 'DROP DATABASE restore_drill;'
```

Close the tunnel.

- [ ] **Step 8: Document the two numbers that matter**

In `docs/HANDOFF.md`, replace item 1 of "What's actually left" with a completed entry
carrying the measured figures:

```markdown
1. **✅ Backups — DONE 2026-08-12.** Daily (6d) + weekly (1mo) volume snapshots and PITR
   (~4-week window) are enabled on the Postgres service; both are dashboard-only, there
   is no CLI. A restore drill was run the same day: dump took <N>s, restore took <M>s,
   and row counts matched across all six app-owned tables (`goals`, `notes`,
   `dismissals`, `feature_checkoffs`, `brief_recommendations`, `briefs`).
   **Measured recovery point: <age of dump>. Measured recovery time: <M>s.**
   ⚠️ Wiping a volume deletes its backups — the `pg_dump` in `data/backups/` is the only
   layer that survives losing the volume or project. Re-run the drill after any change
   to the data model.
```

Fill in the real numbers. Do not leave the placeholders.

- [ ] **Step 9: Commit**

```bash
git add docs/HANDOFF.md .claude/skills/deploy-coach-web/SKILL.md
git commit -m "docs: record the restore drill and its measured RTO/RPO"
```

---

### Task 4: Tighten the CSP

**Files:**
- Modify: `apps/coach_web/main.py:48-58`
- Test: `tests/web/test_security.py`

**Interfaces:**
- Consumes: nothing.
- Produces: a `Content-Security-Policy` header carrying nine directives. No other task
  depends on it.

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_security.py`:

```python
# Every directive is listed individually so a partial regression names the
# directive that vanished, rather than failing on one opaque string compare.
CSP_DIRECTIVES = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
]


@pytest.mark.parametrize("directive", CSP_DIRECTIVES)
def test_csp_carries_every_directive(client, directive):
    csp = client.get("/api/health").headers["content-security-policy"]
    assert directive in csp


def test_csp_keeps_unsafe_inline_for_styles_only(client):
    # Recharts injects inline styles at runtime, so 'unsafe-inline' in
    # style-src is permanent -- but it must never leak into script-src,
    # which is the directive actually holding back injected script.
    csp = client.get("/api/health").headers["content-security-policy"]
    script = next(d for d in csp.split(";") if d.strip().startswith("script-src"))
    assert "unsafe-inline" not in script
    assert "unsafe-eval" not in script
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/web/test_security.py -q -k csp
```

Expected: FAIL — the current header is `frame-ancestors 'none'` only, so eight of the
nine directive assertions fail.

- [ ] **Step 3: Write the implementation**

Replace `apps/coach_web/main.py:48-58` with:

```python
    # The SPA loads nothing external -- a same-origin favicon and the bundled
    # module, nothing more -- so 'self' everywhere holds.
    #
    # style-src keeps 'unsafe-inline' permanently: Recharts injects inline
    # styles at runtime, so moving the SPA's own style={{}} usages to classes
    # would not let this be tightened. It is not a placeholder for that work.
    CSP = ("default-src 'self'; script-src 'self'; "
           "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
           "connect-src 'self'; object-src 'none'; base-uri 'self'; "
           "form-action 'self'; frame-ancestors 'none'")

    SECURITY_HEADERS = {
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": CSP,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/web/test_security.py -q
```

Expected: PASS, including the pre-existing `test_security_headers_present_on_api`, which
asserts `"frame-ancestors 'none'" in ...` and must still hold.

- [ ] **Step 5: Mutation-check the new guard**

Delete `script-src 'self'; ` from the `CSP` string, re-run:

```bash
.venv/bin/python -m pytest tests/web/test_security.py -q -k csp
```

Expected: FAIL on the `script-src 'self'` parametrization. **Restore the string.** If it
passed, the test is decorative — fix it before continuing.

- [ ] **Step 6: Run the full suite**

```bash
.venv/bin/python -m pytest -q
```

Expected: all pass (419 + 2 new parametrized cases at plan time).

- [ ] **Step 7: Commit**

```bash
git add apps/coach_web/main.py tests/web/test_security.py
git commit -m "feat(security): a real CSP, not just frame-ancestors"
```

- [ ] **Step 8: Deploy and verify in a browser — this step is not optional**

```bash
railway up --service coach-web --detach
```

Wait for SUCCESS, then walk **all six pages** — Overview, Capabilities, Activity, Cost,
Adoption, Goals & Coach — with the browser console open, watching for
`Refused to ... because it violates the Content Security Policy`. Also exercise the
**write paths** (create a goal, dismiss an item), since those are the routes a
`connect-src` or `form-action` mistake would break.

A green test suite cannot catch a missed directive blanking a page. If any page reports a
violation, add the directive it names, re-test, redeploy.

Then the standard deploy checks:

```bash
curl -s https://coach-web-production-1f04.up.railway.app/api/health
curl -sI https://coach-web-production-1f04.up.railway.app/ | grep -icE 'x-frame|content-security|strict-transport|x-content-type|referrer-policy'
make sweep
```

Expected: `{"status":"ok"}`, `5`, and `shipped=1 queued=0`.

---

### Task 5: Drift staleness — API

**Files:**
- Modify: `apps/coach_web/api.py:301-319`
- Test: `tests/web/test_truecost.py`

**Interfaces:**
- Consumes: nothing.
- Produces: the `drift` object in `GET /api/truecost` gains two fields —
  `age_days: int` (days from `console_to` to today) and `stale: bool`
  (`age_days > 35`). `drift` stays `null` when unset or invalid. Task 6 renders these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_truecost.py`:

```python
def _anchor(monkeypatch, days_old, spend="5.00"):
    """Pin the Console window to a period ending `days_old` days ago."""
    end = (date.today() - timedelta(days=days_old)).isoformat()
    start = (date.today() - timedelta(days=days_old + 30)).isoformat()
    monkeypatch.setenv("COACH_CONSOLE_FROM", start)
    monkeypatch.setenv("COACH_CONSOLE_TO", end)
    monkeypatch.setenv("COACH_CONSOLE_SPEND", spend)


def test_drift_age_days_counts_from_the_anchor_end(client, monkeypatch):
    _anchor(monkeypatch, 10)
    login(client)
    drift = client.get("/api/truecost").json()["drift"]
    assert drift["age_days"] == 10
    assert drift["stale"] is False


def test_drift_is_not_stale_at_the_threshold(client, monkeypatch):
    # 35 days, not 30: the anchor is re-set from a monthly Console figure, so a
    # 30-day threshold would flag as stale during the normal window between one
    # month's reading and the next.
    _anchor(monkeypatch, 35)
    login(client)
    drift = client.get("/api/truecost").json()["drift"]
    assert drift["age_days"] == 35
    assert drift["stale"] is False


def test_drift_is_stale_one_day_past_the_threshold(client, monkeypatch):
    _anchor(monkeypatch, 36)
    login(client)
    drift = client.get("/api/truecost").json()["drift"]
    assert drift["age_days"] == 36
    assert drift["stale"] is True


def test_drift_is_stale_at_production_neglect_magnitude(client, monkeypatch):
    # A forgotten anchor is months old, not days. Fixtures must span the real
    # magnitude -- a boundary test alone would pass against a broken unit.
    _anchor(monkeypatch, 180)
    login(client)
    drift = client.get("/api/truecost").json()["drift"]
    assert drift["age_days"] == 180
    assert drift["stale"] is True
```

Both sides of the boundary are asserted deliberately: a single "stale when old" test is
symmetric and would stay green if `>` became `>=`.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
.venv/bin/python -m pytest tests/web/test_truecost.py -q -k "age_days or stale or threshold or neglect"
```

Expected: FAIL with `KeyError: 'age_days'`.

- [ ] **Step 3: Write the implementation**

In `apps/coach_web/api.py`, add near the other module constants:

```python
# The Console figure is re-read monthly, so a 30-day threshold would flag as
# stale during the normal gap between readings. 35 gives that slack while still
# catching an anchor nobody has touched.
DRIFT_STALE_AFTER_DAYS = 35
```

Then replace the `drift = {...}` assignment (currently `api.py:317-319`) with:

```python
            age_days = (today - date.fromisoformat(console_to)).days
            drift = {"from": console_from, "to": console_to,
                     "console_usd": console_usd, "tracked_usd": tracked,
                     "gap_usd": round(console_usd - tracked, 2),
                     "age_days": age_days,
                     "stale": age_days > DRIFT_STALE_AFTER_DAYS}
```

`date` and `timedelta` are already imported at `api.py:5`. `today` and the `_is_iso_date`
validation that guards `console_to` are already in scope — `date.fromisoformat` cannot
raise here because the malformed-date branch returns `drift = None` before this line.

- [ ] **Step 4: Run the tests to verify they pass**

```bash
.venv/bin/python -m pytest tests/web/test_truecost.py -q
```

Expected: PASS, including the four pre-existing drift tests.

- [ ] **Step 5: Mutation-check the boundary**

Change `age_days > DRIFT_STALE_AFTER_DAYS` to `>=`, re-run:

```bash
.venv/bin/python -m pytest tests/web/test_truecost.py -q -k threshold
```

Expected: FAIL on `test_drift_is_not_stale_at_the_threshold`. **Restore `>`.** If it
passed, the boundary is unpinned.

- [ ] **Step 6: Commit**

```bash
git add apps/coach_web/api.py tests/web/test_truecost.py
git commit -m "feat(cost): drift reports its own age and staleness"
```

---

### Task 6: Drift staleness — UI

**Files:**
- Modify: `apps/coach_web/frontend/src/components/StatTile.tsx`
- Modify: `apps/coach_web/frontend/src/pages/Cost.tsx:30-31, 66-69`
- Modify: `apps/coach_web/frontend/src/tokens.css`
- Test: `apps/coach_web/frontend/src/__tests__/StatTile.test.tsx` (create),
  `apps/coach_web/frontend/src/__tests__/Cost.test.tsx`

**Interfaces:**
- Consumes: `drift.age_days: number` and `drift.stale: boolean` from Task 5.
- Produces: `StatTile` gains an optional `warn?: boolean` prop. Nothing else consumes it
  yet; it is a tile-level state so future tiles can reuse it rather than the Cost page
  special-casing itself.

All commands in this task run from `apps/coach_web/frontend/`.

- [ ] **Step 1: Write the failing StatTile test**

Create `apps/coach_web/frontend/src/__tests__/StatTile.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatTile from "../components/StatTile";

describe("StatTile", () => {
  it("renders plainly by default", () => {
    const { container } = render(<StatTile label="Untracked spend" value="$4.00" sub="vs Console" />);
    expect(screen.getByText("$4.00")).toBeInTheDocument();
    expect(container.querySelector(".tile-warn")).toBeNull();
  });

  it("marks the tile and its sub-label when warn is set", () => {
    const { container } = render(
      <StatTile label="Untracked spend" value="$4.00" sub="180d old" warn />);
    expect(container.querySelector(".tile-warn")).not.toBeNull();
    expect(screen.getByText("180d old").className).toContain("warn-text");
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

```bash
npm run test -- --run src/__tests__/StatTile.test.tsx
```

Expected: FAIL — `warn` is not a prop, so `.tile-warn` is never rendered.

- [ ] **Step 3: Implement the StatTile change**

Replace `apps/coach_web/frontend/src/components/StatTile.tsx` entirely:

```tsx
export default function StatTile({ label, value, sub, dim, warn }: {
  label: string; value: string | number; sub?: string; dim?: boolean; warn?: boolean;
}) {
  return (
    <div className={warn ? "card tile-warn" : "card"}
      style={dim ? { opacity: 0.5 } : undefined}>
      <div className="muted" style={{ fontSize: 13 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700 }}>{value}</div>
      {sub && <div className={warn ? "warn-text" : "ink2"} style={{ fontSize: 12 }}>{sub}</div>}
    </div>
  );
}
```

Append to `apps/coach_web/frontend/src/tokens.css` (after the `.ink2` rule at line 49):

```css
.tile-warn { border-color: var(--status-warn); }
.warn-text { color: var(--status-warn); }
```

`--status-warn` (`#fab219`) is defined once on `:root` and not overridden in the dark
block, so it reads correctly in both themes.

- [ ] **Step 4: Run it to verify it passes**

```bash
npm run test -- --run src/__tests__/StatTile.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Write the failing Cost page tests**

Append inside the `describe("Cost page", ...)` block in
`apps/coach_web/frontend/src/__tests__/Cost.test.tsx`:

```tsx
  function truecostWithDrift(drift: unknown) {
    return vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/truecost")) {
        return Promise.resolve(jsonResponse({
          window_days: 30,
          window: { start: "2026-07-12", end: "2026-08-11" },
          apps: [
            { app: "coach-web", display: "Coach Web", railway_usd: 5.2, llm_usd: 3.1,
              total_usd: 8.3, share: 1 },
          ],
          totals: { railway_usd: 5.2, llm_usd: 3.1, total_usd: 8.3 },
          railway_period: { period_start: null, to_date_usd: 0 },
          llm_month_to_date_usd: 12.5,
          cache: { input_tokens: 1000, cache_read_tokens: 400,
            cache_creation_tokens: 100, read_ratio: 0.4 },
          drift,
        }));
      }
      return Promise.resolve(jsonResponse({ available: false, days: [], weekly: [],
        total_usd_window: 0, by_model_window: {} }));
    });
  }

  it("shows the anchor window without a warning while the anchor is fresh", async () => {
    vi.stubGlobal("fetch", truecostWithDrift({
      from: "2026-07-01", to: "2026-07-31", console_usd: 100, tracked_usd: 96,
      gap_usd: 4, age_days: 12, stale: false,
    }));

    render(<Cost />);

    await waitFor(() => {
      expect(screen.getByText("vs Console 2026-07-01→2026-07-31")).toBeInTheDocument();
    });
    expect(screen.queryByText(/re-anchor/)).toBeNull();
  });

  it("flags the tile once the anchor has gone stale", async () => {
    vi.stubGlobal("fetch", truecostWithDrift({
      from: "2026-01-01", to: "2026-01-31", console_usd: 100, tracked_usd: 96,
      gap_usd: 4, age_days: 193, stale: true,
    }));

    render(<Cost />);

    await waitFor(() => {
      expect(screen.getByText(/193d old, re-anchor/)).toBeInTheDocument();
    });
  });
```

- [ ] **Step 6: Run them to verify they fail**

```bash
npm run test -- --run src/__tests__/Cost.test.tsx
```

Expected: FAIL — the stale copy is never rendered.

- [ ] **Step 7: Implement the Cost page change**

In `apps/coach_web/frontend/src/pages/Cost.tsx`, extend the `drift` type (lines 30-31):

```tsx
  drift: { from: string; to: string; console_usd: number; tracked_usd: number;
    gap_usd: number; age_days: number; stale: boolean } | null;
```

Replace the Untracked spend tile (lines 66-69):

```tsx
            <StatTile label="Untracked spend"
              value={tc.drift ? `$${tc.drift.gap_usd.toFixed(2)}` : "—"}
              sub={tc.drift
                ? (tc.drift.stale
                    ? `vs Console ${tc.drift.from}→${tc.drift.to} · ${tc.drift.age_days}d old, re-anchor`
                    : `vs Console ${tc.drift.from}→${tc.drift.to}`)
                : "not configured"}
              dim={!tc.drift}
              warn={!!tc.drift?.stale} />
```

The three pre-existing Cost tests pass `drift: null` and are unaffected.

- [ ] **Step 8: Run the frontend suite**

```bash
npm run test -- --run
npx tsc --noEmit
```

Expected: all tests pass (46 + 4 new at plan time), and a clean typecheck — the project
is TS strict, and there is no format or typecheck hook, so run it yourself.

- [ ] **Step 9: Mutation-check the stale rendering**

Change `warn={!!tc.drift?.stale}` to `warn={false}`, re-run:

```bash
npm run test -- --run src/__tests__/Cost.test.tsx
```

Expected: FAIL on the stale test. **Restore it.**

- [ ] **Step 10: Commit**

```bash
git add apps/coach_web/frontend/src
git commit -m "feat(cost): the drift tile now shows its own staleness"
```

- [ ] **Step 11: Deploy and verify against a deliberately stale anchor**

```bash
railway up --service coach-web --detach
```

After SUCCESS, temporarily set the anchor to an old window and confirm the tile flags:

```bash
railway variables --service coach-web --set 'COACH_CONSOLE_TO=2026-01-31'
```

Load the Cost page, confirm the tile is warn-coloured and reads `…d old, re-anchor`, then
**restore the real value**. Capture the original first:

```bash
railway variables list --service coach-web --kv | grep COACH_CONSOLE_TO
```

---

### Task 7: Parental-Stories usage reporter

**Files (different repo — `/Users/tomkeefe/Code Apps/zParental-stories`):**
- Create: `backend/src/app/services/usage_reporter.py` (copy of this repo's
  `reporters/usage.py`)
- Modify: `backend/src/app/services/claude.py`
- Test: `backend/tests/test_claude_service.py`

**Interfaces:**
- Consumes: `reporters/usage.py`'s `report(app, model, usage, url=None, token=None,
  blocking=False)`, which reads `COACH_USAGE_URL` / `COACH_USAGE_TOKEN` from the
  environment and returns silently when either is unset.
- Produces: a `parental-stories` row in coach-web's `llm_daily`. The slug is already
  registered at `apps.yaml:24`.

**Already verified 2026-08-12 — do not redo:** project
`32d8d935-8144-48fd-8b8b-db1359f5532c` has three services (`Front End`, `Postgres`,
`Back End`); only **`Back End`** carries `ANTHROPIC_API_KEY`, and it carries neither
`COACH_USAGE_*` variable. The backend is **Python**, so the `tsc`/`dist` trap that bit
the TypeScript rollouts does not apply here.

**Chokepoint confirmed:** `backend/src/app/services/claude.py` is the only file in
`backend/src` constructing an `Anthropic` client, and it has exactly two
`messages.create` call sites — `ClaudeClient.complete` (line 34) and
`ClaudeClient.complete_with_tools` (line 47). Both need reporting; neither streams.

- [ ] **Step 1: Copy the reporter in, unmodified**

```bash
cp "/Users/tomkeefe/Code Apps/app-builder-coach/reporters/usage.py" \
   "/Users/tomkeefe/Code Apps/zParental-stories/backend/src/app/services/usage_reporter.py"
```

Do not edit it. It is the one source of truth and copies must not fork.

- [ ] **Step 2: Write the failing test**

Append to `backend/tests/test_claude_service.py`:

```python
def test_complete_reports_usage_to_coach(monkeypatch):
    from app.services import claude as claude_mod, usage_reporter

    sent = []
    monkeypatch.setattr(usage_reporter, "report",
                        lambda *a, **kw: sent.append((a, kw)))

    class FakeUsage:
        input_tokens = 120
        output_tokens = 30

    class FakeResp:
        content = [type("B", (), {"type": "text", "text": "hi"})()]
        usage = FakeUsage()

    client = claude_mod.ClaudeClient(api_key="test")
    monkeypatch.setattr(client.client.messages, "create", lambda **kw: FakeResp())

    assert client.complete(system="s", user="u") == "hi"
    assert len(sent) == 1
    app, model, usage = sent[0][0]
    assert app == "parental-stories"
    assert model == claude_mod.DEFAULT_MODEL
    assert usage.input_tokens == 120


def test_reporting_failure_never_breaks_the_call(monkeypatch):
    # The reporter's contract is that it never raises. Pin it at the call site
    # too: a coach-web outage must not take down story generation.
    from app.services import claude as claude_mod, usage_reporter

    def boom(*a, **kw):
        raise RuntimeError("coach-web is down")

    monkeypatch.setattr(usage_reporter, "report", boom)

    class FakeResp:
        content = [type("B", (), {"type": "text", "text": "hi"})()]
        usage = type("U", (), {"input_tokens": 1, "output_tokens": 1})()

    client = claude_mod.ClaudeClient(api_key="test")
    monkeypatch.setattr(client.client.messages, "create", lambda **kw: FakeResp())

    assert client.complete(system="s", user="u") == "hi"
```

- [ ] **Step 3: Run them to verify they fail**

```bash
cd "/Users/tomkeefe/Code Apps/zParental-stories/backend"
python -m pytest tests/test_claude_service.py -q -k "reports_usage or never_breaks"
```

Expected: FAIL — `usage_reporter` is never called, so `sent` is empty. Use whatever
interpreter that repo uses (check for a `.venv` or `uv run`).

- [ ] **Step 4: Wire both call sites**

In `backend/src/app/services/claude.py`, add to the imports:

```python
from app.services import usage_reporter

COACH_APP_SLUG = "parental-stories"
```

Add a private helper on the class:

```python
    def _report(self, resp) -> None:
        """Report token usage to coach-web. Must never affect the caller."""
        try:
            if getattr(resp, "usage", None) is not None:
                usage_reporter.report(COACH_APP_SLUG, self.model, resp.usage)
        except Exception:
            log.debug("usage reporting failed", exc_info=True)
```

The local `try` is belt-and-braces: `report` already swallows everything, but this file
must not depend on that guarantee holding after a future edit to the reporter.

In `complete`, after line 34:

```python
        resp = self.client.messages.create(**kwargs)
        self._report(resp)
```

In `complete_with_tools`, capture the response before returning it:

```python
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            tools=tools,
        )
        self._report(resp)
        return resp
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python -m pytest tests/ -q
```

Expected: PASS, and the pre-existing claude-service tests still green.

- [ ] **Step 6: Mutation-check the report call**

Comment out `self._report(resp)` in `complete`, re-run:

```bash
python -m pytest tests/test_claude_service.py -q -k reports_usage
```

Expected: FAIL. **Restore it.**

- [ ] **Step 7: Commit in that repo**

```bash
git add backend/src/app/services/usage_reporter.py backend/src/app/services/claude.py backend/tests/test_claude_service.py
git commit -m "feat: report Anthropic usage to coach-web"
```

- [ ] **Step 8: Set the two variables on Back End**

Read the token from coach-web without printing it into scrollback, then set it:

```bash
railway variables list -p 9a0fc543-5688-4b67-be19-4ac7f09650f4 -e production -s coach-web --kv | grep COACH_USAGE_TOKEN
```

```bash
railway variables -p 32d8d935-8144-48fd-8b8b-db1359f5532c -e production -s "Back End" \
  --set 'COACH_USAGE_URL=https://coach-web-production-1f04.up.railway.app/api/usage' \
  --set 'COACH_USAGE_TOKEN=<value from above>'
```

⚠️ `COACH_USAGE_TOKEN` must match coach-web's literal exactly — a mismatch is a 401 the
reporter swallows silently, so it surfaces as "no rows appeared", never as an error.

- [ ] **Step 9: Deploy and exercise a real call**

Deploy the Back End service, then trigger a story generation through the app so a real
Anthropic call runs.

- [ ] **Step 10: Verify the row landed**

```bash
curl -s -X POST https://coach-web-production-1f04.up.railway.app/api/login \
  -H 'Content-Type: application/json' -d '{"password":"<password>"}' \
  -c /tmp/cj -o /dev/null -w '%{http_code}\n'
curl -s "https://coach-web-production-1f04.up.railway.app/api/truecost?days=30" -b /tmp/cj
```

Expected: an entry with `"app": "parental-stories"` and a non-zero `llm_usd`.

If nothing appears: the reporter never raises, so check the Back End logs for
`coach-web usage report rejected: status=...` — that line is the only signal of a bad
token or URL.

- [ ] **Step 11: Update the coach repo's records**

In `docs/HANDOFF.md`, change the Parental-Stories row of the service table from
`**the only one left**` to `DONE`, and delete the "the only one left" framing in the
surrounding prose. Then:

```bash
git rm docs/reporter-rollout-prompt.md
git commit -am "docs: parental-stories reports; the reporter rollout is complete"
```

The rollout doc describes work that no longer exists — leaving it makes a finished
rollout look pending.

---

### Task 8: Reconcile HANDOFF.md

**Files:**
- Modify: `docs/HANDOFF.md`

**Interfaces:**
- Consumes: the completed state of Tasks 2-7.
- Produces: a "What's actually left" section holding accepted limitations only.

- [ ] **Step 1: Rewrite "What's actually left"**

Replace the section (currently `docs/HANDOFF.md:472` onward) so it reads as decisions
rather than a backlog:

```markdown
## What's actually left

Nothing is pending. Every item that was open on 2026-08-12 has been closed — backups
(enabled + drill-verified), the CSP, the drift staleness flag, and the Parental-Stories
reporter. See `docs/superpowers/specs/2026-08-12-remaining-work-design.md` for why each
was done the way it was.

What remains are **accepted limitations** — recorded decisions, not work:

1. **`POST /api/reassess` runs its Sonnet call synchronously**, holding a DB session for
   up to the SDK's 600s timeout. `/api/ingest` avoids this with `BackgroundTasks`; this
   endpoint does not, because returning 202 needs frontend polling that was judged not
   worth it for a single user pressing a button. If it starts timing out, that is the fix.
2. **The session is a stateless signed cookie**, so a copied cookie stays valid until its
   30-day expiry. Server-side revocation was offered and declined as disproportionate.
3. **Deploys do not go through git.** `railway up` uploads the working directory, so
   `meta.commitHash` is always null and `origin/main` tells you nothing about what is
   running. Grep the served JS bundle to identify a live build.
4. **`claude-opus-5[1m]` premium long-context pricing is not modelled** in
   `src/usage.py::PRICES`. If 1M-context sessions become common, add a tier.
5. **`style-src 'unsafe-inline'` is permanent.** Recharts injects inline styles at
   runtime, so moving the SPA's 137 `style={{}}` usages to classes would not let the
   directive be tightened. The earlier claim that a full CSP was blocked on that refactor
   was wrong.
6. **Parental-Stories streaming calls, if any are added later, report only after the
   stream drains** — a client that disconnects mid-response leaves that call unreported.
   It surfaces as drift.

The **deferred minors** list above remains a triage decision, not a backlog. Do not work
through it as tickets.
```

- [ ] **Step 2: Verify no stale claims remain**

```bash
grep -n "no database backups\|A bad migration is currently permanent\|only one left\|needs the inline" docs/HANDOFF.md
```

Expected: no matches. Each of those was true before this plan and is false after; leaving
any of them would send the next agent to redo finished work.

- [ ] **Step 3: Run both suites one final time**

```bash
.venv/bin/python -m pytest -q
cd apps/coach_web/frontend && npm run test -- --run && npx tsc --noEmit
```

- [ ] **Step 4: Commit and push**

```bash
git add docs/HANDOFF.md
git commit -m "docs: close out the remaining-work list"
git push origin main
```

---

## Self-review notes

**Spec coverage:** Item 0 → Task 1. Item 1 → Tasks 2 and 3 (config, then drill). Item 2 →
Task 4. Item 3 → Tasks 5 and 6 (API, then UI). Item 4 → Task 7. The spec's "definition of
done" → Task 8.

**Sequencing:** Task 2 gates Tasks 4-7 by policy, not by technical dependency — no
changes to the deployed app until the database it writes to can be restored. Tasks 4, 5-6
and 7 are mutually independent and may run in any order or in parallel.

**Two deploys, not four:** Tasks 4 and 6 each end with a deploy because each needs live
verification that tests cannot provide (browser console for CSP, visible tile state for
drift). If executed back to back, deploy once after Task 6 and do both verifications in
the same browser walk.
