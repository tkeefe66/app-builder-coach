# Overall Grade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a rubric-gated "overall grade" (career-ladder level + progress + gap coaching) to the Overview page, per `docs/superpowers/specs/2026-08-03-overall-grade-design.md`.

**Architecture:** A checked-in `rubric.yaml` (repo root, loaded like `taxonomy.yaml`) defines tag tiers, per-level gates, and affinity pairs. A pure module `apps/coach_web/grade.py` scores feature-unit rows against the rubric. `/api/overview` gains a `grade` field; a new `GradeCard` component renders it. No DB changes, no migrations, no schema-version bump, no collector changes.

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy / PyYAML / pytest; React 19 + TypeScript strict + vitest. No new dependencies.

## Global Constraints

- Python is **3.11** (`str | None` syntax everywhere). If building a fresh venv: `python3.11 -m venv .venv` — plain `python3` grabs system 3.9 and explodes (see `docs/HANDOFF.md`).
- Backend tests: `python3.11 -m pytest <path> -v` from repo root (conftest defaults `DATABASE_URL` to in-memory sqlite).
- Frontend: TS strict; tests `npm --prefix apps/coach_web/frontend test`; typecheck+build `npm --prefix apps/coach_web/frontend run build`. Fresh worktrees need `npm --prefix apps/coach_web/frontend install` first.
- No new pip or npm dependencies. Colors only from existing `tokens.css` variables.
- Rubric thresholds live in `rubric.yaml` and are data, not contract — code must not hardcode them (tests construct their own small rubrics).
- Stale threshold is 180 days with a 0.5 multiplier — reuse constants, do not scatter literals.
- Commit after each task with the exact message given.

---

### Task 1: rubric.yaml + loader with fail-fast validation

**Files:**
- Create: `rubric.yaml` (repo root)
- Create: `apps/coach_web/rubric.py`
- Modify: `apps/coach_web/main.py` (fail-fast call in `create_app`, right after `_check_prod_secrets(settings)`)
- Test: `tests/web/test_rubric.py`

**Interfaces:**
- Consumes: `apps/coach_web/taxonomy.py::all_tags() -> list[str]` (existing).
- Produces: `rubric.load() -> Rubric` (lru-cached), dataclasses `Gate(min_count: int, min_avg_complexity: float | None, within_days: int | None)`, `Level(name: str, label: str, breadth: int, gates: dict[str, Gate], noncore: tuple[int, int] | None)`, `Rubric(tiers: dict[str, str], levels: tuple[Level, ...], pairs_with: dict[str, list[str]])`, exception `RubricError`. Task 2 consumes these types; Task 3 calls `load()`.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_rubric.py`:

```python
import pytest

from apps.coach_web import rubric as rubric_mod
from apps.coach_web import taxonomy
from apps.coach_web.main import create_app
from apps.coach_web.rubric import RubricError, _parse


def _minimal_raw():
    """Smallest valid raw rubric covering every taxonomy tag."""
    tags = taxonomy.all_tags()
    return {
        "tiers": {"core": tags[:1], "standard": tags[1:2], "specialty": tags[2:]},
        "levels": [{"name": "newcomer", "label": "Newcomer"}],
        "pairs_with": {},
    }


def test_real_rubric_loads_and_covers_taxonomy():
    r = rubric_mod.load()
    assert set(r.tiers) == set(taxonomy.all_tags())
    assert [lv.name for lv in r.levels] == [
        "newcomer", "beginner", "junior", "mid", "senior"]
    core = [t for t, tier in r.tiers.items() if tier == "core"]
    assert len(core) == 10
    # Mid gates every core tag; senior adds recency + noncore requirements.
    mid = r.levels[3]
    assert set(mid.gates) == set(core)
    senior = r.levels[4]
    assert senior.noncore == (8, 3)
    assert all(g.within_days == 365 for g in senior.gates.values())
    for tag, related in r.pairs_with.items():
        assert tag in r.tiers
        assert all(t in r.tiers for t in related)


def test_unknown_tag_in_tiers_rejected():
    raw = _minimal_raw()
    raw["tiers"]["core"] = ["not-a-real-tag"]
    with pytest.raises(RubricError, match="unknown tag"):
        _parse(raw)


def test_taxonomy_tag_missing_a_tier_rejected():
    raw = _minimal_raw()
    raw["tiers"]["specialty"] = raw["tiers"]["specialty"][:-1]
    with pytest.raises(RubricError, match="missing a tier"):
        _parse(raw)


