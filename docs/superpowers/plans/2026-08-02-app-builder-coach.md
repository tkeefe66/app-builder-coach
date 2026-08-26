# App Builder Coach Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A two-lane coaching system: a git-sweep ledger of the types of code Tom builds across `Code Apps`, plus an LLM-free tracker of which Claude Code features he uses — consumed by a global `/build-coach` skill that proposes one next challenge.

**Architecture:** Python package `src/` with four pure-ish modules (collector, adoption, classifier, profile) orchestrated by `src/sweep.py`. Data is append-only JSONL under `data/` (gitignored). The only network call is Haiku classification, cached forever by content hash. A launchd agent runs the sweep daily; a global skill reads `data/profile.md`.

**Tech Stack:** Python 3.11+ (stdlib + `pyyaml` + `anthropic`), pytest, git CLI, macOS launchd.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-02-app-builder-coach-design.md`. Read it before starting.
- Model for classification: `claude-haiku-4-5-20251001` (exact string).
- Never write into swept repos; collector is read-only on `Code Apps`.
- Skip directories whose name starts with `z` (archive convention) and non-git dirs.
- `~/.claude/history.jsonl` prompt content must never be sent to any API or copied into data files — only `/`-prefixed command names are extracted.
- All jobs exit 0 on partial failure; log and continue, never crash the sweep.
- `data/` is gitignored except `data/.gitkeep`.
- Cache-forever: a classification unit is classified at most once per content hash; a run with no new work makes zero API calls.
- TDD every task: failing test first, then minimal code.
- **Interpreter:** this machine's bare `python3` is 3.9. Always use the repo venv `.venv` (Python 3.11.15, created from `/opt/homebrew/bin/python3.11`, deps installed from requirements.txt). Run tests as `.venv/bin/python -m pytest tests/ -v`. The Makefile and launchd plist invoke `.venv/bin/python` (absolute path in the plist), never bare `python3`.

---

### Task 1: Scaffold + config

**Files:**
- Create: `src/__init__.py` (empty), `src/config.py`, `tests/__init__.py` (empty), `tests/test_config.py`, `.gitignore`, `requirements.txt`, `data/.gitkeep`

**Interfaces:**
- Produces: `config.REPO_ROOT: Path`, `config.DATA_DIR: Path`, `config.code_apps_root() -> Path` (env override `COACH_ROOT`), `config.ARCHIVE_PREFIX = "z"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path
from src import config


def test_data_dir_is_inside_repo():
    assert config.DATA_DIR == config.REPO_ROOT / "data"


def test_code_apps_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_ROOT", str(tmp_path))
    assert config.code_apps_root() == tmp_path


def test_code_apps_root_default(monkeypatch):
    monkeypatch.delenv("COACH_ROOT", raising=False)
    assert config.code_apps_root() == Path.home() / "Code Apps"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd "/Users/tomkeefe/Code Apps/app-builder-coach" && python3 -m pytest tests/test_config.py -v`
Expected: FAIL (`ModuleNotFoundError: src`)

- [ ] **Step 3: Write minimal implementation**

```python
# src/config.py
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
ARCHIVE_PREFIX = "z"
STALE_AFTER_HOURS = 24


def code_apps_root() -> Path:
    return Path(os.environ.get("COACH_ROOT", str(Path.home() / "Code Apps")))
