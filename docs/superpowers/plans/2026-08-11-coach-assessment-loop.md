# Coach Assessment Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the daily-newsletter brief with a standing assessment over the whole ingested corpus plus change-gated deltas, with structured recommendations whose outcomes feed the next prompt.

**Architecture:** `brief.py` grows two generation paths (assessment over the full corpus on Sonnet 5; delta over a diff on Haiku 4.5), both returning JSON via `output_config.format`. A deterministic Python fingerprint over material DB facts decides whether either runs — an unchanged fingerprint means no model call, no row, no cost. Recommendations become rows in a new `brief_recommendations` table; converting one to a goal or dismissing its target marks every prior row for the same target, and that history is fed back into the next prompt.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic, Postgres (SQLite in tests), Anthropic Python SDK, React + TypeScript strict, Vite, vitest + @testing-library/react, pytest.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-11-coach-assessment-loop-design.md`. Read it before starting.
- **The delta path must send no `effort`, no `thinking`, no `cache_control`.** Haiku 4.5 rejects `effort`, uses the older `budget_tokens` thinking form, and has a 4096-token cache minimum this payload never reaches. `output_config.format` (structured outputs) **is** supported on Haiku 4.5 and is required — see Task 5 Step 1 for the precise test change.
- **The migration is additive. Delete no rows.** This repo has no database backups.
- **`briefs.day` is indexed but NOT unique.** A second delta on one day is legitimate when the fingerprint moved twice.
- **`require_same_origin` stays a router-level dependency on `writes.py`.** Never `add_middleware` — `/api/ingest` and `/api/usage` are bearer-token machine clients that send no `Origin`, and covering them silently kills the daily sweep.
- **The test suite must never hit the network.** The autouse `background_calls` fixture in `tests/web/conftest.py` neutralizes and records `post_ingest`; do not replace it with a bare no-op.
- **v1–v4 snapshot payloads must never be rejected.** This plan does not touch `shared/snapshot.py`; do not bump `SCHEMA_VERSION`.
- **Alembic head is `334147163440`** (`phase5_app_owned_tables`). The new revision's `down_revision` is that value.
- **Run every command from the worktree root**, which already has a configured venv. Backend: `.venv/bin/python -m pytest tests/ -q`. Frontend: `npm --prefix apps/coach_web/frontend test`. Never use bare `python3` — it resolves to system 3.9, where `str | None` annotations raise at import.
- **Baseline before any task: 363 pytest, 28 vitest, all passing.** A count below that means something regressed; do not paper over it.
- Do not `pip install` or `npm install` — both are already done in this worktree.

## File Structure

| File | Responsibility |
|---|---|
| `apps/coach_web/models.py` | *Modify* — add `Brief.kind`/`day`/`fingerprint`, add `BriefRecommendation` |
| `apps/coach_web/alembic/versions/9c1a4f2b7e30_*.py` | *Create* — additive migration |
| `apps/coach_web/gaps.py` | *Create* — the one source of truth for never-built / stale / never-adopted, shared by Overview and the coach |
| `apps/coach_web/brief.py` | *Modify* — fingerprint, corpus + delta context, schema, two generation paths, writer |
| `apps/coach_web/ingest.py` | *Modify* — `post_ingest` consults the gate |
| `apps/coach_web/writes.py` | *Modify* — outcome propagation, `POST /api/reassess` |
| `apps/coach_web/api.py` | *Modify* — `/api/briefs` reshape |
| `frontend/src/components/RecommendationCard.tsx` | *Create* — one recommendation + Add as goal / Dismiss |
| `frontend/src/components/AssessmentCard.tsx` | *Create* — standing assessment + Reassess |
| `frontend/src/components/SinceThen.tsx` | *Create* — deltas since the assessment |
| `frontend/src/components/GoalPicker.tsx` | *Create* — active goals + gap picker (no free-text field) |
| `frontend/src/components/BriefHistory.tsx` | *Create* — recurring rollup + collapsed per-entry `<details>` |
| `frontend/src/pages/Goals.tsx` | *Modify* — becomes composition + data loading only |

`components/BriefCard.tsx` is superseded by `AssessmentCard.tsx` and is deleted in Task 11.

---

### Task 1: Schema — models and migration

**Files:**
- Modify: `apps/coach_web/models.py:122-139`
- Create: `apps/coach_web/alembic/versions/9c1a4f2b7e30_brief_kinds_and_recommendations.py`
- Test: `tests/web/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `models.Brief.kind: str`, `models.Brief.day: str`, `models.Brief.fingerprint: str`; `models.BriefRecommendation` with columns `id: int`, `brief_id: int`, `ord: int`, `title: str`, `kind: str`, `target: str`, `why: str`, `evidence: str`, `outcome: str`, `outcome_at: str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_models.py`:

```python
def test_brief_defaults_to_a_legacy_delta():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as db:
        row = models.Brief(created_at="2026-08-11T07:00:00+00:00", model="m")
        db.add(row)
        db.commit()
        assert row.kind == "delta"
        assert row.day == ""
        assert row.fingerprint == ""


def test_brief_recommendation_round_trips():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as db:
        brief_row = models.Brief(created_at="2026-08-11T07:00:00+00:00",
                                 kind="assessment", day="2026-08-11", model="m")
        db.add(brief_row)
        db.commit()
        rec = models.BriefRecommendation(
            brief_id=brief_row.id, ord=0, title="Containerize purchase-inventory",
            kind="tag", target="deploy-docker", why="because", evidence="14 units, no containers")
        db.add(rec)
        db.commit()
        assert rec.outcome == "open"
        assert rec.outcome_at == ""
        assert rec.brief_id == brief_row.id
```

Ensure the file's imports include `from sqlalchemy import create_engine`, `from sqlalchemy.orm import Session`, and `from apps.coach_web import models`; add any that are missing.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_models.py -q -k "legacy_delta or recommendation_round_trips"`
Expected: FAIL — `AttributeError` / `module 'apps.coach_web.models' has no attribute 'BriefRecommendation'`

- [ ] **Step 3: Add the columns and the table**

In `apps/coach_web/models.py`, inside `class Brief`, after the `error` column:

```python
    kind: Mapped[str] = mapped_column(String(16), default="delta", index=True)
    # NOT unique: a second delta in one day is legitimate when the change gate
    # fires twice. Uniqueness here would wrongly block it.
    day: Mapped[str] = mapped_column(String(10), default="", index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
```

Immediately after `class Brief`, add:

```python
class BriefRecommendation(Base):
    """One concrete recommendation from a brief, tracked to an outcome.

    A table rather than a JSON blob on `briefs`: the recurring rollup is a
    GROUP BY target, and converting one to a goal needs a stable row to mark.
    """
    __tablename__ = "brief_recommendations"
    id: Mapped[int] = mapped_column(primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("briefs.id"), index=True)
    ord: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))
    target: Mapped[str] = mapped_column(String(120), index=True)
    why: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    outcome: Mapped[str] = mapped_column(String(16), default="open", index=True)
    outcome_at: Mapped[str] = mapped_column(String(32), default="")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/web/test_models.py -q`
Expected: PASS

- [ ] **Step 5: Write the migration**

Create `apps/coach_web/alembic/versions/9c1a4f2b7e30_brief_kinds_and_recommendations.py`:

```python
"""brief kinds, day index, and brief_recommendations

Additive only. No rows are deleted: this repo has no database backups, and the
existing prose briefs stay in the history as legacy delta entries.

Revision ID: 9c1a4f2b7e30
Revises: 334147163440
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '9c1a4f2b7e30'
down_revision: Union[str, Sequence[str], None] = '334147163440'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('briefs', sa.Column('kind', sa.String(16),
                                      nullable=False, server_default='delta'))
    op.add_column('briefs', sa.Column('day', sa.String(10),
                                      nullable=False, server_default=''))
    op.add_column('briefs', sa.Column('fingerprint', sa.String(64),
                                      nullable=False, server_default=''))
    op.create_index('ix_briefs_kind', 'briefs', ['kind'])
    # Deliberately NOT unique -- see models.Brief.day.
    op.create_index('ix_briefs_day', 'briefs', ['day'])
    # Backfill day from the ISO timestamp already stored on every row.
    op.execute("UPDATE briefs SET day = substr(created_at, 1, 10)")

    op.create_table(
        'brief_recommendations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('brief_id', sa.Integer(), sa.ForeignKey('briefs.id'),
                  nullable=False),
        sa.Column('ord', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('title', sa.String(200), nullable=False),
        sa.Column('kind', sa.String(16), nullable=False),
        sa.Column('target', sa.String(120), nullable=False),
        sa.Column('why', sa.Text(), nullable=False, server_default=''),
        sa.Column('evidence', sa.Text(), nullable=False, server_default=''),
        sa.Column('outcome', sa.String(16), nullable=False, server_default='open'),
        sa.Column('outcome_at', sa.String(32), nullable=False, server_default=''),
    )
    op.create_index('ix_brief_recommendations_brief_id',
                    'brief_recommendations', ['brief_id'])
    op.create_index('ix_brief_recommendations_target',
                    'brief_recommendations', ['target'])
    op.create_index('ix_brief_recommendations_outcome',
                    'brief_recommendations', ['outcome'])


def downgrade() -> None:
    op.drop_table('brief_recommendations')
    op.drop_index('ix_briefs_day', table_name='briefs')
    op.drop_index('ix_briefs_kind', table_name='briefs')
    op.drop_column('briefs', 'fingerprint')
    op.drop_column('briefs', 'day')
    op.drop_column('briefs', 'kind')
```

- [ ] **Step 6: Verify the migration chain has one head**

Run: `.venv/bin/python -m alembic -c apps/coach_web/alembic.ini heads`
Expected: exactly one head, `9c1a4f2b7e30`. If the ini's `script_location` is
relative and this fails, `cd apps/coach_web` and use `../../.venv/bin/python -m alembic heads`.

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (139 existing tests plus the 2 new ones)

- [ ] **Step 8: Commit**

```bash
git add apps/coach_web/models.py apps/coach_web/alembic/versions/9c1a4f2b7e30_brief_kinds_and_recommendations.py tests/web/test_models.py
git commit -m "feat(brief): add brief kinds and a recommendations table"
```

---

### Task 2: The change fingerprint

**Files:**
- Modify: `apps/coach_web/brief.py`
- Test: `tests/web/test_brief_gate.py` (create)

**Interfaces:**
- Consumes: `models.Brief`, `models.BriefRecommendation` from Task 1.
- Produces: `brief.fingerprint(db, today: date) -> str` (64-char sha256 hex); `brief.get_state(db, key: str, default: str = "") -> str`; `brief.set_state(db, key: str, value: str) -> None`; constants `brief.FP_KEY = "brief.fingerprint"`, `brief.DELTA_COUNT_KEY = "brief.deltas_since_assessment"`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_brief_gate.py`:

```python
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

TODAY = date(2026, 8, 11)


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/g.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def add_unit(db, key, tags, day=TODAY.isoformat(), complexity=3):
    db.add(models.FeatureUnit(key=key, kind="spec", repo="r", date=day,
                              title=f"title {key}", tags=tags,
                              complexity=complexity, summary=f"summary {key}",
                              model="m"))