def test_tag_in_two_tiers_rejected():
    raw = _minimal_raw()
    raw["tiers"]["standard"] = raw["tiers"]["standard"] + raw["tiers"]["core"][:1]
    with pytest.raises(RubricError, match="two tiers"):
        _parse(raw)


def test_empty_levels_rejected():
    raw = _minimal_raw()
    raw["levels"] = []
    with pytest.raises(RubricError, match="non-empty"):
        _parse(raw)


def test_gate_on_unknown_tag_rejected():
    raw = _minimal_raw()
    raw["levels"] = [{"name": "x", "label": "X",
                      "gates": {"nope": {"min_count": 1}}}]
    with pytest.raises(RubricError, match="unknown tag"):
        _parse(raw)


def test_pairs_with_unknown_tag_rejected():
    raw = _minimal_raw()
    raw["pairs_with"] = {"nope": []}
    with pytest.raises(RubricError, match="pairs_with"):
        _parse(raw)


def test_create_app_fails_fast_on_bad_rubric(monkeypatch, settings):
    def boom():
        raise RubricError("rubric.yaml: broken")
    monkeypatch.setattr(rubric_mod, "load", boom)
    with pytest.raises(RubricError, match="broken"):
        create_app(settings)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/web/test_rubric.py -v`
Expected: FAIL / ERROR with `ModuleNotFoundError: No module named 'apps.coach_web.rubric'`

- [ ] **Step 3: Write rubric.yaml**

Create `rubric.yaml` at repo root:

```yaml
# rubric.yaml — grading rubric for the overall grade. Data, not contract:
# tune thresholds freely; every tag must exist in taxonomy.yaml.
tiers:
  core:
    - auth
    - api-backend
    - data-modeling
    - db-migrations
    - testing-depth
    - error-handling
    - frontend-spa
    - deploy-docker
    - deploy-infra
    - privacy-security
  standard:
    - api-client
    - background-jobs
    - caching
    - cli-tooling
    - state-machines
    - llm-integration
    - webhooks
    - frontend-ssr
    - agents-automation
  specialty:
    - charts-svg
    - email-ingestion
    - llm-cost-control
    - payments-money
    - scraping
    - websockets-sse

levels:
  - name: newcomer
    label: Newcomer
  - name: beginner
    label: Beginner
    breadth: 3
  - name: junior
    label: Junior Engineer
    breadth: 10
    gates:
      api-backend: {min_count: 3}
      data-modeling: {min_count: 3}
      frontend-spa: {min_count: 2}
      testing-depth: {min_count: 2}
      db-migrations: {min_count: 1}
      auth: {min_count: 1}
      error-handling: {min_count: 1}
  - name: mid
    label: Mid-Level Engineer
    breadth: 18
    gates:
      auth: {min_count: 5, min_avg_complexity: 3.0}
      api-backend: {min_count: 10, min_avg_complexity: 3.0}
      data-modeling: {min_count: 10, min_avg_complexity: 3.0}
      db-migrations: {min_count: 5, min_avg_complexity: 3.0}
      testing-depth: {min_count: 8, min_avg_complexity: 3.5}
      error-handling: {min_count: 5, min_avg_complexity: 3.0}
      frontend-spa: {min_count: 10, min_avg_complexity: 3.0}
      deploy-docker: {min_count: 2}
      deploy-infra: {min_count: 3, min_avg_complexity: 3.0}
      privacy-security: {min_count: 5, min_avg_complexity: 3.0}
  - name: senior
    label: Senior Engineer
    breadth: 24
    noncore: {tags: 8, min_count: 3}
    gates:
      auth: {min_count: 8, min_avg_complexity: 3.5, within_days: 365}
      api-backend: {min_count: 8, min_avg_complexity: 3.5, within_days: 365}
      data-modeling: {min_count: 8, min_avg_complexity: 3.5, within_days: 365}
      db-migrations: {min_count: 8, min_avg_complexity: 3.5, within_days: 365}
      testing-depth: {min_count: 8, min_avg_complexity: 3.5, within_days: 365}
      error-handling: {min_count: 8, min_avg_complexity: 3.5, within_days: 365}
      frontend-spa: {min_count: 8, min_avg_complexity: 3.5, within_days: 365}
      deploy-docker: {min_count: 8, min_avg_complexity: 3.5, within_days: 365}
      deploy-infra: {min_count: 8, min_avg_complexity: 3.5, within_days: 365}
      privacy-security: {min_count: 8, min_avg_complexity: 3.5, within_days: 365}

