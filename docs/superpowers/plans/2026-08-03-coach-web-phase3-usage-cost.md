# Coach Web Phase 3: Sessions + Cost Lanes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fill the dashboard's two dimmed tiles and the Cost page: the local sweep gains a usage lane (sessions/prompts/tokens from `~/.claude` transcripts), snapshots go to schema v2 carrying daily session counts and cost rollups, and the server stores and serves them.

**Architecture:** A new `src/usage.py` lane parses transcript JSONL incrementally (per-file mtime/size cursors; changed files re-parsed whole, per-file day-aggregates persisted locally, daily rollups summed from those). `shared/snapshot.py` bumps to `SCHEMA_VERSION = 2` **while still accepting v1** (the server deploys from the same merge before the next local sweep runs, and the outbox may hold v1 payloads — v1 must never 400). The server adds a `cost_daily` table + `/api/cost`, fills `activity_daily.sessions/prompts`, and the SPA's Cost page and dimmed tiles come alive. Read `docs/HANDOFF.md` first — especially the deploy-ordering warning.

**Tech Stack:** Existing stack (Python 3.11, FastAPI, SQLAlchemy/Alembic, React+TS). No new dependencies.

## Global Constraints

- **Privacy is absolute:** the usage lane retains counts, token numbers, timestamps, model ids, session ids, and repo names ONLY. Never prompt text, message content, file paths from inside transcripts, or tool outputs. Test fixtures must use synthetic content.
- **v1 payloads stay valid.** `validate_payload` accepts schema_version 1 (Phase 1/2 shape, no cost_daily) and 2. Ingest handles both. A v1 payload must never be rejected — rejection quarantines it permanently (`.rejected`).
- Cost figures are **API-equivalent estimates** (Tom is on a subscription): UI copy says "est. API value". Pricing lives in one table (`src/usage.py::PRICES`), editable, with an explicit fallback.
- Sessions/prompts count **main-chain user prompts** (`type=="user"`, has `promptId`, not `isSidechain`, no `toolUseResult` key); token usage counts **all** assistant rows including sidechains (subagents cost money too).
- The sweep must always exit 0; usage-lane failures log and degrade (ship nulls), never crash the sweep.
- Deploy order within the execution session: merge → `railway up` → verify health → THEN run the first local sweep (which ships v2). Never ship v2 at a v1-only server.
- Worktree venv is built with `python3.11 -m venv` explicitly (see HANDOFF gotchas). Frontend commands via `npm --prefix apps/coach_web/frontend`.

## Transcript format (verified 2026-08-03 by structure-only recon — do not re-guess)

- Layout: `~/.claude/projects/<sanitized-cwd-dirname>/<session-uuid>.jsonl`, one file per session.
- Rows are JSON objects with `type`. Relevant types: `user` (prompt rows carry `promptId`; tool-result rows carry `toolUseResult` instead), `assistant` (carries `message.usage` + `message.model` + `requestId`).
- Common fields: `timestamp` (ISO8601 with `Z`), `sessionId` (uuid), `cwd` (absolute project path, present on user/assistant rows), `isSidechain` (bool).
- `message.usage` keys: `input_tokens`, `output_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens` (others exist; ignore them).
- Malformed lines occur; skip them silently (same stance as `classifier.read_jsonl`).

## File Structure

```
src/usage.py                       (new) transcript parsing, cursors, PRICES, daily rollups
src/sweep.py                       (modify) run usage lane before shipper
src/shipper.py                     (modify) merge usage into activity_daily; add cost_daily
shared/snapshot.py                 (modify) SCHEMA_VERSION 2, versioned validation
apps/coach_web/models.py           (modify) CostDaily model
apps/coach_web/alembic/versions/   (new revision) cost_daily table
apps/coach_web/ingest.py           (modify) v2 ingest: cost upsert, sessions/prompts update
apps/coach_web/api.py              (modify) /api/cost; overview + activity fill-in
apps/coach_web/frontend/src/pages/Cost.tsx      (replace empty state)
apps/coach_web/frontend/src/pages/Overview.tsx  (modify tiles)
apps/coach_web/frontend/src/pages/Activity.tsx  (modify sessions tile)
tests/test_usage.py                (new)
tests/web/test_snapshot_v2.py      (new)
tests/web/test_ingest_v2.py        (new)
tests/web/test_api_cost.py         (new)
```

Existing interfaces this plan builds on (do not change their signatures):
`classifier.read_jsonl(path)`; `shipper.build_snapshot(data_dir, adoption_rows, sweep_stats, captured_at)`; `shipper.ship_all(...)`; `aggregate.week_start/weekly_rollup/streak`; models `ActivityDaily(date PK, commits, by_repo, sessions?, prompts?)`; ingest `apply_snapshot(db, payload)`; sweep summary print contract.

---

### Task 1: Usage lane — transcript parsing and cursors

**Files:**
- Create: `src/usage.py`
- Test: `tests/test_usage.py`

**Interfaces:**
- Produces: `parse_transcript(path: Path) -> dict` returning `{"session_id": str|None, "repo": str|None, "days": {date_iso: {"prompts": int, "tokens": {model: {"in": int, "out": int, "cache_read": int, "cache_create": int}}}}}` (repo = `Path(cwd).name` from the first row carrying `cwd`; `None` if absent). `scan_projects(claude_home: Path, data_dir: Path) -> list[dict]` — walks `claude_home/"projects"/*/*.jsonl`, skips files whose `(mtime, size)` matches `data_dir/"usage_cursors.json"`, re-parses changed/new files whole, persists per-file aggregates to `data_dir/"usage_by_file.jsonl"` (one row per file: `{"file": str, "session_id", "repo", "days": {...}}`, rewritten atomically each call, stale entries for deleted files dropped), updates cursors, and returns ALL rows (cached + fresh). Task 2 consumes the returned rows.

