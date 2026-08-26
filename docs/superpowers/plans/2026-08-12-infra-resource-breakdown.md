# Infra Resource Breakdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show which *service* inside each Railway project spends the money, and how much of it is memory — so "memory is 97% of the bill" becomes "downsize this Postgres."

**Architecture:** The sweep adds a second Railway CLI call **per project** (`railway usage projects --project <id> --json`), which returns a per-service resource breakdown. Those rows ship as a new schema-v4 snapshot section `infra_usage_services`, sibling to `infra_usage`. The server stores them in `infra_service_usage`, derives daily deltas one grouping level finer than the app-level lane, and attaches a `services` array to each app in `/api/truecost`. The Cost page's existing "By app" table gains a disclosure row.

**Tech Stack:** Existing stack (Python 3.11, FastAPI, SQLAlchemy/Alembic, React+TS). No new dependencies.

Read `docs/superpowers/specs/2026-08-12-infra-resource-breakdown-design.md` first, and `docs/HANDOFF.md` for deploy constraints.

## Global Constraints

- **v1, v2, and v3 payloads must never be rejected.** The outbox can hold pre-v4 payloads and a 400 quarantines them permanently as `.rejected`. `validate_payload` dispatches on `schema_version`; older versions keep exactly their current rules.
- A payload below v4 must be rejected if it carries `infra_usage_services`, mirroring the existing v1/`cost_daily` and v2/`infra_usage` guards.
- **Deploy order:** merge → deploy server → verify → *then* run the local sweep. Never ship v4 at a v3-only server.
- The sweep must always exit 0. With 10 CLI calls, **partial failure is the expected case** — ship rows for the projects that succeeded.
- Dollar values round to 6 decimals on write. Railway returns `-0.0` for unused components; **normalize to `0.0`** before rounding.
- Never sum a Railway billing-period figure with an Anthropic calendar-month figure.
- No new dependencies. No new repo-root files (so the Dockerfile `COPY` line needs no change — but do not remove anything from it).

## File Structure

```
src/railway_cost.py                (modify) per-project fetch, service_rows, collect loop
shared/snapshot.py                 (modify) SCHEMA_VERSION 4, infra_usage_services contract
src/shipper.py                     (modify) services= param, new body key
src/sweep.py                       (modify) run the service lane, summary field
apps/coach_web/models.py           (modify) InfraServiceUsage
apps/coach_web/alembic/versions/   (new revision) one table
apps/coach_web/ingest.py           (modify) v4 upsert loop
apps/coach_web/truecost.py         (modify) daily_infra_services, services_by_app
apps/coach_web/api.py              (modify) services array on /api/truecost
apps/coach_web/frontend/src/pages/Cost.tsx  (modify) disclosure sub-table
tests/test_railway_cost.py         (extend)
tests/web/test_snapshot_v4.py      (new)
tests/web/test_ingest_v4.py        (new)
tests/web/test_truecost.py         (extend)
apps/coach_web/frontend/src/__tests__/Cost.test.tsx  (extend)
```

Existing interfaces this plan builds on (do not change their signatures):
`shared.apps.load_apps/by_railway_id/display_map`; `railway_cost.fetch_usage(run=)`, `railway_cost.infra_rows(payload, apps, capture_date)`; `truecost.daily_infra(rows)`, `truecost.window_sums(daily, start, end)`, `truecost.period_to_date(rows)`; `shipper.build_snapshot(data_dir, adoption_rows, sweep_stats, captured_at, usage=None, infra=None)`; `models.InfraUsage`, `models.LlmDaily`.

---

### Task 1: Per-project service collection

**Files:**
- Modify: `src/railway_cost.py`
- Test: `tests/test_railway_cost.py` (extend)

**Interfaces:**
- Produces: `fetch_usage(run=subprocess.run, project: str | None = None) -> dict | None` — when `project` is given, appends `--project <project>` to the command. `service_rows(payload: dict | None, app: str, capture_date: str, expected_project_id: str) -> list[dict]` returning rows `{capture_date, period_start, app, service_id, service_name, cumulative_usd, memory_usd, cpu_usd, egress_usd, volume_usd, backup_usd}`. `collect_service_rows(apps: list[dict], capture_date: str, run=subprocess.run) -> tuple[list[dict], int, int]` returning `(rows, ok_count, attempted_count)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_railway_cost.py`:

```python
PROJECT_PAYLOAD = {
    "billingPeriod": {"start": "2026-07-27T16:07:00+00:00"},
    "project": {"id": "rw-1", "name": "B2B AI News"},
    "services": [
        {"id": "svc-db", "name": "Postgres", "totalDollars": 1.860755721362364,
         "memoryDollars": 1.7546751778741823, "cpuDollars": 0.07843898919753087,
         "egressDollars": 1.182e-07, "volumeDollars": 0.027641436090650975,
         "backupDollars": -0.0},
        {"id": "svc-app", "name": "B2B AI News", "totalDollars": 1.0796139809329401,
         "memoryDollars": 1.0508426830634956, "cpuDollars": 0.017767793819444447,
         "egressDollars": 0.011003504050000001, "volumeDollars": -0.0,
         "backupDollars": -0.0},
    ],
}


def test_fetch_usage_appends_project_flag():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return FakeProc(stdout=json.dumps(PROJECT_PAYLOAD))

    railway_cost.fetch_usage(run=run, project="rw-1")
    assert seen["cmd"] == ["railway", "usage", "projects", "--project", "rw-1", "--json"]


def test_fetch_usage_without_project_unchanged():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return FakeProc(stdout=json.dumps(PAYLOAD))

    railway_cost.fetch_usage(run=run)
    assert seen["cmd"] == ["railway", "usage", "projects", "--json"]


def test_service_rows_parses_and_normalizes():
    rows = railway_cost.service_rows(PROJECT_PAYLOAD, "b2b-ai-news", "2026-08-11", "rw-1")
    assert [r["service_id"] for r in rows] == ["svc-app", "svc-db"]  # sorted by service_id
    db = next(r for r in rows if r["service_id"] == "svc-db")
    assert db["app"] == "b2b-ai-news"
    assert db["service_name"] == "Postgres"
    assert db["period_start"] == "2026-07-27"
    assert db["cumulative_usd"] == 1.860756
    assert db["memory_usd"] == 1.754675
    assert db["egress_usd"] == 0.0          # 1.182e-07 rounds to zero
    # -0.0 must be normalized: repr(-0.0) is "-0.0" and it poisons downstream sums
    assert str(db["backup_usd"]) == "0.0"
    app_row = next(r for r in rows if r["service_id"] == "svc-app")
    assert str(app_row["volume_usd"]) == "0.0"


def test_service_rows_rejects_project_id_mismatch():
    assert railway_cost.service_rows(PROJECT_PAYLOAD, "b2b", "2026-08-11", "other-id") == []


def test_service_rows_none_payload():
    assert railway_cost.service_rows(None, "b2b", "2026-08-11", "rw-1") == []


def test_service_rows_bad_period():
    bad = dict(PROJECT_PAYLOAD, billingPeriod={})
    assert railway_cost.service_rows(bad, "b2b", "2026-08-11", "rw-1") == []


def test_collect_service_rows_partial_failure():
    apps = [
        {"name": "a", "display": "A", "railway_project_id": "rw-1", "active": True},
        {"name": "b", "display": "B", "railway_project_id": "rw-2", "active": True},
    ]

    def run(cmd, **kw):
        if "rw-2" in cmd:
            return FakeProc(1, stderr="boom")
        return FakeProc(stdout=json.dumps(PROJECT_PAYLOAD))

    rows, ok, attempted = railway_cost.collect_service_rows(apps, "2026-08-11", run=run)
    assert ok == 1 and attempted == 2
    assert {r["app"] for r in rows} == {"a"}   # the successful project still ships


def test_collect_service_rows_all_fail():
    apps = [{"name": "a", "display": "A", "railway_project_id": "rw-1", "active": True}]
    rows, ok, attempted = railway_cost.collect_service_rows(
        apps, "2026-08-11", run=lambda *a, **k: FakeProc(1))
    assert rows == [] and ok == 0 and attempted == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_railway_cost.py -v`
Expected: FAIL — `TypeError: fetch_usage() got an unexpected keyword argument 'project'` and `AttributeError: module 'src.railway_cost' has no attribute 'service_rows'`.

- [ ] **Step 3: Implement**

In `src/railway_cost.py`, replace `fetch_usage` with:

```python
def fetch_usage(run=subprocess.run, project: str | None = None) -> dict | None:
    """Return the parsed `railway usage projects` payload, or None.

    With `project` (a Railway project id), returns that project's per-service
    breakdown instead of every project's total. Every failure mode returns None
    so the sweep can degrade: the CLI may be missing, unauthenticated (non-zero
    exit), or slow.
    """
    cmd = ["railway", "usage", "projects"]
    if project:
        cmd += ["--project", project]
    cmd.append("--json")
    try:
        proc = run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        log.exception("railway CLI could not be run")
        return None
    if proc.returncode != 0:
        log.warning("railway usage exited %s: %s",
                    proc.returncode, (proc.stderr or "")[:200])
        return None
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        log.warning("railway usage returned non-JSON output")
        return None
```

Append to the same file:

```python
# Railway field -> our row field. Railway reports unused components as -0.0.
_SERVICE_FIELDS = (
    ("totalDollars", "cumulative_usd"),
    ("memoryDollars", "memory_usd"),
    ("cpuDollars", "cpu_usd"),
    ("egressDollars", "egress_usd"),
    ("volumeDollars", "volume_usd"),
    ("backupDollars", "backup_usd"),
)


def _dollars(raw) -> float:
    value = round(float(raw or 0.0), 6)
    # -0.0 survives rounding and its repr is "-0.0", which is meaningless to
    # display and poisons downstream sums and charts. Collapse it to 0.0.
    return 0.0 if value == 0 else value


def service_rows(payload: dict | None, app: str, capture_date: str,
                 expected_project_id: str) -> list[dict]:
    """Per-service rows for one project's payload, or [] on anything unusable."""
    if not payload:
        return []
    got_id = (payload.get("project") or {}).get("id")
    if got_id != expected_project_id:
        log.warning("railway returned project %r when %r was requested; dropping",
                    got_id, expected_project_id)
        return []
    period_start = str((payload.get("billingPeriod") or {}).get("start") or "")[:10]
    if len(period_start) != 10:
        log.warning("project %s payload has no usable billingPeriod.start", app)
        return []
    rows = []
    for svc in payload.get("services") or []:
        service_id = svc.get("id")
        if not service_id:
            continue
        row = {"capture_date": capture_date, "period_start": period_start,
               "app": app, "service_id": str(service_id),
               "service_name": str(svc.get("name") or "")}
        for src_field, dst_field in _SERVICE_FIELDS:
            row[dst_field] = _dollars(svc.get(src_field))
        rows.append(row)
    return sorted(rows, key=lambda r: r["service_id"])


def collect_service_rows(apps: list[dict], capture_date: str,
                         run=subprocess.run) -> tuple:
    """One CLI call per registry entry. Returns (rows, ok_count, attempted).

    Partial failure is the expected case with this many calls: one project
    failing must not withhold the others' drill-down detail.
    """
    rows: list[dict] = []
    ok = 0
    apps = list(apps)
    for app in apps:
        project_id = app["railway_project_id"]
        payload = fetch_usage(run=run, project=project_id)
        got = service_rows(payload, app["name"], capture_date, project_id)
        if got:
            ok += 1
            rows.extend(got)
    return rows, ok, len(apps)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_railway_cost.py -v`