pairs_with:
  websockets-sse: [api-backend, frontend-spa]
  deploy-docker: [deploy-infra, api-backend]
  deploy-infra: [deploy-docker, api-backend]
  auth: [api-backend, frontend-spa]
  db-migrations: [data-modeling]
  testing-depth: [api-backend, frontend-spa]
  error-handling: [api-backend]
  privacy-security: [auth, api-backend]
  payments-money: [api-backend, data-modeling]
  webhooks: [api-backend]
  caching: [api-backend]
  background-jobs: [deploy-infra, api-backend]
  frontend-ssr: [frontend-spa]
  charts-svg: [frontend-spa]
  api-client: [api-backend]
  llm-cost-control: [llm-integration]
  llm-integration: [api-backend]
  email-ingestion: [api-client]
  scraping: [api-client]
  state-machines: [data-modeling]
  api-backend: [data-modeling]
  data-modeling: [api-backend]
  frontend-spa: [api-backend]
```

- [ ] **Step 4: Write the loader**

Create `apps/coach_web/rubric.py`:

```python
"""Grading rubric, read from the repo's rubric.yaml (single source)."""
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from . import taxonomy

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

TIER_NAMES = ("core", "standard", "specialty")


class RubricError(ValueError):
    """rubric.yaml is invalid; raised at startup, never at request time."""


@dataclass(frozen=True)
class Gate:
    min_count: int
    min_avg_complexity: float | None = None
    within_days: int | None = None


@dataclass(frozen=True)
class Level:
    name: str
    label: str
    breadth: int = 0
    gates: dict[str, Gate] = field(default_factory=dict)
    noncore: tuple[int, int] | None = None  # (distinct tags, min builds each)


@dataclass(frozen=True)
class Rubric:
    tiers: dict[str, str]            # tag -> core | standard | specialty
    levels: tuple[Level, ...]        # ordered, lowest first
    pairs_with: dict[str, list[str]]


def _parse(raw: dict) -> Rubric:
    known = set(taxonomy.all_tags())
    tiers: dict[str, str] = {}
    for tier in TIER_NAMES:
        for tag in (raw.get("tiers", {}).get(tier) or []):
            if tag not in known:
                raise RubricError(
                    f"rubric.yaml: unknown tag {tag!r} in tiers.{tier}")
            if tag in tiers:
                raise RubricError(
                    f"rubric.yaml: tag {tag!r} appears in two tiers")
            tiers[tag] = tier
    missing = known - set(tiers)
    if missing:
        raise RubricError(
            f"rubric.yaml: taxonomy tags missing a tier: {sorted(missing)}")

    raw_levels = raw.get("levels") or []
    if not raw_levels:
        raise RubricError("rubric.yaml: levels must be a non-empty list")
    levels: list[Level] = []
    for lv in raw_levels:
        gates: dict[str, Gate] = {}
        for tag, g in (lv.get("gates") or {}).items():
            if tag not in known:
                raise RubricError(
                    f"rubric.yaml: unknown tag {tag!r} in level "
                    f"{lv.get('name')!r} gates")
            gates[tag] = Gate(
                min_count=int(g["min_count"]),
                min_avg_complexity=(float(g["min_avg_complexity"])
                                    if "min_avg_complexity" in g else None),
                within_days=(int(g["within_days"])
                             if "within_days" in g else None))
        noncore = None
        if "noncore" in lv:
            noncore = (int(lv["noncore"]["tags"]),
                       int(lv["noncore"]["min_count"]))
        levels.append(Level(name=lv["name"], label=lv["label"],
                            breadth=int(lv.get("breadth", 0)),
                            gates=gates, noncore=noncore))

    pairs: dict[str, list[str]] = {}
    for tag, related in (raw.get("pairs_with") or {}).items():
        bad = [t for t in [tag, *(related or [])] if t not in known]
        if bad:
            raise RubricError(
                f"rubric.yaml: unknown tag(s) in pairs_with: {bad}")
        pairs[tag] = list(related or [])

    return Rubric(tiers=tiers, levels=tuple(levels), pairs_with=pairs)


@lru_cache(maxsize=1)
def load() -> Rubric:
    return _parse(yaml.safe_load((REPO_ROOT / "rubric.yaml").read_text()))
```

- [ ] **Step 5: Wire fail-fast into the app factory**

In `apps/coach_web/main.py`, inside `create_app`, immediately after the line `_check_prod_secrets(settings)`, add:

```python
    from . import rubric
    rubric.load()  # invalid rubric.yaml must prevent boot, not break requests
