# Coach Web Phase 1: Ingest API + Postgres + Auth + Shipper — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Railway-hosted FastAPI + Postgres app with authenticated ingest, and make the local sweep ship its existing data (classified units, commit activity, adoption rows) to it daily.

**Architecture:** The existing Python sweep gains a final "shipper" step that builds a versioned snapshot JSON (derived aggregates only) and POSTs it to `POST /api/ingest` on a new FastAPI app under `apps/coach_web/`. The app upserts idempotently into Postgres. Failed ships queue in `data/outbox/` and drain next run. Spec: `docs/superpowers/specs/2026-08-02-coach-web-dashboard-design.md`.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, Postgres (prod) / SQLite (tests), itsdangerous (session cookies), httpx (shipper), pytest.

## Global Constraints

- Snapshot payloads contain **derived aggregates only** — never raw git history, file contents, or prompt content.
- `schema_version` is `1`; server rejects any other value with HTTP 400.
- The sweep must always exit 0; shipping failures log and queue, never raise out of `main()`.
- Package dir is `apps/coach_web/` (underscore — must be Python-importable).
- Env vars: local `.env` → `COACH_INGEST_URL`, `COACH_INGEST_TOKEN`; Railway → `DATABASE_URL`, `COACH_INGEST_TOKEN`, `COACH_PASSWORD_HASH`, `COACH_SECRET_KEY`, `ANTHROPIC_API_KEY`.
- All new code follows repo style: stdlib-first, small modules, terse docstrings, pytest tests in `tests/`.
- Dates are ISO strings (`YYYY-MM-DD` / RFC3339) end to end; no datetime columns except server-side `received_at`.

## File Structure

```
shared/__init__.py            (new, empty)
shared/snapshot.py            (new) schema version, canonical hash, finalize/validate
src/shipper.py                (new) build_snapshot, ship_all, outbox
src/sweep.py                  (modify) call shipper at end of main()
apps/__init__.py              (new, empty)
apps/coach_web/__init__.py    (new, empty)
apps/coach_web/config.py      (new) Settings dataclass, settings_from_env()
apps/coach_web/db.py          (new) engine factory, session dependency
apps/coach_web/models.py      (new) SQLAlchemy models
apps/coach_web/auth.py        (new) token dep, password hash/verify, login route
apps/coach_web/ingest.py      (new) POST /api/ingest + apply_snapshot
apps/coach_web/api.py         (new) GET /api/summary
apps/coach_web/main.py        (new) create_app factory + module-level app
apps/coach_web/alembic.ini    (new, Task 10)
apps/coach_web/alembic/       (new, Task 10)
railway.json                  (new, Task 10)
requirements.txt              (modify) add web deps
tests/web/conftest.py         (new) app + client fixtures
tests/web/test_snapshot.py    (new)
tests/web/test_shipper.py     (new)
tests/web/test_models.py      (new)
tests/web/test_auth.py        (new)
tests/web/test_ingest.py      (new)
tests/web/test_api.py         (new)
tests/web/test_integration.py (new)
```

Existing data shapes this plan consumes (do not change them):
- classification row: `{"key", "kind", "repo", "date", "title", "tags", "complexity", "summary", "model"}` (`data/classifications.jsonl`; collapse with `classifier.effective_rows`)
- ledger row: `{"repo", "date", "message", "files", ...}` (`data/ledger.jsonl`)
- adoption row: `{"name", "lesson", "status", "last_used"}` (computed in `sweep.main`, not persisted)

---

### Task 1: Web scaffold — deps, config, app factory, health endpoint

**Files:**
- Modify: `requirements.txt`
- Create: `apps/__init__.py`, `apps/coach_web/__init__.py`, `apps/coach_web/config.py`, `apps/coach_web/main.py`
- Test: `tests/web/__init__.py` (empty), `tests/web/conftest.py`, `tests/web/test_health.py`

**Interfaces:**
- Produces: `config.Settings(database_url, ingest_token, password_hash, secret_key)`; `config.settings_from_env() -> Settings`; `main.create_app(settings) -> FastAPI`. Later tasks add routers into `create_app` and fixtures into conftest.

- [ ] **Step 1: Add dependencies**

Append to `requirements.txt`:

```
fastapi
uvicorn[standard]
sqlalchemy>=2.0
alembic
psycopg[binary]
itsdangerous
httpx
```

Run: `.venv/bin/pip install -r requirements.txt`

- [ ] **Step 2: Write the failing test**

`tests/web/conftest.py`:

```python
import pytest
from fastapi.testclient import TestClient

from apps.coach_web.config import Settings
from apps.coach_web.main import create_app


@pytest.fixture
def settings():
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        ingest_token="test-ingest-token",
        password_hash="",  # set properly in Task 6
        secret_key="test-secret",
    )


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c
```

`tests/web/test_health.py`:

```python
def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_health.py -v`
Expected: FAIL (ModuleNotFoundError: apps.coach_web)

- [ ] **Step 4: Implement**

`apps/__init__.py` and `apps/coach_web/__init__.py`: empty files.

`apps/coach_web/config.py`:

```python
"""Server settings. All values come from env in prod; tests construct directly."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    ingest_token: str
    password_hash: str
    secret_key: str


def settings_from_env() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", ""),
        ingest_token=os.environ.get("COACH_INGEST_TOKEN", ""),
        password_hash=os.environ.get("COACH_PASSWORD_HASH", ""),
        secret_key=os.environ.get("COACH_SECRET_KEY", ""),
    )
```

`apps/coach_web/main.py`:

```python
"""FastAPI app factory. Routers are added here as they are built."""
from fastapi import FastAPI

from .config import Settings, settings_from_env


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="coach-web")
    app.state.settings = settings

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app(settings_from_env())
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/web/test_health.py -v`
Expected: PASS

- [ ] **Step 6: Run full suite, then commit**

Run: `.venv/bin/python -m pytest tests -q` — all pass.

