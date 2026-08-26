# Coach Web Phase 2: Dashboard UI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A React + Vite SPA (Overview, Capabilities, Activity, Adoption, plus Cost and Goals & Coach empty states) served by the existing FastAPI app on Railway, fed by four new aggregate read endpoints.

**Architecture:** Server-side aggregation — four new endpoints under the existing session-authed router turn raw tables into chart-ready JSON; the SPA stays thin (fetch, render, minimal client logic). The frontend lives in `apps/coach_web/frontend/`, builds to `dist/`, and FastAPI serves it with an SPA fallback. Deploy switches from RAILPACK to a multi-stage Dockerfile (node build → python runtime). Spec: `docs/superpowers/specs/2026-08-02-coach-web-dashboard-design.md`.

**Tech Stack:** FastAPI (existing), React 18 + TypeScript + Vite, react-router-dom, Recharts, vitest + @testing-library/react, Docker (multi-stage) on Railway.

## Global Constraints

- All read endpoints require the session cookie (`require_user`); `/api/ingest` and `/api/health` are unchanged.
- Server does the aggregation; the SPA never re-derives what an endpoint can return. No client-side state library — React state + fetch only.
- Dates remain ISO strings end to end. Week = Monday-start. "Stale" = last activity > 180 days ago.
- Sessions/cost data does not exist until Phase 3: endpoints return `null` for those fields and the UI renders dimmed "Phase 3" placeholders — never fake zeros.
- Design tokens (dataviz reference palette) are the single source of color: categorical slot 1 blue `#2a78d6` light / `#3987e5` dark; surfaces `#fcfcfb` / `#1a1a19`; page plane `#f9f9f7` / `#0d0d0d`; primary ink `#0b0b0b` / `#ffffff`; secondary ink `#52514e` / `#c3c2b7`; muted `#898781`; gridline `#e1e0d9` / `#2c2c2a`; status good `#0ca30c`, warning `#fab219`, serious `#ec835a`, critical `#d03b3b`. Charts: single-series → no legend; text wears ink tokens, never series color; one axis; thin marks; `font-variant-numeric: tabular-nums` on table/axis numbers only. Dark mode via `prefers-color-scheme` on CSS custom properties.
- Status-chip mapping (Adoption): used → good; configured-but-unused → warning; never-touched → neutral muted chip (NOT a status color — absence isn't an alert). Chips always carry a text label, never color alone.
- Python: worktree venv `.venv/bin/python` (3.11), pytest in `tests/web/`. Frontend: Node ≥ 20 (v24 verified locally), vitest for logic; run frontend commands with `npm --prefix apps/coach_web/frontend`.
- The taxonomy is read from repo-root `taxonomy.yaml` on the server (same repo, same deploy) — never duplicated into server code, never shipped in snapshots.

## File Structure

```
apps/coach_web/taxonomy.py            (new) all_tags() from repo-root taxonomy.yaml
apps/coach_web/aggregate.py           (new) pure aggregation helpers (weeks, streak, monthly)
apps/coach_web/api.py                 (modify) + /api/overview, /api/capabilities, /api/activity, /api/adoption/board
apps/coach_web/main.py                (modify) SPA static serving + fallback
apps/coach_web/frontend/              (new) Vite app
  package.json  vite.config.ts  tsconfig.json  index.html
  src/main.tsx  src/App.tsx  src/api.ts  src/tokens.css  src/tokens.ts  src/format.ts
  src/components/StatTile.tsx  Sparkline.tsx  WeeklyBars.tsx  StatusChip.tsx  Empty.tsx
  src/pages/Login.tsx  Overview.tsx  Capabilities.tsx  Activity.tsx  Adoption.tsx  Cost.tsx  Goals.tsx
  src/__tests__/format.test.ts  api.test.ts  StatusChip.test.tsx
Dockerfile                            (new, multi-stage)
.dockerignore                         (new)
railway.json                          (modify) builder DOCKERFILE
tests/web/test_taxonomy.py            (new)
tests/web/test_aggregate.py           (new)
tests/web/test_api_phase2.py          (new)
tests/web/test_spa_serving.py         (new)
```

Data available (Phase 1, live in Postgres): `feature_units(key, kind, repo, date, title, tags, complexity, summary, model)`, `activity_daily(date, commits, by_repo, sessions?, prompts?)`, `adoption_history(snapshot_id, feature_name, lesson, status, last_used)`, `feature_catalog(name, lesson, source, discovered_at)`, `snapshots(id, content_hash, captured_at, sweep_stats, received_at)`. Note: `feature_units.date` for `kind="commits"` units is always the month's first day (`YYYY-MM-01`); spec-kind units carry real dates.

---

### Task 1: Taxonomy loader + aggregation helpers

**Files:**
- Create: `apps/coach_web/taxonomy.py`, `apps/coach_web/aggregate.py`
- Test: `tests/web/test_taxonomy.py`, `tests/web/test_aggregate.py`

**Interfaces:**
- Produces: `taxonomy.all_tags() -> list[str]` (repo taxonomy.yaml, cached), `aggregate.week_start(d: date) -> date` (Monday), `aggregate.weekly_rollup(rows: list[dict], weeks: int, today: date) -> list[dict]` (rows are `{"date": iso, "commits": int, "by_repo": dict}`; returns oldest→newest `[{"start": iso, "commits": int, "by_repo": dict}]`, zero-filled weeks included), `aggregate.streak(rows, today: date) -> dict` (`{"days": int, "last_active": iso|None}` — consecutive commit-days counting back from the most recent active day), `aggregate.weekday_totals(rows) -> list[int]` (7 ints, Monday-first), `aggregate.monthly_counts(dates: list[str], months: int, today: date) -> list[dict]` (`[{"month": "YYYY-MM", "count": int}]` oldest→newest, zero-filled). All pure, no DB.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_taxonomy.py`:

```python
from apps.coach_web import taxonomy


def test_all_tags_reads_repo_taxonomy():
    tags = taxonomy.all_tags()
    assert "api-backend" in tags and "websockets-sse" in tags
    assert len(tags) >= 20
    assert tags == sorted(tags)
```

`tests/web/test_aggregate.py`:

```python
from datetime import date

from apps.coach_web import aggregate

ROWS = [
    {"date": "2026-07-27", "commits": 3, "by_repo": {"a": 3}},  # Mon
    {"date": "2026-07-28", "commits": 2, "by_repo": {"a": 1, "b": 1}},
    {"date": "2026-07-30", "commits": 1, "by_repo": {"b": 1}},  # gap on 29th
    {"date": "2026-07-20", "commits": 5, "by_repo": {"a": 5}},  # prior week Mon
]
TODAY = date(2026, 7, 31)  # Friday


def test_week_start_is_monday():
    assert aggregate.week_start(date(2026, 7, 31)) == date(2026, 7, 27)
    assert aggregate.week_start(date(2026, 7, 27)) == date(2026, 7, 27)


def test_weekly_rollup_zero_fills_and_orders():
    weeks = aggregate.weekly_rollup(ROWS, weeks=3, today=TODAY)
    assert [w["start"] for w in weeks] == ["2026-07-13", "2026-07-20", "2026-07-27"]
    assert weeks[0]["commits"] == 0
    assert weeks[1]["commits"] == 5
    assert weeks[2]["commits"] == 6
    assert weeks[2]["by_repo"] == {"a": 4, "b": 2}


def test_streak_counts_back_from_last_active_over_gap():
    s = aggregate.streak(ROWS, today=TODAY)
    assert s == {"days": 1, "last_active": "2026-07-30"}  # 30th active, 29th gap


def test_streak_consecutive():
    rows = [{"date": "2026-07-29", "commits": 1, "by_repo": {}},
            {"date": "2026-07-30", "commits": 2, "by_repo": {}},
            {"date": "2026-07-31", "commits": 1, "by_repo": {}}]
    assert aggregate.streak(rows, today=TODAY) == {"days": 3, "last_active": "2026-07-31"}


def test_streak_empty():
    assert aggregate.streak([], today=TODAY) == {"days": 0, "last_active": None}


def test_weekday_totals_monday_first():
    totals = aggregate.weekday_totals(ROWS)
    assert totals[0] == 8   # both Mondays
    assert totals[1] == 2   # Tuesday
    assert totals[3] == 1   # Thursday
    assert sum(totals) == 11


def test_monthly_counts_zero_fills():
    months = aggregate.monthly_counts(
        ["2026-07-01", "2026-07-15", "2026-05-02"], months=4, today=TODAY)
    assert months == [{"month": "2026-04", "count": 0},
                      {"month": "2026-05", "count": 1},
                      {"month": "2026-06", "count": 0},
                      {"month": "2026-07", "count": 2}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_taxonomy.py tests/web/test_aggregate.py -v`
Expected: FAIL (ModuleNotFoundError)

- [ ] **Step 3: Implement**

`apps/coach_web/taxonomy.py`:

```python
"""Capability taxonomy, read from the repo's taxonomy.yaml (single source)."""
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=1)
def all_tags() -> list[str]:
    tax = yaml.safe_load((REPO_ROOT / "taxonomy.yaml").read_text())
    return sorted(tax["tags"])
```

`apps/coach_web/aggregate.py`:

```python
"""Pure aggregation helpers for read endpoints. No DB access."""
from collections import Counter
from datetime import date, timedelta


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_rollup(rows: list[dict], weeks: int, today: date) -> list[dict]:
    latest_start = week_start(today)
    starts = [latest_start - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    buckets = {s.isoformat(): {"start": s.isoformat(), "commits": 0, "by_repo": {}}
               for s in starts}
    for row in rows:
        s = week_start(date.fromisoformat(row["date"])).isoformat()
        if s not in buckets:
            continue
        b = buckets[s]
        b["commits"] += row["commits"]
        for repo, n in (row.get("by_repo") or {}).items():
            b["by_repo"][repo] = b["by_repo"].get(repo, 0) + n
    return [buckets[s.isoformat()] for s in starts]


def streak(rows: list[dict], today: date) -> dict:
    active = {row["date"] for row in rows if row["commits"] > 0}
    if not active:
        return {"days": 0, "last_active": None}
    last = max(active)
    days = 0
    cursor = date.fromisoformat(last)
    while cursor.isoformat() in active:
        days += 1
        cursor -= timedelta(days=1)
    return {"days": days, "last_active": last}


def weekday_totals(rows: list[dict]) -> list[int]:
    totals = [0] * 7
    for row in rows:
        totals[date.fromisoformat(row["date"]).weekday()] += row["commits"]
    return totals


def monthly_counts(dates: list[str], months: int, today: date) -> list[dict]:
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    counts = Counter(d[:7] for d in dates)
    return [{"month": k, "count": counts.get(k, 0)} for k in keys]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_taxonomy.py tests/web/test_aggregate.py -v`
Expected: PASS (8 tests)

- [ ] **Step 5: Run full suite, commit**

Run: `.venv/bin/python -m pytest tests -q` — all pass.

```bash
git add apps/coach_web/taxonomy.py apps/coach_web/aggregate.py tests/web/test_taxonomy.py tests/web/test_aggregate.py
git commit -m "feat(web): taxonomy loader and pure aggregation helpers"
```

---

### Task 2: Read endpoints — overview, capabilities, activity, adoption board

**Files:**
- Modify: `apps/coach_web/api.py`
- Test: `tests/web/test_api_phase2.py`

**Interfaces:**
- Consumes: Task 1 helpers; existing models and `require_user`/`get_db`.
- Produces (all session-authed, all consumed verbatim by the SPA):
  - `GET /api/overview` → `{"freshness": {"captured_at": str|null, "received_at": str|null}, "tiles": {"units_this_week": int, "commits_this_week": int, "streak_days": int, "streak_last_active": str|null, "sessions_this_week": null, "cost_this_week": null}, "never_built": [str], "stale": [{"tag": str, "last_done": str}], "adoption_gaps": [str]}`
  - `GET /api/capabilities` → `{"tags": [{"tag", "count", "last_done", "avg_complexity", "monthly": [{"month","count"}×12]}] (count desc), "never_built": [str]}`
  - `GET /api/activity?weeks=12` → `{"weeks": [{"start","commits","by_repo"}], "weekday_totals": [int×7], "streak": {"days","last_active"}, "sessions_available": false}`
  - `GET /api/adoption/board` → `{"features": [{"name","lesson","status","last_used","source","discovered_at","history":[{"captured_at","status"}]}] (lesson asc, name asc)}` — `status`/`last_used` from the LATEST snapshot; features in the catalog missing from the latest snapshot get status "unknown".

- [ ] **Step 1: Write the failing tests**

`tests/web/test_api_phase2.py`:

```python
from datetime import date, timedelta

from shared import snapshot as snap_mod
from tests.web.test_ingest import AUTH


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def _iso(d):
    return d.isoformat()


def make_rich_payload(today):
    monday = today - timedelta(days=today.weekday())
    old = today - timedelta(days=200)
    body = {
        "schema_version": 1,
        "sweep": {"repos": 2, "new_commits": 4},
        "feature_units": [
            {"key": "u1:m", "kind": "spec", "repo": "alpha", "date": _iso(monday),
             "title": "this week", "tags": ["auth"], "complexity": 4,
             "summary": "s", "model": "m"},
            {"key": "u2:m", "kind": "spec", "repo": "alpha", "date": _iso(old),
             "title": "old", "tags": ["caching"], "complexity": 2,
             "summary": "s", "model": "m"},
        ],
        "activity_daily": [
            {"date": _iso(monday), "commits": 3, "by_repo": {"alpha": 3}},
            {"date": _iso(monday + timedelta(days=1)), "commits": 2,
             "by_repo": {"beta": 2}},
        ],
        "adoption": [
            {"name": "plan mode", "lesson": "09-advanced-features",
             "status": "never-touched", "last_used": None},
            {"name": "MCP servers", "lesson": "05-mcp",
             "status": "used", "last_used": _iso(monday)},
        ],
    }
    return snap_mod.finalize_payload(body, f"{_iso(today)}T07:30:00+00:00")


def test_all_read_endpoints_require_login(client):
    for path in ("/api/overview", "/api/capabilities",
                 "/api/activity", "/api/adoption/board"):
        assert client.get(path).status_code == 401, path


def test_overview(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    data = client.get("/api/overview").json()
    assert data["freshness"]["captured_at"].startswith(_iso(today))
    assert data["tiles"]["units_this_week"] == 1
    assert data["tiles"]["commits_this_week"] == 5
    assert data["tiles"]["streak_days"] >= 1
    assert data["tiles"]["sessions_this_week"] is None
    assert data["tiles"]["cost_this_week"] is None
    assert "websockets-sse" in data["never_built"]
    assert "auth" not in data["never_built"]
    assert {"tag": "caching", "last_done": _iso(today - timedelta(days=200))} in data["stale"]
    assert data["adoption_gaps"] == ["plan mode"]


def test_overview_empty_db(client):
    login(client)
    data = client.get("/api/overview").json()
    assert data["freshness"] == {"captured_at": None, "received_at": None}
    assert data["tiles"]["units_this_week"] == 0
    assert data["tiles"]["streak_days"] == 0


def test_capabilities(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    data = client.get("/api/capabilities").json()
    tags = {t["tag"]: t for t in data["tags"]}
    assert tags["auth"]["count"] == 1
    assert tags["auth"]["avg_complexity"] == 4.0
    assert len(tags["auth"]["monthly"]) == 12
    assert tags["auth"]["monthly"][-1]["count"] == 1
    assert "websockets-sse" in data["never_built"]


def test_activity(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    data = client.get("/api/activity?weeks=4").json()
    assert len(data["weeks"]) == 4
    assert data["weeks"][-1]["commits"] == 5
    assert sum(data["weekday_totals"]) == 5
    assert data["streak"]["days"] >= 1
    assert data["sessions_available"] is False


def test_adoption_board(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    data = client.get("/api/adoption/board").json()
    by_name = {f["name"]: f for f in data["features"]}
    assert by_name["plan mode"]["status"] == "never-touched"
    assert by_name["MCP servers"]["status"] == "used"
    assert len(by_name["plan mode"]["history"]) == 1
    lessons = [f["lesson"] for f in data["features"]]
    assert lessons == sorted(lessons)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_api_phase2.py -v`
Expected: FAIL (404s)

- [ ] **Step 3: Implement**

Append to `apps/coach_web/api.py` (extend existing imports: `date`, `timedelta` from datetime, `aggregate`, `taxonomy` relative imports):

```python
@router.get("/api/overview")
def overview(db: Session = Depends(get_db)):
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    today = date.today()
    monday = aggregate.week_start(today).isoformat()

    units_this_week = db.scalar(
        select(func.count(models.FeatureUnit.key))
        .where(models.FeatureUnit.date >= monday)) or 0
    activity_rows = [{"date": r.date, "commits": r.commits, "by_repo": r.by_repo}
                     for r in db.scalars(select(models.ActivityDaily))]
    commits_this_week = sum(r["commits"] for r in activity_rows
                            if r["date"] >= monday)
    stk = aggregate.streak(activity_rows, today)

    last_by_tag: dict[str, str] = {}
    for tags, d in db.execute(select(models.FeatureUnit.tags,
                                     models.FeatureUnit.date)):
        for t in tags or []:
            if t not in last_by_tag or d > last_by_tag[t]:
                last_by_tag[t] = d
    never_built = [t for t in taxonomy.all_tags() if t not in last_by_tag]
    stale_cutoff = (today - timedelta(days=180)).isoformat()
    stale = [{"tag": t, "last_done": d} for t, d in sorted(last_by_tag.items())
             if d <= stale_cutoff]

    adoption_gaps = []
    if latest is not None:
        adoption_gaps = sorted(db.scalars(
            select(models.AdoptionHistory.feature_name)
            .where(models.AdoptionHistory.snapshot_id == latest.id,
                   models.AdoptionHistory.status == "never-touched")))
    return {
        "freshness": {"captured_at": latest.captured_at if latest else None,
                      "received_at": latest.received_at.isoformat()
                      if latest else None},
        "tiles": {"units_this_week": units_this_week,
                  "commits_this_week": commits_this_week,
                  "streak_days": stk["days"],
                  "streak_last_active": stk["last_active"],
                  "sessions_this_week": None, "cost_this_week": None},
        "never_built": never_built,
        "stale": stale,
        "adoption_gaps": adoption_gaps,
    }


@router.get("/api/capabilities")
def capabilities(db: Session = Depends(get_db)):
    today = date.today()
    per_tag: dict[str, dict] = {}
    for tags, d, cx in db.execute(select(models.FeatureUnit.tags,
                                         models.FeatureUnit.date,
                                         models.FeatureUnit.complexity)):
        for t in tags or []:
            entry = per_tag.setdefault(t, {"dates": [], "cx": []})
            entry["dates"].append(d)
            entry["cx"].append(cx)
    out = []
    for t, e in per_tag.items():
        out.append({"tag": t, "count": len(e["dates"]),
                    "last_done": max(e["dates"]),
                    "avg_complexity": round(sum(e["cx"]) / len(e["cx"]), 1),
                    "monthly": aggregate.monthly_counts(e["dates"], 12, today)})
    out.sort(key=lambda r: (-r["count"], r["tag"]))
    return {"tags": out,
            "never_built": [t for t in taxonomy.all_tags() if t not in per_tag]}


@router.get("/api/activity")
def activity(weeks: int = 12, db: Session = Depends(get_db)):
    weeks = max(1, min(weeks, 52))
    today = date.today()
    rows = [{"date": r.date, "commits": r.commits, "by_repo": r.by_repo}
            for r in db.scalars(select(models.ActivityDaily))]
    return {"weeks": aggregate.weekly_rollup(rows, weeks, today),
            "weekday_totals": aggregate.weekday_totals(rows),
            "streak": aggregate.streak(rows, today),
            "sessions_available": False}


@router.get("/api/adoption/board")
def adoption_board(db: Session = Depends(get_db)):
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    latest_rows: dict[str, models.AdoptionHistory] = {}
    if latest is not None:
        for row in db.scalars(select(models.AdoptionHistory)
                              .where(models.AdoptionHistory.snapshot_id == latest.id)):
            latest_rows[row.feature_name] = row
    history: dict[str, list] = {}
    for row, captured in db.execute(
            select(models.AdoptionHistory, models.Snapshot.captured_at)
            .join(models.Snapshot,
                  models.AdoptionHistory.snapshot_id == models.Snapshot.id)
            .order_by(models.Snapshot.id)):
        history.setdefault(row.feature_name, []).append(
            {"captured_at": captured, "status": row.status})
    features = []
    for cat in db.scalars(select(models.FeatureCatalog)):
        latest_row = latest_rows.get(cat.name)
        features.append({
            "name": cat.name, "lesson": cat.lesson, "source": cat.source,
            "discovered_at": cat.discovered_at,
            "status": latest_row.status if latest_row else "unknown",
            "last_used": latest_row.last_used if latest_row else None,
            "history": history.get(cat.name, []),
        })
    features.sort(key=lambda f: (f["lesson"], f["name"]))
    return {"features": features}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_api_phase2.py -v`
Expected: PASS

- [ ] **Step 5: Run full suite, commit**

Run: `.venv/bin/python -m pytest tests -q` — all pass.

```bash
git add apps/coach_web/api.py tests/web/test_api_phase2.py
git commit -m "feat(web): aggregate read endpoints for dashboard"
```

---

### Task 3: Frontend scaffold — Vite app, tokens, API client, router shell, login

**Files:**
- Create: everything under `apps/coach_web/frontend/` listed in File Structure except pages other than Login (create stub pages that render their name), plus `src/__tests__/format.test.ts`, `src/__tests__/api.test.ts`

**Interfaces:**
- Produces: `api.get(path) -> Promise<any>` (fetch wrapper: 401 → redirect `/login`; non-2xx → throw `ApiError(status, detail)`), `api.login(password) -> Promise<boolean>`; `tokens.ts` exporting `series1`, `chartInk()` helpers reading CSS vars; `format.ts` exporting `fmtWeek(iso) -> "Jul 27"`, `fmtDate(iso|null) -> "Jul 27, 2026" | "—"`, `relDays(iso, now) -> "today" | "yesterday" | "N days ago"`; App shell with left-nav (Overview, Capabilities, Activity, Adoption, Cost, Goals & Coach) collapsing to top scroll-nav under 720px. Later tasks fill the stub pages.

- [ ] **Step 1: Scaffold the Vite app**

```bash
npm create vite@latest apps/coach_web/frontend -- --template react-ts
npm --prefix apps/coach_web/frontend install
npm --prefix apps/coach_web/frontend install react-router-dom recharts
npm --prefix apps/coach_web/frontend install -D vitest @testing-library/react @testing-library/jest-dom jsdom
```

Remove scaffold cruft: `src/App.css`, `src/assets/react.svg`, `public/vite.svg`, and the demo content of `App.tsx`/`index.css` (replaced below).

- [ ] **Step 2: Config files**

`apps/coach_web/frontend/vite.config.ts`:

```ts
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/__tests__/setup.ts"],
    globals: true,
  },
});
```

Add to `package.json` scripts: `"test": "vitest run"`.

`src/__tests__/setup.ts`:

```ts
import "@testing-library/jest-dom";
```

- [ ] **Step 3: Design tokens**

`src/tokens.css` (the dataviz reference palette; values verbatim from Global Constraints):

```css
:root {
  color-scheme: light;
  --page: #f9f9f7;
  --surface: #fcfcfb;
  --ink: #0b0b0b;
  --ink-2: #52514e;
  --muted: #898781;
  --grid: #e1e0d9;
  --baseline: #c3c2b7;
  --border: rgba(11, 11, 11, 0.10);
  --series-1: #2a78d6;
  --seq-250: #86b6ef;
  --status-good: #0ca30c;
  --status-warn: #fab219;
  --status-serious: #ec835a;
  --status-critical: #d03b3b;
  --good-text: #006300;
}
@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
    --page: #0d0d0d;
    --surface: #1a1a19;
    --ink: #ffffff;
    --ink-2: #c3c2b7;
    --muted: #898781;
    --grid: #2c2c2a;
    --baseline: #383835;
    --border: rgba(255, 255, 255, 0.10);
    --series-1: #3987e5;
    --seq-250: #1c5cab;
    --good-text: #0ca30c;
  }
}
body {
  margin: 0;
  background: var(--page);
  color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
}
.num { font-variant-numeric: tabular-nums; }
.muted { color: var(--muted); }
.ink2 { color: var(--ink-2); }
```

`src/tokens.ts`:

```ts
export function cssVar(name: string): string {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(name).trim();
}
export const series1 = () => cssVar("--series-1");
export const gridColor = () => cssVar("--grid");
export const mutedColor = () => cssVar("--muted");
```

- [ ] **Step 4: Failing tests for format + api**

`src/__tests__/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { fmtDate, fmtWeek, relDays } from "../format";

describe("format", () => {
  it("fmtWeek renders short month-day", () => {
    expect(fmtWeek("2026-07-27")).toBe("Jul 27");
  });
  it("fmtDate handles null", () => {
    expect(fmtDate(null)).toBe("—");
    expect(fmtDate("2026-07-27")).toBe("Jul 27, 2026");
  });
  it("relDays buckets", () => {
    const now = new Date("2026-07-31T12:00:00Z");
    expect(relDays("2026-07-31", now)).toBe("today");
    expect(relDays("2026-07-30", now)).toBe("yesterday");
    expect(relDays("2026-07-27", now)).toBe("4 days ago");
  });
});
```

`src/__tests__/api.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, get } from "../api";