def test_fingerprint_is_stable_for_unchanged_state(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.commit()
        assert brief.fingerprint(db, TODAY) == brief.fingerprint(db, TODAY)


def test_fingerprint_moves_when_a_new_tag_is_used(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.commit()
        before = brief.fingerprint(db, TODAY)
        add_unit(db, "u2", ["deploy-docker"])
        db.commit()
        assert brief.fingerprint(db, TODAY) != before


def test_fingerprint_moves_when_a_goal_is_added(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.commit()
        before = brief.fingerprint(db, TODAY)
        db.add(models.Goal(kind="tag", target="deploy-docker", title="Containerize",
                           target_date="", status="active",
                           created_at="2026-08-11T00:00:00+00:00"))
        db.commit()
        assert brief.fingerprint(db, TODAY) != before


def test_fingerprint_ignores_sub_dollar_spend_drift(tmp_path):
    # Cent-level drift is noise; only a whole dollar of movement is a fact
    # worth waking the coach for.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.add(models.CostDaily(date=TODAY.isoformat(), input_tokens=1,
                                output_tokens=1, cache_read_tokens=0,
                                cache_creation_tokens=0, cost_usd=2.10,
                                by_model={}))
        db.commit()
        before = brief.fingerprint(db, TODAY)
        db.query(models.CostDaily).filter_by(date=TODAY.isoformat()).update(
            {"cost_usd": 2.40})
        db.commit()
        assert brief.fingerprint(db, TODAY) == before


def test_fingerprint_moves_on_a_whole_dollar_of_spend(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.add(models.CostDaily(date=TODAY.isoformat(), input_tokens=1,
                                output_tokens=1, cache_read_tokens=0,
                                cache_creation_tokens=0, cost_usd=2.10,
                                by_model={}))
        db.commit()
        before = brief.fingerprint(db, TODAY)
        db.query(models.CostDaily).filter_by(date=TODAY.isoformat()).update(
            {"cost_usd": 9.90})
        db.commit()
        assert brief.fingerprint(db, TODAY) != before


def test_state_round_trips(tmp_path):
    with make_db(tmp_path) as db:
        assert brief.get_state(db, brief.FP_KEY) == ""
        brief.set_state(db, brief.FP_KEY, "abc")
        db.commit()
        assert brief.get_state(db, brief.FP_KEY) == "abc"
        brief.set_state(db, brief.FP_KEY, "def")
        db.commit()
        assert brief.get_state(db, brief.FP_KEY) == "def"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_brief_gate.py -q`
Expected: FAIL — `AttributeError: module 'apps.coach_web.brief' has no attribute 'fingerprint'`

- [ ] **Step 3: Implement fingerprint and state helpers**

In `apps/coach_web/brief.py`, add to the imports at the top:

```python
import hashlib
import json
```

Add after the `STALE_DAYS` constant:

```python
FP_KEY = "brief.fingerprint"
DELTA_COUNT_KEY = "brief.deltas_since_assessment"
SPEND_WINDOW_DAYS = 7


def get_state(db, key: str, default: str = "") -> str:
    row = db.get(models.WatcherState, key)
    return row.value if row is not None else default


def set_state(db, key: str, value: str, now: datetime | None = None) -> None:
    """Upsert a watcher_state key. Caller commits."""
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    row = db.get(models.WatcherState, key)
    if row is None:
        db.add(models.WatcherState(key=key, value=value, updated_at=stamp))
    else:
        row.value = value
        row.updated_at = stamp


def fingerprint(db, today: date) -> str:
    """Hash of the facts that make a new brief worth generating.

    Every component is a set, a count, or a dollar-rounded figure, so it moves
    on a real event and not on ordinary drift. An unchanged fingerprint means
    no model call at all -- this is the whole cost control.
    """
    tags: set[str] = set()
    for (row_tags,) in db.execute(select(models.FeatureUnit.tags)):
        tags.update(row_tags or [])

    adopted: set[str] = set()
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    if latest is not None:
        adopted = set(db.scalars(
            select(models.AdoptionHistory.feature_name)
            .where(models.AdoptionHistory.snapshot_id == latest.id,
                   models.AdoptionHistory.status != "never-touched")))

    goals = sorted((g.id, g.status) for g in db.scalars(select(models.Goal)))
    units = db.scalar(select(func.count(models.FeatureUnit.key))) or 0

    since = (today - timedelta(days=SPEND_WINDOW_DAYS)).isoformat()
    spend = sum(r.cost_usd or 0.0 for r in db.scalars(select(models.CostDaily))
                if r.date >= since)

    body = {
        "tags": sorted(tags),
        "adopted": sorted(adopted),
        "goals": goals,
        "units": units,
        "spend_dollars": round(spend),   # whole dollars: cents are noise
        "changelog": get_state(db, "changelog.last_checked_at"),
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
```

Confirm `from sqlalchemy import func, select` is already imported at the top of `brief.py` (it is) and that `timedelta` is in the `datetime` import line (it is).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/web/test_brief_gate.py -q`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/brief.py tests/web/test_brief_gate.py
git commit -m "feat(brief): fingerprint the facts worth regenerating for"
```

---

### Task 3: Shared gap lists and the corpus context builder

**Files:**
- Create: `apps/coach_web/gaps.py`
- Modify: `apps/coach_web/api.py:98-114` (overview calls the shared helper)
- Modify: `apps/coach_web/brief.py`
- Test: `tests/web/test_gaps.py` (create)
- Test: `tests/web/test_brief_corpus.py` (create)

**Interfaces:**
- Consumes: `brief.get_state` from Task 2.
- Produces: `gaps.gap_lists(db, today: date, exclude_dismissed: bool = False) -> dict` with keys `never_built: list[str]`, `stale: list[dict]` (each `{"tag", "last_done"}`), `adoption_gaps: list[str]`; `gaps.STALE_DAYS = 180`; `brief.build_corpus_context(db, today: date) -> dict` with keys `repos`, `work`, `work_note`, `activity`, `cost`, `grade`, `adoption`, `commitments`, `never_built`, `stale`, `adoption_gaps` (here `stale` is a `list[str]` of tags); `brief.render_corpus_prompt(ctx: dict) -> str`; constants `brief.MAX_RECENT_UNITS = 150`, `brief.MAX_COMPLEX_UNITS = 50`.

**Why a new module:** this computation is currently duplicated between
`api.py::overview` and the old `brief.build_context`, with one deliberate
difference — Overview keeps showing dismissed items so a dismissal never
becomes invisible, while the coach must stop re-suggesting them. Extracting it
makes that difference an explicit argument instead of a divergence between two
similar-looking blocks. It does **not** go in `aggregate.py`, whose module
docstring promises "No DB access."

- [ ] **Step 1: Write the failing test for the shared helper**

Create `tests/web/test_gaps.py`:

```python
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import gaps, models

TODAY = date(2026, 8, 11)
OLD = "2026-01-01"      # older than STALE_DAYS before TODAY


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/gaps.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def add_unit(db, key, tags, day):
    db.add(models.FeatureUnit(key=key, kind="spec", repo="r", date=day,
                              title="t", tags=tags, complexity=3, summary="s",
                              model="m"))


def test_never_built_is_the_taxonomy_minus_what_was_used(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"], TODAY.isoformat())
        db.commit()
        out = gaps.gap_lists(db, TODAY)
        assert "auth" not in out["never_built"]
        assert "deploy-docker" in out["never_built"]


def test_stale_carries_the_last_done_date(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"], OLD)
        db.commit()
        out = gaps.gap_lists(db, TODAY)
        assert {"tag": "auth", "last_done": OLD} in out["stale"]


def test_adoption_gaps_come_from_the_latest_snapshot(tmp_path):
    with make_db(tmp_path) as db:
        snap = models.Snapshot(captured_at="2026-08-11T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.AdoptionHistory(snapshot_id=snap.id,
                                      feature_name="plan mode", lesson="l",
                                      status="never-touched", last_used=None))
        db.add(models.AdoptionHistory(snapshot_id=snap.id,
                                      feature_name="hooks", lesson="l",
                                      status="used", last_used="2026-08-01"))
        db.commit()
        out = gaps.gap_lists(db, TODAY)
        assert out["adoption_gaps"] == ["plan mode"]


def test_dismissals_are_kept_by_default_and_dropped_on_request(tmp_path):
    # Overview deliberately still shows dismissed items so a dismissal never
    # becomes invisible; the coach must stop re-suggesting them. That one
    # difference is the whole reason this takes a flag.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"], TODAY.isoformat())
        db.add(models.Dismissal(kind="tag", target="deploy-docker",
                                reason="no need",
                                created_at="2026-08-01T00:00:00+00:00"))
        db.commit()
        assert "deploy-docker" in gaps.gap_lists(db, TODAY)["never_built"]
        assert "deploy-docker" not in gaps.gap_lists(
            db, TODAY, exclude_dismissed=True)["never_built"]


def test_feature_dismissals_only_filter_adoption_gaps(tmp_path):
    with make_db(tmp_path) as db:
        snap = models.Snapshot(captured_at="2026-08-11T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.AdoptionHistory(snapshot_id=snap.id,
                                      feature_name="plan mode", lesson="l",
                                      status="never-touched", last_used=None))
        db.add(models.Dismissal(kind="feature", target="plan mode", reason="",
                                created_at="2026-08-01T00:00:00+00:00"))
        db.commit()
        out = gaps.gap_lists(db, TODAY, exclude_dismissed=True)
        assert out["adoption_gaps"] == []


def test_empty_database_yields_the_whole_taxonomy(tmp_path):
    with make_db(tmp_path) as db:
        out = gaps.gap_lists(db, TODAY)
        assert len(out["never_built"]) > 0
        assert out["stale"] == []
        assert out["adoption_gaps"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_gaps.py -q`
Expected: FAIL — `ImportError: cannot import name 'gaps'`

- [ ] **Step 3: Write gaps.py and point overview at it**

Create `apps/coach_web/gaps.py`:

```python
"""Capability and adoption gaps over ingested data.

One source of truth for Overview and the coach. The two differ in exactly one
way, which is why `exclude_dismissed` is a parameter rather than two functions:
Overview keeps showing dismissed items so a dismissal never becomes invisible,
while the coach must stop re-suggesting them.

Not in aggregate.py -- that module promises "No DB access".
"""
from datetime import date, timedelta

from sqlalchemy import select

from . import models, taxonomy

STALE_DAYS = 180


def gap_lists(db, today: date, exclude_dismissed: bool = False) -> dict:
    last_by_tag: dict[str, str] = {}
    for tags, d in db.execute(select(models.FeatureUnit.tags,
                                     models.FeatureUnit.date)):
        for t in tags or []:
            if t not in last_by_tag or d > last_by_tag[t]:
                last_by_tag[t] = d

    never_built = [t for t in taxonomy.all_tags() if t not in last_by_tag]
    cutoff = (today - timedelta(days=STALE_DAYS)).isoformat()
    stale = [{"tag": t, "last_done": d} for t, d in sorted(last_by_tag.items())
             if d <= cutoff]

    adoption_gaps: list[str] = []
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    if latest is not None:
        adoption_gaps = sorted(db.scalars(
            select(models.AdoptionHistory.feature_name)
            .where(models.AdoptionHistory.snapshot_id == latest.id,
                   models.AdoptionHistory.status == "never-touched")))

    if exclude_dismissed:
        dismissed_tags, dismissed_features = set(), set()
        for row in db.scalars(select(models.Dismissal)):
            if row.kind == "tag":
                dismissed_tags.add(row.target)
            elif row.kind == "feature":
                dismissed_features.add(row.target)
        never_built = [t for t in never_built if t not in dismissed_tags]
        stale = [s for s in stale if s["tag"] not in dismissed_tags]
        adoption_gaps = [f for f in adoption_gaps if f not in dismissed_features]

    return {"never_built": never_built, "stale": stale,
            "adoption_gaps": adoption_gaps}
```

In `apps/coach_web/api.py`, delete the `last_by_tag` / `never_built` /
`stale_cutoff` / `stale` / `adoption_gaps` block in `overview` (the lines
between the cost tile computation and the `unit_rows` query) and replace it
with:

```python
    gap = gaps.gap_lists(db, today)
    never_built = gap["never_built"]
    stale = gap["stale"]
    adoption_gaps = gap["adoption_gaps"]
```

Add `gaps` to the `from . import ...` line in `api.py`. The `latest` variable
is still used elsewhere in `overview` — leave its assignment in place. Do not
change the response shape: `stale` stays a list of `{tag, last_done}` dicts.

- [ ] **Step 4: Run tests to verify the extraction changed no behaviour**

Run: `.venv/bin/python -m pytest tests/web/test_gaps.py tests/web/test_api.py tests/web/test_api_phase2.py tests/web/test_api_grade.py tests/web/test_integrations.py -q`
Expected: PASS — the new tests pass and every existing Overview assertion is unchanged.

- [ ] **Step 5: Commit the extraction**

```bash
git add apps/coach_web/gaps.py apps/coach_web/api.py tests/web/test_gaps.py
git commit -m "refactor: one source of truth for gap lists"
```

- [ ] **Step 6: Write the failing test**

Create `tests/web/test_brief_corpus.py`:

```python
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

TODAY = date(2026, 8, 11)


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/c.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def add_unit(db, key, repo, day, tags, complexity, title, summary):
    db.add(models.FeatureUnit(key=key, kind="spec", repo=repo, date=day,
                              title=title, tags=tags, complexity=complexity,
                              summary=summary, model="m"))


def test_corpus_rolls_up_repos(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", "alpha", "2026-01-05", ["auth"], 2, "Login", "did login")
        add_unit(db, "u2", "alpha", "2026-03-05", ["auth", "testing-depth"], 4,
                 "Tests", "did tests")
        add_unit(db, "u3", "beta", "2026-02-05", ["frontend-spa"], 3, "UI", "did ui")
        db.commit()
        ctx = brief.build_corpus_context(db, TODAY)
        by_name = {r["repo"]: r for r in ctx["repos"]}
        assert by_name["alpha"]["units"] == 2
        assert by_name["alpha"]["first"] == "2026-01-05"
        assert by_name["alpha"]["last"] == "2026-03-05"
        assert by_name["alpha"]["mean_complexity"] == 3.0
        assert sorted(by_name["alpha"]["tags"]) == ["auth", "testing-depth"]
        assert by_name["beta"]["units"] == 1


def test_corpus_carries_titles_and_summaries(tmp_path):
    # The whole point: the model has never seen the prose before.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", "alpha", "2026-01-05", ["auth"], 2,
                 "Add magic-link login", "Swapped passwords for emailed links.")
        db.commit()
        ctx = brief.build_corpus_context(db, TODAY)
        assert ctx["work"][0]["title"] == "Add magic-link login"
        assert ctx["work"][0]["summary"] == "Swapped passwords for emailed links."
        assert ctx["work"][0]["complexity"] == 2


def test_corpus_states_its_truncation(tmp_path):
    # Silent truncation reads to the model as a complete corpus and produces
    # confidently wrong conclusions about coverage.
    with make_db(tmp_path) as db:
        for i in range(brief.MAX_RECENT_UNITS + brief.MAX_COMPLEX_UNITS + 40):
            day = f"2026-01-{(i % 28) + 1:02d}"
            add_unit(db, f"u{i}", "alpha", day, ["auth"], (i % 5) + 1,
                     f"t{i}", f"s{i}")
        db.commit()
        ctx = brief.build_corpus_context(db, TODAY)
        assert len(ctx["work"]) <= brief.MAX_RECENT_UNITS + brief.MAX_COMPLEX_UNITS
        assert "of" in ctx["work_note"]
        assert str(brief.MAX_RECENT_UNITS + brief.MAX_COMPLEX_UNITS + 40) in ctx["work_note"]
        assert ctx["work_note"] in brief.render_corpus_prompt(ctx)


def test_corpus_note_is_empty_when_nothing_was_dropped(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", "alpha", "2026-01-05", ["auth"], 2, "t", "s")
        db.commit()
        assert brief.build_corpus_context(db, TODAY)["work_note"] == ""


def test_corpus_includes_the_grade_and_its_best_fit_repos(tmp_path):
    # compute_grade already works out which repo should host a missing tag.
    # It has never been shown to the model.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", "alpha", "2026-08-01", ["auth"], 3, "t", "s")
        db.commit()
        ctx = brief.build_corpus_context(db, TODAY)
        assert ctx["grade"] is not None
        assert "gaps" in ctx["grade"]


def test_corpus_names_dismissals_explicitly(tmp_path):
    # A dismissed gap was considered and waved off. The model must know that,
    # not just see it missing.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", "alpha", "2026-08-01", ["auth"], 3, "t", "s")
        db.add(models.Dismissal(kind="tag", target="scraping", reason="no need",
                                created_at="2026-08-01T00:00:00+00:00"))
        db.commit()
        ctx = brief.build_corpus_context(db, TODAY)
        assert {"kind": "tag", "target": "scraping",
                "reason": "no need"} in ctx["commitments"]["dismissed"]
        assert "scraping" not in ctx["never_built"]


def test_corpus_on_empty_database(tmp_path):
    with make_db(tmp_path) as db:
        ctx = brief.build_corpus_context(db, TODAY)
        assert ctx["repos"] == []
        assert ctx["work"] == []
        assert ctx["grade"] is None
        assert brief.render_corpus_prompt(ctx)  # renders without raising
```

- [ ] **Step 7: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_brief_corpus.py -q`
Expected: FAIL — `AttributeError: module 'apps.coach_web.brief' has no attribute 'build_corpus_context'`

- [ ] **Step 8: Implement the corpus builder**

In `apps/coach_web/brief.py`, extend the module import line to:

```python
from . import aggregate, gaps, grade as grade_mod, models, rubric, taxonomy
```

Add the constants beside the others:

```python
MAX_RECENT_UNITS = 150
MAX_COMPLEX_UNITS = 50
ASSESSMENT_MAX_TOKENS = 8192
```

Add these functions:

```python
def _monthly(rows, date_attr: str, fields: tuple) -> list[dict]:
    """Bucket dated rows into {month: YYYY-MM, <field>: sum} entries."""
    buckets: dict[str, dict] = {}
    for r in rows:
        month = getattr(r, date_attr)[:7]
        b = buckets.setdefault(month, {"month": month,
                                       **{f: 0 for f in fields}})
        for f in fields:
            b[f] += getattr(r, f) or 0
    return [buckets[m] for m in sorted(buckets)]


def _gap_lists(db, today: date) -> tuple[list[str], list[str], list[str]]:
    """(never_built, stale_tags, adoption_gaps) as the coach sees them.

    Thin adapter over the shared helper: the coach excludes dismissed targets
    and wants stale as bare tags, while Overview keeps dismissals visible and
    renders stale with its last_done date.
    """
    out = gaps.gap_lists(db, today, exclude_dismissed=True)
    return (out["never_built"],
            [s["tag"] for s in out["stale"]],
            out["adoption_gaps"])


def build_corpus_context(db, today: date) -> dict:
    """Everything the coach should know, as a pure function over DB rows.

    Replaces the six-numbers-and-three-lists context that made every brief
    identical. Pure so it stays testable without a key or a network.
    """
    units = list(db.scalars(select(models.FeatureUnit)
                            .order_by(models.FeatureUnit.date.desc(),
                                      models.FeatureUnit.key)))

    repos: dict[str, dict] = {}
    for u in units:
        r = repos.setdefault(u.repo, {"repo": u.repo, "units": 0, "cx_sum": 0,
                                      "first": u.date, "last": u.date,
                                      "tags": set()})
        r["units"] += 1
        r["cx_sum"] += u.complexity or 0
        r["first"] = min(r["first"], u.date)
        r["last"] = max(r["last"], u.date)
        r["tags"].update(u.tags or [])
    repo_list = []
    for r in sorted(repos.values(), key=lambda x: -x["units"]):
        repo_list.append({"repo": r["repo"], "units": r["units"],
                          "first": r["first"], "last": r["last"],
                          "mean_complexity": round(r["cx_sum"] / r["units"], 1),
                          "tags": sorted(r["tags"])})

    # Bound the corpus: most recent, plus the most complex not already picked.
    recent = units[:MAX_RECENT_UNITS]
    picked = {u.key for u in recent}
    complex_extra = sorted((u for u in units if u.key not in picked),
                           key=lambda u: (-(u.complexity or 0), u.date),
                           )[:MAX_COMPLEX_UNITS]
    kept = recent + complex_extra
    work = [{"repo": u.repo, "date": u.date, "title": u.title,
             "summary": u.summary, "complexity": u.complexity,
             "tags": u.tags or []} for u in kept]
    work_note = ""
    if len(kept) < len(units):
        work_note = (f"Showing {len(kept)} of {len(units)} units: the "
                     f"{len(recent)} most recent and the {len(complex_extra)} "
                     f"most complex of the rest.")

    unit_rows = [(u.repo, u.date, u.tags, u.complexity) for u in units]
    grade = grade_mod.compute_grade(unit_rows, rubric.load(), today)

    adoption = []
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    if latest is not None:
        adoption = [{"feature": a.feature_name, "status": a.status,
                     "last_used": a.last_used}
                    for a in db.scalars(
                        select(models.AdoptionHistory)
                        .where(models.AdoptionHistory.snapshot_id == latest.id)
                        .order_by(models.AdoptionHistory.feature_name))]

    commitments = {
        "goals": [{"id": g.id, "kind": g.kind, "target": g.target,
                   "title": g.title, "status": g.status}
                  for g in db.scalars(select(models.Goal)
                                      .order_by(models.Goal.id))],
        "checked_off": [c.feature_name for c in db.scalars(
            select(models.FeatureCheckoff)
            .order_by(models.FeatureCheckoff.feature_name))],
        "dismissed": [{"kind": d.kind, "target": d.target, "reason": d.reason}
                      for d in db.scalars(select(models.Dismissal)
                                          .order_by(models.Dismissal.id))],
    }

    never_built, stale, adoption_gaps = _gap_lists(db, today)
    return {
        "repos": repo_list,
        "work": work,
        "work_note": work_note,
        "activity": _monthly(list(db.scalars(select(models.ActivityDaily))),
                             "date", ("commits", "sessions", "prompts")),
        "cost": _monthly(list(db.scalars(select(models.CostDaily))),
                         "date", ("cost_usd",)),
        "grade": grade,
        "adoption": adoption,
        "commitments": commitments,
        "never_built": never_built,
        "stale": stale,
        "adoption_gaps": adoption_gaps,
    }


def render_corpus_prompt(ctx: dict) -> str:
    def listing(items) -> str:
        return ", ".join(items) if items else "(none)"

    lines = ["## Repos"]
    for r in ctx["repos"]:
        lines.append(f"- {r['repo']}: {r['units']} units, {r['first']} to "
                     f"{r['last']}, mean complexity {r['mean_complexity']}, "
                     f"tags: {listing(r['tags'])}")
    if not ctx["repos"]:
        lines.append("- (nothing ingested yet)")

    lines.append("\n## Work shipped")
    if ctx["work_note"]:
        lines.append(ctx["work_note"])
    for w in ctx["work"]:
        lines.append(f"- [{w['date']}] {w['repo']} (cx {w['complexity']}, "
                     f"{listing(w['tags'])}): {w['title']} — {w['summary']}")

    lines.append("\n## Activity by month")
    for a in ctx["activity"]:
        lines.append(f"- {a['month']}: {a['commits']} commits, "
                     f"{a['sessions']} sessions, {a['prompts']} prompts")

    lines.append("\n## Spend by month")
    for c in ctx["cost"]:
        lines.append(f"- {c['month']}: ${c['cost_usd']:.2f}")

    lines.append("\n## Grade")
    g = ctx["grade"]
    if g is None:
        lines.append("- (not enough data)")
    else:
        lines.append(f"- {g['level_label']}, {g['percent_to_next']}% toward "
                     f"{g['next_label'] or 'the top'}")
        for gap in g["gaps"]:
            lines.append(f"  - gap {gap['tag']}: have {gap['have']['count']}, "
                         f"need {gap['need']['min_count']}; best fit repo: "
                         f"{gap['best_fit_repo']}")

    lines.append("\n## Claude Code adoption")
    for a in ctx["adoption"]:
        lines.append(f"- {a['feature']}: {a['status']} "
                     f"(last used {a['last_used'] or 'never'})")

    lines.append("\n## Commitments")
    for gl in ctx["commitments"]["goals"]:
        lines.append(f"- goal ({gl['status']}): {gl['title']} -> {gl['target']}")
    lines.append(f"- checked off: {listing(ctx['commitments']['checked_off'])}")
    for d in ctx["commitments"]["dismissed"]:
        lines.append(f"- dismissed {d['target']}: {d['reason'] or 'no reason given'} "
                     "(considered and waved off — do not suggest it)")

    lines.append("\n## Allowed recommendation targets")
    lines.append(f"tags never built: {listing(ctx['never_built'])}")
    lines.append(f"tags stale over {STALE_DAYS} days: {listing(ctx['stale'])}")
    lines.append(f"Claude Code features never adopted: {listing(ctx['adoption_gaps'])}")
    return "\n".join(lines)
```

- [ ] **Step 9: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/web/test_brief_corpus.py -q`
Expected: PASS (7 tests)

- [ ] **Step 10: Commit**

```bash
git add apps/coach_web/brief.py tests/web/test_brief_corpus.py
git commit -m "feat(brief): build context from the whole ingested corpus"
```

---

### Task 4: Recommendation history and delta context

**Files:**
- Modify: `apps/coach_web/brief.py`
- Test: `tests/web/test_brief_history.py` (create)

**Interfaces:**
- Consumes: `build_corpus_context` from Task 3; `models.BriefRecommendation` from Task 1.
- Produces: `brief.recommendation_history(db) -> list[dict]` with keys `target`, `kind`, `times`, `first`, `last`, `outcome`; `brief.build_delta_context(db, today, assessment) -> dict` with keys `assessment_summary`, `assessment_recommendations`, `assessment_day`, `changed`, `history`, `never_built`, `stale`, `adoption_gaps`; `brief.render_delta_prompt(ctx) -> str`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_brief_history.py`:

```python
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

TODAY = date(2026, 8, 11)


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/h.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def add_brief(db, day, kind="delta"):
    row = models.Brief(created_at=f"{day}T07:00:00+00:00", kind=kind, day=day,
                       model="m", status="ok", body="prose")
    db.add(row)
    db.commit()
    return row


def add_rec(db, brief_row, target, outcome="open", kind="tag"):
    rec = models.BriefRecommendation(
        brief_id=brief_row.id, ord=0, title=f"Do {target}", kind=kind,
        target=target, why="w", evidence="e", outcome=outcome)
    db.add(rec)
    db.commit()
    return rec


def test_history_counts_repeat_suggestions(tmp_path):
    with make_db(tmp_path) as db:
        for day in ("2026-08-01", "2026-08-05", "2026-08-09"):
            add_rec(db, add_brief(db, day), "deploy-docker")
        hist = {h["target"]: h for h in brief.recommendation_history(db)}
        assert hist["deploy-docker"]["times"] == 3
        assert hist["deploy-docker"]["first"] == "2026-08-01"
        assert hist["deploy-docker"]["last"] == "2026-08-09"
        assert hist["deploy-docker"]["outcome"] == "open"


def test_history_reports_the_strongest_outcome(tmp_path):
    # Converted beats dismissed beats open: what matters is whether it ever
    # led anywhere.
    with make_db(tmp_path) as db:
        add_rec(db, add_brief(db, "2026-08-01"), "deploy-docker", "superseded")
        add_rec(db, add_brief(db, "2026-08-05"), "deploy-docker", "converted")
        hist = {h["target"]: h for h in brief.recommendation_history(db)}
        assert hist["deploy-docker"]["outcome"] == "converted"


def test_history_is_empty_with_no_recommendations(tmp_path):
    with make_db(tmp_path) as db:
        assert brief.recommendation_history(db) == []


def test_delta_prompt_names_ignored_recommendations(tmp_path):
    # The rule that makes the coach stop repeating itself.
    with make_db(tmp_path) as db:
        for day in ("2026-08-01", "2026-08-05", "2026-08-09"):
            add_rec(db, add_brief(db, day), "deploy-docker")
        assessment = add_brief(db, "2026-08-01", kind="assessment")
        ctx = brief.build_delta_context(db, TODAY, assessment)
        text = brief.render_delta_prompt(ctx)
        assert "deploy-docker" in text
        assert "3" in text
        assert "never acted on" in text


def test_delta_context_carries_the_assessment(tmp_path):
    with make_db(tmp_path) as db:
        assessment = add_brief(db, "2026-08-01", kind="assessment")
        assessment.body = "You build fast and deploy by hand."
        add_rec(db, assessment, "deploy-docker")
        db.commit()
        ctx = brief.build_delta_context(db, TODAY, assessment)
        assert ctx["assessment_summary"] == "You build fast and deploy by hand."
        assert ctx["assessment_day"] == "2026-08-01"
        assert ctx["assessment_recommendations"][0]["target"] == "deploy-docker"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_brief_history.py -q`
Expected: FAIL — `AttributeError: module 'apps.coach_web.brief' has no attribute 'recommendation_history'`

- [ ] **Step 3: Implement history and delta context**

Add to `apps/coach_web/brief.py`:

```python
# Strongest-first: what matters about a target is whether it ever led anywhere.
_OUTCOME_RANK = {"converted": 3, "dismissed": 2, "open": 1, "superseded": 0}


def recommendation_history(db) -> list[dict]:
    """Per target: how often it was suggested, over what span, and what came of it."""
    rows = list(db.execute(
        select(models.BriefRecommendation.target, models.BriefRecommendation.kind,
               models.BriefRecommendation.outcome, models.Brief.day)
        .join(models.Brief, models.Brief.id == models.BriefRecommendation.brief_id)
        .order_by(models.Brief.day)))
    agg: dict[str, dict] = {}
    for target, kind, outcome, day in rows:
        e = agg.setdefault(target, {"target": target, "kind": kind, "times": 0,
                                    "first": day, "last": day, "outcome": outcome})
        e["times"] += 1
        e["first"] = min(e["first"], day)
        e["last"] = max(e["last"], day)
        if _OUTCOME_RANK.get(outcome, 0) > _OUTCOME_RANK.get(e["outcome"], 0):
            e["outcome"] = outcome
    return [agg[t] for t in sorted(agg)]


def build_delta_context(db, today: date, assessment) -> dict:
    never_built, stale, adoption_gaps = _gap_lists(db, today)
    recs = list(db.scalars(
        select(models.BriefRecommendation)
        .where(models.BriefRecommendation.brief_id == assessment.id)
        .order_by(models.BriefRecommendation.ord)))
    return {
        "assessment_summary": assessment.body,
        "assessment_day": assessment.day,
        "assessment_recommendations": [
            {"title": r.title, "kind": r.kind, "target": r.target}
            for r in recs],
        "changed": describe_change(db, today),
        "history": recommendation_history(db),
        "never_built": never_built,
        "stale": stale,
        "adoption_gaps": adoption_gaps,
    }


def describe_change(db, today: date) -> list[str]:
    """Human-readable lines for what moved since the last brief.

    The fingerprint tells us *that* something changed; this tells the model
    *what*, so a delta can be specific instead of restating the assessment.
    """
    since = (today - timedelta(days=SPEND_WINDOW_DAYS)).isoformat()
    out = []
    fresh = list(db.scalars(select(models.FeatureUnit)
                            .where(models.FeatureUnit.date >= since)
                            .order_by(models.FeatureUnit.date)))
    for u in fresh:
        out.append(f"shipped [{u.date}] {u.repo}: {u.title} "
                   f"({', '.join(u.tags or []) or 'untagged'})")
    for g in db.scalars(select(models.Goal).order_by(models.Goal.id)):
        out.append(f"goal ({g.status}): {g.title} -> {g.target}")
    for c in db.scalars(select(models.FeatureCheckoff)
                        .order_by(models.FeatureCheckoff.feature_name)):
        out.append(f"checked off: {c.feature_name}")
    return out


def render_delta_prompt(ctx: dict) -> str:
    def listing(items) -> str:
        return ", ".join(items) if items else "(none)"

    lines = [f"## Standing assessment ({ctx['assessment_day']})",
             ctx["assessment_summary"] or "(none)"]
    lines.append("\nIts open recommendations:")
    for r in ctx["assessment_recommendations"]:
        lines.append(f"- {r['title']} -> {r['target']}")
    if not ctx["assessment_recommendations"]:
        lines.append("- (none)")

    lines.append("\n## What has changed since")
    for c in ctx["changed"]:
        lines.append(f"- {c}")
    if not ctx["changed"]:
        lines.append("- (nothing material)")

    lines.append("\n## Recommendation history")
    for h in ctx["history"]:
        if h["outcome"] == "converted":
            fate = "became a goal"
        elif h["outcome"] == "dismissed":
            fate = "dismissed"
        else:
            fate = "never acted on"
        lines.append(f"- {h['target']}: suggested {h['times']}x between "
                     f"{h['first']} and {h['last']}, {fate}")
    if not ctx["history"]:
        lines.append("- (nothing suggested yet)")

    lines.append("\n## Allowed recommendation targets")
    lines.append(f"tags never built: {listing(ctx['never_built'])}")
    lines.append(f"tags stale over {STALE_DAYS} days: {listing(ctx['stale'])}")
    lines.append(f"Claude Code features never adopted: {listing(ctx['adoption_gaps'])}")
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/web/test_brief_history.py -q`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/brief.py tests/web/test_brief_history.py
git commit -m "feat(brief): track recommendation outcomes and build delta context"
```

---

### Task 5: Structured generation — two model paths and the writer

**Files:**
- Modify: `apps/coach_web/brief.py`
- Modify: `tests/web/test_brief.py:130-141` (narrow the pinned parameter test)
- Test: `tests/web/test_brief_generate.py` (create)

**Interfaces:**
- Consumes: `build_corpus_context`, `render_corpus_prompt` (Task 3); `build_delta_context`, `render_delta_prompt` (Task 4).
- Produces: `brief.BRIEF_SCHEMA: dict`; `brief.ASSESSMENT_MODEL = "claude-sonnet-5"`; `brief.generate_assessment(db, client_factory=..., now=None) -> models.Brief`; `brief.generate_delta(db, assessment, client_factory=..., now=None) -> models.Brief`.

- [ ] **Step 1: Narrow the pinned Haiku parameter test**

The existing test asserts `"output_config" not in sent`. That over-states the real constraint: Haiku 4.5 rejects **`effort`**, which lives *inside* `output_config` — but `output_config.format` (structured outputs) is supported on Haiku 4.5 and is how the brief now returns JSON. Banning the whole key would block the feature.

Replace `tests/web/test_brief.py:130-141` with:

```python
def test_delta_sends_no_effort_thinking_or_cache_control(tmp_path):
    # Haiku 4.5 rejects `effort`, uses the older budget_tokens thinking form,
    # and has a 4096-token cache minimum this payload never reaches. It DOES
    # support output_config.format (structured outputs), which is how the brief
    # returns JSON -- so this pins the three forbidden things precisely rather
    # than banning the whole output_config key. Do not widen it back.
    with make_db(tmp_path) as db:
        assessment = models.Brief(created_at="2026-08-01T07:00:00+00:00",
                                  kind="assessment", day="2026-08-01",
                                  model="m", status="ok", body="standing read")
        db.add(assessment)
        db.commit()
        client = FakeClient(reply='{"summary": "s", "recommendations": []}')
        brief.generate_delta(db, assessment, client_factory=lambda: client)
        sent = client.messages.kwargs
        assert "thinking" not in sent
        assert "cache_control" not in sent
        assert "effort" not in sent.get("output_config", {})
        assert sent["output_config"]["format"]["type"] == "json_schema"
        assert sent["max_tokens"] == MAX_TOKENS
```

Add `from apps.coach_web.brief import MAX_TOKENS` to that file's imports, or reference `brief.MAX_TOKENS` — either is fine as long as it resolves.

- [ ] **Step 2: Write the failing tests**

Create `tests/web/test_brief_generate.py`:

```python
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

NOW = datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc)
TODAY = date(2026, 8, 11)


class FakeMessages:
    def __init__(self, reply, raises=None):
        self.reply, self.raises, self.kwargs = reply, raises, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.raises:
            raise self.raises
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.reply)],
            usage=SimpleNamespace(input_tokens=500, output_tokens=250,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0))


class FakeClient:
    def __init__(self, reply, raises=None):
        self.messages = FakeMessages(reply, raises)


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/gen.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def seed(db):
    db.add(models.FeatureUnit(key="u1", kind="spec", repo="alpha",
                              date="2026-08-05", title="Login",
                              tags=["auth"], complexity=3, summary="did login",
                              model="m"))
    db.commit()


def payload(targets):
    return json.dumps({
        "summary": "You ship fast and deploy by hand.",
        "recommendations": [
            {"title": f"Do {t}", "kind": "tag", "target": t,
             "why": "because", "evidence": "1 unit, no containers"}
            for t in targets],
    })


def test_assessment_stores_summary_and_recommendations(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient(payload(["deploy-docker", "websockets-sse"]))
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        assert row.status == "ok"
        assert row.kind == "assessment"
        assert row.day == "2026-08-11"
        assert row.body == "You ship fast and deploy by hand."
        assert row.model == brief.ASSESSMENT_MODEL
        recs = list(db.scalars(select(models.BriefRecommendation)
                               .order_by(models.BriefRecommendation.ord)))
        assert [r.target for r in recs] == ["deploy-docker", "websockets-sse"]
        assert recs[0].evidence == "1 unit, no containers"
        assert recs[0].outcome == "open"


def test_assessment_sends_effort_and_adaptive_thinking(tmp_path):
    # The assessment runs twice a month over ~25k tokens; it is the one call
    # where quality shows. Sonnet 5 accepts both.
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient(payload(["deploy-docker"]))
        brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        sent = client.messages.kwargs
        assert sent["thinking"] == {"type": "adaptive"}
        assert sent["output_config"]["effort"] == "medium"
        assert sent["output_config"]["format"]["type"] == "json_schema"


def test_out_of_vocabulary_target_is_dropped_and_siblings_survive(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient(payload(["deploy-docker", "not-a-real-tag"]))
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        recs = list(db.scalars(select(models.BriefRecommendation)))
        assert [r.target for r in recs] == ["deploy-docker"]
        assert row.status == "ok"


def test_unparseable_json_degrades_instead_of_failing(tmp_path):
    # A degraded brief beats no brief; `failed` stays reserved for call failures,
    # where a run of failed rows is the only signal of a misconfigured key.
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient("Build a Docker pipeline first.")
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        assert row.status == "ok"
        assert row.body == "Build a Docker pipeline first."
        assert db.scalars(select(models.BriefRecommendation)).all() == []


def test_call_failure_records_a_failed_row_and_does_not_raise(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient("", raises=RuntimeError("429"))
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        assert row.status == "failed"
        assert "RuntimeError: 429" in row.error


def test_missing_api_key_records_a_failed_row(tmp_path):
    with make_db(tmp_path) as db:
        row = brief.generate_assessment(db, client_factory=lambda: None, now=NOW)
        db.commit()
        assert row.status == "failed"
        assert "ANTHROPIC_API_KEY" in row.error


def test_reissuing_a_target_supersedes_the_prior_open_row(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        first = brief.generate_assessment(
            db, client_factory=lambda: FakeClient(payload(["deploy-docker"])), now=NOW)
        db.commit()
        old = db.scalars(select(models.BriefRecommendation)
                         .where(models.BriefRecommendation.brief_id == first.id)).one()
        brief.generate_delta(
            db, first,
            client_factory=lambda: FakeClient(payload(["deploy-docker"])), now=NOW)
        db.commit()
        db.refresh(old)
        assert old.outcome == "superseded"
        live = db.scalars(select(models.BriefRecommendation)
                          .where(models.BriefRecommendation.outcome == "open")).all()
        assert len(live) == 1


def test_generation_reports_its_spend_to_llm_daily(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        brief.generate_assessment(
            db, client_factory=lambda: FakeClient(payload(["deploy-docker"])), now=NOW)
        db.commit()
        rows = list(db.scalars(select(models.LlmDaily)))
        assert rows and rows[0].app == "app-builder-coach"
        assert rows[0].cost_usd > 0


def test_assessment_honours_the_model_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_ASSESSMENT_MODEL", "claude-opus-5")
    with make_db(tmp_path) as db:
        seed(db)
        row = brief.generate_assessment(
            db, client_factory=lambda: FakeClient(payload(["deploy-docker"])), now=NOW)
        assert row.model == "claude-opus-5"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_brief_generate.py -q`
Expected: FAIL — `AttributeError: module 'apps.coach_web.brief' has no attribute 'generate_assessment'`

- [ ] **Step 4: Implement the schema, both paths, and the writer**

Add to `apps/coach_web/brief.py`:

```python
ASSESSMENT_MODEL = "claude-sonnet-5"

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "kind": {"type": "string", "enum": ["tag", "feature"]},
                    "target": {"type": "string"},
                    "why": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["title", "kind", "target", "why", "evidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "recommendations"],
    "additionalProperties": False,
}

ASSESSMENT_SYSTEM = (
    "You are a build coach for a solo developer's side projects. You are shown "
    "their entire build history: every unit of work with its title, prose "
    "summary and complexity, per-repo rollups, activity and spend by month, a "
    "rubric grade with per-gap best-fit repos, Claude Code adoption, and their "
    "goals, check-offs and dismissals. Write a standing assessment: a summary "
    "of where they actually are and the pattern in what they build, then 3-5 "
    "ranked recommendations. Every recommendation's `target` MUST be copied "
    "exactly from the Allowed recommendation targets section. Every "
    "`evidence` MUST cite specific work they shipped -- repos, counts, titles -- "
    "and must not restate the recommendation. Be direct and concrete."
)

DELTA_SYSTEM = (
    "You are a build coach for a solo developer's side projects. You are shown "
    "the current standing assessment, what has changed since it was written, "
    "and the history of every recommendation made so far. Write a short "
    "amendment: what the change means, and 0-2 recommendations. Do not restate "
    "the assessment. A recommendation that has been made three or more times "
    "and was never acted on must either be argued on materially different "
    "grounds or dropped in favour of something else. Returning zero "
    "recommendations is a correct answer when nothing warrants one. Every "
    "`target` MUST be copied exactly from the Allowed recommendation targets "
    "section."
)


def _parse(text: str) -> tuple[str, list[dict]]:
    """(summary, recommendations). Unparseable output degrades to prose."""
    try:
        data = json.loads(text)
        return str(data["summary"]), list(data["recommendations"])
    except (ValueError, KeyError, TypeError):
        log.warning("brief response was not the expected JSON; storing as prose")
        return text, []


def _store(db, row: models.Brief, summary: str, recs: list[dict],
           allowed: set[str], now: datetime) -> None:
    """Write the summary and its recommendations, superseding prior open rows.

    A target outside `allowed` is dropped rather than stored: nothing in the UI
    could act on a dangling target, and its siblings are still good.
    """
    row.body = summary
    kept = 0
    for rec in recs:
        target = str(rec.get("target", ""))
        if target not in allowed:
            log.warning("dropping recommendation with unknown target %r", target)
            continue
        for prior in db.scalars(
                select(models.BriefRecommendation)
                .where(models.BriefRecommendation.target == target,
                       models.BriefRecommendation.outcome == "open")):
            prior.outcome = "superseded"
            prior.outcome_at = now.isoformat()
        db.add(models.BriefRecommendation(
            brief=row, ord=kept, title=str(rec.get("title", ""))[:200],
            kind=("feature" if rec.get("kind") == "feature" else "tag"),
            target=target[:120], why=str(rec.get("why", "")),
            evidence=str(rec.get("evidence", ""))))
        kept += 1


def _call(db, row: models.Brief, client_factory, system: str, prompt: str,
          params: dict, now: datetime) -> str:
    """Shared call + accounting. Returns the response text, or "" on failure."""
    client = client_factory()
    if client is None:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set; the server cannot generate briefs")
    response = client.messages.create(
        model=row.model, system=system,
        messages=[{"role": "user", "content": prompt}], **params)
    usage = {
        "input_tokens": getattr(response.usage, "input_tokens", 0) or 0,
        "output_tokens": getattr(response.usage, "output_tokens", 0) or 0,
        "cache_read_input_tokens":
            getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens":
            getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
    }
    from . import usage_api
    row.input_tokens = usage["input_tokens"]
    row.output_tokens = usage["output_tokens"]
    row.cost_usd = usage_api.cost_for(row.model, usage)
    # The server's own spend is real spend; unreported it resurfaces as drift.
    usage_api.upsert_llm_daily(db, now.date().isoformat(),
                               "app-builder-coach", row.model, usage)
    return _text_of(response)


def _new_row(kind: str, model: str, now: datetime, fp: str) -> models.Brief:
    return models.Brief(created_at=now.isoformat(), day=now.date().isoformat(),
                        kind=kind, model=model, status="ok", fingerprint=fp)


def generate_assessment(db, client_factory=_client_factory,
                        now: datetime | None = None) -> models.Brief:
    """Deep pass over the whole corpus. Never raises. Caller commits."""
    now = now or datetime.now(timezone.utc)
    model = os.environ.get("COACH_ASSESSMENT_MODEL") or ASSESSMENT_MODEL
    row = _new_row("assessment", model, now, fingerprint(db, now.date()))
    db.add(row)
    try:
        ctx = build_corpus_context(db, now.date())
        allowed = set(ctx["never_built"]) | set(ctx["stale"]) | set(ctx["adoption_gaps"])
        text = _call(db, row, client_factory, ASSESSMENT_SYSTEM,
                     render_corpus_prompt(ctx),
                     {"max_tokens": ASSESSMENT_MAX_TOKENS,
                      "thinking": {"type": "adaptive"},
                      "output_config": {
                          "effort": "medium",
                          "format": {"type": "json_schema",
                                     "schema": BRIEF_SCHEMA}}},
                     now)
        summary, recs = _parse(text)
        _store(db, row, summary, recs, allowed, now)
    except Exception as exc:
        log.exception("assessment generation failed")
        row.status = "failed"
        row.error = f"{type(exc).__name__}: {exc}"[:500]
        row.body = ""
    return row


def generate_delta(db, assessment, client_factory=_client_factory,
                   now: datetime | None = None) -> models.Brief:
    """Short amendment against the standing assessment. Never raises."""
    now = now or datetime.now(timezone.utc)
    model = os.environ.get("COACH_BRIEF_MODEL") or DEFAULT_MODEL
    row = _new_row("delta", model, now, fingerprint(db, now.date()))
    db.add(row)
    try:
        ctx = build_delta_context(db, now.date(), assessment)
        allowed = set(ctx["never_built"]) | set(ctx["stale"]) | set(ctx["adoption_gaps"])
        # No effort, no thinking, no cache_control: all three are wrong for
        # claude-haiku-4-5. output_config.format IS supported and is required.
        text = _call(db, row, client_factory, DELTA_SYSTEM,
                     render_delta_prompt(ctx),
                     {"max_tokens": MAX_TOKENS,
                      "output_config": {"format": {"type": "json_schema",
                                                   "schema": BRIEF_SCHEMA}}},
                     now)
        summary, recs = _parse(text)
        _store(db, row, summary, recs, allowed, now)
    except Exception as exc:
        log.exception("delta generation failed")
        row.status = "failed"
        row.error = f"{type(exc).__name__}: {exc}"[:500]
        row.body = ""
    return row
```

Add the ORM back-reference so `_store`'s `brief=row` works. In `models.py`, add to `class Brief`:

```python
    recommendations: Mapped[list["BriefRecommendation"]] = relationship(
        back_populates="brief", cascade="all, delete-orphan")
```

and to `class BriefRecommendation`:

```python
    brief: Mapped["Brief"] = relationship(back_populates="recommendations")
```

Add `relationship` to the `sqlalchemy.orm` import line in `models.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_brief_generate.py tests/web/test_brief.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/coach_web/brief.py apps/coach_web/models.py tests/web/test_brief_generate.py tests/web/test_brief.py
git commit -m "feat(brief): structured assessment and delta generation"
```

---

### Task 6: Wire the gate into post_ingest

**Files:**
- Modify: `apps/coach_web/brief.py`
- Modify: `apps/coach_web/ingest.py:99-121`
- Test: `tests/web/test_brief_decide.py` (create)

**Interfaces:**
- Consumes: `generate_assessment`, `generate_delta` (Task 5); `fingerprint`, `get_state`, `set_state` (Task 2).
- Produces: `brief.MAX_DELTAS_BEFORE_REASSESS = 5`; `brief.latest_assessment(db) -> models.Brief | None`; `brief.decide_and_generate(db, client_factory=..., now=None, force=False) -> str` returning one of `"assessment"`, `"delta"`, `"skipped"`.

- [ ] **Step 1: Write the failing test**

Create `tests/web/test_brief_decide.py`:

```python
import json
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

NOW = datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc)


class Recorder:
    """Counts calls so 'no model call at all' is provable, not assumed."""
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(
                {"summary": "s", "recommendations": []}))],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0))


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/d.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def seed(db, key="u1", day="2026-08-05"):
    db.add(models.FeatureUnit(key=key, kind="spec", repo="alpha", date=day,
                              title="t", tags=["auth"], complexity=3,
                              summary="s", model="m"))
    db.commit()


def test_first_run_generates_an_assessment(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "assessment"
        db.commit()
        assert rec.calls == 1
        assert db.scalars(select(models.Brief)).one().kind == "assessment"


def test_unchanged_fingerprint_makes_no_model_call(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "skipped"
        db.commit()
        assert rec.calls == 1                       # no second call
        assert len(db.scalars(select(models.Brief)).all()) == 1   # no second row


def test_a_changed_fingerprint_generates_a_delta(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        seed(db, key="u2", day="2026-08-09")
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "delta"
        db.commit()
        kinds = [b.kind for b in db.scalars(select(models.Brief)
                                            .order_by(models.Brief.id))]
        assert kinds == ["assessment", "delta"]


def test_five_deltas_trigger_a_reassessment(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        for i in range(brief.MAX_DELTAS_BEFORE_REASSESS):
            seed(db, key=f"x{i}", day="2026-08-09")
            assert brief.decide_and_generate(db, lambda: rec, NOW) == "delta"
            db.commit()
        seed(db, key="final", day="2026-08-09")
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "assessment"
        db.commit()
        assert brief.get_state(db, brief.DELTA_COUNT_KEY) == "0"


def test_force_reassesses_regardless_of_fingerprint(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        assert brief.decide_and_generate(db, lambda: rec, NOW, force=True) == "assessment"
        db.commit()
        assert rec.calls == 2


class Boom:
    def __init__(self):
        self.messages = self

    def create(self, **kwargs):
        raise RuntimeError("429")


def test_a_failed_delta_still_records_a_row(tmp_path):
    # A run of failed rows is the only visible signal of a misconfigured key.
    with make_db(tmp_path) as db:
        seed(db)
        brief.decide_and_generate(db, lambda: Recorder(), NOW)
        db.commit()
        seed(db, key="u2", day="2026-08-09")
        brief.decide_and_generate(db, lambda: Boom(), NOW)
        db.commit()
        newest = db.scalars(select(models.Brief)
                            .order_by(models.Brief.id.desc())).first()
        assert newest.status == "failed"


def test_a_failed_generation_does_not_consume_the_change(tmp_path):
    # If a failure advanced the fingerprint, the change it was meant to report
    # would be skipped forever. It must retry on the next ingest.
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        seed(db, key="u2", day="2026-08-09")
        brief.decide_and_generate(db, lambda: Boom(), NOW)
        db.commit()
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "delta"
        db.commit()
        newest = db.scalars(select(models.Brief)
                            .order_by(models.Brief.id.desc())).first()
        assert newest.status == "ok"


def test_a_quiet_stretch_does_not_trigger_reassessment(tmp_path):
    # The skip check outranks the delta counter: five deltas followed by
    # silence must stay silent.
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        for i in range(brief.MAX_DELTAS_BEFORE_REASSESS):
            seed(db, key=f"y{i}", day="2026-08-09")
            brief.decide_and_generate(db, lambda: rec, NOW)
            db.commit()
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "skipped"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_brief_decide.py -q`
Expected: FAIL — `AttributeError: module 'apps.coach_web.brief' has no attribute 'decide_and_generate'`

- [ ] **Step 3: Implement the decision procedure**

Add to `apps/coach_web/brief.py`:

```python
MAX_DELTAS_BEFORE_REASSESS = 5


def latest_assessment(db) -> models.Brief | None:
    return db.scalar(select(models.Brief)
                     .where(models.Brief.kind == "assessment",
                            models.Brief.status == "ok")
                     .order_by(models.Brief.id.desc()).limit(1))


def decide_and_generate(db, client_factory=_client_factory,
                        now: datetime | None = None, force: bool = False) -> str:
    """Generate a brief only when there is something new to say.

    Returns "assessment", "delta" or "skipped". A "skipped" result means no
    model call was made at all -- this is the cost control, and the reason the
    page is not a wall of near-identical essays.
    """
    now = now or datetime.now(timezone.utc)
    today = now.date()
    fp = fingerprint(db, today)
    assessment = latest_assessment(db)
    seen = get_state(db, FP_KEY)
    try:
        deltas = int(get_state(db, DELTA_COUNT_KEY, "0"))
    except ValueError:
        deltas = 0

    def assess() -> str:
        row = generate_assessment(db, client_factory=client_factory, now=now)
        # A failed generation must NOT advance the fingerprint: the change it
        # was meant to report is still unreported, so the next ingest has to
        # retry rather than skip it forever.
        if row.status == "ok":
            set_state(db, FP_KEY, fp, now)
            set_state(db, DELTA_COUNT_KEY, "0", now)
        return "assessment"

    if force or assessment is None:
        return assess()

    # Nothing moved: this is the whole cost control, and it outranks the
    # delta counter -- a quiet stretch must not trigger a reassessment.
    if fp == seen:
        return "skipped"

    if deltas >= MAX_DELTAS_BEFORE_REASSESS:
        return assess()

    row = generate_delta(db, assessment, client_factory=client_factory, now=now)
    if row.status == "ok":
        set_state(db, FP_KEY, fp, now)
        set_state(db, DELTA_COUNT_KEY, str(deltas + 1), now)
    return "delta"
```

- [ ] **Step 4: Rewire `post_ingest`**

In `apps/coach_web/ingest.py`, replace the body of the `with _Session(engine) as db:` block (lines 108-116) with:

```python
        with _Session(engine) as db:
            kwargs = {} if client_factory is None else {"client_factory": client_factory}
            out["brief"] = brief_mod.decide_and_generate(db, now=now, **kwargs)
            when = now or datetime.now(timezone.utc)
            if changelog_mod.due(db, when):
                kw = {} if fetch is None else {"fetch": fetch}
                out["changelog"] = changelog_mod.check(db, now=when, **kw)
            db.commit()
```

Note `decide_and_generate` takes `client_factory` as its first positional; passing it as a keyword works unchanged.

- [ ] **Step 5: Delete the superseded code path**

`post_ingest` was the only caller of `brief.generate`, which was the only caller
of `build_context` and `render_prompt`. All three are now dead. Leaving them
would leave two brief implementations in one module, only one of which runs.

From `apps/coach_web/brief.py`, delete:
- `def generate(...)` (the original, ~lines 138-186)
- `def build_context(...)`
- `def render_prompt(...)`
- the `SYSTEM` constant (superseded by `ASSESSMENT_SYSTEM` / `DELTA_SYSTEM`)

Keep `_client_factory`, `_text_of`, `_week_range`, `DEFAULT_MODEL`, `MAX_TOKENS`
and `STALE_DAYS` — all are still used.

From `tests/web/test_brief.py`, delete the tests that exercised only the removed
functions. Each has an equivalent in `tests/web/test_brief_generate.py`, noted
here so nothing is lost:

| Delete | Covered instead by |
|---|---|
| `test_context_splits_this_week_from_last` | — (weekly split is gone by design) |
| `test_context_carries_gap_lists` | `test_brief_corpus.py::test_corpus_names_dismissals_explicitly` |
| `test_context_on_empty_database` | `test_brief_corpus.py::test_corpus_on_empty_database` |
| `test_render_prompt_mentions_the_real_numbers` | `test_brief_corpus.py::test_corpus_carries_titles_and_summaries` |
| `test_generate_writes_an_ok_brief` | `test_assessment_stores_summary_and_recommendations` |
| `test_generate_reports_its_own_spend_to_llm_daily` | `test_generation_reports_its_spend_to_llm_daily` |
| `test_generate_records_failure_and_does_not_raise` | `test_call_failure_records_a_failed_row_and_does_not_raise` |
| `test_generate_records_failure_when_no_api_key` | `test_missing_api_key_records_a_failed_row` |
| `test_generate_honours_the_model_env_override` | `test_assessment_honours_the_model_env_override` |

Keep in `test_brief.py`: `test_delta_sends_no_effort_thinking_or_cache_control`
(rewritten in Task 5), `test_upsert_llm_daily_accumulates`, and all four
`post_ingest` / `ingest` tests.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_brief_decide.py tests/web/test_brief.py tests/web/test_ingest.py -q`
Expected: PASS. `test_post_ingest_generates_a_brief_and_runs_the_watcher` now sees `out["brief"] == "assessment"` on a fresh DB — update that assertion, which currently pins the old `"ok"` string.

- [ ] **Step 7: Run the full backend suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS — no test references a removed function.

- [ ] **Step 8: Commit**

```bash
git add apps/coach_web/brief.py apps/coach_web/ingest.py tests/web/test_brief_decide.py tests/web/test_brief.py tests/web/test_ingest.py
git commit -m "feat(brief): gate generation on a real change"
```

---

### Task 7: Outcome propagation and the reassess endpoint

**Files:**
- Modify: `apps/coach_web/writes.py:63-70` (create_goal), `:154-167` (create_dismissal)
- Test: `tests/web/test_writes.py`

**Interfaces:**
- Consumes: `models.BriefRecommendation` (Task 1); `brief.decide_and_generate` (Task 6).
- Produces: `POST /api/reassess` returning `{"status": "<assessment|delta|skipped>"}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_writes.py`:

```python
from sqlalchemy.orm import Session

from apps.coach_web import models


def _seed_recommendation(client, target="deploy-docker", kind="tag"):
    """Insert a brief + open recommendation directly, via the app's engine."""
    with Session(client.app.state.engine) as db:
        b = models.Brief(created_at="2026-08-01T07:00:00+00:00", kind="assessment",
                         day="2026-08-01", model="m", status="ok", body="s")
        db.add(b)
        db.commit()
        rec = models.BriefRecommendation(brief_id=b.id, ord=0, title="t",
                                         kind=kind, target=target, why="w",
                                         evidence="e")
        db.add(rec)
        db.commit()
        return rec.id


def _outcome(client, rec_id):
    with Session(client.app.state.engine) as db:
        return db.get(models.BriefRecommendation, rec_id).outcome


def test_creating_a_goal_converts_matching_recommendations(client):
    login(client)
    rec_id = _seed_recommendation(client)
    resp = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "deploy-docker", "title": "Containerize"})
    assert resp.status_code == 200
    assert _outcome(client, rec_id) == "converted"


def test_creating_an_unrelated_goal_leaves_recommendations_open(client):
    login(client)
    rec_id = _seed_recommendation(client)
    client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "scraping", "title": "Scrape"})
    assert _outcome(client, rec_id) == "open"


def test_dismissing_a_target_dismisses_its_recommendations(client):
    login(client)
    rec_id = _seed_recommendation(client)
    resp = client.post("/api/dismissals", headers=ORIGIN, json={
        "kind": "tag", "target": "deploy-docker", "reason": "not now"})
    assert resp.status_code == 200
    assert _outcome(client, rec_id) == "dismissed"


def test_dismissing_twice_still_marks_recommendations(client):
    # The idempotent early-return path must mark too, or a second tab's
    # dismissal silently leaves the recommendation open.
    login(client)
    body = {"kind": "tag", "target": "deploy-docker", "reason": ""}
    client.post("/api/dismissals", headers=ORIGIN, json=body)
    rec_id = _seed_recommendation(client)
    client.post("/api/dismissals", headers=ORIGIN, json=body)
    assert _outcome(client, rec_id) == "dismissed"


def test_reassess_requires_login(client):
    assert client.post("/api/reassess", headers=ORIGIN,
                       json={}).status_code == 401


def test_reassess_rejects_a_foreign_origin(client):
    login(client)
    resp = client.post("/api/reassess", json={},
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
```

`login`, `ORIGIN` and the `client` fixture already exist in this file — do not add a new fixture.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_writes.py -q -k "converts or unrelated or dismisses or reassess"`
Expected: FAIL — outcome stays `"open"`; `/api/reassess` 404s

- [ ] **Step 3: Implement propagation and the endpoint**

In `apps/coach_web/writes.py`, add after the `_goal_json` helper:

```python
def _mark_recommendations(db, kind: str, target: str, outcome: str) -> None:
    """Close out every open recommendation for a target.

    Prior briefs are marked too, not just the newest -- every one of them was
    pitching the same thing, and the count of times-suggested is what feeds
    back into the next prompt.
    """
    for rec in db.scalars(select(models.BriefRecommendation).where(
            models.BriefRecommendation.kind == kind,
            models.BriefRecommendation.target == target,
            models.BriefRecommendation.outcome == "open")):
        rec.outcome = outcome
        rec.outcome_at = _now()
```

In `create_goal`, insert before `db.commit()`:

```python
    _mark_recommendations(db, body.kind, body.target, "converted")
```

In `create_dismissal`, insert before the final `db.commit()` (after `db.add(row)`):

```python
    _mark_recommendations(db, body.kind, body.target, "dismissed")
```

Also mark on the idempotent early-return path — replace the `return _dismissal_json(existing)` line with:

```python
    if existing is not None:
        _mark_recommendations(db, body.kind, body.target, "dismissed")
        db.commit()
        return _dismissal_json(existing)
```

Append the endpoint at the end of `writes.py`:

```python
@router.post("/api/reassess")
def reassess(request: Request, db: Session = Depends(get_db)):
    """Force a fresh standing assessment, bypassing the change gate."""
    from . import brief as brief_mod
    result = brief_mod.decide_and_generate(db, force=True)
    db.commit()
    return {"status": result}
```

Add `Request` to the `fastapi` import line and `from . import brief` is deliberately deferred inside the function to avoid an import cycle at module load.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_writes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/writes.py tests/web/test_writes.py
git commit -m "feat(writes): propagate recommendation outcomes, add reassess"
```

---

### Task 8: Reshape /api/briefs

**Files:**
- Modify: `apps/coach_web/api.py:353-372`
- Test: `tests/web/test_briefs_api.py`

**Interfaces:**
- Consumes: `models.BriefRecommendation` (Task 1); `brief.recommendation_history` (Task 4).
- Produces: `GET /api/briefs` returning `{assessment, deltas, history, recurring}`. `assessment` is `null` or `{created_at, day, summary, stale, error, recommendations: [{id, title, kind, target, why, evidence, outcome}]}`. `deltas` and `history` are lists of `{created_at, day, kind, status, summary, error, recommendations}`. `recurring` is `recommendation_history` output.

- [ ] **Step 1: Write the failing test**

Replace everything in `tests/web/test_briefs_api.py` below its existing `login()` helper (keep the imports and `login`; the old `add_brief` helper and all six old tests go):

```python
def _add(client, day, kind, status="ok", body="s", targets=()):
    with Session(client.app.state.engine) as db:
        row = models.Brief(created_at=f"{day}T07:00:00+00:00", kind=kind,
                           day=day, model="m", status=status, body=body)
        db.add(row)
        db.commit()
        for i, t in enumerate(targets):
            db.add(models.BriefRecommendation(brief_id=row.id, ord=i,
                                              title=f"Do {t}", kind="tag",
                                              target=t, why="w", evidence="e"))
        db.commit()
        return row.id


def test_briefs_requires_login(client):
    assert client.get("/api/briefs").status_code == 401


def test_briefs_empty(client):
    login(client)
    body = client.get("/api/briefs").json()
    assert body["assessment"] is None
    assert body["deltas"] == []
    assert body["history"] == []
    assert body["recurring"] == []


def test_assessment_carries_its_recommendations(client):
    login(client)
    _add(client, "2026-08-01", "assessment", targets=("deploy-docker",))
    body = client.get("/api/briefs").json()
    assert body["assessment"]["day"] == "2026-08-01"
    assert body["assessment"]["recommendations"][0]["target"] == "deploy-docker"
    assert body["assessment"]["stale"] is False


def test_deltas_are_those_after_the_assessment(client):
    login(client)
    _add(client, "2026-08-01", "delta", body="old news")
    _add(client, "2026-08-05", "assessment", body="standing read")
    _add(client, "2026-08-09", "delta", body="new news")
    body = client.get("/api/briefs").json()
    assert [d["summary"] for d in body["deltas"]] == ["new news"]
    assert [h["summary"] for h in body["history"]] == ["old news"]


def test_a_failed_assessment_keeps_the_last_good_one_flagged_stale(client):
    login(client)
    _add(client, "2026-08-01", "assessment", body="standing read")
    _add(client, "2026-08-09", "assessment", status="failed", body="")
    body = client.get("/api/briefs").json()
    assert body["assessment"]["summary"] == "standing read"
    assert body["assessment"]["stale"] is True


def test_recurring_rollup_counts_repeats(client):
    login(client)
    _add(client, "2026-08-01", "assessment", targets=("deploy-docker",))
    _add(client, "2026-08-05", "delta", targets=("deploy-docker",))
    body = client.get("/api/briefs").json()
    entry = body["recurring"][0]
    assert entry["target"] == "deploy-docker"
    assert entry["times"] == 2


def test_legacy_prose_briefs_still_render(client):
    # The ten pre-existing rows have kind "delta", no day, no recommendations.
    login(client)
    with Session(client.app.state.engine) as db:
        db.add(models.Brief(created_at="2026-08-12T07:00:00+00:00", model="m",
                            status="ok", body="a wall of prose"))
        db.commit()
    body = client.get("/api/briefs").json()
    assert body["assessment"] is None
    assert body["history"][0]["summary"] == "a wall of prose"
    assert body["history"][0]["recommendations"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_briefs_api.py -q`
Expected: FAIL — `KeyError: 'assessment'`

- [ ] **Step 3: Implement the endpoint**

Replace `apps/coach_web/api.py:353-372` with:

```python
def _rec_json(r) -> dict:
    return {"id": r.id, "title": r.title, "kind": r.kind, "target": r.target,
            "why": r.why, "evidence": r.evidence, "outcome": r.outcome}


def _brief_json(row) -> dict:
    return {"created_at": row.created_at, "day": row.day or row.created_at[:10],
            "kind": row.kind, "status": row.status, "summary": row.body,
            "error": row.error,
            "recommendations": [_rec_json(r) for r in
                                sorted(row.recommendations, key=lambda r: r.ord)]}


@router.get("/api/briefs")
def briefs(limit: int = 30, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    rows = list(db.scalars(select(models.Brief)
                           .order_by(models.Brief.created_at.desc())
                           .limit(limit)))

    # A failed newest assessment must not blank the page: fall back to the last
    # good one and say plainly that it is stale, carrying the failure's message.
    newest_assessment = next((r for r in rows if r.kind == "assessment"), None)
    good = next((r for r in rows
                 if r.kind == "assessment" and r.status == "ok"), None)
    assessment = None
    if good is not None:
        assessment = _brief_json(good)
        assessment["stale"] = newest_assessment is not good
        assessment["error"] = (newest_assessment.error
                               if newest_assessment is not good else "")

    cutoff = good.created_at if good is not None else None
    deltas, history = [], []
    for r in rows:
        if r is good:
            continue
        if cutoff is not None and r.created_at > cutoff and r.kind == "delta":
            deltas.append(_brief_json(r))
        else:
            history.append(_brief_json(r))

    return {"assessment": assessment, "deltas": deltas, "history": history,
            "recurring": brief_mod.recommendation_history(db)}
```

Add `from . import brief as brief_mod` to the imports at the top of `api.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_briefs_api.py -q`
Expected: PASS (8 tests)

- [ ] **Step 5: Run the full backend suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add apps/coach_web/api.py tests/web/test_briefs_api.py
git commit -m "feat(api): reshape /api/briefs around assessment and deltas"
```

---

### Task 9: RecommendationCard and AssessmentCard

**Files:**
- Create: `apps/coach_web/frontend/src/components/RecommendationCard.tsx`
- Create: `apps/coach_web/frontend/src/components/AssessmentCard.tsx`
- Test: `apps/coach_web/frontend/src/__tests__/AssessmentCard.test.tsx` (create)

**Interfaces:**
- Consumes: `/api/briefs` shape from Task 8.
- Produces: exported types `Recommendation = {id: number; title: string; kind: string; target: string; why: string; evidence: string; outcome: string}` and `Brief = {created_at: string; day: string; kind: string; status: string; summary: string; error: string; recommendations: Recommendation[]}` and `Assessment = Brief & {stale: boolean}` from `AssessmentCard.tsx`; components `RecommendationCard({rec, onAdd, onDismiss})` and `AssessmentCard({assessment, onAdd, onDismiss, onReassess, busy})`.

- [ ] **Step 1: Write the failing test**

Create `apps/coach_web/frontend/src/__tests__/AssessmentCard.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import AssessmentCard, { type Assessment } from "../components/AssessmentCard";

const rec = {
  id: 1, title: "Containerize purchase-inventory", kind: "tag",
  target: "deploy-docker", why: "It is foundational.",
  evidence: "14 units across 5 repos, zero container work.", outcome: "open",
};

const assessment: Assessment = {
  created_at: "2026-08-11T07:00:00+00:00", day: "2026-08-11", kind: "assessment",
  status: "ok", summary: "You ship fast and deploy by hand.", error: "",
  stale: false, recommendations: [rec],
};

describe("AssessmentCard", () => {
  it("shows the summary, the recommendation, and its evidence", () => {
    render(<AssessmentCard assessment={assessment} onAdd={vi.fn()}
      onDismiss={vi.fn()} onReassess={vi.fn()} busy={false} />);
    expect(screen.getByText("You ship fast and deploy by hand.")).toBeInTheDocument();
    expect(screen.getByText("Containerize purchase-inventory")).toBeInTheDocument();
    expect(screen.getByText(/zero container work/)).toBeInTheDocument();
  });

  it("hands the whole recommendation to onAdd", async () => {
    const onAdd = vi.fn();
    render(<AssessmentCard assessment={assessment} onAdd={onAdd}
      onDismiss={vi.fn()} onReassess={vi.fn()} busy={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Add as goal" }));
    expect(onAdd).toHaveBeenCalledWith(rec);
  });

  it("hands the recommendation to onDismiss", async () => {
    const onDismiss = vi.fn();
    render(<AssessmentCard assessment={assessment} onAdd={vi.fn()}
      onDismiss={onDismiss} onReassess={vi.fn()} busy={false} />);
    await userEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledWith(rec);
  });

  it("flags a stale assessment and shows the error", () => {
    render(<AssessmentCard assessment={{ ...assessment, stale: true,
      error: "RuntimeError: 429" }} onAdd={vi.fn()} onDismiss={vi.fn()}
      onReassess={vi.fn()} busy={false} />);
    expect(screen.getByText(/could not be regenerated/)).toBeInTheDocument();
    expect(screen.getByText(/429/)).toBeInTheDocument();
  });

  it("shows an empty state when there is no assessment yet", () => {
    render(<AssessmentCard assessment={null} onAdd={vi.fn()} onDismiss={vi.fn()}
      onReassess={vi.fn()} busy={false} />);
    expect(screen.getByText("No assessment yet")).toBeInTheDocument();
  });

  it("disables Reassess while one is running", () => {
    render(<AssessmentCard assessment={assessment} onAdd={vi.fn()}
      onDismiss={vi.fn()} onReassess={vi.fn()} busy={true} />);
    expect(screen.getByRole("button", { name: /Reassessing/ })).toBeDisabled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix apps/coach_web/frontend test -- AssessmentCard`
Expected: FAIL — cannot resolve `../components/AssessmentCard`

- [ ] **Step 3: Write RecommendationCard**

Create `apps/coach_web/frontend/src/components/RecommendationCard.tsx`:

```tsx
export type Recommendation = {
  id: number; title: string; kind: string; target: string;
  why: string; evidence: string; outcome: string;
};

type Props = {
  rec: Recommendation;
  onAdd: (rec: Recommendation) => void;
  onDismiss: (rec: Recommendation) => void;
};

export default function RecommendationCard({ rec, onAdd, onDismiss }: Props) {
  return (
    <div style={{ borderTop: "1px solid var(--line)", paddingTop: 10, marginTop: 10 }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
        <strong style={{ fontSize: 14, flex: 1 }}>{rec.title}</strong>
        <span className="muted" style={{ fontSize: 12 }}>{rec.target}</span>
      </div>
      <p className="ink2" style={{ fontSize: 13, margin: "6px 0 4px" }}>{rec.why}</p>
      <p className="muted" style={{ fontSize: 12, margin: "0 0 8px" }}>{rec.evidence}</p>
      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" onClick={() => onAdd(rec)}>Add as goal</button>
        <button type="button" onClick={() => onDismiss(rec)}>Dismiss</button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Write AssessmentCard**

Create `apps/coach_web/frontend/src/components/AssessmentCard.tsx`:

```tsx
import { fmtDate } from "../format";
import RecommendationCard, { type Recommendation } from "./RecommendationCard";

export type { Recommendation };

export type Brief = {
  created_at: string; day: string; kind: string; status: string;
  summary: string; error: string; recommendations: Recommendation[];
};

export type Assessment = Brief & { stale: boolean };

type Props = {
  assessment: Assessment | null;
  onAdd: (rec: Recommendation) => void;
  onDismiss: (rec: Recommendation) => void;
  onReassess: () => void;
  busy: boolean;
};

export default function AssessmentCard(
  { assessment, onAdd, onDismiss, onReassess, busy }: Props,
) {
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <h2 style={{ marginTop: 0, fontSize: 15, flex: 1 }}>
          Assessment{" "}
          {assessment && (
            <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>
              {fmtDate(assessment.day)}
            </span>
          )}
        </h2>
        <button type="button" onClick={onReassess} disabled={busy}>
          {busy ? "Reassessing…" : "Reassess"}
        </button>
      </div>

      {!assessment && (
        <>
          <p style={{ fontSize: 14, margin: "4px 0" }}>No assessment yet</p>
          <p className="muted" style={{ fontSize: 13 }}>
            One is written after the next sweep, or press Reassess to build it now.
          </p>
        </>
      )}

      {assessment && (
        <>
          {assessment.stale && (
            <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
              This assessment could not be regenerated — showing the last one that
              worked.{assessment.error ? ` (${assessment.error})` : ""}
            </p>
          )}
          <p className="ink2" style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
            {assessment.summary}
          </p>
          {assessment.recommendations.map((r) => (
            <RecommendationCard key={r.id} rec={r} onAdd={onAdd} onDismiss={onDismiss} />
          ))}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm --prefix apps/coach_web/frontend test -- AssessmentCard`
Expected: PASS (6 tests)

- [ ] **Step 6: Commit**

```bash
git add apps/coach_web/frontend/src/components/RecommendationCard.tsx apps/coach_web/frontend/src/components/AssessmentCard.tsx apps/coach_web/frontend/src/__tests__/AssessmentCard.test.tsx
git commit -m "feat(frontend): assessment and recommendation cards"
```

---

### Task 10: SinceThen, GoalPicker and BriefHistory

**Files:**
- Create: `apps/coach_web/frontend/src/components/SinceThen.tsx`
- Create: `apps/coach_web/frontend/src/components/GoalPicker.tsx`
- Create: `apps/coach_web/frontend/src/components/BriefHistory.tsx`
- Test: `apps/coach_web/frontend/src/__tests__/GoalPicker.test.tsx` (create)
- Test: `apps/coach_web/frontend/src/__tests__/BriefHistory.test.tsx` (create)

**Interfaces:**
- Consumes: `Brief`, `Recommendation` from `AssessmentCard.tsx` (Task 9).
- Produces: `SinceThen({deltas, since})`; `GoalPicker({goals, gaps, onAdd, onDone, onDelete})` where `Goal = {id: number; kind: string; target: string; title: string; target_date: string; status: string}` and `Gaps = {never_built: string[]; stale: string[]; adoption_gaps: string[]}`; `BriefHistory({recurring, history})` where `Recurring = {target: string; kind: string; times: number; first: string; last: string; outcome: string}`.

- [ ] **Step 1: Write the failing tests**

Create `apps/coach_web/frontend/src/__tests__/GoalPicker.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import GoalPicker from "../components/GoalPicker";

const gaps = {
  never_built: ["deploy-docker", "websockets-sse"],
  stale: ["scraping"],
  adoption_gaps: ["background tasks"],
};

describe("GoalPicker", () => {
  it("offers no free-text field", () => {
    render(<GoalPicker goals={[]} gaps={gaps} onAdd={vi.fn()} onDone={vi.fn()}
      onDelete={vi.fn()} />);
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("adds a tag gap with a generated title", async () => {
    const onAdd = vi.fn();
    render(<GoalPicker goals={[]} gaps={gaps} onAdd={onAdd} onDone={vi.fn()}
      onDelete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Add deploy-docker" }));
    expect(onAdd).toHaveBeenCalledWith({
      kind: "tag", target: "deploy-docker", title: "Build something with deploy-docker",
    });
  });

  it("adds a feature gap with an adoption title", async () => {
    const onAdd = vi.fn();
    render(<GoalPicker goals={[]} gaps={gaps} onAdd={onAdd} onDone={vi.fn()}
      onDelete={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Add background tasks" }));
    expect(onAdd).toHaveBeenCalledWith({
      kind: "feature", target: "background tasks", title: "Adopt background tasks",
    });
  });

  it("hides gaps that are already goals", () => {
    render(<GoalPicker gaps={gaps} onAdd={vi.fn()} onDone={vi.fn()} onDelete={vi.fn()}
      goals={[{ id: 1, kind: "tag", target: "deploy-docker",
        title: "Containerize", target_date: "", status: "active" }]} />);
    expect(screen.queryByRole("button", { name: "Add deploy-docker" })).toBeNull();
    expect(screen.getByRole("button", { name: "Add websockets-sse" })).toBeInTheDocument();
  });

  it("lists active goals with done and delete", async () => {
    const onDone = vi.fn();
    render(<GoalPicker gaps={gaps} onAdd={vi.fn()} onDone={onDone} onDelete={vi.fn()}
      goals={[{ id: 7, kind: "tag", target: "deploy-docker",
        title: "Containerize", target_date: "", status: "active" }]} />);
    expect(screen.getByText("Containerize")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onDone).toHaveBeenCalledWith(7);
  });
});
```

Create `apps/coach_web/frontend/src/__tests__/BriefHistory.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import BriefHistory from "../components/BriefHistory";

const recurring = [
  { target: "deploy-docker", kind: "tag", times: 8, first: "2026-08-01",
    last: "2026-08-09", outcome: "open" },
  { target: "background tasks", kind: "feature", times: 2, first: "2026-08-02",
    last: "2026-08-06", outcome: "converted" },
];

const history = [
  { created_at: "2026-08-09T07:00:00+00:00", day: "2026-08-09", kind: "delta",
    status: "ok", summary: "Shipped the cost page.", error: "",
    recommendations: [{ id: 3, title: "Do it", kind: "tag",
      target: "deploy-docker", why: "w", evidence: "e", outcome: "superseded" }] },
];

describe("BriefHistory", () => {
  it("counts repeat suggestions and names what became of them", () => {
    render(<BriefHistory recurring={recurring} history={history} />);
    expect(screen.getByText(/suggested 8×/)).toBeInTheDocument();
    expect(screen.getByText(/never acted on/)).toBeInTheDocument();
    expect(screen.getByText(/became a goal/)).toBeInTheDocument();
  });

  it("collapses each entry behind a summary line naming its targets", () => {
    render(<BriefHistory recurring={recurring} history={history} />);
    expect(screen.getByText(/deploy-docker/)).toBeInTheDocument();
    // The body is present but inside a closed <details>.
    expect(screen.getByText("Shipped the cost page.")).toBeInTheDocument();
    expect(document.querySelector("details")?.open).toBe(false);
  });

  it("shows empty states", () => {
    render(<BriefHistory recurring={[]} history={[]} />);
    expect(screen.getByText("Nothing in the history yet.")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm --prefix apps/coach_web/frontend test -- GoalPicker BriefHistory`
Expected: FAIL — cannot resolve the component modules

- [ ] **Step 3: Write SinceThen**

Create `apps/coach_web/frontend/src/components/SinceThen.tsx`:

```tsx
import { fmtDate } from "../format";
import type { Brief } from "./AssessmentCard";

export default function SinceThen(
  { deltas, since }: { deltas: Brief[]; since: string | null },
) {
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2 style={{ marginTop: 0, fontSize: 15 }}>Since then</h2>
      {deltas.length === 0 && (
        <p className="muted" style={{ fontSize: 13 }}>
          {since ? `No material change since ${fmtDate(since)}.`
            : "Nothing yet."}
        </p>
      )}
      {deltas.map((d) => (
        <div key={d.created_at} style={{ marginBottom: 12 }}>
          <p className="muted" style={{ fontSize: 12, marginBottom: 2 }}>
            {fmtDate(d.day)}
            {d.status === "failed" ? " — generation failed" : ""}
          </p>
          <p className="ink2" style={{ fontSize: 13, whiteSpace: "pre-wrap",
            marginTop: 0 }}>
            {d.status === "failed" ? d.error : d.summary}
          </p>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Write GoalPicker**

Create `apps/coach_web/frontend/src/components/GoalPicker.tsx`:

```tsx
export type Goal = {
  id: number; kind: string; target: string; title: string;
  target_date: string; status: string;
};

export type Gaps = {
  never_built: string[]; stale: string[]; adoption_gaps: string[];
};

export type NewGoal = { kind: string; target: string; title: string };

type Props = {
  goals: Goal[];
  gaps: Gaps;
  onAdd: (goal: NewGoal) => void;
  onDone: (id: number) => void;
  onDelete: (id: number) => void;
};

// There is deliberately no free-text field: a goal is always a taxonomy tag or
// a Claude Code feature, which is what the write API has always required.
export default function GoalPicker({ goals, gaps, onAdd, onDone, onDelete }: Props) {
  const active = goals.filter((g) => g.status === "active");
  const taken = new Set(active.map((g) => `${g.kind}:${g.target}`));

  const options: NewGoal[] = [
    ...gaps.never_built.map((t) => ({
      kind: "tag", target: t, title: `Build something with ${t}` })),
    ...gaps.stale.map((t) => ({
      kind: "tag", target: t, title: `Come back to ${t}` })),
    ...gaps.adoption_gaps.map((f) => ({
      kind: "feature", target: f, title: `Adopt ${f}` })),
  ].filter((o) => !taken.has(`${o.kind}:${o.target}`));

  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2 style={{ marginTop: 0, fontSize: 15 }}>Goals</h2>
      {active.length === 0 && (
        <p className="muted" style={{ fontSize: 13 }}>No active goals.</p>
      )}
      {active.map((g) => (
        <div key={g.id} style={{ display: "flex", gap: 8, alignItems: "baseline",
          marginBottom: 6 }}>
          <span className="ink2" style={{ fontSize: 13, flex: 1 }}>
            {g.title}{" "}
            <span className="muted">({g.target}
            {g.target_date ? ` · by ${g.target_date}` : ""})</span>
          </span>
          <button type="button" onClick={() => onDone(g.id)}>Done</button>
          <button type="button" onClick={() => onDelete(g.id)}>Delete</button>
        </div>
      ))}

      <details style={{ marginTop: 10 }}>
        <summary style={{ cursor: "pointer", fontSize: 13 }}>
          Add from gaps ({options.length})
        </summary>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
          {options.map((o) => (
            <button key={`${o.kind}:${o.target}`} type="button"
              onClick={() => onAdd(o)}>
              Add {o.target}
            </button>
          ))}
          {options.length === 0 && (
            <p className="muted" style={{ fontSize: 13 }}>
              Every known gap is already a goal.
            </p>
          )}
        </div>
      </details>
    </div>
  );
}
```

- [ ] **Step 5: Write BriefHistory**

Create `apps/coach_web/frontend/src/components/BriefHistory.tsx`:

```tsx
import { fmtDate } from "../format";
import type { Brief } from "./AssessmentCard";

export type Recurring = {
  target: string; kind: string; times: number;
  first: string; last: string; outcome: string;
};

function fate(outcome: string): string {
  if (outcome === "converted") return "became a goal";
  if (outcome === "dismissed") return "dismissed";
  return "never acted on";
}

export default function BriefHistory(
  { recurring, history }: { recurring: Recurring[]; history: Brief[] },
) {
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <h2 style={{ marginTop: 0, fontSize: 15 }}>History</h2>

      {recurring.length > 0 && (
        <>
          <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
            Recurring suggestions — what the coach keeps coming back to.
          </p>
          {recurring.map((r) => (
            <p key={r.target} className="ink2"
              style={{ fontSize: 13, margin: "0 0 4px" }}>
              <strong>{r.target}</strong> — suggested {r.times}× between{" "}
              {fmtDate(r.first)} and {fmtDate(r.last)}, {fate(r.outcome)}
            </p>
          ))}
        </>
      )}

      {recurring.length === 0 && history.length === 0 && (
        <p className="muted" style={{ fontSize: 13 }}>Nothing in the history yet.</p>
      )}

      {history.map((h) => (
        <details key={h.created_at} style={{ marginTop: 8 }}>
          <summary style={{ cursor: "pointer", fontSize: 13 }}>
            <span className="muted">{fmtDate(h.day)}</span>{" "}
            {h.status === "failed"
              ? "generation failed"
              : h.recommendations.map((r) => r.target).join(" · ") || "no recommendations"}
          </summary>
          <p className="ink2" style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
            {h.status === "failed" ? h.error : h.summary}
          </p>
          {h.recommendations.map((r) => (
            <p key={r.id} className="muted" style={{ fontSize: 12, margin: "0 0 4px" }}>
              {r.title} → {r.target}
            </p>
          ))}
        </details>
      ))}
    </div>
  );
}
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `npm --prefix apps/coach_web/frontend test -- GoalPicker BriefHistory`
Expected: PASS (8 tests)

- [ ] **Step 7: Commit**

```bash
git add apps/coach_web/frontend/src/components/SinceThen.tsx apps/coach_web/frontend/src/components/GoalPicker.tsx apps/coach_web/frontend/src/components/BriefHistory.tsx apps/coach_web/frontend/src/__tests__/GoalPicker.test.tsx apps/coach_web/frontend/src/__tests__/BriefHistory.test.tsx
git commit -m "feat(frontend): deltas, gap-driven goal picker, collapsed history"
```

---

### Task 11: Rebuild the Goals page

**Files:**
- Modify: `apps/coach_web/frontend/src/pages/Goals.tsx` (full rewrite)
- Modify: `apps/coach_web/frontend/src/pages/Overview.tsx` (it also consumes the old shape — see below)
- Delete: `apps/coach_web/frontend/src/components/BriefCard.tsx`
- Modify: `apps/coach_web/frontend/src/__tests__/BriefCard.test.tsx` → rename to `Goals.test.tsx` and rewrite

> **Plan correction, found during Task 8's review.** `Overview.tsx` was missing from this
> plan's original file list. It renders `<BriefCard brief={briefs.latest} />` at line 37 from
> a `get("/api/briefs?limit=1")` — so deleting `BriefCard.tsx` breaks its import and
> `tsc -b` fails, and `briefs.latest` no longer exists in the reshaped response anyway.
> Step 3b below covers it.

**Interfaces:**
- Consumes: every component from Tasks 9–10; `/api/briefs` (Task 8), `/api/overview`, `/api/goals`, `/api/dismissals`, `/api/reassess` (Task 7).
- Produces: the composed page. No new exports.

- [ ] **Step 1: Write the failing test**

Delete `apps/coach_web/frontend/src/__tests__/BriefCard.test.tsx` and create `apps/coach_web/frontend/src/__tests__/Goals.test.tsx`:

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import Goals from "../pages/Goals";

afterEach(() => vi.restoreAllMocks());

function jsonResponse(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 });
}

const briefs = {
  assessment: {
    created_at: "2026-08-11T07:00:00+00:00", day: "2026-08-11",
    kind: "assessment", status: "ok", stale: false, error: "",
    summary: "You ship fast and deploy by hand.",
    recommendations: [{ id: 1, title: "Containerize purchase-inventory",
      kind: "tag", target: "deploy-docker", why: "Foundational.",
      evidence: "14 units, zero containers.", outcome: "open" }],
  },
  deltas: [],
  history: [],
  recurring: [],
};

const overview = {
  never_built: ["websockets-sse"], stale: [],
  adoption_gaps: ["background tasks"], active_goals: [],
};

// Route by URL: a stub returning one body for every path would feed the briefs
// payload to the goals loader.
function stub(posts: string[] = []) {
  vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.method && init.method !== "GET") posts.push(`${init.method} ${url}`);
    if (url.startsWith("/api/briefs")) return Promise.resolve(jsonResponse(briefs));
    if (url.startsWith("/api/overview")) return Promise.resolve(jsonResponse(overview));
    if (url.startsWith("/api/goals")) return Promise.resolve(jsonResponse({ goals: [] }));
    if (url.startsWith("/api/dismissals")) {
      return Promise.resolve(jsonResponse({ dismissals: [] }));
    }
    return Promise.resolve(jsonResponse({ status: "ok" }));
  }));
}

describe("Goals page", () => {
  it("leads with the assessment, not the archive", async () => {
    stub();
    render(<Goals />);
    expect(await screen.findByText("You ship fast and deploy by hand."))
      .toBeInTheDocument();
    expect(screen.getByText("Containerize purchase-inventory")).toBeInTheDocument();
  });

  it("adds a goal from a recommendation with its target prefilled", async () => {
    const posts: string[] = [];
    stub(posts);
    render(<Goals />);
    await userEvent.click(await screen.findByRole("button", { name: "Add as goal" }));
    await waitFor(() => expect(posts).toContain("POST /api/goals"));
  });

  it("dismisses a recommendation through the dismissals endpoint", async () => {
    const posts: string[] = [];
    stub(posts);
    render(<Goals />);
    await userEvent.click(await screen.findByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(posts).toContain("POST /api/dismissals"));
  });

  it("forces a reassessment", async () => {
    const posts: string[] = [];
    stub(posts);
    render(<Goals />);
    await userEvent.click(await screen.findByRole("button", { name: "Reassess" }));
    await waitFor(() => expect(posts).toContain("POST /api/reassess"));
  });

  it("says so plainly when nothing has changed", async () => {
    stub();
    render(<Goals />);
    expect(await screen.findByText(/No material change since/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm --prefix apps/coach_web/frontend test -- Goals`
Expected: FAIL — the page still renders the old `latest`/`archive` shape

- [ ] **Step 3: Rewrite the page**

Replace `apps/coach_web/frontend/src/pages/Goals.tsx` entirely:

```tsx
import { useEffect, useState } from "react";
import { del, get, patch, post } from "../api";
import AssessmentCard, { type Assessment, type Brief, type Recommendation }
  from "../components/AssessmentCard";
import BriefHistory, { type Recurring } from "../components/BriefHistory";
import GoalPicker, { type Gaps, type Goal, type NewGoal }
  from "../components/GoalPicker";
import SinceThen from "../components/SinceThen";

type Briefs = {
  assessment: Assessment | null; deltas: Brief[]; history: Brief[];
  recurring: Recurring[];
};
type Dismissal = { id: number; kind: string; target: string; reason: string;
  created_at: string };

const NO_GAPS: Gaps = { never_built: [], stale: [], adoption_gaps: [] };

export default function Goals() {
  const [briefs, setBriefs] = useState<Briefs | null>(null);
  const [gaps, setGaps] = useState<Gaps>(NO_GAPS);
  const [goals, setGoals] = useState<Goal[]>([]);
  const [dismissals, setDismissals] = useState<Dismissal[]>([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const loadBriefs = () =>
    get("/api/briefs").then(setBriefs).catch((e) => setErr(String(e)));
  const loadGoals = () =>
    get("/api/goals").then((d) => setGoals(d.goals ?? [])).catch(() => {});
  const loadDismissals = () =>
    get("/api/dismissals").then((d) => setDismissals(d.dismissals ?? [])).catch(() => {});
  const loadGaps = () =>
    get("/api/overview").then((d) => setGaps({
      never_built: d.never_built ?? [],
      stale: (d.stale ?? []).map((s: { tag: string }) => s.tag),
      adoption_gaps: d.adoption_gaps ?? [],
    })).catch(() => {});

  useEffect(() => {
    loadBriefs(); loadGoals(); loadDismissals(); loadGaps();
  }, []);

  async function addGoal(goal: NewGoal) {
    await post("/api/goals", goal);
    // The write also converts matching recommendations, so both must reload.
    await Promise.all([loadGoals(), loadBriefs()]);
  }

  async function dismiss(rec: Recommendation) {
    await post("/api/dismissals", { kind: rec.kind, target: rec.target, reason: "" });
    await Promise.all([loadDismissals(), loadBriefs(), loadGaps()]);
  }

  async function reassess() {
    setBusy(true);
    try {
      await post("/api/reassess", {});
      await loadBriefs();
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Goals &amp; Coach</h1>
      {err && <p className="muted">Failed to load: {err}</p>}

      <AssessmentCard
        assessment={briefs?.assessment ?? null}
        onAdd={(rec) => addGoal({ kind: rec.kind, target: rec.target, title: rec.title })}
        onDismiss={dismiss}
        onReassess={reassess}
        busy={busy}
      />

      <SinceThen deltas={briefs?.deltas ?? []}
        since={briefs?.assessment?.day ?? null} />

      <GoalPicker
        goals={goals}
        gaps={gaps}
        onAdd={addGoal}
        onDone={async (id) => { await patch(`/api/goals/${id}`, { status: "done" }); loadGoals(); }}
        onDelete={async (id) => { await del(`/api/goals/${id}`); loadGoals(); }}
      />

      <BriefHistory recurring={briefs?.recurring ?? []}
        history={briefs?.history ?? []} />

      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 15 }}>Dismissed</h2>
        <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
          Hidden from the coach's suggestions. Still counted in the gap lists on Overview.
        </p>
        {dismissals.length === 0 && (
          <p className="muted" style={{ fontSize: 13 }}>Nothing dismissed.</p>
        )}
        {dismissals.map((d) => (
          <div key={d.id} style={{ display: "flex", gap: 8, alignItems: "baseline",
            marginBottom: 6 }}>
            <span className="ink2" style={{ fontSize: 13, flex: 1 }}>
              {d.target}{" "}
              <span className="muted">({d.kind}
              {d.reason ? ` · ${d.reason}` : ""})</span>
            </span>
            <button type="button" onClick={async () => {
              await del(`/api/dismissals/${d.id}`);
              await Promise.all([loadDismissals(), loadGaps()]);
            }}>Un-dismiss</button>
          </div>
        ))}
      </div>
    </>
  );
}
```

- [ ] **Step 3b: Point Overview at the new shape**

`Overview.tsx` fetches `/api/briefs?limit=1` and renders `<BriefCard brief={briefs.latest} />`.
Both halves are now wrong: `latest` no longer exists, and `BriefCard` is deleted in Step 4.

Two changes. First, the fetch — **`limit=1` is not enough**. The newest row is usually a
delta, and with `limit=1` the response would carry no assessment at all. At most
`MAX_DELTAS_BEFORE_REASSESS` (5) deltas separate two assessments, so `limit=10` always
reaches one:

```tsx
  useEffect(() => { get("/api/briefs?limit=10").then(setBriefs).catch(() => setBriefs(null)); }, []);
```

Second, render the assessment summary read-only. Overview is a dashboard, not the place to
act on recommendations — no Add-as-goal, no Dismiss, no Reassess; those live on the Goals
page. Replace the `<BriefCard .../>` line with:

```tsx
      {briefs?.assessment && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2 style={{ marginTop: 0, fontSize: 15 }}>
            Assessment{" "}
            <span className="muted" style={{ fontSize: 12, fontWeight: 400 }}>
              {fmtDate(briefs.assessment.day)}
            </span>
          </h2>
          <p className="ink2" style={{ fontSize: 13, whiteSpace: "pre-wrap" }}>
            {briefs.assessment.summary}
          </p>
        </div>
      )}
```

Update the file's `Briefs` type import to the one exported from `AssessmentCard.tsx`
(`{ assessment: Assessment | null; deltas: Brief[]; history: Brief[]; recurring: Recurring[] }`
— define it locally if that is simpler than exporting it twice), drop the now-unused
`BriefCard` import, and ensure `fmtDate` is imported from `../format`.

- [ ] **Step 4: Delete the superseded component**

```bash
rm apps/coach_web/frontend/src/components/BriefCard.tsx
```

- [ ] **Step 5: Run the frontend suite and typecheck**

Run: `npm --prefix apps/coach_web/frontend test`
Expected: PASS

Run: `npm --prefix apps/coach_web/frontend run build`
Expected: `tsc -b` clean, Vite build succeeds

Run: `npm --prefix apps/coach_web/frontend run lint`
Expected: clean

- [ ] **Step 6: Run the full backend suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add -A apps/coach_web/frontend/src
git commit -m "feat(frontend): rebuild Goals around the standing assessment"
```

---

## Post-implementation

Not part of any task, but required before this is done:

1. **Update `docs/HANDOFF.md`.** The Phase 4 section states the brief runs on every ingest — that is now false. Record: the change gate and its fingerprint components, `MAX_DELTAS_BEFORE_REASSESS`, the two model paths and why the delta path's parameter constraints still hold, and the fact that `briefs.day` is deliberately not unique.
2. **Set `COACH_ASSESSMENT_MODEL` on Railway** only if overriding the `claude-sonnet-5` default. It is **not** a required secret — a missing value falls back to the default, and unlike `COACH_USAGE_TOKEN` it must never enter `REQUIRED_PROD_SECRETS`.
3. **Deploy via the `deploy-coach-web` skill**, run `alembic upgrade head`, then verify live: the first ingest after deploy should produce exactly one `assessment` and the second should produce `skipped`.
4. **Expect the first assessment to be a visible spend spike** in `llm_daily` under `app-builder-coach`. That is correct.