```bash
git add requirements.txt apps tests/web
git commit -m "feat(web): coach_web scaffold with settings and health endpoint"
```

---

### Task 2: Shared snapshot schema module

**Files:**
- Create: `shared/__init__.py` (empty), `shared/snapshot.py`
- Test: `tests/web/test_snapshot.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION = 1`; `canonical_hash(body: dict) -> str` (sha256 hex of sorted-key compact JSON); `finalize_payload(body: dict, captured_at: str) -> dict` (returns body + `captured_at` + `content_hash`; hash covers body only, NOT captured_at, so identical data on different days dedupes); `validate_payload(p: dict) -> None` (raises `ValueError` with a specific message on bad version, missing keys, or hash mismatch). Consumed by shipper (Task 3) and ingest (Task 7).

- [ ] **Step 1: Write the failing test**

`tests/web/test_snapshot.py`:

```python
import pytest

from shared import snapshot

BODY = {
    "schema_version": 1,
    "sweep": {"repos": 2, "new_commits": 5},
    "feature_units": [{"key": "abc:m", "kind": "commits", "repo": "r",
                       "date": "2026-08-01", "title": "r 2026-08",
                       "tags": ["auth"], "complexity": 3, "summary": "s",
                       "model": "heuristics"}],
    "activity_daily": [{"date": "2026-08-01", "commits": 5, "by_repo": {"r": 5}}],
    "adoption": [{"name": "plan mode", "lesson": "09-advanced-features",
                  "status": "never-touched", "last_used": None}],
}


def test_finalize_adds_hash_and_timestamp():
    p = snapshot.finalize_payload(dict(BODY), captured_at="2026-08-02T07:30:00+00:00")
    assert p["captured_at"] == "2026-08-02T07:30:00+00:00"
    assert len(p["content_hash"]) == 64
    snapshot.validate_payload(p)  # no raise


def test_hash_ignores_captured_at():
    a = snapshot.finalize_payload(dict(BODY), captured_at="2026-08-02T07:30:00+00:00")
    b = snapshot.finalize_payload(dict(BODY), captured_at="2026-08-03T07:30:00+00:00")
    assert a["content_hash"] == b["content_hash"]


def test_validate_rejects_wrong_version():
    p = snapshot.finalize_payload({**BODY, "schema_version": 2}, "2026-08-02T07:30:00+00:00")
    with pytest.raises(ValueError, match="schema_version"):
        snapshot.validate_payload(p)


def test_validate_rejects_tampered_payload():
    p = snapshot.finalize_payload(dict(BODY), "2026-08-02T07:30:00+00:00")
    p["feature_units"] = []
    with pytest.raises(ValueError, match="content_hash"):
        snapshot.validate_payload(p)


def test_validate_rejects_missing_key():
    p = snapshot.finalize_payload(dict(BODY), "2026-08-02T07:30:00+00:00")
    del p["adoption"]
    with pytest.raises(ValueError, match="adoption"):
        snapshot.validate_payload(p)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_snapshot.py -v`
Expected: FAIL (ModuleNotFoundError: shared)

- [ ] **Step 3: Implement**

`shared/snapshot.py`:

```python
"""Snapshot payload contract between local sweep and coach-web server."""
import hashlib
import json

SCHEMA_VERSION = 1
REQUIRED_KEYS = ("schema_version", "sweep", "feature_units",
                 "activity_daily", "adoption")


def canonical_hash(body: dict) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def finalize_payload(body: dict, captured_at: str) -> dict:
    return {**body, "captured_at": captured_at, "content_hash": canonical_hash(body)}


def validate_payload(p: dict) -> None:
    for key in REQUIRED_KEYS + ("captured_at", "content_hash"):
        if key not in p:
            raise ValueError(f"snapshot missing key: {key}")
    if p["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            f"unsupported schema_version {p['schema_version']!r}, expected {SCHEMA_VERSION}")
    body = {k: v for k, v in p.items() if k not in ("captured_at", "content_hash")}
    if canonical_hash(body) != p["content_hash"]:
        raise ValueError("content_hash mismatch: payload corrupted or tampered")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/web/test_snapshot.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add shared tests/web/test_snapshot.py
git commit -m "feat(shared): versioned snapshot payload with canonical content hash"
```

---

### Task 3: Shipper — build_snapshot from local data

**Files:**
- Create: `src/shipper.py`
- Test: `tests/web/test_shipper.py`

**Interfaces:**
- Consumes: `classifier.read_jsonl`, `classifier.effective_rows`, `shared.snapshot.finalize_payload`, `SCHEMA_VERSION`.
- Produces: `build_snapshot(data_dir: Path, adoption_rows: list[dict], sweep_stats: dict, captured_at: str) -> dict` — a finalized payload. Task 4 adds `ship_all` to this module.

- [ ] **Step 1: Write the failing test**

`tests/web/test_shipper.py`:

```python
import json
from pathlib import Path

from shared import snapshot
from src import shipper

LEDGER = [
    {"repo": "alpha", "date": "2026-08-01T10:00:00-04:00", "message": "m1", "files": ["a.py"]},
    {"repo": "alpha", "date": "2026-08-01T11:00:00-04:00", "message": "m2", "files": ["b.py"]},
    {"repo": "beta", "date": "2026-08-02T09:00:00-04:00", "message": "m3", "files": ["c.py"]},
]
CLS = [
    {"key": "h1:h", "kind": "commits", "repo": "alpha", "date": "2026-08-01",
     "title": "alpha 2026-08", "tags": ["api-backend"], "complexity": 2,
     "summary": "s", "model": "heuristics"},
    {"key": "h1:m", "kind": "commits", "repo": "alpha", "date": "2026-08-01",
     "title": "alpha 2026-08", "tags": ["api-backend", "auth"], "complexity": 3,
     "summary": "s", "model": "claude-haiku-4-5-20251001"},
]
ADOPTION = [{"name": "plan mode", "lesson": "09-advanced-features",
             "status": "never-touched", "last_used": None}]


def write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_build_snapshot(tmp_path):
    write_jsonl(tmp_path / "ledger.jsonl", LEDGER)
    write_jsonl(tmp_path / "classifications.jsonl", CLS)
    p = shipper.build_snapshot(tmp_path, ADOPTION, {"repos": 2},
                               captured_at="2026-08-02T11:30:00+00:00")
    snapshot.validate_payload(p)
    # tiered rows collapse: :m wins over :h for same base hash
    assert len(p["feature_units"]) == 1
    assert p["feature_units"][0]["key"] == "h1:m"
    # activity is grouped per day with per-repo counts
    assert p["activity_daily"] == [
        {"date": "2026-08-01", "commits": 2, "by_repo": {"alpha": 2}},
        {"date": "2026-08-02", "commits": 1, "by_repo": {"beta": 1}},
    ]
    assert p["adoption"] == ADOPTION
    assert p["sweep"] == {"repos": 2}


def test_build_snapshot_empty_data_dir(tmp_path):
    p = shipper.build_snapshot(tmp_path, [], {}, captured_at="2026-08-02T11:30:00+00:00")
    snapshot.validate_payload(p)
    assert p["feature_units"] == [] and p["activity_daily"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_shipper.py -v`
Expected: FAIL (No module named 'src.shipper' / AttributeError)

- [ ] **Step 3: Implement**

`src/shipper.py`:

```python
"""Ship snapshot payloads to coach-web. Never raises out of ship_all."""
import logging
from collections import defaultdict
from pathlib import Path

from shared.snapshot import SCHEMA_VERSION, finalize_payload
from . import classifier

log = logging.getLogger("shipper")

UNIT_FIELDS = ("key", "kind", "repo", "date", "title",
               "tags", "complexity", "summary", "model")


def build_snapshot(data_dir: Path, adoption_rows: list[dict],
                   sweep_stats: dict, captured_at: str) -> dict:
    ledger = classifier.read_jsonl(data_dir / "ledger.jsonl")
    units = classifier.effective_rows(
        classifier.read_jsonl(data_dir / "classifications.jsonl"))
    daily: dict[str, dict] = defaultdict(lambda: {"commits": 0, "by_repo": {}})
    for row in ledger:
        day = row["date"][:10]
        daily[day]["commits"] += 1
        daily[day]["by_repo"][row["repo"]] = daily[day]["by_repo"].get(row["repo"], 0) + 1
    body = {
        "schema_version": SCHEMA_VERSION,
        "sweep": sweep_stats,
        "feature_units": [{f: u.get(f) for f in UNIT_FIELDS} for u in units],
        "activity_daily": [{"date": d, **v} for d, v in sorted(daily.items())],
        "adoption": adoption_rows,
    }
    return finalize_payload(body, captured_at)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/web/test_shipper.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/shipper.py tests/web/test_shipper.py
git commit -m "feat(shipper): build snapshot payload from local sweep data"
```

---

### Task 4: Shipper — POST with outbox retry; wire into sweep

**Files:**
- Modify: `src/shipper.py`, `src/sweep.py`
- Test: `tests/web/test_shipper.py` (extend)

**Interfaces:**
- Consumes: `build_snapshot` (Task 3).
- Produces: `ship_all(payload: dict, url: str, token: str, outbox_dir: Path, post=None) -> dict` returning `{"shipped": int, "queued": int}`. `post(url, json, headers, timeout)` defaults to `httpx.post`; injectable for tests (Task 9 reuses this seam). `sweep.main` calls the shipper when `COACH_INGEST_URL` is set.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_shipper.py`:

```python
class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def make_post(fail_times=0, log=None):
    calls = {"n": 0}
    def post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if log is not None:
            log.append(json["content_hash"])
        if calls["n"] <= fail_times:
            raise ConnectionError("down")
        return FakeResponse(200)
    return post


def _payload(tag):
    return shipper.build_snapshot(
        Path("/nonexistent"), [], {"tag": tag}, captured_at="2026-08-02T11:30:00+00:00")


def test_ship_all_success(tmp_path):
    result = shipper.ship_all(_payload("a"), "http://x/api/ingest", "tok",
                              tmp_path / "outbox", post=make_post())
    assert result == {"shipped": 1, "queued": 0}
    assert not list((tmp_path / "outbox").glob("*.json"))


def test_ship_all_failure_queues_to_outbox(tmp_path):
    result = shipper.ship_all(_payload("a"), "http://x/api/ingest", "tok",
                              tmp_path / "outbox", post=make_post(fail_times=99))
    assert result == {"shipped": 0, "queued": 1}
    assert len(list((tmp_path / "outbox").glob("*.json"))) == 1


def test_ship_all_drains_outbox_oldest_first(tmp_path):
    outbox = tmp_path / "outbox"
    # queue two payloads while "offline"
    shipper.ship_all(_payload("a"), "http://x/api/ingest", "tok", outbox,
                     post=make_post(fail_times=99))
    shipper.ship_all(_payload("b"), "http://x/api/ingest", "tok", outbox,
                     post=make_post(fail_times=99))
    shipped_hashes = []
    result = shipper.ship_all(_payload("c"), "http://x/api/ingest", "tok", outbox,
                              post=make_post(log=shipped_hashes))
    assert result == {"shipped": 3, "queued": 0}
    assert not list(outbox.glob("*.json"))
    assert shipped_hashes[-1] == _payload("c")["content_hash"]