afterEach(() => vi.restoreAllMocks());

describe("api.get", () => {
  it("returns parsed json on 200", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ ok: 1 }), { status: 200 })));
    expect(await get("/api/overview")).toEqual({ ok: 1 });
  });
  it("redirects to /login on 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response("{}", { status: 401 })));
    const assign = vi.fn();
    vi.stubGlobal("location", { assign, pathname: "/overview" } as any);
    await expect(get("/api/overview")).rejects.toThrow(ApiError);
    expect(assign).toHaveBeenCalledWith("/login");
  });
  it("throws ApiError with status on 500", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ detail: "boom" }), { status: 500 })));
    await expect(get("/api/overview")).rejects.toMatchObject({ status: 500 });
  });
});
```

Run: `npm --prefix apps/coach_web/frontend run test`
Expected: FAIL (modules missing)

- [ ] **Step 5: Implement format.ts and api.ts**

`src/format.ts`:

```ts
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function parts(iso: string): { y: number; m: number; d: number } {
  const [y, m, d] = iso.slice(0, 10).split("-").map(Number);
  return { y, m, d };
}

export function fmtWeek(iso: string): string {
  const { m, d } = parts(iso);
  return `${MONTHS[m - 1]} ${d}`;
}

export function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const { y, m, d } = parts(iso);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}