Expected: PASS (8 existing + 8 new = 16 tests)

- [ ] **Step 5: Sanity-check against the real CLI**

Run:
```bash
.venv/bin/python -c "
from pathlib import Path
from shared import apps
from src import railway_cost
rows, ok, n = railway_cost.collect_service_rows(apps.load_apps(Path('apps.yaml')), '2026-08-12')
print(f'{ok}/{n} projects ok, {len(rows)} services')
print('memory share:', round(sum(r['memory_usd'] for r in rows) / sum(r['cumulative_usd'] for r in rows) * 100), '%')
"
```
Expected: roughly `9/9 projects ok, 24 services` and a memory share near 97%. A lower `ok` count means some projects failed — report the count, do not change the code to mask it.

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add src/railway_cost.py tests/test_railway_cost.py
git commit -m "feat(infra): per-project service breakdown from the railway CLI"
```

---

### Task 2: Schema v4 + shipper + sweep wiring

**Files:**
- Modify: `shared/snapshot.py`, `src/shipper.py`, `src/sweep.py`
- Test: `tests/web/test_snapshot_v4.py` (new), `tests/web/test_shipper.py` (extend)

**Interfaces:**
- Consumes: `railway_cost.collect_service_rows` from Task 1.
- Produces: `SCHEMA_VERSION = 4`; `SUPPORTED_VERSIONS = (1, 2, 3, 4)`; v4 payload = v3 keys + required top-level `infra_usage_services`. `build_snapshot(..., infra=None, services=None)`; when `services` is None the key ships as `[]`.

**Bump and emit in one commit** — `build_snapshot` stamps `SCHEMA_VERSION`, so bumping it without also emitting `infra_usage_services` leaves the shipper and integration suites red in between. This is the third time this pattern applies (v2 and v3 both did it).

- [ ] **Step 1: Write the failing tests**

`tests/web/test_snapshot_v4.py`:

```python
import pytest

from shared import snapshot

SERVICE_ROW = {"capture_date": "2026-08-12", "period_start": "2026-07-27",
               "app": "b2b-ai-news", "service_id": "svc-db",
               "service_name": "Postgres", "cumulative_usd": 1.860756,
               "memory_usd": 1.754675, "cpu_usd": 0.078439,
               "egress_usd": 0.0, "volume_usd": 0.027641, "backup_usd": 0.0}

V4_BODY = {
    "schema_version": 4,
    "sweep": {"repos": 1},
    "feature_units": [],
    "activity_daily": [],
    "adoption": [],
    "cost_daily": [],
    "infra_usage": [],
    "infra_usage_services": [SERVICE_ROW],
}


def fin(body):
    return snapshot.finalize_payload(dict(body), "2026-08-12T07:30:00+00:00")


def test_schema_version_is_4():
    assert snapshot.SCHEMA_VERSION == 4


def test_v4_valid():
    snapshot.validate_payload(fin(V4_BODY))


def test_v3_still_valid_without_services():
    v3 = {k: v for k, v in V4_BODY.items() if k != "infra_usage_services"}
    v3["schema_version"] = 3
    snapshot.validate_payload(fin(v3))


def test_v2_still_valid():
    v2 = {k: v for k, v in V4_BODY.items()
          if k not in ("infra_usage_services", "infra_usage")}
    v2["schema_version"] = 2
    snapshot.validate_payload(fin(v2))


def test_v1_still_valid():
    v1 = {k: v for k, v in V4_BODY.items()
          if k not in ("infra_usage_services", "infra_usage", "cost_daily")}
    v1["schema_version"] = 1
    snapshot.validate_payload(fin(v1))


def test_v3_with_services_rejected():
    v3 = dict(V4_BODY)
    v3["schema_version"] = 3
    with pytest.raises(ValueError, match="infra_usage_services"):
        snapshot.validate_payload(fin(v3))


def test_v4_missing_services_rejected():
    v4 = {k: v for k, v in V4_BODY.items() if k != "infra_usage_services"}
    with pytest.raises(ValueError, match="infra_usage_services"):
        snapshot.validate_payload(fin(v4))


def test_v4_bad_service_row():
    v4 = dict(V4_BODY)
    v4["infra_usage_services"] = [{"app": "x"}]
    with pytest.raises(ValueError, match="capture_date"):
        snapshot.validate_payload(fin(v4))


def test_v4_service_dollars_must_be_number():
    v4 = dict(V4_BODY)
    v4["infra_usage_services"] = [dict(SERVICE_ROW, memory_usd=True)]
    with pytest.raises(ValueError, match="memory_usd"):
        snapshot.validate_payload(fin(v4))


def test_v5_rejected():
    v5 = dict(V4_BODY)
    v5["schema_version"] = 5
    with pytest.raises(ValueError, match="schema_version"):
        snapshot.validate_payload(fin(v5))