def test_ship_all_http_error_status_queues(tmp_path):
    def post(url, json=None, headers=None, timeout=None):
        return FakeResponse(500)
    result = shipper.ship_all(_payload("a"), "http://x/api/ingest", "tok",
                              tmp_path / "outbox", post=post)
    assert result == {"shipped": 0, "queued": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_shipper.py -v`
Expected: new tests FAIL (no attribute ship_all)

- [ ] **Step 3: Implement**

Append to `src/shipper.py`:

```python
def _try_post(payload: dict, url: str, token: str, post) -> bool:
    try:
        resp = post(url, json=payload,
                    headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if 200 <= resp.status_code < 300:
            return True
        log.warning("ship failed: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        log.warning("ship failed: %s: %s", type(exc).__name__, exc)
    return False


def ship_all(payload: dict, url: str, token: str,
             outbox_dir: Path, post=None) -> dict:
    """Ship pending outbox payloads (oldest first) then the current one.
    Failures queue the payload; a failure stops draining to preserve order."""
    import json as _json
    if post is None:
        import httpx
        post = httpx.post
    outbox_dir.mkdir(parents=True, exist_ok=True)
    shipped = queued = 0
    blocked = False
    for path in sorted(outbox_dir.glob("*.json")):
        pending = _json.loads(path.read_text())
        if not blocked and _try_post(pending, url, token, post):
            path.unlink()
            shipped += 1
        else:
            blocked = True
            queued += 1
    if not blocked and _try_post(payload, url, token, post):
        shipped += 1
    else:
        name = f"{payload['captured_at'].replace(':', '')}-{payload['content_hash'][:8]}.json"
        (outbox_dir / name).write_text(_json.dumps(payload))
        queued += 1
    return {"shipped": shipped, "queued": queued}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_shipper.py -v`
Expected: PASS (all)

- [ ] **Step 5: Wire into sweep**

In `src/sweep.py`, add `from . import shipper` to the existing relative import line, and insert after the `profile.write_profile(data_dir, text)` line (inside the `try`), before the final `print`:

```python
        ingest_url = os.environ.get("COACH_INGEST_URL")
        ship_stats = None
        if ingest_url:
            payload = shipper.build_snapshot(
                data_dir, adoption_rows,
                {"repos": swept["repos"], "new_commits": swept["new_commits"],
                 "specs": swept["specs"], "errors": len(swept["errors"])},
                captured_at=datetime.now(timezone.utc).isoformat())
            ship_stats = shipper.ship_all(
                payload, ingest_url, os.environ.get("COACH_INGEST_TOKEN", ""),
                data_dir / "outbox")
```

And extend the summary `print` with:

```python
              + (f" shipped={ship_stats['shipped']} queued={ship_stats['queued']}"
                 if ship_stats else "")
```

(Adjust the existing f-string print to append this conditional suffix — keep the existing fields unchanged.)

- [ ] **Step 6: Add a sweep-level test**

Append to `tests/web/test_shipper.py`:

```python
def test_sweep_ships_when_url_set(monkeypatch, tmp_path):
    from src import sweep
    root = tmp_path / "root"; root.mkdir()
    data = tmp_path / "data"
    monkeypatch.setenv("COACH_INGEST_URL", "http://x/api/ingest")
    monkeypatch.setenv("COACH_INGEST_TOKEN", "tok")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    sweep.main(root=root, data_dir=data)
    # no server reachable -> payload must be queued in the outbox
    assert len(list((data / "outbox").glob("*.json"))) == 1
```

Run: `.venv/bin/python -m pytest tests/web/test_shipper.py tests/test_sweep_main.py -v`
Expected: PASS (existing sweep tests unaffected — no COACH_INGEST_URL means no shipping)

- [ ] **Step 7: Run full suite, commit**

Run: `.venv/bin/python -m pytest tests -q` — all pass.

```bash
git add src/shipper.py src/sweep.py tests/web/test_shipper.py
git commit -m "feat(shipper): outbox retry and sweep wiring"
```

---

### Task 5: Database models and engine

**Files:**
- Create: `apps/coach_web/db.py`, `apps/coach_web/models.py`
- Modify: `apps/coach_web/main.py`, `tests/web/conftest.py`
- Test: `tests/web/test_models.py`

**Interfaces:**
- Produces: `models.Base` plus models `Snapshot(id, content_hash, captured_at, sweep_stats, received_at)`, `FeatureUnit(key, kind, repo, date, title, tags, complexity, summary, model)`, `ActivityDaily(date, commits, by_repo, sessions, prompts)`, `AdoptionHistory(id, snapshot_id, feature_name, lesson, status, last_used)`, `FeatureCatalog(name, lesson, source, discovered_at)`. `db.make_engine(url)`, `db.get_db` FastAPI dependency yielding a `Session`. `create_app` builds the engine, stores it on `app.state.engine`, and runs `create_all` only for SQLite (prod schema comes from Alembic, Task 10).

- [ ] **Step 1: Write the failing test**

`tests/web/test_models.py`:

```python
from sqlalchemy import select

from apps.coach_web import models
from apps.coach_web.db import make_engine
from sqlalchemy.orm import Session


def test_models_create_and_roundtrip():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        snap = models.Snapshot(content_hash="c" * 64,
                               captured_at="2026-08-02T07:30:00+00:00",
                               sweep_stats={"repos": 7})
        s.add(snap)
        s.flush()
        s.add(models.FeatureUnit(key="h1:m", kind="commits", repo="alpha",
                                 date="2026-08-01", title="alpha 2026-08",
                                 tags=["auth"], complexity=3, summary="s",
                                 model="claude-haiku-4-5-20251001"))
        s.add(models.ActivityDaily(date="2026-08-01", commits=2,
                                   by_repo={"alpha": 2}))
        s.add(models.AdoptionHistory(snapshot_id=snap.id, feature_name="plan mode",
                                     lesson="09-advanced-features",
                                     status="never-touched", last_used=None))
        s.add(models.FeatureCatalog(name="plan mode",
                                    lesson="09-advanced-features",
                                    source="checklist",
                                    discovered_at="2026-08-02"))
        s.commit()
        assert s.scalar(select(models.FeatureUnit)).tags == ["auth"]
        assert s.scalar(select(models.Snapshot)).received_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_models.py -v`
Expected: FAIL (no module apps.coach_web.models)

- [ ] **Step 3: Implement**

`apps/coach_web/models.py`:

```python
"""Ingested tables (sweep-owned). App-owned tables arrive in Phase 5."""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Snapshot(Base):
    __tablename__ = "snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    captured_at: Mapped[str] = mapped_column(String(40))
    sweep_stats: Mapped[dict] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now())


class FeatureUnit(Base):
    __tablename__ = "feature_units"
    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    repo: Mapped[str] = mapped_column(String(200), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    title: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    complexity: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text, default="")
    model: Mapped[str] = mapped_column(String(64), default="")


class ActivityDaily(Base):
    __tablename__ = "activity_daily"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    commits: Mapped[int] = mapped_column(Integer, default=0)
    by_repo: Mapped[dict] = mapped_column(JSON, default=dict)
    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompts: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AdoptionHistory(Base):
    __tablename__ = "adoption_history"
    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"), index=True)
    feature_name: Mapped[str] = mapped_column(String(120), index=True)
    lesson: Mapped[str] = mapped_column(String(60), default="")
    status: Mapped[str] = mapped_column(String(30))
    last_used: Mapped[str | None] = mapped_column(String(10), nullable=True)


class FeatureCatalog(Base):
    __tablename__ = "feature_catalog"
    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    lesson: Mapped[str] = mapped_column(String(60), default="")
    source: Mapped[str] = mapped_column(String(20), default="checklist")
    discovered_at: Mapped[str] = mapped_column(String(10))
```

`apps/coach_web/db.py`:

```python
"""Engine and session plumbing. SQLite (tests) needs StaticPool sharing."""
from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def make_engine(url: str):
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False},
                             poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True)