export function relDays(iso: string, now: Date): string {
  const then = Date.UTC(parts(iso).y, parts(iso).m - 1, parts(iso).d);
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const diff = Math.round((today - then) / 86400000);
  if (diff <= 0) return "today";
  if (diff === 1) return "yesterday";
  return `${diff} days ago`;
}
```

`src/api.ts`:

```ts
export class ApiError extends Error {
  constructor(public status: number, detail: string) {
    super(detail);
  }
}

export async function get(path: string): Promise<any> {
  const resp = await fetch(path, { credentials: "same-origin" });
  if (resp.status === 401) {
    location.assign("/login");
    throw new ApiError(401, "login required");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail ?? detail; } catch { /* keep */ }
    throw new ApiError(resp.status, detail);
  }
  return resp.json();
}

export async function login(password: string): Promise<boolean> {
  const resp = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: JSON.stringify({ password }),
  });
  return resp.ok;
}
```

Run: `npm --prefix apps/coach_web/frontend run test` — Expected: PASS

- [ ] **Step 6: App shell, router, login page, stub pages**

`src/App.tsx`:

```tsx
import { NavLink, Route, Routes } from "react-router-dom";
import Login from "./pages/Login";
import Overview from "./pages/Overview";
import Capabilities from "./pages/Capabilities";
import Activity from "./pages/Activity";
import Adoption from "./pages/Adoption";
import Cost from "./pages/Cost";
import Goals from "./pages/Goals";