- [ ] **Step 1: Write the failing tests**

`tests/test_usage.py`:

```python
import json
from pathlib import Path

from src import usage


def w(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def transcript_rows():
    return [
        {"type": "user", "promptId": "p1", "sessionId": "s-1",
         "cwd": "/Users/x/Code Apps/alpha", "isSidechain": False,
         "timestamp": "2026-08-01T14:00:00.000Z"},
        {"type": "assistant", "sessionId": "s-1", "isSidechain": False,
         "timestamp": "2026-08-01T14:00:05.000Z",
         "message": {"model": "claude-sonnet-5",
                     "usage": {"input_tokens": 100, "output_tokens": 50,
                               "cache_read_input_tokens": 1000,
                               "cache_creation_input_tokens": 200}}},
        # tool-result user row: NOT a prompt
        {"type": "user", "toolUseResult": {"x": 1}, "sessionId": "s-1",
         "timestamp": "2026-08-01T14:00:10.000Z"},
        # sidechain prompt: NOT a prompt, but its assistant usage counts
        {"type": "user", "promptId": "p2", "sessionId": "s-1",
         "isSidechain": True, "timestamp": "2026-08-01T14:01:00.000Z"},
        {"type": "assistant", "sessionId": "s-1", "isSidechain": True,
         "timestamp": "2026-08-02T01:00:00.000Z",
         "message": {"model": "claude-haiku-4-5",
                     "usage": {"input_tokens": 10, "output_tokens": 5,
                               "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0}}},
        "garbage-not-json",
        {"type": "ai-title"},  # no timestamp/usage: ignored
    ]


def test_parse_transcript(tmp_path):
    f = tmp_path / "abc.jsonl"
    f.write_text("".join(
        (json.dumps(r) if isinstance(r, dict) else r) + "\n"
        for r in transcript_rows()))
    out = usage.parse_transcript(f)
    assert out["session_id"] == "s-1"
    assert out["repo"] == "alpha"
    d1 = out["days"]["2026-08-01"]
    assert d1["prompts"] == 1  # main-chain prompt only
    assert d1["tokens"]["claude-sonnet-5"] == {
        "in": 100, "out": 50, "cache_read": 1000, "cache_create": 200}
    # sidechain assistant row lands on its own (UTC) date
    assert out["days"]["2026-08-02"]["tokens"]["claude-haiku-4-5"]["in"] == 10
    assert out["days"]["2026-08-02"]["prompts"] == 0


def make_home(tmp_path, name="proj-a", fname="s1.jsonl"):
    d = tmp_path / "home" / "projects" / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / fname
    f.write_text("".join(
        (json.dumps(r) if isinstance(r, dict) else r) + "\n"
        for r in transcript_rows()))
    return tmp_path / "home", f


def test_scan_projects_cursors_skip_unchanged(tmp_path):
    home, f = make_home(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    rows1 = usage.scan_projects(home, data)
    assert len(rows1) == 1 and rows1[0]["repo"] == "alpha"
    # corrupt the file WITHOUT changing mtime/size -> cached row reused
    stat = f.stat()
    f.write_text("x" * stat.st_size)
    import os
    os.utime(f, (stat.st_atime, stat.st_mtime))
    rows2 = usage.scan_projects(home, data)
    assert rows2 == rows1  # cursor hit, no re-parse


def test_scan_projects_reparses_changed_and_drops_deleted(tmp_path):
    home, f = make_home(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    usage.scan_projects(home, data)
    # append another prompt -> size changes -> re-parse
    with f.open("a") as fh:
        fh.write(json.dumps({"type": "user", "promptId": "p9",
                             "sessionId": "s-1", "isSidechain": False,
                             "timestamp": "2026-08-01T15:00:00.000Z"}) + "\n")
    rows = usage.scan_projects(home, data)
    assert rows[0]["days"]["2026-08-01"]["prompts"] == 2
    f.unlink()
    assert usage.scan_projects(home, data) == []


def test_scan_projects_missing_home(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    assert usage.scan_projects(tmp_path / "nope", data) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_usage.py -v`
Expected: FAIL (no module src.usage)

- [ ] **Step 3: Implement**

`src/usage.py`:

```python
"""Claude Code usage lane: sessions, prompts, tokens from ~/.claude transcripts.
Retains counts/numbers/ids ONLY — never prompt or tool content."""
import json
import logging
from pathlib import Path

log = logging.getLogger("usage")

# $ per MTok: (input, output, cache_read, cache_write). Estimates for the
# dashboard ("est. API value") — edit freely when pricing changes.
PRICES = {
    "claude-haiku-4-5": (1.00, 5.00, 0.10, 1.25),
    "claude-sonnet": (3.00, 15.00, 0.30, 3.75),
    "claude-opus": (15.00, 75.00, 1.50, 18.75),
    "claude-fable": (15.00, 75.00, 1.50, 18.75),
}
FALLBACK_PRICE = (15.00, 75.00, 1.50, 18.75)


def price_for(model: str) -> tuple:
    for prefix, p in PRICES.items():
        if model.startswith(prefix):
            return p
    return FALLBACK_PRICE


def _is_prompt(row: dict) -> bool:
    return (row.get("type") == "user" and "promptId" in row
            and not row.get("isSidechain") and "toolUseResult" not in row)


def parse_transcript(path: Path) -> dict:
    session_id = repo = None
    days: dict = {}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {"session_id": None, "repo": None, "days": {}}
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        session_id = session_id or row.get("sessionId")
        if repo is None and row.get("cwd"):
            repo = Path(row["cwd"]).name
        ts = row.get("timestamp")
        if not isinstance(ts, str) or len(ts) < 10:
            continue
        day = days.setdefault(ts[:10], {"prompts": 0, "tokens": {}})
        if _is_prompt(row):
            day["prompts"] += 1
        msg = row.get("message")
        if (row.get("type") == "assistant" and isinstance(msg, dict)
                and isinstance(msg.get("usage"), dict)):
            u = msg["usage"]
            model = str(msg.get("model") or "unknown")
            t = day["tokens"].setdefault(
                model, {"in": 0, "out": 0, "cache_read": 0, "cache_create": 0})
            t["in"] += int(u.get("input_tokens") or 0)
            t["out"] += int(u.get("output_tokens") or 0)
            t["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
            t["cache_create"] += int(u.get("cache_creation_input_tokens") or 0)
    return {"session_id": session_id, "repo": repo, "days": days}


def scan_projects(claude_home: Path, data_dir: Path) -> list[dict]:
    projects = claude_home / "projects"
    cursor_path = data_dir / "usage_cursors.json"
    store_path = data_dir / "usage_by_file.jsonl"
    try:
        cursors = json.loads(cursor_path.read_text())
    except (OSError, json.JSONDecodeError):
        cursors = {}
    cached: dict[str, dict] = {}
    if store_path.exists():
        for line in store_path.read_text().splitlines():
            try:
                row = json.loads(line)
                cached[row["file"]] = row
            except (json.JSONDecodeError, KeyError):
                continue
    out: list[dict] = []
    new_cursors: dict[str, dict] = {}
    if projects.is_dir():
        for f in sorted(projects.glob("*/*.jsonl")):
            key = str(f)
            try:
                stat = f.stat()
            except OSError:
                continue
            sig = {"mtime": stat.st_mtime, "size": stat.st_size}
            new_cursors[key] = sig
            if cursors.get(key) == sig and key in cached:
                out.append(cached[key])
                continue
            parsed = parse_transcript(f)
            out.append({"file": key, **parsed})
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = store_path.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in out))
    tmp.replace(store_path)
    cursor_path.write_text(json.dumps(new_cursors))
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_usage.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Sanity-check against the REAL transcript store (structure only)**

Run a scratchpad script that calls `usage.scan_projects(Path.home()/".claude", <scratchpad tmp dir>)` and prints ONLY: file count, total sessions, total prompts, total cost via `price_for`. It must not print content. Confirm plausible non-zero numbers and no exceptions. Delete the scratchpad output dir after.

- [ ] **Step 6: Run full suite, commit**

```bash
git add src/usage.py tests/test_usage.py
git commit -m "feat(usage): transcript parsing lane with per-file cursors"
```

---

### Task 2: Usage rollups + sweep wiring

**Files:**
- Modify: `src/usage.py`, `src/sweep.py`
- Test: `tests/test_usage.py` (extend)

**Interfaces:**
- Produces: `usage.daily_rollups(rows: list[dict]) -> dict` returning `{"activity": {date: {"sessions": int, "prompts": int}}, "cost": [{"date", "input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost_usd", "by_model": {model: cost_usd}} ...sorted by date]}`. Sessions per day = count of distinct session_ids with any activity that day. `cost_usd` rounded to 4 places; `by_model` values likewise. `sweep.main` calls `usage.scan_projects(Path.home()/".claude", data_dir)` + `daily_rollups` inside its try block (wrapped in its own try/except → on failure log + use `{"activity": {}, "cost": []}`), and passes the result into `shipper.build_snapshot` (Task 4 changes that signature — in THIS task, just compute `usage_data` and pass nothing yet; add `usage=` wiring in Task 4).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_usage.py`:

```python
def test_daily_rollups():
    rows = [
        {"file": "a", "session_id": "s-1", "repo": "alpha",
         "days": {"2026-08-01": {"prompts": 3, "tokens": {
             "claude-haiku-4-5": {"in": 1_000_000, "out": 0,
                                  "cache_read": 0, "cache_create": 0}}}}},
        {"file": "b", "session_id": "s-2", "repo": "beta",
         "days": {"2026-08-01": {"prompts": 2, "tokens": {}},
                  "2026-08-02": {"prompts": 1, "tokens": {
                      "claude-sonnet-5": {"in": 0, "out": 1_000_000,
                                          "cache_read": 0, "cache_create": 0}}}}},
    ]
    out = usage.daily_rollups(rows)
    assert out["activity"]["2026-08-01"] == {"sessions": 2, "prompts": 5}
    assert out["activity"]["2026-08-02"] == {"sessions": 1, "prompts": 1}
    days = {c["date"]: c for c in out["cost"]}
    assert days["2026-08-01"]["input_tokens"] == 1_000_000
    assert days["2026-08-01"]["cost_usd"] == 1.0          # 1 MTok haiku input
    assert days["2026-08-01"]["by_model"] == {"claude-haiku-4-5": 1.0}
    assert days["2026-08-02"]["cost_usd"] == 15.0         # 1 MTok sonnet output
    assert [c["date"] for c in out["cost"]] == ["2026-08-01", "2026-08-02"]


def test_daily_rollups_empty():
    assert usage.daily_rollups([]) == {"activity": {}, "cost": []}
```

- [ ] **Step 2: Run to verify FAIL, then implement**

Append to `src/usage.py`:

```python
def daily_rollups(rows: list[dict]) -> dict:
    activity: dict[str, dict] = {}
    cost: dict[str, dict] = {}
    sessions: dict[str, set] = {}
    for row in rows:
        sid = row.get("session_id")
        for date, day in (row.get("days") or {}).items():
            a = activity.setdefault(date, {"sessions": 0, "prompts": 0})
            a["prompts"] += day.get("prompts", 0)
            if sid:
                sessions.setdefault(date, set()).add(sid)
            for model, t in (day.get("tokens") or {}).items():
                c = cost.setdefault(date, {
                    "date": date, "input_tokens": 0, "output_tokens": 0,
                    "cache_read_tokens": 0, "cache_creation_tokens": 0,
                    "cost_usd": 0.0, "by_model": {}})
                c["input_tokens"] += t["in"]
                c["output_tokens"] += t["out"]
                c["cache_read_tokens"] += t["cache_read"]
                c["cache_creation_tokens"] += t["cache_create"]
                pin, pout, pread, pwrite = price_for(model)
                usd = (t["in"] * pin + t["out"] * pout
                       + t["cache_read"] * pread
                       + t["cache_create"] * pwrite) / 1_000_000
                c["by_model"][model] = round(c["by_model"].get(model, 0.0) + usd, 4)
    for date, a in activity.items():
        a["sessions"] = len(sessions.get(date, set()))
    for c in cost.values():
        c["cost_usd"] = round(sum(c["by_model"].values()), 4)
    return {"activity": activity, "cost": sorted(cost.values(), key=lambda c: c["date"])}
```

Run: `.venv/bin/python -m pytest tests/test_usage.py -v` — PASS.

- [ ] **Step 3: Wire into sweep (compute only; shipping wired in Task 4)**

In `src/sweep.py`, add `from . import usage` to the relative import and, inside `main()`'s try block right before the `ingest_url` block:

```python
        try:
            usage_data = usage.daily_rollups(
                usage.scan_projects(Path.home() / ".claude", data_dir))
        except Exception:
            log.exception("usage lane failed; shipping without usage data")
            usage_data = {"activity": {}, "cost": []}
```

