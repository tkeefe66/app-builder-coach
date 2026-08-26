"""Claude Code feature-adoption lane. LLM-free; prompt content never retained."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import yaml

log = logging.getLogger("adoption")


def _to_date(ts) -> str | None:
    try:
        if isinstance(ts, (int, float)):
            if ts > 1e12:          # epoch millis
                ts = ts / 1000
            return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
        if isinstance(ts, str) and len(ts) >= 10:
            return ts[:10]
    except (OverflowError, OSError, ValueError):
        pass
    return None


def parse_history_commands(path: Path) -> tuple[dict[str, dict], int]:
    out: dict[str, dict] = {}
    skipped = 0
    if not path.exists():
        return out, skipped
    for line in path.read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue
        try:
            display = row.get("display", "")
            if not isinstance(display, str) or not display.startswith("/"):
                continue
            cmd = display.split()[0].lower()
            entry = out.setdefault(cmd, {"count": 0, "last": None})
            entry["count"] += 1
            date = _to_date(row.get("timestamp") or row.get("created_at") or row.get("ts"))
            if date and (entry["last"] is None or date > entry["last"]):
                entry["last"] = date
        except Exception:
            skipped += 1
            continue
    return out, skipped


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


def load_checklist(path: Path) -> list[dict]:
    try:
        features = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        log.warning("failed to load checklist %s: %s", path, exc)
        return []
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