const NAV = [
  ["/", "Overview"], ["/capabilities", "Capabilities"],
  ["/activity", "Activity"], ["/adoption", "Adoption"],
  ["/cost", "Cost"], ["/goals", "Goals & Coach"],
] as const;

export default function App() {
  return (
    <div className="shell">
      <nav className="sidenav">
        <div className="brand">Build Coach</div>
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>
        ))}
      </nav>
      <main>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route path="/" element={<Overview />} />
          <Route path="/capabilities" element={<Capabilities />} />
          <Route path="/activity" element={<Activity />} />
          <Route path="/adoption" element={<Adoption />} />
          <Route path="/cost" element={<Cost />} />
          <Route path="/goals" element={<Goals />} />
        </Routes>
      </main>
    </div>
  );
}
```

`src/main.tsx`:

```tsx
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./tokens.css";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
```

`src/index.css` (shell layout only — tokens.css owns color):

```css
.shell { display: flex; min-height: 100vh; }
.sidenav {
  width: 200px; flex-shrink: 0; padding: 24px 16px;
  border-right: 1px solid var(--border);
  display: flex; flex-direction: column; gap: 4px;
}
.brand { font-weight: 700; margin-bottom: 16px; }
.sidenav a {
  color: var(--ink-2); text-decoration: none;
  padding: 8px 12px; border-radius: 6px; font-size: 14px;
}
.sidenav a.active { color: var(--ink); background: var(--surface); font-weight: 600; }
main { flex: 1; padding: 24px; max-width: 1080px; }
h1 { font-size: 20px; margin: 0 0 16px; }
.tile-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; }
@media (max-width: 720px) {
  .shell { flex-direction: column; }
  .sidenav { width: auto; flex-direction: row; overflow-x: auto;
    border-right: none; border-bottom: 1px solid var(--border); }
  .brand { display: none; }
}
```

`src/pages/Login.tsx`:

```tsx
import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { login } from "../api";