```

(Use exactly `from . import rubric` + `rubric.load()` — the test monkeypatches `rubric.load`, which only works through the module attribute.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/web/test_rubric.py -v`
Expected: all PASS

- [ ] **Step 7: Run the full backend suite (no regressions)**

Run: `python3.11 -m pytest tests/ -q`
Expected: all PASS (139 existing + new)

- [ ] **Step 8: Commit**

```bash
git add rubric.yaml apps/coach_web/rubric.py apps/coach_web/main.py tests/web/test_rubric.py
git commit -m "feat(grade): rubric.yaml + validated loader with startup fail-fast"
```

---

### Task 2: grade.py scoring engine (pure functions)

**Files:**
- Create: `apps/coach_web/grade.py`
- Test: `tests/web/test_grade.py`

**Interfaces:**
- Consumes: `Gate`, `Level`, `Rubric` from `apps/coach_web/rubric.py` (Task 1).
- Produces: `compute_grade(rows, rubric, today) -> dict | None` where `rows` is an iterable of `(repo: str, date_iso: str, tags: list[str], complexity: int)` tuples. Return shape (consumed by Task 3):
  `{"level": str, "level_label": str, "next_level": str | None, "next_label": str | None, "percent_to_next": int, "gaps": [{"tag", "have": {"count", "avg_complexity", "last_done"}, "need": {"min_count", "min_avg_complexity", "within_days"}, "best_fit_repo"}]}`.
  Also exposes helpers `tag_stats`, `gate_fraction`, `best_fit_repo` for tests.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_grade.py`:

```python
from datetime import date, timedelta

from apps.coach_web.grade import (best_fit_repo, compute_grade, gate_fraction,
                                  tag_stats)
from apps.coach_web.rubric import Gate, Level, Rubric

TODAY = date(2026, 8, 3)
RECENT = (TODAY - timedelta(days=10)).isoformat()
STALE = (TODAY - timedelta(days=200)).isoformat()


def make_rubric():
    return Rubric(
        tiers={"auth": "core", "api-backend": "core",
               "caching": "standard", "websockets-sse": "specialty"},
        levels=(
            Level(name="newcomer", label="Newcomer"),
            Level(name="junior", label="Junior", breadth=2,
                  gates={"api-backend": Gate(min_count=2)}),
            Level(name="mid", label="Mid", breadth=3,
                  gates={"auth": Gate(min_count=4, min_avg_complexity=3.0),
                         "api-backend": Gate(min_count=4)}),
            Level(name="senior", label="Senior", breadth=4, noncore=(2, 2),
                  gates={"auth": Gate(min_count=6, within_days=365)}),
        ),
        pairs_with={"websockets-sse": ["api-backend"]},
    )


def test_tag_stats_counts_avg_and_last_done():
    rows = [("a", "2026-01-01", ["auth"], 2),
            ("a", "2026-03-01", ["auth", "caching"], 5),
            ("b", "2026-02-01", ["auth"], 4)]
    s = tag_stats(rows)
    assert s["auth"]["count"] == 3
    assert s["auth"]["avg_complexity"] == 3.7  # (2+5+4)/3 rounded
    assert s["auth"]["last_done"] == "2026-03-01"
    assert s["caching"]["count"] == 1


def test_gate_fraction_partial_count():
    s = tag_stats([("a", RECENT, ["auth"], 3), ("a", RECENT, ["auth"], 3)])
    assert gate_fraction(Gate(min_count=4), s.get("auth"), TODAY) == 0.5


def test_gate_fraction_complexity_shortfall():
    s = tag_stats([("a", RECENT, ["auth"], 2), ("a", RECENT, ["auth"], 1)])
    # count 2/2 = 1.0, avg cx 1.5/3.0 = 0.5
    g = Gate(min_count=2, min_avg_complexity=3.0)
    assert gate_fraction(g, s.get("auth"), TODAY) == 0.5


def test_gate_fraction_stale_halves():
    s = tag_stats([("a", STALE, ["auth"], 3), ("a", STALE, ["auth"], 3)])
    assert gate_fraction(Gate(min_count=2), s.get("auth"), TODAY) == 0.5


def test_gate_fraction_missing_tag_is_zero():
    assert gate_fraction(Gate(min_count=1), None, TODAY) == 0.0


def test_compute_grade_empty_rows_is_none():
    assert compute_grade([], make_rubric(), TODAY) is None


