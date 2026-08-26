"""Claude Code usage lane: sessions, prompts, tokens from ~/.claude transcripts.
Retains counts/numbers/ids ONLY — never prompt or tool content."""
import json
import logging
from pathlib import Path

log = logging.getLogger("usage")

# Moved to shared/pricing.py (the server needs it and the Dockerfile does not
# copy src/); re-exported here so existing callers and tests keep working.
from shared.pricing import FALLBACK_PRICE, PRICES, price_for  # noqa: F401,E402


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