def get_db(request: Request):
    with Session(request.app.state.engine) as session:
        yield session
```

In `apps/coach_web/main.py`, extend `create_app` (after `app.state.settings = settings`):

```python
    from . import models
    from .db import make_engine

    app.state.engine = make_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        models.Base.metadata.create_all(app.state.engine)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web -v`
Expected: PASS (health test still green — sqlite create_all runs in fixture app)

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/models.py apps/coach_web/db.py apps/coach_web/main.py tests/web/test_models.py
git commit -m "feat(web): ingested-data models and engine plumbing"
```

---

### Task 6: Auth — machine bearer token and human password session

**Files:**
- Create: `apps/coach_web/auth.py`
- Modify: `apps/coach_web/main.py`, `tests/web/conftest.py`
- Test: `tests/web/test_auth.py`

**Interfaces:**
- Produces: `hash_password(pw) -> str` (format `pbkdf2$<iters>$<salt_hex>$<digest_hex>`), `verify_password(pw, stored) -> bool`, FastAPI deps `require_ingest_token` and `require_user`, router with `POST /api/login` (body `{"password": ...}`, sets signed cookie `coach_session`, 30-day max age). CLI: `python -m apps.coach_web.auth <password>` prints a hash for Railway env setup. Tasks 7–8 guard their routes with these deps.

- [ ] **Step 1: Update conftest to use a real hash**

In `tests/web/conftest.py`, replace `password_hash=""` with:

```python
        password_hash=hash_password("correct-horse"),
```

adding the import `from apps.coach_web.auth import hash_password`.

- [ ] **Step 2: Write the failing tests**

`tests/web/test_auth.py`:

```python
from apps.coach_web.auth import hash_password, verify_password


def test_password_hash_roundtrip():
    h = hash_password("s3cret")
    assert h.startswith("pbkdf2$")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)
    assert not verify_password("s3cret", "garbage")


def test_login_sets_session_cookie(client):
    resp = client.post("/api/login", json={"password": "correct-horse"})
    assert resp.status_code == 200
    assert "coach_session" in resp.cookies


def test_login_rejects_bad_password(client):
    resp = client.post("/api/login", json={"password": "nope"})
    assert resp.status_code == 401
    assert "coach_session" not in resp.cookies
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_auth.py -v`
Expected: FAIL (no module apps.coach_web.auth)

- [ ] **Step 4: Implement**

`apps/coach_web/auth.py`:

```python
"""Single-user auth: machine bearer token (ingest) + password session (human)."""
import hashlib
import secrets
import sys

from fastapi import APIRouter, HTTPException, Request, Response
from itsdangerous import BadSignature, TimestampSigner
from pydantic import BaseModel

SESSION_COOKIE = "coach_session"
SESSION_MAX_AGE = 30 * 86400

router = APIRouter()


def hash_password(pw: str, iterations: int = 600_000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt),
                                 iterations).hex()
    return f"pbkdf2${iterations}${salt}${digest}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        scheme, iters, salt, digest = stored.split("$")
        if scheme != "pbkdf2":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", pw.encode(),
                                        bytes.fromhex(salt), int(iters)).hex()
        return secrets.compare_digest(candidate, digest)
    except (ValueError, AttributeError):
        return False


def require_ingest_token(request: Request) -> None:
    token = request.app.state.settings.ingest_token
    header = request.headers.get("Authorization", "")
    if not token or not secrets.compare_digest(header, f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="invalid ingest token")


def require_user(request: Request) -> None:
    signer = TimestampSigner(request.app.state.settings.secret_key)
    cookie = request.cookies.get(SESSION_COOKIE, "")
    try:
        signer.unsign(cookie, max_age=SESSION_MAX_AGE)
    except BadSignature:
        raise HTTPException(status_code=401, detail="login required")


class LoginBody(BaseModel):
    password: str


@router.post("/api/login")
def login(body: LoginBody, request: Request, response: Response):
    settings = request.app.state.settings
    if not settings.password_hash or not verify_password(body.password,
                                                         settings.password_hash):
        raise HTTPException(status_code=401, detail="wrong password")
    signer = TimestampSigner(settings.secret_key)
    response.set_cookie(SESSION_COOKIE, signer.sign(b"user").decode(),
                        max_age=SESSION_MAX_AGE, httponly=True,
                        secure=True, samesite="lax")
    return {"status": "ok"}


if __name__ == "__main__":
    print(hash_password(sys.argv[1]))
```