export default function Login() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const nav = useNavigate();
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (await login(password)) nav("/");
    else setError("Wrong password (or rate-limited — wait a minute).");
  }
  return (
    <form onSubmit={submit} className="card" style={{ maxWidth: 320, margin: "20vh auto" }}>
      <h1>Build Coach</h1>
      <input type="password" value={password} autoFocus
        onChange={(e) => setPassword(e.target.value)} placeholder="Password"
        style={{ width: "100%", padding: 8, boxSizing: "border-box" }} />
      <button type="submit" style={{ marginTop: 12, padding: "8px 16px" }}>
        Sign in
      </button>
      {error && <p className="muted">{error}</p>}
    </form>
  );
}
```

Stub pages (each of `Overview.tsx`, `Capabilities.tsx`, `Activity.tsx`, `Adoption.tsx`, `Cost.tsx`, `Goals.tsx` until their task fills them):

```tsx
export default function Overview() {
  return <h1>Overview</h1>;
}
```

(adjust the function name/heading per file)

- [ ] **Step 7: Verify build + tests, commit**

```bash
npm --prefix apps/coach_web/frontend run build
npm --prefix apps/coach_web/frontend run test
```

Expected: build emits `dist/`, tests pass. `dist/` and `node_modules/` must be gitignored — append to `.gitignore`:

```
apps/coach_web/frontend/node_modules/
apps/coach_web/frontend/dist/
```

```bash
git add apps/coach_web/frontend .gitignore
git commit -m "feat(frontend): vite scaffold, tokens, api client, router shell, login"
```

---

### Task 4: Shared chart/tile components

**Files:**
- Create: `src/components/StatTile.tsx`, `src/components/Sparkline.tsx`, `src/components/WeeklyBars.tsx`, `src/components/StatusChip.tsx`, `src/components/Empty.tsx`
- Test: `src/__tests__/StatusChip.test.tsx`

**Interfaces:**
- Produces: `<StatTile label value sub? dim?>`, `<Sparkline data={[{month,count}]}>` (tiny single-series line, series-1, no legend/axes), `<WeeklyBars data={[{start,commits}]}>` (single-series bar, thin marks, 4px rounded top, hairline grid, tooltip), `<StatusChip status>` (used→good, configured-but-unused→warning, never-touched/unknown→muted outline; always shows text label), `<Empty title body>`.

- [ ] **Step 1: Failing test**

`src/__tests__/StatusChip.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatusChip from "../components/StatusChip";

describe("StatusChip", () => {
  it("always renders the status text", () => {
    for (const s of ["used", "configured-but-unused", "never-touched", "unknown"]) {
      render(<StatusChip status={s} />);
      expect(screen.getByText(s)).toBeInTheDocument();
    }
  });
  it("uses status color only for used/configured", () => {
    const { container } = render(<StatusChip status="never-touched" />);
    expect((container.firstChild as HTMLElement).className).toContain("chip-neutral");
  });
});
```

Run: `npm --prefix apps/coach_web/frontend run test` — Expected: FAIL

- [ ] **Step 2: Implement components**

`src/components/StatusChip.tsx`:

```tsx
const KIND: Record<string, string> = {
  "used": "chip-good",
  "configured-but-unused": "chip-warn",
};

export default function StatusChip({ status }: { status: string }) {
  return <span className={`chip ${KIND[status] ?? "chip-neutral"}`}>{status}</span>;
}
```

Append chip styles to `src/index.css`:

```css
.chip {
  display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 12px; border: 1px solid var(--border); color: var(--ink-2);
}
.chip-good { border-color: var(--status-good); color: var(--good-text); }
.chip-warn { border-color: var(--status-warn); }
.chip-neutral { color: var(--muted); }
```

`src/components/StatTile.tsx`:

```tsx
export default function StatTile({ label, value, sub, dim }: {
  label: string; value: string | number; sub?: string; dim?: boolean;
}) {
  return (
    <div className="card" style={dim ? { opacity: 0.5 } : undefined}>
      <div className="muted" style={{ fontSize: 13 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700 }}>{value}</div>
      {sub && <div className="ink2" style={{ fontSize: 12 }}>{sub}</div>}
    </div>
  );
}
```

`src/components/Sparkline.tsx`:

```tsx
import { Line, LineChart, ResponsiveContainer } from "recharts";
import { series1 } from "../tokens";