def test_attains_junior_with_progress_and_sorted_gaps():
    rows = [("alpha", RECENT, ["api-backend"], 3),
            ("alpha", RECENT, ["api-backend"], 3),
            ("beta", RECENT, ["auth"], 3)]
    g = compute_grade(rows, make_rubric(), TODAY)
    assert g["level"] == "junior"
    assert g["next_level"] == "mid"
    # mid fractions: auth 1/4*1=.25, api 2/4=.5, breadth 2/3=.667 -> 47%
    assert g["percent_to_next"] == 47
    assert [x["tag"] for x in g["gaps"]] == ["auth", "api-backend"]  # worst first
    assert g["gaps"][0]["have"]["count"] == 1
    assert g["gaps"][0]["need"]["min_count"] == 4


def test_stale_core_demotes():
    rows = [("alpha", STALE, ["api-backend"], 3),
            ("alpha", STALE, ["api-backend"], 3),
            ("beta", RECENT, ["auth"], 3)]
    g = compute_grade(rows, make_rubric(), TODAY)
    assert g["level"] == "newcomer"  # junior gate at 1.0*0.5 = 0.5 < 1


def test_top_level_has_no_next():
    rows = ([("a", RECENT, ["auth"], 4)] * 6
            + [("a", RECENT, ["api-backend"], 4)] * 4
            + [("a", RECENT, ["caching"], 4)] * 2
            + [("a", RECENT, ["websockets-sse"], 4)] * 2)
    g = compute_grade(rows, make_rubric(), TODAY)
    assert g["level"] == "senior"
    assert g["next_level"] is None and g["next_label"] is None
    assert g["percent_to_next"] == 100
    assert g["gaps"] == []


def test_never_built_gap_shape():
    rows = [("alpha", RECENT, ["api-backend"], 3),
            ("alpha", RECENT, ["api-backend"], 3),
            ("beta", RECENT, ["caching"], 3)]
    g = compute_grade(rows, make_rubric(), TODAY)
    auth_gap = next(x for x in g["gaps"] if x["tag"] == "auth")
    assert auth_gap["have"] == {"count": 0, "avg_complexity": None,
                                "last_done": None}


def test_best_fit_prefers_repo_with_most_recent_related_work():
    rubric = make_rubric()
    rows = [("alpha", RECENT, ["api-backend"], 3),
            ("alpha", RECENT, ["api-backend"], 3),
            ("beta", RECENT, ["api-backend"], 3)]
    assert best_fit_repo("websockets-sse", rubric, rows, TODAY) == "alpha"


def test_best_fit_tie_breaks_on_recency():
    rubric = make_rubric()
    older = (TODAY - timedelta(days=20)).isoformat()
    rows = [("alpha", older, ["api-backend"], 3),
            ("beta", RECENT, ["api-backend"], 3)]
    assert best_fit_repo("websockets-sse", rubric, rows, TODAY) == "beta"


def test_best_fit_falls_back_to_most_recent_repo():
    rubric = make_rubric()
    rows = [("alpha", "2026-01-01", ["caching"], 3),
            ("beta", RECENT, ["caching"], 3)]
    # auth has no pairs_with entry -> fallback
    assert best_fit_repo("auth", rubric, rows, TODAY) == "beta"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/web/test_grade.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'apps.coach_web.grade'`

- [ ] **Step 3: Write the implementation**

Create `apps/coach_web/grade.py`:

```python
"""Overall-grade scoring. Pure functions: rows + rubric + today -> grade dict.

rows are (repo, date_iso, tags, complexity) tuples from feature units.
"""
from datetime import date, timedelta

from .rubric import Gate, Level, Rubric

STALE_DAYS = 180        # matches the dashboard's stale threshold
STALE_MULTIPLIER = 0.5  # stale skills count at half credit


def _older_than(iso_day: str, days: int, today: date) -> bool:
    return iso_day <= (today - timedelta(days=days)).isoformat()


def tag_stats(rows) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for _repo, d, tags, cx in rows:
        for t in tags or []:
            e = stats.setdefault(t, {"count": 0, "cx_sum": 0, "last_done": d})
            e["count"] += 1
            e["cx_sum"] += cx
            if d > e["last_done"]:
                e["last_done"] = d
    for e in stats.values():
        e["avg_complexity"] = round(e.pop("cx_sum") / e["count"], 1)
    return stats