```

Append to `tests/web/test_shipper.py`:

```python
def test_build_snapshot_v4_carries_services(tmp_path):
    services = [{"capture_date": "2026-08-12", "period_start": "2026-07-27",
                 "app": "b2b-ai-news", "service_id": "svc-db",
                 "service_name": "Postgres", "cumulative_usd": 1.86,
                 "memory_usd": 1.75, "cpu_usd": 0.08, "egress_usd": 0.0,
                 "volume_usd": 0.03, "backup_usd": 0.0}]
    p = shipper.build_snapshot(tmp_path, [], {}, captured_at="2026-08-12T07:30:00+00:00",
                               services=services)
    snapshot.validate_payload(p)
    assert p["schema_version"] == 4
    assert p["infra_usage_services"] == services


def test_build_snapshot_no_services_ships_empty(tmp_path):
    p = shipper.build_snapshot(tmp_path, [], {}, captured_at="2026-08-12T07:30:00+00:00")
    snapshot.validate_payload(p)
    assert p["infra_usage_services"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_snapshot_v4.py -v`
Expected: FAIL — `assert 3 == 4`, then unsupported-schema_version errors.

- [ ] **Step 3: Update `shared/snapshot.py`**

Version constants:

```python
SCHEMA_VERSION = 4
SUPPORTED_VERSIONS = (1, 2, 3, 4)
```

Add after `V3_REQUIRED_KEYS`:

```python
V4_REQUIRED_KEYS = V3_REQUIRED_KEYS + ("infra_usage_services",)
```

Add beside `INFRA_KEYS`:

```python
SERVICE_KEYS = {"capture_date", "period_start", "app", "service_id", "service_name",
                "cumulative_usd", "memory_usd", "cpu_usd", "egress_usd",
                "volume_usd", "backup_usd"}
V4_ITEM_SCHEMAS = {"infra_usage_services": (SERVICE_KEYS, SERVICE_KEYS)}
V4_FIELD_TYPES = tuple(
    ("infra_usage_services", field, (int, float), "number")
    for field in ("cumulative_usd", "memory_usd", "cpu_usd",
                  "egress_usd", "volume_usd", "backup_usd")
)
```

In `validate_payload`, extend the required-keys ladder and the below-version guard, and add the item validation. The version dispatch becomes:

```python
    if version == 4:
        required = V4_REQUIRED_KEYS
    elif version == 3:
        required = V3_REQUIRED_KEYS
    elif version == 2:
        required = V2_REQUIRED_KEYS
    else:
        required = REQUIRED_KEYS
```

Add beside the existing `version < 3` guard:

```python
    if version < 4 and "infra_usage_services" in p:
        raise ValueError(
            f"schema_version {version} must not carry infra_usage_services")
```

And after the `version >= 3` item validation:

```python
    if version >= 4:
        _validate_items(p, V4_ITEM_SCHEMAS, V4_FIELD_TYPES)
```

- [ ] **Step 4: Update `src/shipper.py`**

Signature gains one parameter:

```python
def build_snapshot(data_dir: Path, adoption_rows: list[dict],
                   sweep_stats: dict, captured_at: str,
                   usage: dict | None = None,
                   infra: list[dict] | None = None,
                   services: list[dict] | None = None) -> dict:
```

Add one key to `body`, after `"infra_usage"`:

```python
        "infra_usage_services": services or [],
```

- [ ] **Step 5: Update `src/sweep.py`**

After the existing infra block, add:

```python
        try:
            service_rows, svc_ok, svc_n = railway_cost.collect_service_rows(
                apps_registry.load_apps(config.REPO_ROOT / "apps.yaml"), today)
        except Exception:
            log.exception("infra service lane failed; shipping without service data")
            service_rows, svc_ok, svc_n = [], 0, 0
```

Pass it through in the `build_snapshot` call: `services=service_rows`.

Add a summary field distinguishing full success from partial. Extend the summary f-string's `infra=` segment to:

```python
              f"infra={'ok' if infra_rows else 'failed'} "
              f"infra_services={'ok' if svc_ok and svc_ok == svc_n else ('failed' if not svc_ok else f'partial({svc_ok}/{svc_n})')}"
```

Keep the existing `+ (f" shipped=..." ...)` continuation exactly as it is.

- [ ] **Step 6: Run the full suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. `tests/web/test_snapshot_v3.py::test_v4_rejected` now asserts a valid version — change that one test to use version 5. That is the only permitted edit to that file and must be called out in the commit message. If any other pre-existing test breaks purely because `SCHEMA_VERSION` changed, apply the same minimal mechanical fix and call each one out in the commit message too.

- [ ] **Step 7: Commit**

```bash
git add shared/snapshot.py src/shipper.py src/sweep.py \
        tests/web/test_snapshot_v4.py tests/web/test_shipper.py tests/web/test_snapshot_v3.py
git commit -m "feat(shared,shipper): schema v4 with infra_usage_services, v1-v3 still accepted

test_snapshot_v3.py::test_v4_rejected now uses v5 (4 is valid now)."
```

---

### Task 3: Server table, migration, v4 ingest

**Files:**
- Modify: `apps/coach_web/models.py`, `apps/coach_web/ingest.py`
- Create: alembic revision (autogenerated)
- Test: `tests/web/test_ingest_v4.py`

**Interfaces:**
- Produces: `models.InfraServiceUsage` with composite PK `(period_start, app, service_id, capture_date)` **declared in that order** — `db.get(Model, tuple)` resolves positionally. `apply_snapshot` upserts `infra_usage_services` rows on that key.

- [ ] **Step 1: Write the failing test**

`tests/web/test_ingest_v4.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.coach_web import models
from shared import snapshot as snap_mod
from tests.web.test_ingest import AUTH


def v4_payload(memory=1.75, capture="2026-08-12"):
    body = {
        "schema_version": 4, "sweep": {"repos": 1}, "feature_units": [],
        "activity_daily": [], "adoption": [], "cost_daily": [], "infra_usage": [],
        "infra_usage_services": [
            {"capture_date": capture, "period_start": "2026-07-27",
             "app": "b2b-ai-news", "service_id": "svc-db", "service_name": "Postgres",
             "cumulative_usd": 1.86, "memory_usd": memory, "cpu_usd": 0.08,
             "egress_usd": 0.0, "volume_usd": 0.03, "backup_usd": 0.0}],
    }
    return snap_mod.finalize_payload(body, f"2026-08-12T0{len(capture) % 9}:00:00+00:00")


def test_v4_ingest_stores_service_row(client):
    assert client.post("/api/ingest", json=v4_payload(), headers=AUTH).status_code == 200
    with Session(client.app.state.engine) as s:
        row = s.get(models.InfraServiceUsage,
                    ("2026-07-27", "b2b-ai-news", "svc-db", "2026-08-12"))
        assert row.service_name == "Postgres"
        assert row.memory_usd == 1.75
        assert row.cumulative_usd == 1.86


def test_v4_ingest_upserts_same_capture(client):
    client.post("/api/ingest", json=v4_payload(memory=1.0), headers=AUTH)
    client.post("/api/ingest", json=v4_payload(memory=2.0), headers=AUTH)
    with Session(client.app.state.engine) as s:
        rows = s.scalars(select(models.InfraServiceUsage)).all()
        assert len(rows) == 1 and rows[0].memory_usd == 2.0


def test_v4_ingest_keeps_separate_captures(client):
    client.post("/api/ingest", json=v4_payload(capture="2026-08-11"), headers=AUTH)
    client.post("/api/ingest", json=v4_payload(capture="2026-08-12"), headers=AUTH)
    with Session(client.app.state.engine) as s:
        assert len(s.scalars(select(models.InfraServiceUsage)).all()) == 2


def test_v3_payload_still_ingests(client):
    from tests.web.test_ingest_v3 import v3_payload
    assert client.post("/api/ingest", json=v3_payload(), headers=AUTH).status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_ingest_v4.py -v`
Expected: FAIL — `AttributeError: ... has no attribute 'InfraServiceUsage'`

- [ ] **Step 3: Add the model**

In `apps/coach_web/models.py`, add immediately after `class InfraUsage`:

```python
class InfraServiceUsage(Base):
    """Per-service Railway cost within a project, as captured.

    Cumulative period-to-date like InfraUsage, one grouping level finer. The PK
    uses service_id rather than service_name because Railway ids are stable and
    names are user-editable.
    """
    __tablename__ = "infra_service_usage"
    period_start: Mapped[str] = mapped_column(String(10), primary_key=True)
    app: Mapped[str] = mapped_column(String(64), primary_key=True)
    service_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    capture_date: Mapped[str] = mapped_column(String(10), primary_key=True)
    service_name: Mapped[str] = mapped_column(String(120), default="")
    cumulative_usd: Mapped[float] = mapped_column(Float, default=0.0)
    memory_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cpu_usd: Mapped[float] = mapped_column(Float, default=0.0)
    egress_usd: Mapped[float] = mapped_column(Float, default=0.0)
    volume_usd: Mapped[float] = mapped_column(Float, default=0.0)
    backup_usd: Mapped[float] = mapped_column(Float, default=0.0)
```

- [ ] **Step 4: Add the ingest loop**

In `apps/coach_web/ingest.py`, after the existing `infra_usage` loop and before `db.commit()`:

```python
    for s in payload.get("infra_usage_services", []):
        key = (s["period_start"], s["app"], s["service_id"], s["capture_date"])
        existing_service = db.get(models.InfraServiceUsage, key)
        if existing_service is None:
            db.add(models.InfraServiceUsage(**s))
        else:
            for field in ("service_name", "cumulative_usd", "memory_usd", "cpu_usd",
                          "egress_usd", "volume_usd", "backup_usd"):
                setattr(existing_service, field, s[field])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_ingest_v4.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Generate and verify the Alembic revision**

```bash
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini upgrade head
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini revision --autogenerate -m "infra_service_usage table"
```

Inspect the generated file: `upgrade()` must contain ONLY `create_table('infra_service_usage')` and `downgrade()` only the matching `drop_table`. Anything else — a column alter, an index change, a drop of another table — means stop and report rather than commit. Then cycle it:

```bash
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini upgrade head
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini downgrade -1
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini upgrade head
.venv/bin/python -c "from pathlib import Path; Path('alembic-dev.db').unlink(missing_ok=True)"
```

- [ ] **Step 7: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add apps/coach_web/models.py apps/coach_web/ingest.py apps/coach_web/alembic \
        tests/web/test_ingest_v4.py
git commit -m "feat(web): infra_service_usage table and v4 ingest"
```

---

### Task 4: Derivation and the services array

**Files:**
- Modify: `apps/coach_web/truecost.py`, `apps/coach_web/api.py`
- Test: `tests/web/test_truecost.py` (extend)

**Interfaces:**
- Consumes: `models.InfraServiceUsage` from Task 3.
- Produces: `truecost.daily_infra_services(rows, field="cumulative_usd") -> dict` returning `{capture_date: {(app, service_id): daily_usd}}` for one dollar field; `truecost.services_by_app(total_map, memory_map, name_map) -> dict[str, list[dict]]`. `/api/truecost` app entries gain a `services` array.

**Why `daily_infra` is not generalized:** it is shipped, tested, and called from `api.py`. A sibling function with the same delta rule one grouping level finer leaves it untouched. `window_sums` needs **no change** — it already sums `{date: {key: usd}}` for any hashable key, and a `(app, service_id)` tuple is one. The test below pins that as intentional.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_truecost.py`:

```python
def svc(period, app, sid, capture, total, memory, name="Postgres"):
    return SimpleNamespace(period_start=period, app=app, service_id=sid,
                           capture_date=capture, cumulative_usd=total,
                           memory_usd=memory, service_name=name)


def test_daily_infra_services_derives_deltas_per_service():
    rows = [svc("2026-07-27", "a", "s1", "2026-08-10", 1.0, 0.9),
            svc("2026-07-27", "a", "s1", "2026-08-11", 2.5, 2.4),
            svc("2026-07-27", "a", "s2", "2026-08-11", 4.0, 3.9)]
    daily = truecost.daily_infra_services(rows)
    assert daily["2026-08-10"][("a", "s1")] == 1.0
    assert daily["2026-08-11"][("a", "s1")] == 1.5
    assert daily["2026-08-11"][("a", "s2")] == 4.0   # first capture = cumulative


def test_daily_infra_services_clamps_decrease_and_handles_rollover():
    rows = [svc("2026-07-27", "a", "s1", "2026-08-10", 5.0, 5.0),
            svc("2026-07-27", "a", "s1", "2026-08-11", 4.0, 4.0),
            svc("2026-08-27", "a", "s1", "2026-08-27", 0.4, 0.4)]
    daily = truecost.daily_infra_services(rows)
    assert daily["2026-08-11"][("a", "s1")] == 0.0   # decrease clamps, never negative
    assert daily["2026-08-27"][("a", "s1")] == 0.4   # new period, not a delta


def test_daily_infra_services_selects_field():
    rows = [svc("2026-07-27", "a", "s1", "2026-08-10", 10.0, 9.0)]
    assert truecost.daily_infra_services(rows, "memory_usd")["2026-08-10"][("a", "s1")] == 9.0


def test_window_sums_works_unmodified_with_tuple_keys():
    # Pins the claim that window_sums is key-agnostic, so daily_infra_services
    # can hand it composite keys without touching it.
    daily = {"2026-08-01": {("a", "s1"): 1.0, ("a", "s2"): 2.0},
             "2026-07-01": {("a", "s1"): 99.0}}
    assert truecost.window_sums(daily, "2026-08-01", "2026-08-31") == {
        ("a", "s1"): 1.0, ("a", "s2"): 2.0}


def test_services_by_app_shapes_and_sorts():
    totals = {("a", "s1"): 4.0, ("a", "s2"): 6.0, ("b", "s3"): 1.0}
    memory = {("a", "s1"): 3.0, ("a", "s2"): 5.5, ("b", "s3"): 0.5}
    names = {("a", "s1"): "web", ("a", "s2"): "Postgres", ("b", "s3"): "web"}
    out = truecost.services_by_app(totals, memory, names)
    assert [s["service"] for s in out["a"]] == ["Postgres", "web"]   # desc by total
    assert out["a"][0]["total_usd"] == 6.0
    assert out["a"][0]["memory_usd"] == 5.5
    assert out["a"][0]["share_of_app"] == 0.6                        # 6.0 / 10.0
    assert round(sum(s["share_of_app"] for s in out["a"]), 4) == 1.0


def test_services_by_app_zero_total():
    out = truecost.services_by_app({("a", "s1"): 0.0}, {("a", "s1"): 0.0},
                                   {("a", "s1"): "web"})
    assert out["a"][0]["share_of_app"] == 0.0    # no ZeroDivisionError


def test_truecost_includes_services(client):
    today = date.today().isoformat()
    from shared import snapshot as snap_mod
    payload = snap_mod.finalize_payload({
        "schema_version": 4, "sweep": {}, "feature_units": [], "activity_daily": [],
        "adoption": [], "cost_daily": [], "infra_usage": [],
        "infra_usage_services": [
            {"capture_date": today, "period_start": today, "app": "app-builder-coach",
             "service_id": "svc-db", "service_name": "Postgres", "cumulative_usd": 3.0,
             "memory_usd": 2.8, "cpu_usd": 0.2, "egress_usd": 0.0,
             "volume_usd": 0.0, "backup_usd": 0.0}]}, f"{today}T07:30:00+00:00")
    client.post("/api/ingest", json=payload, headers=AUTH)
    login(client)
    data = client.get("/api/truecost?days=30").json()
    entry = next(a for a in data["apps"] if a["app"] == "app-builder-coach")
    assert entry["services"][0]["service"] == "Postgres"
    assert entry["services"][0]["memory_usd"] == 2.8


def test_truecost_omits_services_when_absent(client):
    login(client)
    assert client.get("/api/truecost").json()["apps"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_truecost.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'daily_infra_services'`

- [ ] **Step 3: Implement the derivation**

Append to `apps/coach_web/truecost.py`:

```python
def daily_infra_services(rows, field: str = "cumulative_usd") -> dict:
    """{capture_date: {(app, service_id): daily_usd}} for one dollar field.

    Same delta rule as daily_infra, grouped one level finer. Deliberately a
    sibling rather than a generalization: daily_infra is shipped, tested, and
    called from api.py, and this needs a different key without changing that.
    """
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row.period_start, row.app, row.service_id)].append(row)
    out: dict = {}
    for (_period, app, service_id), group in grouped.items():
        group.sort(key=lambda r: r.capture_date)
        previous = 0.0
        for row in group:
            current = getattr(row, field) or 0.0
            delta = current - previous
            # Same reasoning as daily_infra: a decrease is a restatement, not a
            # fresh series, so the day's spend is 0.0 rather than the cumulative.
            if delta < 0:
                delta = 0.0
            out.setdefault(row.capture_date, {})[(app, service_id)] = round(delta, 6)
            previous = current
    return out


def services_by_app(total_map: dict, memory_map: dict, name_map: dict) -> dict:
    """Group windowed per-service sums into {app: [service dicts, desc by total]}."""
    per_app: dict = defaultdict(list)
    app_totals: dict = defaultdict(float)
    for (app, service_id), usd in total_map.items():
        app_totals[app] += usd
    for (app, service_id), usd in total_map.items():
        per_app[app].append({
            "service": name_map.get((app, service_id), service_id),
            "service_id": service_id,
            "total_usd": round(usd, 2),
            "memory_usd": round(memory_map.get((app, service_id), 0.0), 2),
            "share_of_app": (round(usd / app_totals[app], 4)
                             if app_totals[app] else 0.0),
        })
    for app in per_app:
        per_app[app].sort(key=lambda s: -s["total_usd"])
    return dict(per_app)
```

- [ ] **Step 4: Attach services to `/api/truecost`**

In `apps/coach_web/api.py`, inside the `truecost` endpoint, after the existing `railway_by_app` computation:

```python
    service_rows = list(db.scalars(select(models.InfraServiceUsage)))
    svc_totals = truecost_mod.window_sums(
        truecost_mod.daily_infra_services(service_rows), start, end)
    svc_memory = truecost_mod.window_sums(
        truecost_mod.daily_infra_services(service_rows, "memory_usd"), start, end)
    svc_names: dict = {}
    for r in service_rows:
        key = (r.app, r.service_id)
        current = svc_names.get(key)
        if current is None or r.capture_date > current[0]:
            svc_names[key] = (r.capture_date, r.service_name)
    services_map = truecost_mod.services_by_app(
        svc_totals, svc_memory, {k: v[1] for k, v in svc_names.items()})
```

Then, in the loop that builds `apps_out`, add one key to each entry:

```python
                         "services": services_map.get(app, []),
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_truecost.py -v`
Expected: PASS

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add apps/coach_web/truecost.py apps/coach_web/api.py tests/web/test_truecost.py
git commit -m "feat(web): per-service derivation and services array on /api/truecost"
```

---

### Task 5: Frontend disclosure rows

**Files:**
- Modify: `apps/coach_web/frontend/src/pages/Cost.tsx`
- Test: `apps/coach_web/frontend/src/__tests__/Cost.test.tsx` (extend)

**Interfaces:**
- Consumes: the `services` array added to each `/api/truecost` app entry in Task 4.

- [ ] **Step 1: Extend the type**

In `Cost.tsx`, add to the `apps` member of the `TrueCost` type:

```tsx
    services?: { service: string; service_id: string; total_usd: number;
      memory_usd: number; share_of_app: number }[];
```

- [ ] **Step 2: Add expansion state and the disclosure UI**

Add alongside the existing state:

```tsx
  const [open, setOpen] = useState<Record<string, boolean>>({});
```

Replace the per-app `<tr>` in the "By app" table body with a fragment that renders the app row plus, when expanded, one row per service:

```tsx
                {tc.apps.map((a) => {
                  const hasServices = !!a.services && a.services.length > 0;
                  const isOpen = !!open[a.app];
                  return (
                    <Fragment key={a.app}>
                      <tr>
                        <td className="ink2">
                          {hasServices ? (
                            <button type="button"
                              onClick={() => setOpen((o) => ({ ...o, [a.app]: !o[a.app] }))}
                              aria-expanded={isOpen}
                              style={{ background: "none", border: "none", padding: 0,
                                cursor: "pointer", color: "inherit", font: "inherit" }}>
                              {isOpen ? "▾" : "▸"} {a.display}
                            </button>
                          ) : a.display}
                        </td>
                        <td className="num" style={{ textAlign: "right" }}>${a.railway_usd.toFixed(2)}</td>
                        <td className="num" style={{ textAlign: "right" }}>${a.llm_usd.toFixed(2)}</td>
                        <td className="num" style={{ textAlign: "right" }}>${a.total_usd.toFixed(2)}</td>
                      </tr>
                      {isOpen && a.services!.map((s) => (
                        <tr key={s.service_id} className="muted" style={{ fontSize: 12 }}>
                          <td style={{ paddingLeft: 24 }}>
                            {s.service} <span className="ink2">
                              ({(s.share_of_app * 100).toFixed(0)}%)</span>
                          </td>
                          <td className="num" style={{ textAlign: "right" }}>${s.total_usd.toFixed(2)}</td>
                          <td colSpan={2} style={{ textAlign: "right" }}>
                            memory ${s.memory_usd.toFixed(2)}
                          </td>
                        </tr>
                      ))}
                    </Fragment>
                  );
                })}
```

Import `Fragment` and `useState` from `react` if not already imported.

- [ ] **Step 3: Add the test**

Append to `apps/coach_web/frontend/src/__tests__/Cost.test.tsx`, following the mocking pattern the existing tests in that file already use:

```tsx
it("reveals per-service rows when an app is expanded", async () => {
  // /api/truecost returns one app with two services; /api/cost may be anything.
  render(<Cost />);
  const toggle = await screen.findByRole("button", { name: /B2B AI News/ });
  expect(screen.queryByText(/Postgres/)).not.toBeInTheDocument();
  await userEvent.click(toggle);
  expect(await screen.findByText(/Postgres/)).toBeInTheDocument();
  expect(screen.getByText(/memory \$7\.45/)).toBeInTheDocument();
});
```

Wire the fetch mock so `/api/truecost` returns an app named `B2B AI News` whose `services` array contains `{service: "Postgres", service_id: "s1", total_usd: 7.90, memory_usd: 7.45, share_of_app: 0.64}` and one other service. If the existing tests mock `get` from `../api` rather than `fetch`, follow that.

- [ ] **Step 4: Typecheck, test, build**

```bash
cd apps/coach_web/frontend && npx tsc --noEmit && npx vitest run && npm run build
```
Expected: tsc clean, all tests pass, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/frontend/src/pages/Cost.tsx \
        apps/coach_web/frontend/src/__tests__/Cost.test.tsx
git commit -m "feat(frontend): per-service disclosure rows on the By app table"
```

---

### Task 6: Deploy

**Files:** none new (uses `.claude/skills/deploy-coach-web/SKILL.md`)

- [ ] **Step 1: Finish the branch.** Final whole-branch review, then merge to main. Confirm the Python suite and the frontend typecheck/build are green on the merge commit.

- [ ] **Step 2: Deploy the server first.**

```bash
railway up --service coach-web --detach
```
Poll `railway deployment list --service coach-web --limit 1 --json` until terminal (`SUCCESS|FAILED|CRASHED`); run the poll loop as a background task. The Dockerfile `CMD` runs the new migration automatically. No new environment variable is required by this feature.

- [ ] **Step 3: Verify the server before shipping any v4.**

```bash
curl -s https://coach-web-production-1f04.up.railway.app/api/health
railway logs --service coach-web | grep -i infra_service_usage
```
Expected: `{"status":"ok"}` and a line showing the migration ran. **Note:** a `GET` on a POST-only route returns 404 here, not 405 — the SPA fallback swallows it. Do not use a GET to test route presence.

- [ ] **Step 4: Run the local sweep (first v4 ship).**

```bash
make sweep
```
Expected: the summary line shows `infra=ok infra_services=ok` (or `partial(k/n)`) and `shipped=1 queued=0`. Any v1–v3 payloads sitting in `data/outbox/` ship fine; older versions remain valid.

- [ ] **Step 5: Verify live.**

```bash
railway ssh --service coach-web "python -c \"
import os
from sqlalchemy import create_engine, text
e = create_engine(os.environ['DATABASE_URL'].replace('postgresql://','postgresql+psycopg://'))
with e.connect() as c:
    n = c.execute(text('select count(*) from infra_service_usage')).scalar()
    mem = c.execute(text('select round((sum(memory_usd)/nullif(sum(cumulative_usd),0)*100)::numeric,0) from infra_service_usage')).scalar()
    top = c.execute(text('select app, service_name, round(cumulative_usd::numeric,2) from infra_service_usage order by cumulative_usd desc limit 5')).all()
print('service rows:', n, '| memory share:', mem, '%')
print('top:', top)
\""
```
Expected: roughly 24 rows and a memory share near 97%.

Then browser-walk the Cost page: expand an app row and confirm the service sub-rows render with dollars, share, and memory.

- [ ] **Step 6: Update `docs/HANDOFF.md`** — record that schema is now v4, that the sweep makes 10 Railway CLI calls, and what `infra_services=partial(k/n)` in the summary line means.

---

## Self-Review Notes

- **Spec coverage:** per-project CLI call ✓ T1; `-0.0` normalization ✓ T1; project-id mismatch guard ✓ T1; partial-failure degradation ✓ T1+T2; schema v4 sibling section with v1–v3 compatibility ✓ T2; summary field ✓ T2; table + migration + upsert ✓ T3; sibling derivation and the `window_sums`-unchanged claim pinned by test ✓ T4; services array on `/api/truecost` ✓ T4; disclosure UI showing service, dollars, share, memory ✓ T5; deploy ordering ✓ T6.
- **Deviation from spec, deliberate:** the spec left the API's exact per-service field set open; this plan settles on `service`, `service_id`, `total_usd`, `memory_usd`, `share_of_app` — dropping the per-resource cpu/egress/volume/backup breakdown from the response. The spec said those "stay available in the API response for anyone who wants them", but nothing consumes them, they are stored and queryable, and shipping unused fields is the kind of speculative surface this repo has been trimming. Add them when something needs them.
- **Deviation, deliberate:** `daily_infra_services` takes a `field` parameter so the endpoint can derive total and memory from the same function rather than duplicating the loop six times. The spec did not specify how multiple dollar dimensions would be windowed.
- **Type consistency:** service row keys are identical across T1 producing, T2 validating, T3 storing, T4 reading. `(app, service_id)` is the composite key in `daily_infra_services`, `window_sums` output, `services_by_app` input, and `svc_names` — one shape throughout.
- **Placeholder scan:** none. Task 5's test needs the file's existing mocking pattern read first, which is an instruction, not a TBD.
- **Known risk for the executor:** Task 2 changes `SCHEMA_VERSION`, which mechanically breaks any pre-existing test asserting the old value. One is known (`test_snapshot_v3.py::test_v4_rejected`); the step says to fix others the same way and call each out. Do not weaken a test to make it pass — if one cannot be fixed mechanically, stop and report.