Existing sweep tests must stay green (`tests/test_sweep_main.py` uses tmp dirs; `scan_projects` on a real home is read-only and safe, but to keep tests hermetic monkeypatch is NOT needed — it writes `usage_cursors.json`/`usage_by_file.jsonl` into the test's `data_dir` only). Run the full suite to confirm.

- [ ] **Step 4: Commit**

```bash
git add src/usage.py src/sweep.py tests/test_usage.py
git commit -m "feat(usage): daily session/prompt/cost rollups wired into sweep"
```

---

### Task 3: Snapshot schema v2 (accepting v1)

**Files:**
- Modify: `shared/snapshot.py`
- Test: `tests/web/test_snapshot_v2.py` (new; leave existing test_snapshot.py untouched — it must keep passing to prove v1 stays valid)

**Interfaces:**
- Produces: `SCHEMA_VERSION = 2`. v2 payload = v1 keys + required top-level `cost_daily` list. `activity_daily` rows may carry `sessions`/`prompts` ints (already allowed in v1's optional set). `cost_daily` item schema: required `{date, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cost_usd, by_model}`, no extras; types: token fields int (bool rejected), `cost_usd` int-or-float, `by_model` dict. `validate_payload` dispatches on `payload["schema_version"]`: 1 → exactly the old rules (cost_daily must NOT be present), 2 → new rules; anything else → ValueError naming schema_version.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_snapshot_v2.py`:

```python
import pytest

from shared import snapshot

V2_BODY = {
    "schema_version": 2,
    "sweep": {"repos": 1},
    "feature_units": [],
    "activity_daily": [{"date": "2026-08-01", "commits": 2,
                        "by_repo": {"a": 2}, "sessions": 3, "prompts": 40}],
    "adoption": [],
    "cost_daily": [{"date": "2026-08-01", "input_tokens": 5, "output_tokens": 2,
                    "cache_read_tokens": 100, "cache_creation_tokens": 10,
                    "cost_usd": 0.12, "by_model": {"claude-sonnet-5": 0.12}}],
}


def fin(body):
    return snapshot.finalize_payload(dict(body), "2026-08-03T07:30:00+00:00")


def test_schema_version_is_2():
    assert snapshot.SCHEMA_VERSION == 2


def test_v2_valid():
    snapshot.validate_payload(fin(V2_BODY))


def test_v1_still_valid_without_cost():
    v1 = {k: v for k, v in V2_BODY.items() if k != "cost_daily"}
    v1["schema_version"] = 1
    v1["activity_daily"] = [{"date": "2026-08-01", "commits": 2, "by_repo": {"a": 2}}]
    snapshot.validate_payload(fin(v1))


def test_v1_with_cost_daily_rejected():
    v1 = dict(V2_BODY)
    v1["schema_version"] = 1
    with pytest.raises(ValueError, match="cost_daily"):
        snapshot.validate_payload(fin(v1))


def test_v2_missing_cost_daily_rejected():
    v2 = {k: v for k, v in V2_BODY.items() if k != "cost_daily"}
    with pytest.raises(ValueError, match="cost_daily"):
        snapshot.validate_payload(fin(v2))


def test_v2_bad_cost_row():
    v2 = dict(V2_BODY)
    v2["cost_daily"] = [{"date": "2026-08-01"}]
    with pytest.raises(ValueError, match="input_tokens"):
        snapshot.validate_payload(fin(v2))


def test_v3_rejected():
    v3 = dict(V2_BODY)
    v3["schema_version"] = 3
    with pytest.raises(ValueError, match="schema_version"):
        snapshot.validate_payload(fin(v3))
```

- [ ] **Step 2: Run to verify FAIL, then implement**

Refactor `shared/snapshot.py` minimally: `SCHEMA_VERSION = 2`; `SUPPORTED_VERSIONS = (1, 2)`; add `"cost_daily"` to a v2 required-keys tuple; add to `ITEM_SCHEMAS` a `cost_daily` entry `(required and allowed) = {date, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, cost_usd, by_model}` with `FIELD_TYPES` additions (`input_tokens/output_tokens/cache_read_tokens/cache_creation_tokens` int-not-bool, `by_model` dict; `cost_usd` int-or-float-not-bool). In `validate_payload`: version not in SUPPORTED → ValueError naming schema_version; v1 → old required keys AND explicit ValueError mentioning cost_daily if the key is present; v2 → required keys + cost_daily items validated. Hash check unchanged (covers whatever keys the body has). Keep every existing v1 test in `tests/web/test_snapshot.py` passing unmodified EXCEPT: that file's `BODY` uses `schema_version: 1`, which remains valid — but `test_validate_rejects_wrong_version` asserts version 2 is rejected; update ONLY that test to use version 3. This is the single permitted edit to the old test file and must be called out in the commit message.

- [ ] **Step 3: Run full suite, commit**

```bash
git add shared/snapshot.py tests/web/test_snapshot_v2.py tests/web/test_snapshot.py
git commit -m "feat(shared): schema v2 with cost_daily, v1 still accepted (old wrong-version test now uses v3)"
```

---

### Task 4: Shipper builds v2 snapshots

**Files:**
- Modify: `src/shipper.py`, `src/sweep.py`
- Test: `tests/web/test_shipper.py` (extend)

**Interfaces:**
- Produces: `build_snapshot(data_dir, adoption_rows, sweep_stats, captured_at, usage=None)` — `usage` is Task 2's rollup dict or None. When provided: each activity_daily row gains `sessions`/`prompts` from `usage["activity"]` (days present in usage but with no commits still get a row with `commits: 0, by_repo: {}`), and top-level `cost_daily = usage["cost"]`. When None: `cost_daily: []` and no sessions/prompts keys (v2 allows their absence). `sweep.main` passes `usage=usage_data`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_shipper.py`:

```python
def test_build_snapshot_v2_merges_usage(tmp_path):
    write_jsonl(tmp_path / "ledger.jsonl", LEDGER)
    write_jsonl(tmp_path / "classifications.jsonl", CLS)
    usage = {"activity": {"2026-08-01": {"sessions": 2, "prompts": 30},
                          "2026-08-05": {"sessions": 1, "prompts": 4}},
             "cost": [{"date": "2026-08-01", "input_tokens": 1, "output_tokens": 2,
                       "cache_read_tokens": 3, "cache_creation_tokens": 4,
                       "cost_usd": 0.5, "by_model": {"m": 0.5}}]}
    p = shipper.build_snapshot(tmp_path, ADOPTION, {"repos": 2},
                               captured_at="2026-08-06T11:30:00+00:00",
                               usage=usage)
    snapshot.validate_payload(p)
    assert p["schema_version"] == 2
    by_date = {a["date"]: a for a in p["activity_daily"]}
    assert by_date["2026-08-01"]["sessions"] == 2
    assert by_date["2026-08-01"]["prompts"] == 30
    assert by_date["2026-08-01"]["commits"] == 2          # from LEDGER
    assert by_date["2026-08-05"] == {"date": "2026-08-05", "commits": 0,
                                     "by_repo": {}, "sessions": 1, "prompts": 4}
    assert p["cost_daily"][0]["cost_usd"] == 0.5


def test_build_snapshot_no_usage_ships_empty_cost(tmp_path):
    p = shipper.build_snapshot(tmp_path, [], {}, captured_at="2026-08-06T11:30:00+00:00")
    snapshot.validate_payload(p)
    assert p["cost_daily"] == []
```

- [ ] **Step 2: Run to verify FAIL, then implement**

In `src/shipper.py::build_snapshot`, add the `usage=None` parameter; after the existing `daily` aggregation, merge:

```python
    act = {d: {"date": d, **v} for d, v in sorted(daily.items())}
    if usage:
        for d, u in usage["activity"].items():
            row = act.setdefault(d, {"date": d, "commits": 0, "by_repo": {}})
            row["sessions"] = u["sessions"]
            row["prompts"] = u["prompts"]
    body = {
        "schema_version": SCHEMA_VERSION,
        "sweep": sweep_stats,
        "feature_units": [...unchanged...],
        "activity_daily": [act[d] for d in sorted(act)],
        "adoption": adoption_rows,
        "cost_daily": (usage or {}).get("cost", []),
    }
```

(The `[...unchanged...]` marker means: keep the existing feature_units expression exactly as it is today.) In `src/sweep.py`, pass `usage=usage_data` in the `build_snapshot` call.

- [ ] **Step 3: Run FULL suite (existing shipper/integration tests now produce v2 payloads validated by the v2 rules — they should pass untouched since build_snapshot always includes cost_daily), commit**

```bash
git add src/shipper.py src/sweep.py tests/web/test_shipper.py
git commit -m "feat(shipper): v2 snapshots carrying sessions, prompts, and cost"
```

---

### Task 5: Server — CostDaily model, migration, v2 ingest

**Files:**
- Modify: `apps/coach_web/models.py`, `apps/coach_web/ingest.py`
- Create: alembic revision (autogenerate)
- Test: `tests/web/test_ingest_v2.py` (new)

**Interfaces:**
- Produces: `models.CostDaily(date: str PK, input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_creation_tokens: int, cost_usd: float, by_model: JSON)`. `apply_snapshot` upserts cost_daily rows by date and, for activity rows carrying sessions/prompts, sets those columns (leaving them untouched when absent). v1 payloads ingest exactly as before. Alembic revision adds ONLY the cost_daily table (verify by inspection); `upgrade head` + `downgrade -1` cycle tested against a scratch sqlite file then deleted.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_ingest_v2.py`:

```python
from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.coach_web import models
from shared import snapshot as snap_mod
from tests.web.test_ingest import AUTH


def v2_payload(cost_usd=0.5, sessions=3):
    body = {
        "schema_version": 2,
        "sweep": {"repos": 1},
        "feature_units": [],
        "activity_daily": [{"date": "2026-08-01", "commits": 2,
                            "by_repo": {"a": 2}, "sessions": sessions,
                            "prompts": 40}],
        "adoption": [],
        "cost_daily": [{"date": "2026-08-01", "input_tokens": 10,
                        "output_tokens": 5, "cache_read_tokens": 0,
                        "cache_creation_tokens": 0, "cost_usd": cost_usd,
                        "by_model": {"claude-sonnet-5": cost_usd}}],
    }
    return snap_mod.finalize_payload(body, f"2026-08-03T0{sessions}:00:00+00:00")


def test_v2_ingest_stores_cost_and_sessions(client):
    resp = client.post("/api/ingest", json=v2_payload(), headers=AUTH)
    assert resp.status_code == 200
    with Session(client.app.state.engine) as s:
        cost = s.get(models.CostDaily, "2026-08-01")
        assert cost.cost_usd == 0.5
        act = s.get(models.ActivityDaily, "2026-08-01")
        assert act.sessions == 3 and act.prompts == 40


def test_v2_ingest_upserts_cost(client):
    client.post("/api/ingest", json=v2_payload(cost_usd=0.5), headers=AUTH)
    client.post("/api/ingest", json=v2_payload(cost_usd=0.9, sessions=4), headers=AUTH)
    with Session(client.app.state.engine) as s:
        assert len(s.scalars(select(models.CostDaily)).all()) == 1
        assert s.get(models.CostDaily, "2026-08-01").cost_usd == 0.9
        assert s.get(models.ActivityDaily, "2026-08-01").sessions == 4


def test_v1_payload_still_ingests(client):
    from tests.web.test_ingest import make_payload
    resp = client.post("/api/ingest", json=make_payload(), headers=AUTH)
    assert resp.status_code == 200
    with Session(client.app.state.engine) as s:
        act = s.get(models.ActivityDaily, "2026-08-01")
        assert act.sessions is None  # untouched by v1
```

- [ ] **Step 2: Run to verify FAIL, then implement**

`models.py`: add

```python
class CostDaily(Base):
    __tablename__ = "cost_daily"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cache_creation_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    by_model: Mapped[dict] = mapped_column(JSON, default=dict)
```

(add `Float` to the sqlalchemy import). `ingest.py::apply_snapshot`: in the activity loop, when the payload row has sessions/prompts keys set them on the ORM row (both insert and update paths); after the adoption loop add:

```python
    for c in payload.get("cost_daily", []):
        row = db.get(models.CostDaily, c["date"])
        if row is None:
            db.add(models.CostDaily(**c))
        else:
            for field in ("input_tokens", "output_tokens", "cache_read_tokens",
                          "cache_creation_tokens", "cost_usd", "by_model"):
                setattr(row, field, c[field])
```

Note the activity insert path currently does `models.ActivityDaily(**a)` — that already carries sessions/prompts when present; the update path needs explicit `if "sessions" in a:` guards so v1 updates don't null out existing values.

- [ ] **Step 3: Alembic revision**

```bash
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini revision --autogenerate -m "cost_daily table"
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini upgrade head
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini downgrade -1
rm -f alembic-dev.db
```

Inspect the revision: creates cost_daily ONLY (the autogen diff runs against the empty scratch db after upgrading to the previous head — verify no spurious ops).

- [ ] **Step 4: Run full suite, commit**

```bash
git add apps/coach_web/models.py apps/coach_web/ingest.py apps/coach_web/alembic tests/web/test_ingest_v2.py
git commit -m "feat(web): cost_daily table and v2 ingest"
```

---

### Task 6: Server — /api/cost, live tiles, sessions_available

**Files:**
- Modify: `apps/coach_web/api.py`
- Test: `tests/web/test_api_cost.py` (new)

**Interfaces:**
- Produces: `GET /api/cost?weeks=12` (session auth) → `{"days": [{date, cost_usd, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens} in-window sorted], "weekly": [{"start", "cost_usd"} same weeks window as /api/activity], "total_usd_window": float, "by_model_window": {model: usd}, "available": bool}` (`available` = any CostDaily rows exist at all). `/api/overview` tiles: `sessions_this_week` = sum of ActivityDaily.sessions (non-null) since Monday, but `null` when NO row anywhere has sessions (Phase 3 not yet shipped data); same pattern for `cost_this_week` from CostDaily. `/api/activity`: `sessions_available` = any ActivityDaily.sessions non-null; when true, weeks rows include a `sessions` sum per week.
- Also (HANDOFF rider): add regression test `test_units_this_week_excludes_commit_clusters` to `tests/web/test_api_cost.py` — ingest a v2 payload containing a `kind:"commits"` unit dated the 1st of the current month and assert it does NOT count toward `units_this_week`... **correction**: the invariant is that such units are never *generated* for the current month; the server-side guard is a `kind == "spec"` OR date-resolution check. Implement the server-side guard: `units_this_week` counts only units where `kind != "commits"` OR the unit's date is >= monday AND not a first-of-month cluster date... Keep it simple and honest: count units with `date >= monday` AND `kind != "commits"` (commit clusters are month-resolution and should never count toward a week tile). Test asserts a commits-kind unit dated this week's Monday does not increment the tile while a spec-kind unit does.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_api_cost.py`:

```python
from datetime import date, timedelta

from shared import snapshot as snap_mod
from tests.web.test_ingest import AUTH


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def payload_with(units=(), activity=(), cost=(), captured="2026-08-03T07:00:00+00:00"):
    body = {"schema_version": 2, "sweep": {"repos": 1},
            "feature_units": list(units), "activity_daily": list(activity),
            "adoption": [], "cost_daily": list(cost)}
    return snap_mod.finalize_payload(body, captured)


def cost_row(d, usd):
    return {"date": d, "input_tokens": 1, "output_tokens": 1,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "cost_usd": usd, "by_model": {"m": usd}}


def test_cost_endpoint_empty(client):
    login(client)
    data = client.get("/api/cost").json()
    assert data["available"] is False
    assert data["days"] == [] and data["total_usd_window"] == 0


def test_cost_endpoint_windows_and_totals(client):
    today = date.today()
    old = (today - timedelta(days=120)).isoformat()
    recent = today.isoformat()
    client.post("/api/ingest", json=payload_with(
        cost=[cost_row(old, 9.0), cost_row(recent, 2.5)]), headers=AUTH)
    login(client)
    data = client.get("/api/cost?weeks=4").json()
    assert data["available"] is True
    assert [d["date"] for d in data["days"]] == [recent]
    assert data["total_usd_window"] == 2.5
    assert data["by_model_window"] == {"m": 2.5}
    assert sum(w["cost_usd"] for w in data["weekly"]) == 2.5
    assert len(data["weekly"]) == 4


def test_overview_tiles_fill_when_data_exists(client):
    today = date.today().isoformat()
    client.post("/api/ingest", json=payload_with(
        activity=[{"date": today, "commits": 1, "by_repo": {"a": 1},
                   "sessions": 2, "prompts": 10}],
        cost=[cost_row(today, 1.25)]), headers=AUTH)
    login(client)
    tiles = client.get("/api/overview").json()["tiles"]
    assert tiles["sessions_this_week"] == 2
    assert tiles["cost_this_week"] == 1.25


def test_overview_tiles_null_without_usage_data(client):
    login(client)
    tiles = client.get("/api/overview").json()["tiles"]
    assert tiles["sessions_this_week"] is None
    assert tiles["cost_this_week"] is None


def test_units_this_week_excludes_commit_clusters(client):
    today = date.today()
    monday = (today - timedelta(days=today.weekday())).isoformat()
    units = [
        {"key": "c1:m", "kind": "commits", "repo": "a", "date": monday,
         "title": "a cluster", "tags": ["auth"], "complexity": 2,
         "summary": "s", "model": "m"},
        {"key": "s1:m", "kind": "spec", "repo": "a", "date": monday,
         "title": "real", "tags": ["auth"], "complexity": 3,
         "summary": "s", "model": "m"},
    ]
    client.post("/api/ingest", json=payload_with(units=units), headers=AUTH)
    login(client)
    assert client.get("/api/overview").json()["tiles"]["units_this_week"] == 1
```

- [ ] **Step 2: Run to verify FAIL, then implement in `apps/coach_web/api.py`**

- `units_this_week` query gains `.where(models.FeatureUnit.kind != "commits")`.
- Overview tiles: `sessions_this_week = None if db.scalar(select(func.count()).select_from(models.ActivityDaily).where(models.ActivityDaily.sessions.is_not(None))) == 0 else <sum of sessions where date >= monday>`; `cost_this_week` analogous over CostDaily (`round(sum, 2)`).
- `/api/activity`: `sessions_available` from the same non-null check; when true add `"sessions": <sum>` into each weekly bucket (extend the loop that builds `weeks` — sum ActivityDaily.sessions per window week, treating null as 0).
- New endpoint:

```python
@router.get("/api/cost")
def cost(weeks: int = 12, db: Session = Depends(get_db)):
    weeks = max(1, min(weeks, 52))
    today = date.today()
    oldest = (aggregate.week_start(today) - timedelta(weeks=weeks - 1)).isoformat()
    all_rows = db.scalars(select(models.CostDaily)).all()
    window = sorted((r for r in all_rows if r.date >= oldest), key=lambda r: r.date)
    weekly_map: dict[str, float] = {}
    by_model: dict[str, float] = {}
    for r in window:
        wk = aggregate.week_start(date.fromisoformat(r.date)).isoformat()
        weekly_map[wk] = round(weekly_map.get(wk, 0.0) + r.cost_usd, 4)
        for m, usd in (r.by_model or {}).items():
            by_model[m] = round(by_model.get(m, 0.0) + usd, 4)
    starts = [(aggregate.week_start(today) - timedelta(weeks=i)).isoformat()
              for i in range(weeks - 1, -1, -1)]
    return {
        "available": len(all_rows) > 0,
        "days": [{"date": r.date, "cost_usd": r.cost_usd,
                  "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
                  "cache_read_tokens": r.cache_read_tokens,
                  "cache_creation_tokens": r.cache_creation_tokens}
                 for r in window],
        "weekly": [{"start": s, "cost_usd": weekly_map.get(s, 0.0)} for s in starts],
        "total_usd_window": round(sum(r.cost_usd for r in window), 2),
        "by_model_window": by_model,
    }
```

- [ ] **Step 3: Run full suite (existing test_api_phase2 overview tests still pass — no usage data → nulls preserved), commit**

```bash
git add apps/coach_web/api.py tests/web/test_api_cost.py
git commit -m "feat(web): cost endpoint, live usage tiles, commit-cluster tile guard"
```

---

### Task 7: Frontend — Cost page + live tiles

**Files:**
- Modify: `apps/coach_web/frontend/src/pages/Cost.tsx`, `Overview.tsx`, `Activity.tsx`

**Interfaces:**
- Consumes: `GET /api/cost?weeks=12` (Task 6 shape), existing `WeeklyBars` (works for any `{start, <numeric>}` after passing a mapped array), `StatTile`, `Empty`, `fmtWeek`, tokens helpers.

- [ ] **Step 1: Implement `Cost.tsx`**

```tsx
import { useEffect, useState } from "react";
import { get } from "../api";
import Empty from "../components/Empty";
import StatTile from "../components/StatTile";
import WeeklyBars from "../components/WeeklyBars";

type CostData = {
  available: boolean;
  days: { date: string; cost_usd: number; input_tokens: number;
    output_tokens: number; cache_read_tokens: number;
    cache_creation_tokens: number }[];
  weekly: { start: string; cost_usd: number }[];
  total_usd_window: number;
  by_model_window: Record<string, number>;
};

export default function Cost() {
  const [data, setData] = useState<CostData | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { get("/api/cost?weeks=12").then(setData).catch((e) => setErr(String(e))); }, []);
  if (err) return <p className="muted">Failed to load: {err}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  if (!data.available) {
    return (<><h1>Cost</h1>
      <Empty title="No cost data yet"
        body="Run a sweep after Phase 3 ships locally — the usage lane fills this page." /></>);
  }
  const tokens = data.days.reduce((a, d) => a + d.input_tokens + d.output_tokens, 0);
  return (
    <>
      <h1>Cost <span className="muted" style={{ fontSize: 13 }}>(est. API value)</span></h1>
      <div className="tile-row">
        <StatTile label="12-week est. spend" value={`$${data.total_usd_window.toFixed(2)}`} />
        <StatTile label="In+out tokens (12w)" value={tokens.toLocaleString()} />
        <StatTile label="Models used" value={Object.keys(data.by_model_window).length} />
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 15 }}>Est. spend per week</h2>
        <WeeklyBars data={data.weekly.map((w) => ({ start: w.start, commits: w.cost_usd }))} />
        <p className="muted" style={{ fontSize: 12 }}>
          Y axis is USD (est.) — API-equivalent pricing applied to subscription usage.
        </p>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 15 }}>By model (12 weeks)</h2>
        <table style={{ width: "100%", fontSize: 13 }}>
          <tbody>
            {Object.entries(data.by_model_window).sort((a, b) => b[1] - a[1])
              .map(([m, usd]) => (
                <tr key={m}><td className="ink2">{m}</td>
                  <td className="num" style={{ textAlign: "right" }}>${usd.toFixed(2)}</td></tr>
              ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
```

(Reusing `WeeklyBars` with cost mapped onto its `commits` datakey is deliberate YAGNI — if the axis label bothers a later phase, generalize the prop name then. The tooltip will show the USD number.)

- [ ] **Step 2: Overview.tsx tiles**

Type change: `sessions_this_week: number | null; cost_this_week: number | null`. Render:

```tsx
<StatTile label="Sessions this week"
  value={data.tiles.sessions_this_week ?? "—"}
  sub={data.tiles.sessions_this_week === null ? "no data yet" : undefined}
  dim={data.tiles.sessions_this_week === null} />
<StatTile label="Spend this week (est.)"
  value={data.tiles.cost_this_week === null ? "—" : `$${data.tiles.cost_this_week.toFixed(2)}`}
  sub={data.tiles.cost_this_week === null ? "no data yet" : undefined}
  dim={data.tiles.cost_this_week === null} />
```

- [ ] **Step 3: Activity.tsx sessions tile**

Type gains `weeks: {..., sessions?: number}[]`. Replace the dim Sessions tile: when `data.sessions_available`, show `value={thisWeek?.sessions ?? 0}`; else keep the dim placeholder with sub "no data yet".

- [ ] **Step 4: Tests + build (strict mode will enforce the null handling), commit**

```bash
npm --prefix apps/coach_web/frontend run test
npm --prefix apps/coach_web/frontend run build
git add apps/coach_web/frontend/src/pages
git commit -m "feat(frontend): live cost page and usage tiles"
```

---

### Task 8: Deploy in the safe order + first v2 ship

**Files:** none new (uses `.claude/skills/deploy-coach-web/SKILL.md`)

- [ ] **Step 1:** Finish the branch (final whole-branch review → merge to main per the SDD process — this task assumes that's done and you are on merged main).
- [ ] **Step 2:** Deploy: `railway up --service coach-web --detach`; poll `railway deployment list --service coach-web --limit 1 --json` → SUCCESS. The Dockerfile CMD runs the new Alembic migration automatically.
- [ ] **Step 3:** Verify: `curl -s .../api/health`; login; `GET /api/cost` returns `{"available": false, ...}` (server upgraded, no data yet).
- [ ] **Step 4:** ONLY NOW run the local sweep: `make sweep`. Expect the summary line to end `shipped=1 queued=0` (first v2 payload). If anything was queued as v1 in `data/outbox/` it ships fine — v1 remains valid.
- [ ] **Step 5:** Verify live: `GET /api/cost` → `available: true` with real days; browser-walk Cost page, Overview tiles (sessions + est. spend filled), Activity sessions tile.
- [ ] **Step 6:** Update `docs/HANDOFF.md`: mark Phase 3 shipped, note anything deferred.

---

## Self-Review Notes

- Spec coverage (Phase 3 = "New collector lanes: sessions, cost" + spec's Sessions/Cost lane paragraphs + Cost page + tile fill): sessions lane ✓ T1-2, cost lane + pricing table ✓ T1-2, privacy stance ✓ constraint + fixture-only tests, snapshot schema ✓ T3-4, server storage/serving ✓ T5-6, Cost page + tiles ✓ T7, deploy-order safety ✓ T8 + v1-compat in T3/T5.
- Deviation from spec, deliberate: spec's "walk new transcript files since cursor" is implemented as whole-file re-parse on mtime/size change with per-file aggregate caching — appending to a transcript changes the file, and partial-offset resume risks torn JSON lines; per-file re-parse is idempotent and the cache keeps daily sweeps cheap. Spec's "pricing table checked into the repo" lives as `PRICES` in `src/usage.py` rather than a YAML — one fewer parse path, same editability; move to YAML if a non-code consumer ever needs it.
- Placeholder scan: the single `[...unchanged...]` marker in Task 4 explicitly instructs keeping existing code — not a TBD. All other code steps are complete.
- Type consistency: `usage` rollup dict keys match between T2 (produces), T4 (consumes); cost_daily row fields identical across T2/T3/T5/T6/T7; `sessions_available` name consistent T6/T7.
- Known risk for the executor: `tests/web/test_shipper.py` and `test_integration.py` build snapshots via `build_snapshot` and will emit v2 — T4 keeps them green because cost_daily is always present; if any hand-built v1 fixture in old tests trips the new validator, the fix is to leave its schema_version at 1 (still supported), never to force-upgrade fixtures.