def gate_fraction(gate: Gate, stat: dict | None, today: date) -> float:
    if not stat or stat["count"] == 0:
        return 0.0
    frac = min(1.0, stat["count"] / gate.min_count)
    if gate.min_avg_complexity is not None:
        frac *= min(1.0, stat["avg_complexity"] / gate.min_avg_complexity)
    if _older_than(stat["last_done"], STALE_DAYS, today):
        frac *= STALE_MULTIPLIER
    if (gate.within_days is not None
            and _older_than(stat["last_done"], gate.within_days, today)):
        frac *= STALE_MULTIPLIER
    return frac


def _level_fractions(level: Level, stats: dict, rubric: Rubric,
                     today: date) -> list[float]:
    fracs = [gate_fraction(g, stats.get(t), today)
             for t, g in sorted(level.gates.items())]
    if level.breadth:
        fracs.append(min(1.0, len(stats) / level.breadth))
    if level.noncore:
        need_tags, need_count = level.noncore
        n = sum(1 for t, e in stats.items()
                if rubric.tiers.get(t) != "core" and e["count"] >= need_count)
        fracs.append(min(1.0, n / need_tags))
    return fracs


def best_fit_repo(tag: str, rubric: Rubric, rows, today: date) -> str:
    """Repo with the most recent related work; deterministic fallback."""
    pairs = set(rubric.pairs_with.get(tag, []))
    scores: dict[str, list] = {}  # repo -> [count, latest_date]
    for repo, d, tags, _cx in rows:
        if (pairs and not _older_than(d, STALE_DAYS, today)
                and pairs & set(tags or [])):
            e = scores.setdefault(repo, [0, ""])
            e[0] += 1
            e[1] = max(e[1], d)
    if scores:
        return max(scores.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]
    return max(rows, key=lambda r: r[1])[0]


def _gaps(level: Level, stats: dict, rubric: Rubric, rows,
          today: date) -> list[dict]:
    out = []
    for tag, gate in level.gates.items():
        frac = gate_fraction(gate, stats.get(tag), today)
        if frac >= 1.0:
            continue
        stat = stats.get(tag)
        have = ({"count": stat["count"],
                 "avg_complexity": stat["avg_complexity"],
                 "last_done": stat["last_done"]} if stat
                else {"count": 0, "avg_complexity": None, "last_done": None})
        out.append((frac, {
            "tag": tag,
            "have": have,
            "need": {"min_count": gate.min_count,
                     "min_avg_complexity": gate.min_avg_complexity,
                     "within_days": gate.within_days},
            "best_fit_repo": best_fit_repo(tag, rubric, rows, today),
        }))
    out.sort(key=lambda p: (p[0], p[1]["tag"]))  # worst first, stable
    return [g for _f, g in out]


def compute_grade(rows, rubric: Rubric, today: date) -> dict | None:
    rows = list(rows)
    if not rows:
        return None
    stats = tag_stats(rows)

    attained_idx = 0
    for i, lvl in enumerate(rubric.levels):
        if all(f >= 1.0 for f in _level_fractions(lvl, stats, rubric, today)):
            attained_idx = i
        else:
            break
    attained = rubric.levels[attained_idx]

    if attained_idx + 1 < len(rubric.levels):
        nxt = rubric.levels[attained_idx + 1]
        fracs = _level_fractions(nxt, stats, rubric, today)
        percent = round(100 * sum(fracs) / len(fracs))
        gaps = _gaps(nxt, stats, rubric, rows, today)
        next_level, next_label = nxt.name, nxt.label
    else:
        percent, gaps, next_level, next_label = 100, [], None, None

    return {"level": attained.name, "level_label": attained.label,
            "next_level": next_level, "next_label": next_label,
            "percent_to_next": percent, "gaps": gaps}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/web/test_grade.py -v`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/grade.py tests/web/test_grade.py
git commit -m "feat(grade): pure scoring engine (gates, decay, gaps, best-fit repo)"
```

---

### Task 3: expose grade on /api/overview

**Files:**
- Modify: `apps/coach_web/api.py` (imports + `overview()` only)
- Test: `tests/web/test_api_grade.py`

**Interfaces:**
- Consumes: `compute_grade(rows, rubric, today)` from Task 2; `rubric.load()` from Task 1; existing `make_rich_payload`/`login`/`AUTH` test helpers.
- Produces: `/api/overview` response gains key `"grade"` with the Task 2 shape, or `null` when the DB has no feature units. Frontend (Task 4) relies on exactly this.

- [ ] **Step 1: Write the failing tests**

Create `tests/web/test_api_grade.py`:

```python
from datetime import date

from tests.web.test_api_phase2 import login, make_rich_payload
from tests.web.test_ingest import AUTH


def test_overview_grade_null_when_no_units(client):
    login(client)
    assert client.get("/api/overview").json()["grade"] is None


def test_overview_grade_present_with_shape(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    g = client.get("/api/overview").json()["grade"]
    # rich payload = 2 units (auth recent, caching 200d old): breadth 2,
    # below beginner's 3 -> newcomer, progressing toward beginner.
    assert g["level"] == "newcomer"
    assert g["level_label"] == "Newcomer"
    assert g["next_level"] == "beginner"
    assert 0 < g["percent_to_next"] < 100
    assert g["gaps"] == []  # beginner is breadth-only, no per-tag gates
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3.11 -m pytest tests/web/test_api_grade.py -v`
Expected: FAIL with `KeyError: 'grade'`

- [ ] **Step 3: Wire into the endpoint**

In `apps/coach_web/api.py`:

1. Extend the import line `from . import aggregate, models, taxonomy` to:

```python
from . import aggregate, grade as grade_mod, models, rubric, taxonomy
```

2. In `overview()`, after the `adoption_gaps` block and before the `return`, add:

```python
    unit_rows = [(r.repo, r.date, r.tags, r.complexity)
                 for r in db.scalars(select(models.FeatureUnit))]
    grade = grade_mod.compute_grade(unit_rows, rubric.load(), today)
```

