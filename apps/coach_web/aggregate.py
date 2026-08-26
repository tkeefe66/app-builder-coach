"""Pure aggregation helpers for read endpoints. No DB access."""
from collections import Counter
from datetime import date, timedelta


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def weekly_rollup(rows: list[dict], weeks: int, today: date) -> list[dict]:
    latest_start = week_start(today)
    starts = [latest_start - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
    buckets = {s.isoformat(): {"start": s.isoformat(), "commits": 0, "by_repo": {}}
               for s in starts}
    for row in rows:
        s = week_start(date.fromisoformat(row["date"])).isoformat()
        if s not in buckets:
            continue
        b = buckets[s]
        b["commits"] += row["commits"]
        for repo, n in (row.get("by_repo") or {}).items():
            b["by_repo"][repo] = b["by_repo"].get(repo, 0) + n
    return [buckets[s.isoformat()] for s in starts]


def streak(rows: list[dict], today: date) -> dict:
    active = {row["date"] for row in rows if row["commits"] > 0}
    if not active:
        return {"days": 0, "last_active": None}
    last = max(active)
    days = 0
    cursor = date.fromisoformat(last)
    while cursor.isoformat() in active:
        days += 1
        cursor -= timedelta(days=1)
    return {"days": days, "last_active": last}


def weekday_totals(rows: list[dict]) -> list[int]:
    totals = [0] * 7
    for row in rows:
        totals[date.fromisoformat(row["date"]).weekday()] += row["commits"]
    return totals


def monthly_counts(dates: list[str], months: int, today: date) -> list[dict]:
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    keys.reverse()
    counts = Counter(d[:7] for d in dates)
    return [{"month": k, "count": counts.get(k, 0)} for k in keys]