```

```gitignore
# .gitignore
data/*
!data/.gitkeep
__pycache__/
*.pyc
.pytest_cache/
```

```text
# requirements.txt
pyyaml>=6
anthropic>=0.40
pytest>=8
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_config.py -v` — Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: scaffold package with config and data layout"
```

---

### Task 2: Language detection + git log parsing (pure functions)

**Files:**
- Create: `src/collector.py`, `tests/test_collector_parse.py`

**Interfaces:**
- Produces: `collector.detect_languages(files: list[str]) -> dict[str, int]`; `collector.parse_git_log(raw: str) -> list[dict]` where each dict has keys `sha, date, message, files, languages, insertions, deletions` (no `repo` key yet — added by `collect_repo` in Task 3).
- Git log record format consumed: `--pretty=format:%x1e%H%x1f%aI%x1f%s --numstat` (`\x1e` record sep, `\x1f` field sep).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_collector_parse.py
from src.collector import detect_languages, parse_git_log

RAW = (
    "\x1eaaa111\x1f2026-07-01T10:00:00-06:00\x1fAdd auth login\n"
    "10\t2\tapi/app/auth.py\n"
    "5\t0\tweb/login.tsx\n"
    "\x1ebbb222\x1f2026-07-02T11:00:00-06:00\x1fBinary asset\n"
    "-\t-\tlogo.png\n"
)


def test_detect_languages_counts_by_extension():
    assert detect_languages(["a.py", "b/c.py", "d.tsx", "Dockerfile", "x.png"]) == {
        "python": 2, "typescript": 1, "docker": 1,
    }


def test_parse_git_log_two_commits():
    rows = parse_git_log(RAW)
    assert [r["sha"] for r in rows] == ["aaa111", "bbb222"]
    first = rows[0]
    assert first["message"] == "Add auth login"
    assert first["files"] == ["api/app/auth.py", "web/login.tsx"]
    assert first["insertions"] == 15 and first["deletions"] == 2
    assert first["languages"] == {"python": 1, "typescript": 1}


def test_parse_git_log_binary_numstat_dashes():
    rows = parse_git_log(RAW)
    assert rows[1]["insertions"] == 0 and rows[1]["deletions"] == 0
    assert rows[1]["files"] == ["logo.png"]


def test_parse_git_log_empty_input():
    assert parse_git_log("") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_collector_parse.py -v` — Expected: FAIL (import error)

- [ ] **Step 3: Write minimal implementation**

```python
# src/collector.py
"""Read-only git sweep across Code Apps. Never writes into swept repos."""

EXT_LANG = {
    ".py": "python", ".ts": "typescript", ".tsx": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".sql": "sql", ".sh": "shell", ".css": "css", ".html": "html",
    ".md": "markdown", ".yml": "yaml", ".yaml": "yaml", ".json": "json",
    ".toml": "toml", ".swift": "swift", ".go": "go", ".rs": "rust",
}


def detect_languages(files: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in files:
        name = path.rsplit("/", 1)[-1]
        if name.lower() == "dockerfile":
            lang = "docker"
        else:
            dot = name.rfind(".")
            lang = EXT_LANG.get(name[dot:].lower()) if dot > 0 else None
        if lang:
            counts[lang] = counts.get(lang, 0) + 1
    return counts


def parse_git_log(raw: str) -> list[dict]:
    commits = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")
        if not record:
            continue
        lines = record.splitlines()
        sha, date, message = lines[0].split("\x1f", 2)
        files, ins, dels = [], 0, 0
        for line in lines[1:]:
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added, deleted, path = parts
            files.append(path)
            if added.isdigit():
                ins += int(added)
            if deleted.isdigit():
                dels += int(deleted)
        commits.append({
            "sha": sha, "date": date, "message": message, "files": files,
            "languages": detect_languages(files),
            "insertions": ins, "deletions": dels,
        })
    return commits
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_collector_parse.py -v` — Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: language detection and git-log parsing"
```

---

### Task 3: Repo collection with cursors + ledger append

**Files:**
- Modify: `src/collector.py` (append functions)
- Create: `tests/test_collector_repo.py`, `tests/conftest.py`

**Interfaces:**
- Consumes: `parse_git_log` (Task 2).
- Produces: `collector.collect_repo(repo_path: Path, since_sha: str | None) -> list[dict]` (adds `repo` key = dir name; raises `collector.CollectError` on git failure); `collector.read_cursors(path: Path) -> dict[str, str]`; `collector.write_cursors(path: Path, cursors: dict) -> None`; `collector.append_ledger(path: Path, rows: list[dict]) -> None` (JSONL append).
- Test fixture `make_repo(tmp_path)` in conftest builds a real git repo with 2 commits — later tasks reuse it.

- [ ] **Step 1: Write the fixture and failing test**

```python
# tests/conftest.py
import subprocess
from pathlib import Path
import pytest


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin", "HOME": str(repo)},
    )


@pytest.fixture
def make_repo(tmp_path):
    def _make(name: str = "demo-app", commits: int = 2) -> Path:
        repo = tmp_path / name
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        for i in range(commits):
            f = repo / f"file{i}.py"
            f.write_text(f"print({i})\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", f"commit {i}")
        return repo
    return _make
```

```python
# tests/test_collector_repo.py
import json
from src import collector


def test_collect_repo_full_history(make_repo):
    repo = make_repo()
    rows = collector.collect_repo(repo, since_sha=None)
    assert len(rows) == 2
    assert rows[0]["message"] == "commit 0"          # --reverse: oldest first
    assert all(r["repo"] == "demo-app" for r in rows)


def test_collect_repo_incremental(make_repo):
    repo = make_repo()
    first = collector.collect_repo(repo, since_sha=None)
    newer = collector.collect_repo(repo, since_sha=first[0]["sha"])
    assert [r["message"] for r in newer] == ["commit 1"]


def test_collect_repo_bad_path_raises(tmp_path):
    import pytest
    with pytest.raises(collector.CollectError):
        collector.collect_repo(tmp_path / "not-a-repo", since_sha=None)


def test_cursors_roundtrip_and_ledger_append(tmp_path):
    cpath = tmp_path / "cursors.json"
    assert collector.read_cursors(cpath) == {}
    collector.write_cursors(cpath, {"demo-app": "abc"})
    assert collector.read_cursors(cpath) == {"demo-app": "abc"}

    lpath = tmp_path / "ledger.jsonl"
    collector.append_ledger(lpath, [{"sha": "a"}, {"sha": "b"}])
    collector.append_ledger(lpath, [{"sha": "c"}])
    lines = [json.loads(x) for x in lpath.read_text().splitlines()]
    assert [r["sha"] for r in lines] == ["a", "b", "c"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_collector_repo.py -v` — Expected: FAIL (`AttributeError: collect_repo`)

- [ ] **Step 3: Write minimal implementation (append to `src/collector.py`)**

```python
import json
import subprocess
from pathlib import Path


class CollectError(Exception):
    pass


def collect_repo(repo_path: Path, since_sha: str | None) -> list[dict]:
    rng = f"{since_sha}..HEAD" if since_sha else "HEAD"
    proc = subprocess.run(
        ["git", "-C", str(repo_path), "log", "--reverse", "--no-merges",
         "--pretty=format:%x1e%H%x1f%aI%x1f%s", "--numstat", rng],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise CollectError(f"{repo_path.name}: {proc.stderr.strip()[:200]}")
    rows = parse_git_log(proc.stdout)
    for r in rows:
        r["repo"] = repo_path.name
    return rows


def read_cursors(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_cursors(path: Path, cursors: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cursors, indent=1, sort_keys=True))


def append_ledger(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_collector_repo.py -v` — Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: repo collection with cursors and ledger append"
```

---

### Task 4: Spec indexing + full sweep across the root

**Files:**
- Modify: `src/collector.py`
- Create: `tests/test_collector_sweep.py`

**Interfaces:**
- Consumes: `collect_repo`, cursors/ledger helpers (Task 3), `config.ARCHIVE_PREFIX`.
- Produces: `collector.index_specs(repo_path: Path) -> list[dict]` (keys `repo, spec_path, date, title`); `collector.discover_repos(root: Path) -> list[Path]`; `collector.sweep(root: Path, data_dir: Path) -> dict` returning `{"repos": int, "new_commits": int, "specs": int, "errors": [str]}`. Sweep writes `ledger.jsonl`, `specs.jsonl` (rewritten each run), `cursors.json` in `data_dir`.
- Sweep never raises: per-repo failures append to `errors` and continue (exit-0 rule).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_collector_sweep.py
import json
from src import collector


def test_index_specs_reads_date_and_title(tmp_path):
    repo = tmp_path / "demo-app"
    specs = repo / "docs" / "superpowers" / "specs"
    specs.mkdir(parents=True)
    (specs / "2026-07-01-cool-feature-design.md").write_text("# Cool Feature\n\nBody.")
    rows = collector.index_specs(repo)
    assert rows == [{
        "repo": "demo-app",
        "spec_path": "docs/superpowers/specs/2026-07-01-cool-feature-design.md",
        "date": "2026-07-01", "title": "Cool Feature",
    }]


def test_index_specs_no_dir(tmp_path):
    assert collector.index_specs(tmp_path / "bare") == []


def test_discover_repos_skips_archives_and_nonrepos(tmp_path, make_repo):
    make_repo("alpha")
    (tmp_path / "zOld").mkdir()          # archive prefix -> skipped
    (tmp_path / "notes").mkdir()         # no .git -> skipped
    names = [p.name for p in collector.discover_repos(tmp_path)]
    assert names == ["alpha"]


def test_sweep_is_incremental(tmp_path, make_repo):
    repo = make_repo("alpha")
    data = tmp_path / "coach-data"
    r1 = collector.sweep(tmp_path, data)
    assert r1["repos"] == 1 and r1["new_commits"] == 2 and r1["errors"] == []
    r2 = collector.sweep(tmp_path, data)
    assert r2["new_commits"] == 0
    ledger = [json.loads(x) for x in (data / "ledger.jsonl").read_text().splitlines()]
    assert len(ledger) == 2  # no duplicates
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_collector_sweep.py -v` — Expected: FAIL

- [ ] **Step 3: Write minimal implementation (append to `src/collector.py`)**

```python
import re
from .config import ARCHIVE_PREFIX

_HEADING = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SPEC_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")


def index_specs(repo_path: Path) -> list[dict]:
    specs_dir = repo_path / "docs" / "superpowers" / "specs"
    if not specs_dir.is_dir():
        return []
    rows = []
    for p in sorted(specs_dir.glob("*.md")):
        m_date = _SPEC_DATE.match(p.stem)
        m_head = _HEADING.search(p.read_text(errors="replace"))
        rows.append({
            "repo": repo_path.name,
            "spec_path": str(p.relative_to(repo_path)),
            "date": m_date.group(1) if m_date else None,
            "title": m_head.group(1).strip() if m_head else p.stem,
        })
    return rows


def discover_repos(root: Path) -> list[Path]:
    return [p for p in sorted(root.iterdir())
            if p.is_dir()
            and not p.name.startswith((ARCHIVE_PREFIX, "."))
            and (p / ".git").exists()]


def sweep(root: Path, data_dir: Path) -> dict:
    cursors = read_cursors(data_dir / "cursors.json")
    result = {"repos": 0, "new_commits": 0, "specs": 0, "errors": []}
    all_specs: list[dict] = []
    for repo in discover_repos(root):
        result["repos"] += 1
        try:
            rows = collect_repo(repo, cursors.get(repo.name))
        except CollectError as exc:
            result["errors"].append(str(exc))
            continue
        if rows:
            append_ledger(data_dir / "ledger.jsonl", rows)
            cursors[repo.name] = rows[-1]["sha"]
            result["new_commits"] += len(rows)
        all_specs.extend(index_specs(repo))
    (data_dir / "specs.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (data_dir / "specs.jsonl").write_text(
        "".join(json.dumps(s, sort_keys=True) + "\n" for s in all_specs))
    result["specs"] = len(all_specs)
    write_cursors(data_dir / "cursors.json", cursors)
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/ -v` — Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: spec indexing and incremental sweep"
```

---

### Task 5: Adoption lane — history parsing + config inventory

**Files:**
- Create: `src/adoption.py`, `tests/test_adoption.py`

**Interfaces:**
- Produces: `adoption.parse_history_commands(path: Path) -> dict[str, dict]` mapping lowercase `/command` → `{"count": int, "last": "YYYY-MM-DD" | None}`; `adoption.inventory_config(settings_path: Path, skills_dir: Path) -> dict` with keys `hooks: list[str]`, `skills: list[str]`.
- Privacy rule enforced here: only `display` strings **starting with `/`** are read, and only their first token is kept. Nothing else from history rows is retained.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_adoption.py
import json
from src import adoption


def _write_history(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_parse_history_extracts_only_slash_commands(tmp_path):
    h = tmp_path / "history.jsonl"
    _write_history(h, [
        {"display": "/ss 2 look at this", "timestamp": "2026-07-01T10:00:00Z"},
        {"display": "/ss", "timestamp": "2026-07-05T10:00:00Z"},
        {"display": "my private prompt about health", "timestamp": "2026-07-02T10:00:00Z"},
        {"display": "/code-review", "timestamp": 1751500800},          # epoch seconds
    ])
    out = adoption.parse_history_commands(h)
    assert set(out) == {"/ss", "/code-review"}          # prose never appears
    assert out["/ss"]["count"] == 2
    assert out["/ss"]["last"] == "2026-07-05"


def test_parse_history_tolerates_garbage(tmp_path):
    h = tmp_path / "history.jsonl"
    h.write_text('not json\n{"display": "/loop"}\n')
    out = adoption.parse_history_commands(h)
    assert out["/loop"]["count"] == 1 and out["/loop"]["last"] is None


def test_parse_history_missing_file(tmp_path):
    assert adoption.parse_history_commands(tmp_path / "nope.jsonl") == {}


def test_inventory_config(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "python3 x.py"}]}]}}))
    skills = tmp_path / "skills"
    (skills / "railway-cli").mkdir(parents=True)
    (skills / "railway-cli" / "SKILL.md").write_text("---\n---\n")
    (skills / "empty-dir").mkdir()
    inv = adoption.inventory_config(settings, skills)
    assert inv == {"hooks": ["python3 x.py"], "skills": ["railway-cli"]}


def test_inventory_config_missing_files(tmp_path):
    inv = adoption.inventory_config(tmp_path / "no.json", tmp_path / "no-skills")
    assert inv == {"hooks": [], "skills": []}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_adoption.py -v` — Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# src/adoption.py
"""Claude Code feature-adoption lane. LLM-free; prompt content never retained."""
import json
from datetime import datetime, timezone
from pathlib import Path


def _to_date(ts) -> str | None:
    if isinstance(ts, (int, float)):
        if ts > 1e12:          # epoch millis
            ts = ts / 1000
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    if isinstance(ts, str) and len(ts) >= 10:
        return ts[:10]
    return None


def parse_history_commands(path: Path) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        display = row.get("display", "")
        if not isinstance(display, str) or not display.startswith("/"):
            continue
        cmd = display.split()[0].lower()
        entry = out.setdefault(cmd, {"count": 0, "last": None})
        entry["count"] += 1
        date = _to_date(row.get("timestamp") or row.get("created_at") or row.get("ts"))
        if date and (entry["last"] is None or date > entry["last"]):
            entry["last"] = date
    return out


def inventory_config(settings_path: Path, skills_dir: Path) -> dict:
    hooks: list[str] = []
    try:
        cfg = json.loads(settings_path.read_text())
        for groups in (cfg.get("hooks") or {}).values():
            for group in groups:
                for hook in group.get("hooks", []):
                    if hook.get("command"):
                        hooks.append(hook["command"])
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    skills = []
    if skills_dir.is_dir():
        skills = sorted(d.name for d in skills_dir.iterdir()
                        if (d / "SKILL.md").exists())
    return {"hooks": hooks, "skills": skills}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_adoption.py -v` — Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: adoption lane history parsing and config inventory"
```

---

### Task 6: Feature checklist + evaluation

**Files:**
- Create: `feature-checklist.yaml`, `tests/test_checklist.py`
- Modify: `src/adoption.py`

**Interfaces:**
- Consumes: `parse_history_commands`, `inventory_config` output shapes (Task 5).
- Produces: `adoption.load_checklist(path: Path) -> list[dict]`; `adoption.evaluate_checklist(features: list[dict], usage: dict, inventory: dict) -> list[dict]` where each output row is `{"name", "lesson", "status", "last_used"}` and `status ∈ {"used", "configured-but-unused", "never-touched"}`.
- Checklist entry schema: `name` (str), `lesson` (str, claude-howto module dir), `detect` (dict with optional keys `commands: [str]`, `hook_substring: str`, `skill: str`).

- [ ] **Step 1: Write the checklist data file**

```yaml
# feature-checklist.yaml — Claude Code feature surface vs. actual usage.
# detect.commands: slash-commands whose use proves the feature is used.
# detect.hook_substring: settings hook command containing this -> configured.
# detect.skill: global skill dir whose presence -> configured.
- {name: custom slash commands / skills, lesson: 01-slash-commands, detect: {commands: ["/ss", "/loop", "/simplify"]}}
- {name: memory / CLAUDE.md editing, lesson: 02-memory, detect: {commands: ["/memory", "/init"]}}
- {name: auto-invoked skills, lesson: 03-skills, detect: {skill: skill-promotion-audit}}
- {name: writing your own skills, lesson: 03-skills, detect: {skill: build-coach}}
- {name: subagent delegation, lesson: 04-subagents, detect: {commands: ["/agents"]}}
- {name: custom subagent definitions (.claude/agents), lesson: 04-subagents, detect: {}}
- {name: MCP servers, lesson: 05-mcp, detect: {commands: ["/mcp"]}}
- {name: MCP resources via @-mentions, lesson: 05-mcp, detect: {}}
- {name: Stop hooks, lesson: 06-hooks, detect: {hook_substring: skill-extraction-gate}}
- {name: PreToolUse / PostToolUse hooks, lesson: 06-hooks, detect: {}}
- {name: prompt-type and component-scoped hooks, lesson: 06-hooks, detect: {}}
- {name: plugins / marketplace, lesson: 07-plugins, detect: {commands: ["/plugin"]}}
- {name: checkpoints and rewind, lesson: 08-checkpoints, detect: {commands: ["/rewind"]}}
- {name: plan mode, lesson: 09-advanced-features, detect: {commands: ["/plan"]}}
- {name: permission modes (acceptEdits/dontAsk), lesson: 09-advanced-features, detect: {commands: ["/permissions"]}}
- {name: extended thinking toggle, lesson: 09-advanced-features, detect: {}}
- {name: git worktrees (claude -w / EnterWorktree), lesson: 09-advanced-features, detect: {}}
- {name: background tasks (Ctrl+B), lesson: 09-advanced-features, detect: {}}
- {name: remote control / web handoff, lesson: 09-advanced-features, detect: {commands: ["/teleport", "/desktop"]}}
- {name: print mode (claude -p) in scripts, lesson: 10-cli, detect: {}}
- {name: session resumption (-c / -r), lesson: 10-cli, detect: {}}
- {name: structured output (--json-schema), lesson: 10-cli, detect: {}}
- {name: compaction hygiene, lesson: 02-memory, detect: {commands: ["/compact"]}}
- {name: model switching, lesson: 10-cli, detect: {commands: ["/model", "/fast"]}}
- {name: config management, lesson: 09-advanced-features, detect: {commands: ["/config"]}}
- {name: code review workflows, lesson: 09-advanced-features, detect: {commands: ["/code-review", "/review"]}}
- {name: scheduled cloud agents (routines), lesson: 09-advanced-features, detect: {commands: ["/schedule"]}}
- {name: recurring loops, lesson: 09-advanced-features, detect: {commands: ["/loop"]}}
- {name: security review, lesson: 09-advanced-features, detect: {commands: ["/security-review"]}}
- {name: cost/usage awareness, lesson: 10-cli, detect: {commands: ["/cost", "/usage", "/stats"]}}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_checklist.py
from pathlib import Path
from src import adoption, config

CHECKLIST_PATH = config.REPO_ROOT / "feature-checklist.yaml"


def test_load_checklist_real_file():
    features = adoption.load_checklist(CHECKLIST_PATH)
    assert len(features) >= 25
    assert all("name" in f and "lesson" in f and "detect" in f for f in features)


def test_evaluate_statuses():
    features = [
        {"name": "cmd feature", "lesson": "01", "detect": {"commands": ["/x"]}},
        {"name": "hook feature", "lesson": "06", "detect": {"hook_substring": "gate.py"}},
        {"name": "skill feature", "lesson": "03", "detect": {"skill": "cool-skill"}},
        {"name": "untouched", "lesson": "09", "detect": {}},
    ]
    usage = {"/x": {"count": 3, "last": "2026-07-01"}}
    inventory = {"hooks": ["python3 gate.py"], "skills": ["cool-skill"]}
    rows = adoption.evaluate_checklist(features, usage, inventory)
    by_name = {r["name"]: r for r in rows}
    assert by_name["cmd feature"]["status"] == "used"
    assert by_name["cmd feature"]["last_used"] == "2026-07-01"
    assert by_name["hook feature"]["status"] == "configured-but-unused"
    assert by_name["skill feature"]["status"] == "configured-but-unused"
    assert by_name["untouched"]["status"] == "never-touched"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_checklist.py -v` — Expected: FAIL

- [ ] **Step 4: Write minimal implementation (append to `src/adoption.py`)**

```python
import yaml


def load_checklist(path: Path) -> list[dict]:
    features = yaml.safe_load(path.read_text())
    return [{**f, "detect": f.get("detect") or {}} for f in features]


def evaluate_checklist(features: list[dict], usage: dict, inventory: dict) -> list[dict]:
    rows = []
    for feat in features:
        det = feat["detect"]
        status, last = "never-touched", None
        hits = [usage[c] for c in det.get("commands", []) if c in usage]
        if hits:
            status = "used"
            dates = [h["last"] for h in hits if h["last"]]
            last = max(dates) if dates else None
        elif det.get("hook_substring") and any(
                det["hook_substring"] in h for h in inventory["hooks"]):
            status = "configured-but-unused"
        elif det.get("skill") and det["skill"] in inventory["skills"]:
            status = "configured-but-unused"
        rows.append({"name": feat["name"], "lesson": feat["lesson"],
                     "status": status, "last_used": last})
    return rows
```

- [ ] **Step 5: Run test to verify it passes, then commit**

Run: `python3 -m pytest tests/ -v` — Expected: all PASS

```bash
git add -A && git commit -m "feat: feature checklist and adoption evaluation"
```

---

### Task 7: Taxonomy + heuristic tagger

**Files:**
- Create: `taxonomy.yaml`, `tests/test_classifier_heuristics.py`, `src/classifier.py`

**Interfaces:**
- Produces: `classifier.load_taxonomy(path: Path) -> dict` with keys `tags: list[str]`, `heuristics: dict[str, str]` (substring → tag); `classifier.heuristic_tags(files: list[str], message: str, taxonomy: dict) -> list[str]` (sorted, deduped, always subset of `tags`).
- Taxonomy tags are THE vocabulary — classifier output (Task 8) and profile (Task 9) both validate against `taxonomy["tags"]`.

- [ ] **Step 1: Write the taxonomy data file**

```yaml
# taxonomy.yaml — fixed capability vocabulary. Adding a tag is a code change.
tags:
  - api-backend          # HTTP API services (FastAPI, Express)
  - api-client           # consuming third-party APIs
  - auth                 # login, sessions, OAuth
  - background-jobs      # schedulers, cron, queues
  - caching              # cache layers, TTLs, invalidation
  - charts-svg           # hand-rolled charts / dataviz
  - cli-tooling          # command-line tools and entrypoints
  - data-modeling        # schema design, ORMs
  - db-migrations        # schema migration mechanics
  - deploy-docker        # Dockerfiles, container build
  - deploy-infra         # Railway/launchd/CI wiring
  - email-ingestion      # parsing inbound email
  - error-handling       # redaction boundaries, safe status
  - frontend-spa         # React/Next/Vite UI work
  - frontend-ssr         # server rendering, RSC
  - llm-integration      # Claude/other LLM calls
  - llm-cost-control     # caching, throttles, skip-hashes
  - payments-money       # money math, ledgers
  - privacy-security     # secrets handling, private data rules
  - scraping             # web scraping / mirroring
  - state-machines       # lifecycle/status modeling
  - testing-depth        # test infrastructure beyond basics
  - webhooks             # inbound webhook handling
  - websockets-sse       # realtime push, SSE
  - agents-automation    # agent workflows, hooks, skills
heuristics:
  "auth": auth
  "login": auth
  "migration": db-migrations
  "alembic": db-migrations
  "dockerfile": deploy-docker
  "cron": background-jobs
  "scheduler": background-jobs
  "cache": caching
  "chart": charts-svg
  "anthropic": llm-integration
  "prompt": llm-integration
  "webhook": webhooks
  "scrape": scraping
  "test_": testing-depth
  ".spec.": testing-depth
  "railway": deploy-infra
  "gmail": email-ingestion
  "receipt": email-ingestion
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_classifier_heuristics.py
from src import classifier, config

TAX_PATH = config.REPO_ROOT / "taxonomy.yaml"


def test_load_taxonomy():
    tax = classifier.load_taxonomy(TAX_PATH)
    assert "auth" in tax["tags"] and len(tax["tags"]) >= 20
    assert all(tag in tax["tags"] for tag in tax["heuristics"].values())


def test_heuristic_tags_match_paths_and_message():
    tax = {"tags": ["auth", "db-migrations", "caching"],
           "heuristics": {"auth": "auth", "migration": "db-migrations"}}
    tags = classifier.heuristic_tags(
        ["api/app/auth.py", "migrations/0007_x.py"], "Add login flow", tax)
    assert tags == ["auth", "db-migrations"]


def test_heuristic_tags_empty_when_no_match():
    tax = {"tags": ["auth"], "heuristics": {"auth": "auth"}}
    assert classifier.heuristic_tags(["readme.md"], "docs", tax) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_classifier_heuristics.py -v` — Expected: FAIL

- [ ] **Step 4: Write minimal implementation**

```python
# src/classifier.py
"""Capability tagging: cheap heuristics + cache-forever Haiku classification."""
from pathlib import Path

import yaml

HAIKU_MODEL = "claude-haiku-4-5-20251001"
PRICE_IN_PER_MTOK = 1.00
PRICE_OUT_PER_MTOK = 5.00


def load_taxonomy(path: Path) -> dict:
    tax = yaml.safe_load(path.read_text())
    return {"tags": list(tax["tags"]), "heuristics": dict(tax.get("heuristics") or {})}


def heuristic_tags(files: list[str], message: str, taxonomy: dict) -> list[str]:
    haystack = " ".join(files + [message]).lower()
    hits = {tag for needle, tag in taxonomy["heuristics"].items()
            if needle in haystack and tag in taxonomy["tags"]}
    return sorted(hits)
```

- [ ] **Step 5: Run test to verify it passes, then commit**

Run: `python3 -m pytest tests/ -v` — Expected: all PASS

```bash
git add -A && git commit -m "feat: capability taxonomy and heuristic tagger"
```

---

### Task 8: Haiku classifier with cache-forever + cost log

**Files:**
- Modify: `src/classifier.py`
- Create: `tests/test_classifier_llm.py`

**Interfaces:**
- Consumes: `load_taxonomy`, `heuristic_tags` (Task 7); ledger/specs JSONL shapes (Tasks 3–4).
- Produces:
  - `classifier.content_hash(text: str) -> str` (16-hex sha256 prefix)
  - `classifier.build_units(ledger_rows, spec_rows) -> list[dict]` — one unit per spec (`{"kind": "spec", "repo", "date", "title", "text"}` where text = title + spec path); for repos with **no** specs, one unit per repo-month (`{"kind": "commits", "repo", "date": "YYYY-MM-01", "title": "<repo> <YYYY-MM>", "text": joined messages + file paths}`)
  - `classifier.classify_unit(text: str, taxonomy: dict, client) -> dict` — one Haiku call, returns `{"tags": [...], "complexity": int, "summary": str, "input_tokens": int, "output_tokens": int}`; tags validated ⊆ taxonomy, invalid dropped
  - `classifier.run_classifier(data_dir: Path, taxonomy: dict, client_factory) -> dict` — `{"classified": int, "cached": int, "failed": int}`; appends to `data/classifications.jsonl` (row: `{"key", "kind", "repo", "date", "title", "tags", "complexity", "summary", "model"}`) and `data/llm_costs.jsonl` (row: `{"ts", "unit", "input_tokens", "output_tokens", "cost_usd"}`). `client_factory()` returns an anthropic client or `None` (no API key) → heuristics fallback with `"model": "heuristics"`.
- **When implementing the real API call, consult the `claude-api` skill.**

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classifier_llm.py
import json
from src import classifier

TAX = {"tags": ["auth", "caching", "llm-integration"], "heuristics": {"auth": "auth"}}


class FakeContent:
    def __init__(self, text): self.text = text


class FakeResponse:
    def __init__(self, text, tin=100, tout=50):
        self.content = [FakeContent(text)]
        self.usage = type("U", (), {"input_tokens": tin, "output_tokens": tout})()


class FakeClient:
    def __init__(self, text): self._text, self.calls = text, 0
    @property
    def messages(self): return self
    def create(self, **kwargs):
        self.calls += 1
        return FakeResponse(self._text)


def test_content_hash_stable():
    assert classifier.content_hash("abc") == classifier.content_hash("abc")
    assert len(classifier.content_hash("abc")) == 16


def test_build_units_spec_repos_and_commit_clusters():
    ledger = [
        {"repo": "has-specs", "date": "2026-07-01T10:00:00Z", "message": "m1", "files": ["a.py"]},
        {"repo": "no-specs", "date": "2026-07-03T10:00:00Z", "message": "m2", "files": ["b.py"]},
        {"repo": "no-specs", "date": "2026-07-20T10:00:00Z", "message": "m3", "files": ["c.py"]},
        {"repo": "no-specs", "date": "2026-08-01T10:00:00Z", "message": "m4", "files": ["d.py"]},
    ]
    specs = [{"repo": "has-specs", "spec_path": "docs/superpowers/specs/x.md",
              "date": "2026-07-01", "title": "Feature X"}]
    units = classifier.build_units(ledger, specs)
    kinds = sorted((u["kind"], u["repo"], u["date"]) for u in units)
    assert kinds == [("commits", "no-specs", "2026-07-01"),
                     ("commits", "no-specs", "2026-08-01"),
                     ("spec", "has-specs", "2026-07-01")]


def test_classify_unit_parses_json_and_validates_tags():
    client = FakeClient('{"tags": ["auth", "bogus-tag"], "complexity": 3, "summary": "s"}')
    out = classifier.classify_unit("some text", TAX, client)
    assert out["tags"] == ["auth"]           # bogus dropped
    assert out["complexity"] == 3 and out["input_tokens"] == 100


def test_run_classifier_caches_forever(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(json.dumps(
        {"repo": "r", "date": "2026-07-01T00:00:00Z", "message": "add auth", "files": ["auth.py"]}) + "\n")
    (tmp_path / "specs.jsonl").write_text("")
    client = FakeClient('{"tags": ["auth"], "complexity": 2, "summary": "s"}')
    r1 = classifier.run_classifier(tmp_path, TAX, lambda: client)
    assert r1 == {"classified": 1, "cached": 0, "failed": 0}
    r2 = classifier.run_classifier(tmp_path, TAX, lambda: client)
    assert r2 == {"classified": 0, "cached": 1, "failed": 0}
    assert client.calls == 1                 # zero API calls on second run
    costs = (tmp_path / "llm_costs.jsonl").read_text().splitlines()
    assert len(costs) == 1


def test_run_classifier_no_client_falls_back_to_heuristics(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(json.dumps(
        {"repo": "r", "date": "2026-07-01T00:00:00Z", "message": "add auth", "files": ["auth.py"]}) + "\n")
    (tmp_path / "specs.jsonl").write_text("")
    r = classifier.run_classifier(tmp_path, TAX, lambda: None)
    assert r["classified"] == 1
    row = json.loads((tmp_path / "classifications.jsonl").read_text())
    assert row["model"] == "heuristics" and row["tags"] == ["auth"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_classifier_llm.py -v` — Expected: FAIL

- [ ] **Step 3: Write minimal implementation (append to `src/classifier.py`)**

```python
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_units(ledger_rows: list[dict], spec_rows: list[dict]) -> list[dict]:
    units = []
    repos_with_specs = {s["repo"] for s in spec_rows}
    for s in spec_rows:
        units.append({"kind": "spec", "repo": s["repo"], "date": s["date"],
                      "title": s["title"],
                      "text": f"{s['title']}\n{s['spec_path']}"})
    clusters: dict[tuple, list[dict]] = defaultdict(list)
    for row in ledger_rows:
        if row["repo"] in repos_with_specs:
            continue
        month = row["date"][:7]
        clusters[(row["repo"], month)].append(row)
    for (repo, month), rows in sorted(clusters.items()):
        text = "\n".join(f"{r['message']} | {' '.join(r['files'][:20])}" for r in rows)
        units.append({"kind": "commits", "repo": repo, "date": f"{month}-01",
                      "title": f"{repo} {month}", "text": text})
    return units


_PROMPT = """You classify a software feature into capability tags.
Allowed tags (choose 1-4, ONLY from this list): {tags}
Feature description:
{text}

Reply with ONLY a JSON object: {{"tags": [...], "complexity": 1-5, "summary": "one line"}}
complexity: 1=config tweak, 3=solid feature, 5=multi-system architecture."""


def classify_unit(text: str, taxonomy: dict, client) -> dict:
    resp = client.messages.create(
        model=HAIKU_MODEL, max_tokens=300,
        messages=[{"role": "user", "content": _PROMPT.format(
            tags=", ".join(taxonomy["tags"]), text=text[:6000])}],
    )
    raw = resp.content[0].text
    match = _JSON_BLOCK.search(raw)
    data = json.loads(match.group(0)) if match else {}
    tags = [t for t in data.get("tags", []) if t in taxonomy["tags"]]
    return {"tags": tags,
            "complexity": int(data.get("complexity", 2)),
            "summary": str(data.get("summary", ""))[:200],
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens}


def _read_jsonl(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def run_classifier(data_dir: Path, taxonomy: dict, client_factory) -> dict:
    ledger = _read_jsonl(data_dir / "ledger.jsonl")
    specs = _read_jsonl(data_dir / "specs.jsonl")
    done = {row["key"] for row in _read_jsonl(data_dir / "classifications.jsonl")}
    client = client_factory()
    result = {"classified": 0, "cached": 0, "failed": 0}
    out_path = data_dir / "classifications.jsonl"
    cost_path = data_dir / "llm_costs.jsonl"
    for unit in build_units(ledger, specs):
        key = content_hash(unit["text"])
        if key in done:
            result["cached"] += 1
            continue
        if client is None:
            files = [p for r in ledger if r["repo"] == unit["repo"] for p in r["files"]]
            row = {"tags": heuristic_tags(files, unit["text"], taxonomy),
                   "complexity": 2, "summary": unit["title"], "model": "heuristics"}
        else:
            try:
                got = classify_unit(unit["text"], taxonomy, client)
            except Exception:
                result["failed"] += 1
                continue
            row = {"tags": got["tags"], "complexity": got["complexity"],
                   "summary": got["summary"], "model": HAIKU_MODEL}
            cost = (got["input_tokens"] * PRICE_IN_PER_MTOK
                    + got["output_tokens"] * PRICE_OUT_PER_MTOK) / 1_000_000
            with cost_path.open("a") as fh:
                fh.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "unit": unit["title"],
                    "input_tokens": got["input_tokens"],
                    "output_tokens": got["output_tokens"],
                    "cost_usd": round(cost, 6)}) + "\n")
        with out_path.open("a") as fh:
            fh.write(json.dumps({"key": key, "kind": unit["kind"],
                                 "repo": unit["repo"], "date": unit["date"],
                                 "title": unit["title"], **row},
                                sort_keys=True) + "\n")
        result["classified"] += 1
    return result
```

- [ ] **Step 4: Run test to verify it passes, then commit**

Run: `python3 -m pytest tests/ -v` — Expected: all PASS

```bash
git add -A && git commit -m "feat: cached Haiku classifier with cost log and heuristic fallback"
```

---

### Task 9: Profile builder

**Files:**
- Create: `src/profile.py`, `tests/test_profile.py`

**Interfaces:**
- Consumes: `classifications.jsonl` row shape (Task 8), `evaluate_checklist` row shape (Task 6), taxonomy tags (Task 7).
- Produces: `profile.build_matrix(classification_rows: list[dict], taxonomy_tags: list[str], today: str) -> dict` with keys `rows` (list of `{"tag", "count", "last", "avg_complexity"}` sorted by count desc), `never` (tags with count 0), `stale` (tags with last > 180 days before `today`); `profile.render(matrix: dict, adoption_rows: list[dict], meta: dict) -> str` (markdown); `profile.write_profile(data_dir: Path, text: str) -> None`. `today` is passed in (testability — no hidden clock).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_profile.py
from src import profile

CLS = [
    {"tags": ["auth"], "complexity": 3, "date": "2026-07-01", "repo": "a", "title": "t1"},
    {"tags": ["auth", "caching"], "complexity": 5, "date": "2025-01-01", "repo": "b", "title": "t2"},
]
TAGS = ["auth", "caching", "webhooks"]


def test_build_matrix_counts_last_and_gaps():
    m = profile.build_matrix(CLS, TAGS, today="2026-08-02")
    rows = {r["tag"]: r for r in m["rows"]}
    assert rows["auth"]["count"] == 2
    assert rows["auth"]["last"] == "2026-07-01"
    assert rows["auth"]["avg_complexity"] == 4.0
    assert m["never"] == ["webhooks"]
    assert m["stale"] == ["caching"]          # last done 2025-01-01, >180d


def test_render_contains_sections():
    m = profile.build_matrix(CLS, TAGS, today="2026-08-02")
    adoption = [{"name": "plan mode", "lesson": "09", "status": "never-touched", "last_used": None}]
    text = profile.render(m, adoption, {"generated": "2026-08-02", "commits": 10, "repos": 2})
    assert "## Capability matrix" in text
    assert "## Never built" in text and "webhooks" in text
    assert "## Claude Code feature adoption" in text and "plan mode" in text
    assert "2026-08-02" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_profile.py -v` — Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# src/profile.py
"""Render data/profile.md — the coach's single input. Deterministic, no LLM."""
from datetime import date
from pathlib import Path

STALE_DAYS = 180


def _days_between(a: str, b: str) -> int:
    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def build_matrix(classification_rows: list[dict], taxonomy_tags: list[str],
                 today: str) -> dict:
    per_tag: dict[str, list[dict]] = {t: [] for t in taxonomy_tags}
    for row in classification_rows:
        for tag in row.get("tags", []):
            if tag in per_tag:
                per_tag[tag].append(row)
    rows, never, stale = [], [], []
    for tag, hits in per_tag.items():
        if not hits:
            never.append(tag)
            continue
        last = max(h["date"] for h in hits if h.get("date")) if any(
            h.get("date") for h in hits) else None
        rows.append({"tag": tag, "count": len(hits), "last": last,
                     "avg_complexity": round(
                         sum(h.get("complexity", 2) for h in hits) / len(hits), 1)})
        if last and _days_between(last[:10], today) > STALE_DAYS:
            stale.append(tag)
    rows.sort(key=lambda r: (-r["count"], r["tag"]))
    return {"rows": rows, "never": sorted(never), "stale": sorted(stale)}


def render(matrix: dict, adoption_rows: list[dict], meta: dict) -> str:
    lines = [f"# Build profile — generated {meta['generated']}", "",
             f"{meta.get('commits', '?')} commits across {meta.get('repos', '?')} repos.", "",
             "## Capability matrix", "",
             "| Tag | Features | Last done | Avg complexity |",
             "|---|---|---|---|"]
    for r in matrix["rows"]:
        lines.append(f"| {r['tag']} | {r['count']} | {r['last'] or '?'} | {r['avg_complexity']} |")
    lines += ["", "## Never built", ""]
    lines += [f"- {t}" for t in matrix["never"]] or ["- (none)"]
    lines += ["", "## Stale (6+ months)", ""]
    lines += [f"- {t}" for t in matrix["stale"]] or ["- (none)"]
    lines += ["", "## Claude Code feature adoption", "",
              "| Feature | Lesson | Status | Last used |", "|---|---|---|---|"]
    for a in adoption_rows:
        lines.append(f"| {a['name']} | {a['lesson']} | {a['status']} | {a['last_used'] or '—'} |")
    return "\n".join(lines) + "\n"


def write_profile(data_dir: Path, text: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "profile.md").write_text(text)
```

- [ ] **Step 4: Run test to verify it passes, then commit**

Run: `python3 -m pytest tests/ -v` — Expected: all PASS

```bash
git add -A && git commit -m "feat: profile builder rendering both lanes"
```

---

### Task 10: Sweep orchestrator + Makefile + first real run

**Files:**
- Create: `src/sweep.py`, `Makefile`, `tests/test_sweep_main.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `sweep.main(root: Path | None = None, data_dir: Path | None = None) -> int` (always returns 0; prints one status line) and `python3 -m src.sweep` entrypoint. Anthropic client built inside `sweep._client_factory()`: returns `None` when `ANTHROPIC_API_KEY` unset, else `anthropic.Anthropic()`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sweep_main.py
from src import sweep


def test_main_end_to_end_without_api_key(tmp_path, make_repo, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    make_repo("alpha")
    data = tmp_path / "coach-data"
    rc = sweep.main(root=tmp_path, data_dir=data)
    assert rc == 0
    assert (data / "profile.md").exists()
    out = capsys.readouterr().out
    assert "repos=1" in out and "new_commits=2" in out


def test_main_survives_empty_root(tmp_path, capsys):
    rc = sweep.main(root=tmp_path / "empty", data_dir=tmp_path / "d")
    assert rc == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_sweep_main.py -v` — Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

```python
# src/sweep.py
"""Daily sweep: collect -> classify -> profile. Always exits 0."""
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from . import adoption, classifier, collector, config, profile

log = logging.getLogger("sweep")


def _client_factory():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    import anthropic
    return anthropic.Anthropic()


def main(root: Path | None = None, data_dir: Path | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    root = root or config.code_apps_root()
    data_dir = data_dir or config.DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()

    if not root.is_dir():
        print(f"sweep: root {root} missing; repos=0 new_commits=0")
        return 0

    swept = collector.sweep(root, data_dir)
    for err in swept["errors"]:
        log.warning("collect error: %s", err)

    taxonomy = classifier.load_taxonomy(config.REPO_ROOT / "taxonomy.yaml")
    classed = classifier.run_classifier(data_dir, taxonomy, _client_factory)

    home = Path.home() / ".claude"
    usage = adoption.parse_history_commands(home / "history.jsonl")
    inventory = adoption.inventory_config(home / "settings.json", home / "skills")
    features = adoption.load_checklist(config.REPO_ROOT / "feature-checklist.yaml")
    adoption_rows = adoption.evaluate_checklist(features, usage, inventory)

    cls_rows = classifier._read_jsonl(data_dir / "classifications.jsonl")
    ledger_len = len(classifier._read_jsonl(data_dir / "ledger.jsonl"))
    matrix = profile.build_matrix(cls_rows, taxonomy["tags"], today)
    text = profile.render(matrix, adoption_rows,
                          {"generated": today, "commits": ledger_len,
                           "repos": swept["repos"]})
    profile.write_profile(data_dir, text)

    print(f"sweep: repos={swept['repos']} new_commits={swept['new_commits']} "
          f"specs={swept['specs']} classified={classed['classified']} "
          f"cached={classed['cached']} failed={classed['failed']} "
          f"errors={len(swept['errors'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

```makefile
# Makefile
sweep:
	.venv/bin/python -m src.sweep

test:
	.venv/bin/python -m pytest tests/ -v

install-schedule:
	cp launchd/com.tomkeefe.app-builder-coach.plist ~/Library/LaunchAgents/
	launchctl unload ~/Library/LaunchAgents/com.tomkeefe.app-builder-coach.plist 2>/dev/null || true
	launchctl load ~/Library/LaunchAgents/com.tomkeefe.app-builder-coach.plist
.PHONY: sweep test install-schedule
```

- [ ] **Step 4: Run tests, then the FIRST REAL SWEEP (heuristics-only)**

Run: `.venv/bin/python -m pytest tests/ -v` — Expected: all PASS
Run: `env -u ANTHROPIC_API_KEY .venv/bin/python -m src.sweep` — Expected: exit 0, a status line with real repo counts, `data/profile.md` populated. **Read `data/profile.md` and sanity-check it** — repos present, no archive (`z*`) repos listed, adoption table shows real statuses.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: sweep orchestrator and Makefile"
```

---

### Task 11: launchd daily schedule

**Files:**
- Create: `launchd/com.tomkeefe.app-builder-coach.plist`

**Interfaces:**
- Consumes: `make sweep` / `python3 -m src.sweep` (Task 10).

- [ ] **Step 1: Write the plist**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.tomkeefe.app-builder-coach</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>cd "/Users/tomkeefe/Code Apps/app-builder-coach" &amp;&amp; "/Users/tomkeefe/Code Apps/app-builder-coach/.venv/bin/python" -m src.sweep</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key>
  <string>/Users/tomkeefe/Code Apps/app-builder-coach/data/sweep.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/tomkeefe/Code Apps/app-builder-coach/data/sweep.log</string>
</dict>
</plist>
```

- [ ] **Step 2: Install and verify**

Run: `make install-schedule`
Run: `launchctl list | grep app-builder-coach` — Expected: one row (exit status `-` or `0`).
Run: `launchctl start com.tomkeefe.app-builder-coach` then check `data/sweep.log` contains a fresh `sweep:` status line.

Note: the launchd-run sweep has no `ANTHROPIC_API_KEY` (heuristics-only) unless an `EnvironmentVariables` dict is added; that's fine for v1 — the coach-invoked sweep can classify with the key from the session. Do NOT put the API key in the plist file (it's committed to git).

- [ ] **Step 3: Commit**

```bash
git add -A && git commit -m "feat: launchd daily sweep schedule"
```

---

### Task 12: Global /build-coach skill + end-to-end dry run

**Files:**
- Create: `/Users/tomkeefe/.claude/skills/build-coach/SKILL.md` (outside this repo — the global skills tree, which is itself a git repo; commit there too)

**Interfaces:**
- Consumes: `data/profile.md` (Task 9 render shape), `make sweep` (Task 10).

- [ ] **Step 1: Write the skill**

```markdown
---
name: build-coach
description: Use when asked /build-coach, "what should I build next", "coach me", "what am I not using in Claude Code", "review my building progress", or for a periodic check on capability growth across Code Apps projects.
---

# Build Coach

## Overview

Reads the build profile generated by `Code Apps/app-builder-coach` and coaches:
what Tom has built, where the gaps are, and ONE next challenge. The profile has
two lanes: capability tags from real git history, and Claude Code feature
adoption from real usage.

## Procedure

1. **Freshen data.** If `data/profile.md` in
   `/Users/tomkeefe/Code Apps/app-builder-coach` is older than 24h, run
   `make -C "/Users/tomkeefe/Code Apps/app-builder-coach" sweep` first
   (with `ANTHROPIC_API_KEY` available if possible, so classification runs).
2. **Read** `data/profile.md`. Do not re-derive from raw JSONL.
3. **Deliver the coaching report** with exactly these four parts:
   1. **Snapshot** — 3-5 sentences: strongest capabilities, trajectory.
   2. **Gaps** — the most meaningful `Never built` / `Stale` tags (not all of
      them; pick what matters for his goals) and 2-3 `never-touched` Claude
      Code features.
   3. **ONE challenge** — a single concrete, buildable feature or mini-project,
      one notch above current complexity, that exercises 1-2 gap tags. Name
      what makes it a stretch. Never a list of options; one recommendation
      with reasoning.
   4. **Tooling pairing** — 1-2 unused Claude Code features to deliberately
      use while building the challenge, with the concrete first step.

## Rules

- Challenge must build on an existing interest (his repos show the domains) —
  not an abstract exercise.
- If the profile is missing or empty, run the sweep; if still empty, say so
  and stop — never invent a profile.
- Complexity notch: propose work at avg_complexity + 1 of his strongest tags,
  not a leap to 5.
```

- [ ] **Step 2: Verify the skill triggers and the loop closes (manual test)**

- Confirm the file registers: it should appear in the skills list of a new session (or this one's reminder).
- Dry-run the procedure yourself in-session: run step 1-3 exactly as written against the real `data/profile.md` and confirm each part is producible. Fix the skill text where it doesn't hold up (e.g. profile path wrong, staleness check awkward).

- [ ] **Step 3: Commit (in `~/.claude`)**

```bash
git -C ~/.claude add skills/build-coach/SKILL.md && git -C ~/.claude commit -m "Add build-coach skill consuming app-builder-coach profile"
```

- [ ] **Step 4: Final full-suite run in the repo**

Run: `cd "/Users/tomkeefe/Code Apps/app-builder-coach" && python3 -m pytest tests/ -v` — Expected: all PASS.
Run: `make sweep` (with API key if available) — Expected: classification runs, `data/llm_costs.jsonl` shows per-call costs, `data/profile.md` gains real tags.
```

---

## Self-review notes

- Spec coverage: collector (T2-4), adoption (T5-6), classifier (T7-8), profile (T9), orchestrator+Makefile (T10), launchd (T11), skill (T12). Error-handling and privacy constraints embedded in T4/T5/T8/T10. Cost logging in T8. ✔
- launchd has no API key by design (documented in T11); coach-side sweep covers classification. ✔
- Type consistency: ledger row shape defined T2/T3 and consumed T8/T10; classification row defined T8, consumed T9; adoption row defined T6, consumed T9/T10. ✔