3. Add `"grade": grade,` to the returned dict (after `"adoption_gaps": adoption_gaps`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3.11 -m pytest tests/web/test_api_grade.py tests/web/test_api_phase2.py -v`
Expected: all PASS (existing overview test must stay green)

- [ ] **Step 5: Run the full backend suite**

Run: `python3.11 -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add apps/coach_web/api.py tests/web/test_api_grade.py
git commit -m "feat(grade): grade field on /api/overview"
```

---

### Task 4: GradeCard on Overview + full verification

**Files:**
- Create: `apps/coach_web/frontend/src/components/GradeCard.tsx`
- Modify: `apps/coach_web/frontend/src/index.css` (append progress-bar rules)
- Modify: `apps/coach_web/frontend/src/pages/Overview.tsx` (full replacement below)
- Test: `apps/coach_web/frontend/src/__tests__/GradeCard.test.tsx`

**Interfaces:**
- Consumes: the `grade` field from Task 3 (shape above).
- Produces: `GradeCard({ grade }: { grade: Grade })` default export and exported type `Grade`.

- [ ] **Step 1: Write the failing tests**

Create `apps/coach_web/frontend/src/__tests__/GradeCard.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import GradeCard, { type Grade } from "../components/GradeCard";

const grade: Grade = {
  level: "junior", level_label: "Junior Engineer",
  next_level: "mid", next_label: "Mid-Level Engineer",
  percent_to_next: 72,
  gaps: [
    { tag: "deploy-docker",
      have: { count: 0, avg_complexity: null, last_done: null },
      need: { min_count: 2, min_avg_complexity: null, within_days: null },
      best_fit_repo: "coach-web" },
    { tag: "auth",
      have: { count: 3, avg_complexity: 3.7, last_done: "2026-07-23" },
      need: { min_count: 5, min_avg_complexity: 3.0, within_days: null },
      best_fit_repo: "budget-app" },
  ],
};

describe("GradeCard", () => {
  it("shows empty state when grade is null", () => {
    render(<GradeCard grade={null} />);
    expect(screen.getByText("No data yet.")).toBeInTheDocument();
  });
  it("renders level, progress, gap lines, and caption", () => {
    render(<GradeCard grade={grade} />);
    expect(screen.getByText("Junior Engineer")).toBeInTheDocument();
    expect(screen.getByText("72% to Mid-Level Engineer")).toBeInTheDocument();
    expect(screen.getByText(/deploy-docker \(never built\)/)).toBeInTheDocument();
    expect(screen.getByText(/auth \(3 builds — need 5\+\)/)).toBeInTheDocument();
    expect(screen.getByText(/best fit: coach-web/)).toBeInTheDocument();
    expect(screen.getByText(/not years of experience/)).toBeInTheDocument();
  });
  it("renders top-of-ladder state without a target", () => {
    render(<GradeCard grade={{ ...grade, next_level: null, next_label: null,
      percent_to_next: 100, gaps: [] }} />);
    expect(screen.getByText("Top of the ladder.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix apps/coach_web/frontend test`
Expected: FAIL — cannot resolve `../components/GradeCard`

- [ ] **Step 3: Write the component**

Create `apps/coach_web/frontend/src/components/GradeCard.tsx`:

```tsx
type Gap = {
  tag: string;
  have: { count: number; avg_complexity: number | null; last_done: string | null };
  need: { min_count: number; min_avg_complexity: number | null; within_days: number | null };
  best_fit_repo: string;
};
export type Grade = {
  level: string; level_label: string;
  next_level: string | null; next_label: string | null;
  percent_to_next: number; gaps: Gap[];
} | null;

function gapLine(g: Gap): string {
  if (g.have.count === 0) return `${g.tag} (never built)`;
  const parts = [`${g.have.count} builds — need ${g.need.min_count}+`];
  if (g.need.min_avg_complexity !== null && g.have.avg_complexity !== null
      && g.have.avg_complexity < g.need.min_avg_complexity) {
    parts.push(`avg complexity ${g.have.avg_complexity} — need ${g.need.min_avg_complexity}+`);
  }
  return `${g.tag} (${parts.join("; ")})`;
}

export default function GradeCard({ grade }: { grade: Grade }) {
  if (!grade) {
    return (
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="muted" style={{ fontSize: 13 }}>Overall grade</div>
        <p className="muted">No data yet.</p>
      </div>
    );
  }
  return (
    <div className="card" style={{ marginBottom: 16 }}>
      <div className="muted" style={{ fontSize: 13 }}>Operating at</div>
      <div style={{ fontSize: 28, fontWeight: 700 }}>{grade.level_label}</div>
      {grade.next_label ? (
        <>
          <div className="progress-track" style={{ marginTop: 8 }}>
            <div className="progress-fill"
              style={{ width: `${grade.percent_to_next}%` }} />
          </div>
          <div className="ink2 num" style={{ fontSize: 12, marginTop: 4 }}>
            {grade.percent_to_next}% to {grade.next_label}
          </div>
          {grade.gaps.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div className="muted" style={{ fontSize: 13 }}>
                To reach {grade.next_label}, build:
              </div>
              <ul className="ink2" style={{ margin: "4px 0 0", paddingLeft: 20 }}>
                {grade.gaps.slice(0, 3).map((g) => (
                  <li key={g.tag}>
                    {gapLine(g)}{" "}
                    <span className="muted">best fit: {g.best_fit_repo}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      ) : (
        <div className="ink2" style={{ fontSize: 12, marginTop: 4 }}>
          Top of the ladder.
        </div>
      )}
      <div className="muted" style={{ fontSize: 12, marginTop: 12 }}>
        Based on what you've shipped across skill areas — not years of experience.
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Append progress-bar styles**

Append to `apps/coach_web/frontend/src/index.css`:

```css
.progress-track {
  height: 8px; border-radius: 999px;
  background: var(--grid); overflow: hidden;
}
.progress-fill {
  height: 100%; border-radius: 999px; background: var(--series-1);
}
```

- [ ] **Step 5: Render it on Overview**

Replace `apps/coach_web/frontend/src/pages/Overview.tsx` entirely with:

```tsx
import { useEffect, useState } from "react";
import { get } from "../api";
import GradeCard, { type Grade } from "../components/GradeCard";
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
  grade: Grade;
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
      <GradeCard grade={data.grade} />
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

- [ ] **Step 6: Run frontend tests**

Run: `npm --prefix apps/coach_web/frontend test`
Expected: all PASS (existing 8 + new 3)

- [ ] **Step 7: Typecheck + production build**

Run: `npm --prefix apps/coach_web/frontend run build`
Expected: `tsc -b` clean, vite build succeeds

- [ ] **Step 8: Full backend suite one last time**

Run: `python3.11 -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 9: Commit**

```bash
git add apps/coach_web/frontend/src/components/GradeCard.tsx apps/coach_web/frontend/src/pages/Overview.tsx apps/coach_web/frontend/src/index.css apps/coach_web/frontend/src/__tests__/GradeCard.test.tsx
git commit -m "feat(grade): GradeCard hero on Overview"
```

---

## Post-plan notes for the finishing pass

- Merge via superpowers:finishing-a-development-branch; deploy from main with the
  `deploy-coach-web` skill (ordinary single deploy — no schema/version ordering here).
- After deploy, verify live: log in, Overview shows the grade card; `curl` the
  overview endpoint and check `grade.level` is `junior` against real data.
- Expected against today's real data: **Junior Engineer, ~72% to Mid-Level**, top gaps
  deploy-docker (never built), deploy-infra, auth/error-handling thin.