In `apps/coach_web/main.py`, inside `create_app` before `return app`:

```python
    from .auth import router as auth_router
    app.include_router(auth_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web -v`
Expected: PASS. Note: TestClient uses http, but `secure=True` cookies still round-trip through TestClient; if the cookie assertion fails for that reason, assert on `resp.headers["set-cookie"]` containing `coach_session=` instead.

- [ ] **Step 6: Commit**

```bash
git add apps/coach_web/auth.py apps/coach_web/main.py tests/web/conftest.py tests/web/test_auth.py
git commit -m "feat(web): bearer-token ingest auth and password session login"
```

---

### Task 7: Ingest endpoint with idempotent upsert

**Files:**
- Create: `apps/coach_web/ingest.py`
- Modify: `apps/coach_web/main.py`
- Test: `tests/web/test_ingest.py`

**Interfaces:**
- Consumes: `validate_payload` (Task 2), models (Task 5), `require_ingest_token` (Task 6), `get_db` (Task 5).
- Produces: `POST /api/ingest` → 200 `{"snapshot_id": int, "duplicate": bool}`; 400 on schema/hash errors; 401 without token. Internal `apply_snapshot(db, payload) -> dict` (used directly by tests). Duplicate content refreshes `snapshots.captured_at` so freshness stays current.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_ingest.py`:

```python
from shared import snapshot as snap_mod


def make_payload(captured="2026-08-02T07:30:00+00:00", commits=2):
    body = {
        "schema_version": 1,
        "sweep": {"repos": 1, "new_commits": commits},
        "feature_units": [{"key": "h1:m", "kind": "commits", "repo": "alpha",
                           "date": "2026-08-01", "title": "alpha 2026-08",
                           "tags": ["auth"], "complexity": 3, "summary": "s",
                           "model": "m"}],
        "activity_daily": [{"date": "2026-08-01", "commits": commits,
                            "by_repo": {"alpha": commits}}],
        "adoption": [{"name": "plan mode", "lesson": "09-advanced-features",
                      "status": "never-touched", "last_used": None}],
    }
    return snap_mod.finalize_payload(body, captured)


AUTH = {"Authorization": "Bearer test-ingest-token"}


def test_ingest_requires_token(client):
    assert client.post("/api/ingest", json=make_payload()).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert client.post("/api/ingest", json=make_payload(), headers=bad).status_code == 401


