"""Read-only git sweep across Code Apps. Never writes into swept repos."""

import json
import re
import subprocess
from pathlib import Path
from typing import Optional

from .config import ARCHIVE_PREFIX

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


class CollectError(Exception):
    pass


def collect_repo(repo_path: Path, since_sha: Optional[str]) -> list[dict]:
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


_HEADING = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_SPEC_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})-")


def index_specs(repo_path: Path) -> list[dict]:
    specs_dir = repo_path / "docs" / "superpowers" / "specs"
    if not specs_dir.is_dir():
        return []
    rows = []
    for p in sorted(specs_dir.glob("*.md")):
        text = p.read_text(errors="replace")
        m_date = _SPEC_DATE.match(p.stem)
        m_head = _HEADING.search(text)
        rows.append({
            "repo": repo_path.name,
            "spec_path": str(p.relative_to(repo_path)),
            "date": m_date.group(1) if m_date else None,
            "title": m_head.group(1).strip() if m_head else p.stem,
            "body": text[:6000],
        })
    return rows


def discover_repos(root: Path) -> list[Path]:
    return [p for p in sorted(root.iterdir())
            if p.is_dir()
            and not p.name.startswith((ARCHIVE_PREFIX, "."))
            and (p / ".git").exists()]


def _load_existing_shas(ledger_path: Path) -> dict:
    existing: dict[str, set] = {}
    if not ledger_path.exists():
        return existing
    for line in ledger_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            existing.setdefault(row["repo"], set()).add(row["sha"])
        except (json.JSONDecodeError, KeyError):
            continue
    return existing


def sweep(root: Path, data_dir: Path) -> dict:
    cursors = read_cursors(data_dir / "cursors.json")
    result = {"repos": 0, "new_commits": 0, "specs": 0, "errors": []}
    all_specs: list[dict] = []
    ledger_path = data_dir / "ledger.jsonl"
    existing_shas = _load_existing_shas(ledger_path)
    for repo in discover_repos(root):
        result["repos"] += 1
        cursor = cursors.get(repo.name)
        try:
            try:
                rows = collect_repo(repo, cursor)
                new_cursor = rows[-1]["sha"] if rows else None
            except CollectError:
                if cursor is None:
                    raise
                # Stranded cursor (e.g. rewritten history): re-collect full
                # history and filter out shas we've already ledgered.
                rows = collect_repo(repo, None)
                new_cursor = rows[-1]["sha"] if rows else None
                seen = existing_shas.get(repo.name, set())
                rows = [r for r in rows if r["sha"] not in seen]
            if rows:
                append_ledger(ledger_path, rows)
                result["new_commits"] += len(rows)
            if new_cursor is not None:
                cursors[repo.name] = new_cursor
            all_specs.extend(index_specs(repo))
        except Exception as exc:
            result["errors"].append(f"{repo.name}: {str(exc)[:200]}")
            continue
    (data_dir / "specs.jsonl").parent.mkdir(parents=True, exist_ok=True)
    (data_dir / "specs.jsonl").write_text(
        "".join(json.dumps(s, sort_keys=True) + "\n" for s in all_specs))
    result["specs"] = len(all_specs)
    write_cursors(data_dir / "cursors.json", cursors)
    return result
