"""Overall-grade scoring. Pure functions: rows + rubric + today -> grade dict.

rows are (repo, date_iso, tags, complexity) tuples from feature units.

Staleness decay (>180d, x0.5) and a gate's own within_days window stack: work older
than both is quartered (x0.5 * x0.5 = x0.25).

Levels are checked in order and the climb stops at the first unsatisfied level.
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
        known_count = sum(1 for t in stats if t in rubric.tiers)
        fracs.append(min(1.0, known_count / level.breadth))
    if level.noncore:
        need_tags, need_count = level.noncore
        n = sum(1 for t, e in stats.items()
                if t in rubric.tiers and rubric.tiers.get(t) != "core"
                and e["count"] >= need_count)
        fracs.append(min(1.0, n / need_tags))
    return fracs


def best_fit_repo(tag: str, rubric: Rubric, rows, today: date) -> str:
    """Repo with the most recent related work; deterministic fallback.

    Callers must pass non-empty rows (compute_grade guards this).
    """
    pairs = set(rubric.pairs_with.get(tag, []))
    scores: dict[str, list] = {}  # repo -> [count, latest_date]
    for repo, d, tags, _cx in rows:
        if (pairs and not _older_than(d, STALE_DAYS, today)
                and pairs & set(tags or [])):
            e = scores.setdefault(repo, [0, ""])
            e[0] += 1
            e[1] = max(e[1], d)
    if scores:
        return max(scores.items(),
                    key=lambda kv: (kv[1][0], kv[1][1], kv[0]))[0]
    return max(rows, key=lambda r: (r[1], r[0]))[0]


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
        percent = min(99, round(100 * sum(fracs) / len(fracs)))
        gaps = _gaps(nxt, stats, rubric, rows, today)
        next_level, next_label = nxt.name, nxt.label
    else:
        percent, gaps, next_level, next_label = 100, [], None, None

    return {"level": attained.name, "level_label": attained.label,
            "next_level": next_level, "next_label": next_label,
            "percent_to_next": percent, "gaps": gaps}
