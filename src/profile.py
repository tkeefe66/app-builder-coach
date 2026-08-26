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
        for tag in (row.get("tags") or []):
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
    lines += ["", "---",
              f"Generated {meta['generated']} · history lines skipped: "
              f"{meta.get('history_skipped', 0)} · profile is stale if this date is >1 day old."]
    return "\n".join(lines) + "\n"


def write_profile(data_dir: Path, text: str) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "profile.md").write_text(text)