export default function Sparkline({ data }: { data: { month: string; count: number }[] }) {
  return (
    <ResponsiveContainer width={120} height={28}>
      <LineChart data={data} margin={{ top: 2, right: 2, bottom: 2, left: 2 }}>
        <Line dataKey="count" stroke={series1()} strokeWidth={2}
          dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
```

`src/components/WeeklyBars.tsx`:

```tsx
import { Bar, BarChart, CartesianGrid, ResponsiveContainer,
  Tooltip, XAxis, YAxis } from "recharts";
import { fmtWeek } from "../format";
import { gridColor, mutedColor, series1 } from "../tokens";

export default function WeeklyBars({ data, height = 220 }: {
  data: { start: string; commits: number }[]; height?: number;
}) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke={gridColor()} />
        <XAxis dataKey="start" tickFormatter={fmtWeek}
          tick={{ fill: mutedColor(), fontSize: 12 }}
          axisLine={false} tickLine={false} />
        <YAxis allowDecimals={false} width={32}
          tick={{ fill: mutedColor(), fontSize: 12 }}
          axisLine={false} tickLine={false} />
        <Tooltip labelFormatter={(v) => fmtWeek(String(v))} />
        <Bar dataKey="commits" fill={series1()} radius={[4, 4, 0, 0]}
          maxBarSize={28} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}
```

`src/components/Empty.tsx`:

```tsx
export default function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="card" style={{ textAlign: "center", padding: 48 }}>
      <h2 style={{ margin: 0 }}>{title}</h2>
      <p className="muted">{body}</p>
    </div>
  );
}
```

- [ ] **Step 3: Verify tests + build, commit**

```bash
npm --prefix apps/coach_web/frontend run test
npm --prefix apps/coach_web/frontend run build
```

Expected: PASS / build clean.

```bash
git add apps/coach_web/frontend/src
git commit -m "feat(frontend): shared tile, chart, chip components"
```

---

### Task 5: Overview page

**Files:**
- Modify: `src/pages/Overview.tsx`

**Interfaces:**
- Consumes: `GET /api/overview` (Task 2 shape), `StatTile`, `Empty`, `fmtDate`, `relDays`.

- [ ] **Step 1: Implement**

`src/pages/Overview.tsx`:

```tsx
import { useEffect, useState } from "react";
import { get } from "../api";
import StatTile from "../components/StatTile";
import { fmtDate, relDays } from "../format";

type Overview = {
  freshness: { captured_at: string | null; received_at: string | null };
  tiles: { units_this_week: number; commits_this_week: number;
    streak_days: number; streak_last_active: string | null;
    sessions_this_week: null; cost_this_week: null };
  never_built: string[];
  stale: { tag: string; last_done: string }[];
  adoption_gaps: string[];
};