def test_ingest_stores_snapshot(client):
    resp = client.post("/api/ingest", json=make_payload(), headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["duplicate"] is False and data["snapshot_id"] >= 1


def test_ingest_is_idempotent(client):
    p = make_payload()
    first = client.post("/api/ingest", json=p, headers=AUTH).json()
    again = client.post("/api/ingest",
                        json=snap_mod.finalize_payload(
                            {k: v for k, v in p.items()
                             if k not in ("captured_at", "content_hash")},
                            "2026-08-03T07:30:00+00:00"),
                        headers=AUTH).json()
    assert again["duplicate"] is True
    assert again["snapshot_id"] == first["snapshot_id"]


def test_ingest_upserts_units_and_activity(client):
    client.post("/api/ingest", json=make_payload(), headers=AUTH)
    resp = client.post("/api/ingest", json=make_payload(commits=5), headers=AUTH)
    assert resp.status_code == 200
    # verify via db: one unit row, activity updated to 5 commits
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from apps.coach_web import models
    engine = client.app.state.engine
    with Session(engine) as s:
        assert len(s.scalars(select(models.FeatureUnit)).all()) == 1
        assert s.get(models.ActivityDaily, "2026-08-01").commits == 5
        assert s.get(models.FeatureCatalog, "plan mode") is not None


def test_ingest_rejects_bad_schema_version(client):
    p = make_payload()
    p["schema_version"] = 99
    p = snap_mod.finalize_payload(
        {k: v for k, v in p.items() if k not in ("captured_at", "content_hash")},
        "2026-08-02T07:30:00+00:00")
    resp = client.post("/api/ingest", json=p, headers=AUTH)
    assert resp.status_code == 400
    assert "schema_version" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_ingest.py -v`
Expected: FAIL (404 — route not registered)

- [ ] **Step 3: Implement**

`apps/coach_web/ingest.py`:

```python
"""Idempotent snapshot ingest. One transaction per snapshot."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.snapshot import validate_payload
from . import models
from .auth import require_ingest_token
from .db import get_db

router = APIRouter(dependencies=[Depends(require_ingest_token)])


def apply_snapshot(db: Session, payload: dict) -> dict:
    existing = db.scalar(select(models.Snapshot).where(
        models.Snapshot.content_hash == payload["content_hash"]))
    if existing is not None:
        existing.captured_at = payload["captured_at"]
        db.commit()
        return {"snapshot_id": existing.id, "duplicate": True}

    snap = models.Snapshot(content_hash=payload["content_hash"],
                           captured_at=payload["captured_at"],
                           sweep_stats=payload["sweep"])
    db.add(snap)
    db.flush()

    for u in payload["feature_units"]:
        row = db.get(models.FeatureUnit, u["key"])
        if row is None:
            db.add(models.FeatureUnit(**u))
        else:
            for field in ("kind", "repo", "date", "title", "tags",
                          "complexity", "summary", "model"):
                setattr(row, field, u[field])

    for a in payload["activity_daily"]:
        row = db.get(models.ActivityDaily, a["date"])
        if row is None:
            db.add(models.ActivityDaily(**a))
        else:
            row.commits = a["commits"]
            row.by_repo = a["by_repo"]

    today = date.today().isoformat()
    for r in payload["adoption"]:
        db.add(models.AdoptionHistory(snapshot_id=snap.id,
                                      feature_name=r["name"],
                                      lesson=r.get("lesson", ""),
                                      status=r["status"],
                                      last_used=r.get("last_used")))
        if db.get(models.FeatureCatalog, r["name"]) is None:
            db.add(models.FeatureCatalog(name=r["name"],
                                         lesson=r.get("lesson", ""),
                                         source="checklist",
                                         discovered_at=today))
    db.commit()
    return {"snapshot_id": snap.id, "duplicate": False}


@router.post("/api/ingest")
def ingest(payload: dict, db: Session = Depends(get_db)):
    try:
        validate_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return apply_snapshot(db, payload)
```

In `apps/coach_web/main.py`, next to the auth router include:

```python
    from .ingest import router as ingest_router
    app.include_router(ingest_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/ingest.py apps/coach_web/main.py tests/web/test_ingest.py
git commit -m "feat(web): idempotent snapshot ingest endpoint"
```

---

### Task 8: Read endpoint — /api/summary

**Files:**
- Create: `apps/coach_web/api.py`
- Modify: `apps/coach_web/main.py`
- Test: `tests/web/test_api.py`

**Interfaces:**
- Consumes: models (Task 5), `require_user` (Task 6), `get_db` (Task 5).
- Produces: `GET /api/summary` (session-cookie auth) → `{"latest": {"captured_at", "sweep_stats"} | null, "unit_count": int, "tag_counts": {tag: n}, "adoption": {"used": n, "configured-but-unused": n, "never-touched": n}}`. Phase 2 builds the full read API alongside the SPA; this endpoint proves auth + data path.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_api.py`:

```python
from tests.web.test_ingest import AUTH, make_payload


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def test_summary_requires_login(client):
    assert client.get("/api/summary").status_code == 401


def test_summary_empty_db(client):
    login(client)
    data = client.get("/api/summary").json()
    assert data == {"latest": None, "unit_count": 0, "tag_counts": {},
                    "adoption": {}}


def test_summary_after_ingest(client):
    client.post("/api/ingest", json=make_payload(), headers=AUTH)
    login(client)
    data = client.get("/api/summary").json()
    assert data["latest"]["captured_at"] == "2026-08-02T07:30:00+00:00"
    assert data["unit_count"] == 1
    assert data["tag_counts"] == {"auth": 1}
    assert data["adoption"] == {"never-touched": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_api.py -v`
Expected: FAIL (404)

- [ ] **Step 3: Implement**

`apps/coach_web/api.py`:

```python
"""Read endpoints for the SPA. Phase 1: summary only."""
from collections import Counter

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import models
from .auth import require_user
from .db import get_db

router = APIRouter(dependencies=[Depends(require_user)])


@router.get("/api/summary")
def summary(db: Session = Depends(get_db)):
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    tag_counts: Counter = Counter()
    for (tags,) in db.execute(select(models.FeatureUnit.tags)):
        tag_counts.update(tags or [])
    adoption: dict[str, int] = {}
    if latest is not None:
        rows = db.execute(
            select(models.AdoptionHistory.status, func.count())
            .where(models.AdoptionHistory.snapshot_id == latest.id)
            .group_by(models.AdoptionHistory.status))
        adoption = {status: n for status, n in rows}
    return {
        "latest": ({"captured_at": latest.captured_at,
                    "sweep_stats": latest.sweep_stats}
                   if latest is not None else None),
        "unit_count": db.scalar(select(func.count(models.FeatureUnit.key))) or 0,
        "tag_counts": dict(tag_counts),
        "adoption": adoption,
    }
```

In `apps/coach_web/main.py`, next to the other router includes:

```python
    from .api import router as api_router
    app.include_router(api_router)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/api.py apps/coach_web/main.py tests/web/test_api.py
git commit -m "feat(web): summary read endpoint behind session auth"
```

---

### Task 9: End-to-end integration test — shipper against the real app

**Files:**
- Test: `tests/web/test_integration.py`

**Interfaces:**
- Consumes: `shipper.build_snapshot`, `shipper.ship_all` (post-injection seam), the full app via TestClient.

- [ ] **Step 1: Write the test**

`tests/web/test_integration.py`:

```python
import json
from pathlib import Path

from src import shipper
from tests.web.test_shipper import ADOPTION, CLS, LEDGER, write_jsonl


def client_post(client):
    def post(url, json=None, headers=None, timeout=None):
        return client.post("/api/ingest", json=json, headers=headers)
    return post


def test_sweep_data_ships_and_lands(client, tmp_path):
    write_jsonl(tmp_path / "ledger.jsonl", LEDGER)
    write_jsonl(tmp_path / "classifications.jsonl", CLS)
    payload = shipper.build_snapshot(tmp_path, ADOPTION, {"repos": 2},
                                     captured_at="2026-08-02T07:30:00+00:00")
    result = shipper.ship_all(payload, "/api/ingest", "test-ingest-token",
                              tmp_path / "outbox", post=client_post(client))
    assert result == {"shipped": 1, "queued": 0}

    client.post("/api/login", json={"password": "correct-horse"})
    data = client.get("/api/summary").json()
    assert data["unit_count"] == 1
    assert data["latest"]["sweep_stats"] == {"repos": 2}


def test_outbox_drains_into_app(client, tmp_path):
    payload = shipper.build_snapshot(tmp_path, ADOPTION, {"repos": 1},
                                     captured_at="2026-08-02T07:30:00+00:00")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "2026-08-01-aaaa.json").write_text(json.dumps(payload))
    second = shipper.build_snapshot(tmp_path, ADOPTION, {"repos": 2},
                                    captured_at="2026-08-02T08:30:00+00:00")
    result = shipper.ship_all(second, "/api/ingest", "test-ingest-token",
                              outbox, post=client_post(client))
    assert result == {"shipped": 2, "queued": 0}
    assert not list(outbox.glob("*.json"))
```

- [ ] **Step 2: Run the test**

Run: `.venv/bin/python -m pytest tests/web/test_integration.py -v`
Expected: PASS (all pieces already built; if it fails, fix the seam it exposes before continuing)

- [ ] **Step 3: Run full suite, commit**

Run: `.venv/bin/python -m pytest tests -q` — all pass.

```bash
git add tests/web/test_integration.py
git commit -m "test(web): end-to-end shipper-to-summary integration"
```

---

### Task 10: Alembic migrations, Railway deploy, first real ship

**Files:**
- Create: `apps/coach_web/alembic.ini`, `apps/coach_web/alembic/` (env.py + initial revision), `railway.json`
- Modify: `.env` (local, NOT committed), `Makefile` (optional `deploy` docs comment)

**Interfaces:**
- Consumes: everything prior.
- Produces: a live Railway service with Postgres, migrated schema, and the local sweep shipping to it.

- [ ] **Step 1: Init Alembic**

```bash
.venv/bin/alembic init apps/coach_web/alembic
mv alembic.ini apps/coach_web/alembic.ini
```

Edit `apps/coach_web/alembic.ini`: set `script_location = apps/coach_web/alembic`, delete the `sqlalchemy.url` line.

Edit `apps/coach_web/alembic/env.py` — replace the config/url/target section with:

```python
import os
from apps.coach_web import models

config = context.config
config.set_main_option("sqlalchemy.url",
                       os.environ.get("DATABASE_URL", "sqlite:///alembic-dev.db"))
target_metadata = models.Base.metadata
```

(Keep the rest of the generated env.py as-is.)

- [ ] **Step 2: Generate and verify the initial migration**

```bash
.venv/bin/alembic -c apps/coach_web/alembic.ini revision --autogenerate -m "initial ingested tables"
.venv/bin/alembic -c apps/coach_web/alembic.ini upgrade head
.venv/bin/alembic -c apps/coach_web/alembic.ini downgrade base
rm -f alembic-dev.db
```

Inspect the generated revision: it must create the five tables from Task 5 and nothing else.

- [ ] **Step 3: Railway config**

`railway.json` at repo root:

```json
{
  "$schema": "https://railway.com/railway.schema.json",
  "build": {"builder": "RAILPACK"},
  "deploy": {
    "startCommand": "alembic -c apps/coach_web/alembic.ini upgrade head && uvicorn apps.coach_web.main:app --host 0.0.0.0 --port $PORT",
    "healthcheckPath": "/api/health"
  }
}
```

- [ ] **Step 4: Commit**

```bash
git add apps/coach_web/alembic.ini apps/coach_web/alembic railway.json
git commit -m "feat(web): alembic migrations and railway deploy config"
```

- [ ] **Step 5: Provision and deploy (use railway-cli skill; CONFIRM TARGET WITH USER FIRST)**

Per global CLAUDE.md: **confirm the target Railway project/service with the user before the first deploy.** Then:

```bash
railway init   # or railway link if project exists — create project "app-builder-coach"
railway add --database postgres
railway up     # deploys the coach-web service from repo root
```

Generate secrets and set Railway env vars (service: coach-web):

```bash
.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'   # -> INGEST token value
.venv/bin/python -c 'import secrets; print(secrets.token_hex(32))'   # -> SECRET_KEY value
.venv/bin/python -m apps.coach_web.auth '<password Tom chooses>'     # -> PASSWORD_HASH value
railway variables --set "COACH_INGEST_TOKEN=<token>" --set "COACH_SECRET_KEY=<secret>" --set "COACH_PASSWORD_HASH=<hash>" --set "ANTHROPIC_API_KEY=<from local .env>"
```

`DATABASE_URL` is provided by the Postgres plugin reference — confirm it's wired (`railway variables`).

Generate a domain: `railway domain`.

NOTE (bash hygiene): run each command as its own Bash call; no `cd &&` chains. The `python -c` calls above run simple stdlib expressions with no braces/f-strings; if they prompt, fall back to a scratchpad script.

- [ ] **Step 6: Verify the deployment**

```bash
curl -s https://<domain>/api/health
```

Expected: `{"status":"ok"}`

```bash
curl -s -X POST https://<domain>/api/login -H 'Content-Type: application/json' -d '{"password":"<password>"}' -c /tmp/cookies -o /dev/null -w '%{http_code}'
curl -s https://<domain>/api/summary -b /tmp/cookies
```

Expected: `200`, then the empty-DB summary JSON.

- [ ] **Step 7: Point the local sweep at it and ship for real**

Append to local `.env` (do NOT commit):

```
COACH_INGEST_URL=https://<domain>/api/ingest
COACH_INGEST_TOKEN=<same token as Railway>
```

Run: `make sweep`
Expected: summary line ends with `shipped=1 queued=0`.

Then: `curl -s https://<domain>/api/summary -b /tmp/cookies` shows `unit_count` ≈ 90+ and real tag counts.

- [ ] **Step 8: Final commit (if Makefile/docs touched)**

```bash
git add -A
git commit -m "chore: phase 1 deploy notes"
```

Only commit if there are actual tracked changes; `.env` must never be staged.

---

## Self-Review Notes

- Spec coverage (Phase 1 scope): ingest API ✓ (Task 7), Postgres ✓ (5, 10), machine auth ✓ (6), human auth ✓ (6), shipper + outbox ✓ (3, 4), schema_version enforcement ✓ (2, 7), idempotency ✓ (7), Alembic ✓ (10), Railway deploy ✓ (10), sweep exits 0 with shipping failure ✓ (4 Step 6). Briefs/changelog/UI/new lanes are Phases 2–4 by design.
- Type consistency: adoption rows use `name` in payload but `feature_name` in the DB column — mapped explicitly in `apply_snapshot` (Task 7); `tests/web/test_api.py` imports `AUTH`/`make_payload` from `test_ingest` and `test_integration.py` imports fixtures from `test_shipper` — both files exist by the time they run.
- `snapshots.captured_at` refresh on duplicate keeps the freshness stamp honest when data is unchanged.