export default function Overview() {
  const [data, setData] = useState<Overview | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get("/api/overview").then(setData).catch((e) => setErr(String(e))); }, []);
  if (err) return <p className="muted">Failed to load: {err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const f = data.freshness.captured_at;
  return (
    <>
      <h1>Overview</h1>
      <p className="muted" style={{ marginTop: -8 }}>
        Data as of {f ? `${fmtDate(f.slice(0, 10))} (${relDays(f.slice(0, 10), new Date())})` : "—"}
      </p>
      <div className="tile-row">
        <StatTile label="Features this week" value={data.tiles.units_this_week} />
        <StatTile label="Commits this week" value={data.tiles.commits_this_week} />
        <StatTile label="Streak" value={`${data.tiles.streak_days}d`}
          sub={data.tiles.streak_last_active
            ? `last active ${relDays(data.tiles.streak_last_active, new Date())}` : undefined} />
        <StatTile label="Sessions this week" value="—" sub="Phase 3" dim />
        <StatTile label="Spend this week" value="—" sub="Phase 3" dim />
      </div>
      <div className="tile-row" style={{ marginTop: 16, gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        <div className="card">
          <h2 style={{ marginTop: 0, fontSize: 15 }}>Never built</h2>
          <ul className="ink2">{data.never_built.map((t) => <li key={t}>{t}</li>)}</ul>
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0, fontSize: 15 }}>Stale (6+ months)</h2>
          {data.stale.length === 0 && <p className="muted">Nothing stale.</p>}
          <ul className="ink2">{data.stale.map((s) => (
            <li key={s.tag}>{s.tag} <span className="muted">({fmtDate(s.last_done)})</span></li>
          ))}</ul>
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0, fontSize: 15 }}>Claude Code gaps</h2>
          <ul className="ink2">{data.adoption_gaps.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 2: Verify against the live backend locally**

Terminal 1: `DATABASE_URL=sqlite:///dev.db COACH_SECRET_KEY=dev COACH_INGEST_TOKEN=dev COACH_PASSWORD_HASH=$(.venv/bin/python -m apps.coach_web.auth devpw) .venv/bin/uvicorn apps.coach_web.main:app --port 8000` — then ingest a local snapshot: `.venv/bin/python -m src.sweep` will NOT target it (COACH_INGEST_URL points at prod), so instead POST a payload with curl using the shipper outbox pattern, or simply verify against empty-DB rendering (tiles 0, lists render). Empty-DB verification is sufficient here.
Terminal 2: `npm --prefix apps/coach_web/frontend run dev` → open http://localhost:5173, login `devpw`, confirm Overview renders tiles and the three gap cards without console errors. Then stop both and `rm -f dev.db`.

- [ ] **Step 3: Tests + build, commit**

```bash
npm --prefix apps/coach_web/frontend run test
npm --prefix apps/coach_web/frontend run build
git add apps/coach_web/frontend/src/pages/Overview.tsx
git commit -m "feat(frontend): overview page with tiles and gap cards"
```

---

### Task 6: Capabilities page

**Files:**
- Modify: `src/pages/Capabilities.tsx`

**Interfaces:**
- Consumes: `GET /api/capabilities`, `Sparkline`, `fmtDate`.

- [ ] **Step 1: Implement**

`src/pages/Capabilities.tsx`:

```tsx
import { useEffect, useState } from "react";
import { get } from "../api";
import Sparkline from "../components/Sparkline";
import { fmtDate } from "../format";

type TagRow = { tag: string; count: number; last_done: string;
  avg_complexity: number; monthly: { month: string; count: number }[] };

export default function Capabilities() {
  const [data, setData] = useState<{ tags: TagRow[]; never_built: string[] } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get("/api/capabilities").then(setData).catch((e) => setErr(String(e))); }, []);
  if (err) return <p className="muted">Failed to load: {err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <>
      <h1>Capabilities</h1>
      <div className="card" style={{ overflowX: "auto", padding: 0 }}>
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
          <thead>
            <tr className="muted" style={{ textAlign: "left" }}>
              <th style={{ padding: 12 }}>Tag</th>
              <th className="num">Features</th>
              <th>Last done</th>
              <th className="num">Avg complexity</th>
              <th>12-month trend</th>
            </tr>
          </thead>
          <tbody>
            {data.tags.map((t) => (
              <tr key={t.tag} style={{ borderTop: "1px solid var(--grid)" }}>
                <td style={{ padding: 12, fontWeight: 600 }}>{t.tag}</td>
                <td className="num">{t.count}</td>
                <td className="ink2">{fmtDate(t.last_done)}</td>
                <td className="num">{t.avg_complexity.toFixed(1)}</td>
                <td><Sparkline data={t.monthly} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 15 }}>Never built</h2>
        <p className="ink2">{data.never_built.join(" · ") || "—"}</p>
      </div>
    </>
  );
}
```

- [ ] **Step 2: Tests + build, commit**

```bash
npm --prefix apps/coach_web/frontend run test
npm --prefix apps/coach_web/frontend run build
git add apps/coach_web/frontend/src/pages/Capabilities.tsx
git commit -m "feat(frontend): capabilities matrix with trend sparklines"
```

---

### Task 7: Activity page

**Files:**
- Modify: `src/pages/Activity.tsx`

**Interfaces:**
- Consumes: `GET /api/activity?weeks=12`, `WeeklyBars`, `StatTile`, `fmtWeek`.

- [ ] **Step 1: Implement**

`src/pages/Activity.tsx`:

```tsx
import { useEffect, useState } from "react";
import { get } from "../api";
import StatTile from "../components/StatTile";
import WeeklyBars from "../components/WeeklyBars";
import { relDays } from "../format";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

type Activity = {
  weeks: { start: string; commits: number; by_repo: Record<string, number> }[];
  weekday_totals: number[];
  streak: { days: number; last_active: string | null };
  sessions_available: boolean;
};

export default function Activity() {
  const [data, setData] = useState<Activity | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get("/api/activity?weeks=12").then(setData).catch((e) => setErr(String(e))); }, []);
  if (err) return <p className="muted">Failed to load: {err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const max = Math.max(1, ...data.weekday_totals);
  const thisWeek = data.weeks[data.weeks.length - 1];
  const repoTotals: Record<string, number> = {};
  for (const w of data.weeks) {
    for (const [r, n] of Object.entries(w.by_repo)) repoTotals[r] = (repoTotals[r] ?? 0) + n;
  }
  return (
    <>
      <h1>Activity</h1>
      <div className="tile-row">
        <StatTile label="Commits this week" value={thisWeek?.commits ?? 0} />
        <StatTile label="Streak" value={`${data.streak.days}d`}
          sub={data.streak.last_active
            ? `last active ${relDays(data.streak.last_active, new Date())}` : undefined} />
        <StatTile label="Sessions" value="—" sub="Phase 3" dim />
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 15 }}>Commits per week</h2>
        <WeeklyBars data={data.weeks} />
      </div>
      <div className="tile-row" style={{ marginTop: 16, gridTemplateColumns: "1fr 1fr" }}>
        <div className="card">
          <h2 style={{ marginTop: 0, fontSize: 15 }}>By weekday (12 weeks)</h2>
          {data.weekday_totals.map((n, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, margin: "4px 0" }}>
              <span className="muted" style={{ width: 32, fontSize: 12 }}>{DAYS[i]}</span>
              <div style={{ height: 12, borderRadius: 4, background: "var(--series-1)",
                width: `${(n / max) * 100}%`, minWidth: n > 0 ? 4 : 0 }} />
              <span className="num ink2" style={{ fontSize: 12 }}>{n}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <h2 style={{ marginTop: 0, fontSize: 15 }}>By repo (12 weeks)</h2>
          <table style={{ width: "100%", fontSize: 13 }}>
            <tbody>
              {Object.entries(repoTotals).sort((a, b) => b[1] - a[1]).map(([r, n]) => (
                <tr key={r}><td className="ink2">{r}</td>
                  <td className="num" style={{ textAlign: "right" }}>{n}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 2: Tests + build, commit**

```bash
npm --prefix apps/coach_web/frontend run test
npm --prefix apps/coach_web/frontend run build
git add apps/coach_web/frontend/src/pages/Activity.tsx
git commit -m "feat(frontend): activity page with weekly and weekday charts"
```

---

### Task 8: Adoption page

**Files:**
- Modify: `src/pages/Adoption.tsx`

**Interfaces:**
- Consumes: `GET /api/adoption/board`, `StatusChip`, `fmtDate`.

- [ ] **Step 1: Implement**

`src/pages/Adoption.tsx`:

```tsx
import { useEffect, useState } from "react";
import { get } from "../api";
import StatusChip from "../components/StatusChip";
import { fmtDate } from "../format";

type Feature = { name: string; lesson: string; status: string;
  last_used: string | null; source: string; discovered_at: string;
  history: { captured_at: string; status: string }[] };

export default function Adoption() {
  const [data, setData] = useState<{ features: Feature[] } | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get("/api/adoption/board").then(setData).catch((e) => setErr(String(e))); }, []);
  if (err) return <p className="muted">Failed to load: {err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const lessons = [...new Set(data.features.map((f) => f.lesson))];
  const fresh = data.features.filter((f) => f.source === "changelog");
  return (
    <>
      <h1>Claude Code adoption</h1>
      {fresh.length > 0 && (
        <div className="card" style={{ marginBottom: 16 }}>
          <h2 style={{ marginTop: 0, fontSize: 15 }}>New since last check</h2>
          <p className="ink2">{fresh.map((f) => f.name).join(" · ")}</p>
        </div>
      )}
      {lessons.map((lesson) => (
        <div className="card" style={{ marginBottom: 12 }} key={lesson}>
          <h2 style={{ marginTop: 0, fontSize: 15 }}>{lesson}</h2>
          <table style={{ width: "100%", fontSize: 14, borderCollapse: "collapse" }}>
            <tbody>
              {data.features.filter((f) => f.lesson === lesson).map((f) => (
                <tr key={f.name} style={{ borderTop: "1px solid var(--grid)" }}>
                  <td style={{ padding: "8px 0", width: "45%" }}>{f.name}</td>
                  <td><StatusChip status={f.status} /></td>
                  <td className="ink2" style={{ textAlign: "right" }}>
                    {f.last_used ? `last used ${fmtDate(f.last_used)}` : ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </>
  );
}
```

- [ ] **Step 2: Tests + build, commit**

```bash
npm --prefix apps/coach_web/frontend run test
npm --prefix apps/coach_web/frontend run build
git add apps/coach_web/frontend/src/pages/Adoption.tsx
git commit -m "feat(frontend): adoption status board grouped by lesson"
```

---

### Task 9: Cost and Goals & Coach empty states

**Files:**
- Modify: `src/pages/Cost.tsx`, `src/pages/Goals.tsx`

**Interfaces:**
- Consumes: `Empty` component.

- [ ] **Step 1: Implement**

`src/pages/Cost.tsx`:

```tsx
import Empty from "../components/Empty";

export default function Cost() {
  return (
    <>
      <h1>Cost</h1>
      <Empty title="No cost data yet"
        body="Token and spend tracking arrives in Phase 3 — the local sweep will parse Claude Code transcripts and ship daily cost rollups." />
    </>
  );
}
```

`src/pages/Goals.tsx`:

```tsx
import Empty from "../components/Empty";

export default function Goals() {
  return (
    <>
      <h1>Goals & Coach</h1>
      <Empty title="Coach not wired up yet"
        body="Weekly LLM briefs land in Phase 4; goals, check-offs and notes in Phase 5. For now, the Overview page's gap lists are the coach." />
    </>
  );
}
```

- [ ] **Step 2: Tests + build, commit**

```bash
npm --prefix apps/coach_web/frontend run test
npm --prefix apps/coach_web/frontend run build
git add apps/coach_web/frontend/src/pages/Cost.tsx apps/coach_web/frontend/src/pages/Goals.tsx
git commit -m "feat(frontend): cost and goals empty states"
```

---

### Task 10: FastAPI serves the SPA

**Files:**
- Modify: `apps/coach_web/main.py`
- Test: `tests/web/test_spa_serving.py`

**Interfaces:**
- Produces: when `apps/coach_web/frontend/dist/` exists, `GET /` and any non-`/api` path serve the SPA (`index.html` fallback for client routes; real files served directly). `/api/*` and `/api/health` behavior unchanged. When `dist/` is absent (dev/test), `/` returns 404 and nothing else changes.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_spa_serving.py`:

```python
from pathlib import Path

from fastapi.testclient import TestClient

from apps.coach_web.auth import hash_password
from apps.coach_web.config import Settings
from apps.coach_web.main import create_app


def make_client(dist: Path | None):
    settings = Settings(database_url="sqlite+pysqlite:///:memory:",
                        ingest_token="t", password_hash=hash_password("p"),
                        secret_key="s")
    app = create_app(settings, spa_dist=dist)
    return TestClient(app, base_url="https://testserver")


def test_no_dist_root_404(tmp_path):
    c = make_client(None)
    assert c.get("/").status_code == 404
    assert c.get("/api/health").status_code == 200


def test_spa_serves_index_and_fallback(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>coach</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    c = make_client(dist)
    assert "coach" in c.get("/").text
    assert "coach" in c.get("/capabilities").text        # client route fallback
    assert c.get("/assets/app.js").text.startswith("console")
    assert c.get("/api/health").json() == {"status": "ok"}
    assert c.get("/api/nope").status_code == 404          # api never falls back


def test_spa_fallback_does_not_leak_dotfiles(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>coach</html>")
    c = make_client(dist)
    resp = c.get("/../../etc/passwd")
    assert "coach" in resp.text or resp.status_code in (404, 403)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_spa_serving.py -v`
Expected: FAIL (create_app has no spa_dist param)

- [ ] **Step 3: Implement**

In `apps/coach_web/main.py`: change the signature to `create_app(settings: Settings, spa_dist: Path | None = None)`; default the module-level app to the real dist dir. After all router includes, add:

```python
    if spa_dist is None:
        default_dist = Path(__file__).parent / "frontend" / "dist"
        spa_dist = default_dist if default_dist.is_dir() else None
    if spa_dist is not None and Path(spa_dist).is_dir():
        dist = Path(spa_dist).resolve()
        index = dist / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            candidate = (dist / full_path).resolve()
            if (full_path and candidate.is_file()
                    and candidate.is_relative_to(dist)):
                return FileResponse(candidate)
            return FileResponse(index)
```

Imports to add in `main.py`: `from pathlib import Path`, `from fastapi import HTTPException`, `from fastapi.responses import FileResponse`.
Note: the explicit `spa_dist=dist_pathlib_or_None` test seam matters because tests must not depend on whether a real `dist/` exists in the repo. The `full_path.startswith("api/")` guard keeps unknown API paths as clean 404s. The `resolve()` + `is_relative_to` pair blocks path traversal.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web -q`
Expected: PASS (including all pre-existing tests — the catch-all is registered after API routers, so `/api/*` routes still win).

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/main.py tests/web/test_spa_serving.py
git commit -m "feat(web): serve SPA with client-route fallback"
```

---

### Task 11: Dockerfile, deploy, live verification

**Files:**
- Create: `Dockerfile`, `.dockerignore`
- Modify: `railway.json`

**Interfaces:**
- Consumes: everything. Railway service `coach-web` — follow the project skill `.claude/skills/deploy-coach-web/SKILL.md` for commands and topology (deploy from repo root, `railway up --service coach-web --detach`).

- [ ] **Step 1: Dockerfile**

```dockerfile
FROM node:22-slim AS frontend
WORKDIR /build
COPY apps/coach_web/frontend/package.json apps/coach_web/frontend/package-lock.json ./
RUN npm ci
COPY apps/coach_web/frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY apps/ apps/
COPY taxonomy.yaml .
COPY --from=frontend /build/dist apps/coach_web/frontend/dist
CMD alembic -c apps/coach_web/alembic.ini upgrade head && uvicorn apps.coach_web.main:app --host 0.0.0.0 --port $PORT
```

`.dockerignore`:

```
.env
.venv
data
docs
.claude
.superpowers
.git
apps/coach_web/frontend/node_modules
apps/coach_web/frontend/dist
tests
src
launchd
__pycache__
```

- [ ] **Step 2: railway.json — switch builder**

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"},
  "deploy": {"healthcheckPath": "/api/health"}
}
```

(startCommand moves into the Dockerfile CMD; remove it here so the image is self-contained.)

- [ ] **Step 3: Local docker sanity (only if docker is available)**

Run `docker --version`; if Docker is not installed locally, skip this step — Railway builds the image — and rely on Step 5's live verification.

```bash
docker build -t coach-web-test .
```

Expected: both stages build; final image contains `apps/coach_web/frontend/dist/index.html`.

- [ ] **Step 4: Commit and deploy**

```bash
git add Dockerfile .dockerignore railway.json
git commit -m "feat(deploy): multi-stage docker build serving SPA"
railway up --service coach-web --detach
```

Poll: `railway deployment list --service coach-web --limit 1 --json` → `.status` until `SUCCESS` (or `FAILED` → `railway logs --service coach-web --build`).

- [ ] **Step 5: Live verification**

```bash
curl -s https://coach-web-production-1f04.up.railway.app/api/health
curl -s https://coach-web-production-1f04.up.railway.app/ | head -5
curl -s https://coach-web-production-1f04.up.railway.app/capabilities | head -5
```

Expected: health JSON; both page requests return the SPA's `index.html`. Then browser check: open the domain, log in, walk all six pages — Overview shows real tiles and gap lists, Capabilities shows the matrix with sparklines, Activity shows weekly bars, Adoption shows the status board, Cost/Goals show their empty states, and dark mode (OS setting) renders correctly.

- [ ] **Step 6: Final commit if anything was adjusted**

```bash
git add -A
git commit -m "chore: phase 2 deploy adjustments"
```

Only if there are actual tracked changes.

---

## Self-Review Notes

- Spec coverage (Phase 2 = "Dashboard UI over ingested data"): Overview (tiles, gaps, freshness stamp) ✓ Task 5; Capabilities (live matrix, trendlines, never-built/stale) ✓ Tasks 2+6; Activity (weekly charts, day-of-week, streaks, per-repo) ✓ Tasks 2+7; Adoption (status board, changelog-newcomer strip renders when Phase 4 data arrives) ✓ Task 8; Cost + Goals pages exist as honest empty states ✓ Task 9; mobile-responsive ✓ shell CSS Task 3; "data as of" staleness stamp ✓ Task 5; charts follow dataviz system (single-series/no legend, ink tokens for text, thin marks, tabular-nums, validated palette values) ✓ Tasks 3-4.
- Placeholder scan: none — every code step carries full code; Task 5 Step 2's dev-verification explicitly allows empty-DB rendering.
- Type consistency: endpoint JSON shapes in Task 2's Interfaces match the TS types declared in Tasks 5-8 field-for-field; `create_app(settings, spa_dist=None)` signature change (Task 10) is backward-compatible with every existing call site (all pass settings only).
- Deliberate scope cuts (YAGNI): no column sorting on the matrix (count-desc default is the spec's own ordering), no logout button (deferred with M9), no client-side caching layer.
