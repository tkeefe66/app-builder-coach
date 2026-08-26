"""Derive daily infra spend from cumulative captures, and window both lanes.

Railway reports billing-period-to-date totals, so daily spend is the difference
between consecutive captures within one period. At a period boundary the
cumulative resets, which is why deltas are computed per (period_start, app)
rather than across the whole series.
"""
from collections import defaultdict


def daily_infra(rows) -> dict:
    """Return {capture_date: {app: daily_usd}} from cumulative InfraUsage rows."""
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row.period_start, row.app)].append(row)
    out: dict = {}
    for (_period, app), group in grouped.items():
        group.sort(key=lambda r: r.capture_date)
        previous = 0.0
        for row in group:
            delta = row.cumulative_usd - previous
            # A decrease within a period means Railway restated a prior figure
            # downward or a project was removed -- it is not a fresh series
            # starting from zero. The correct daily spend for that capture is
            # 0.0; re-booking the whole period-to-date cumulative as a single
            # day's spend would double-count everything already captured
            # earlier in the period.
            if delta < 0:
                delta = 0.0
            out.setdefault(row.capture_date, {})[app] = round(delta, 6)
            previous = row.cumulative_usd
    return out


def window_sums(daily: dict, start: str, end: str) -> dict:
    """Sum {date: {app: usd}} over an inclusive date window."""
    totals: dict = defaultdict(float)
    for day, per_app in daily.items():
        if start <= day <= end:
            for app, usd in per_app.items():
                totals[app] += usd
    return {app: round(usd, 6) for app, usd in totals.items()}


def period_to_date(rows) -> tuple:
    """Sum each app's latest capture within the most recent period_start.

    Returns (period_start, total_usd). period_start is None, and total_usd is
    0.0, when rows is empty. This is a single-source figure reported on
    Railway's own billing-period window; it must never be added to an
    Anthropic month-to-date figure.
    """
    if not rows:
        return None, 0.0
    latest_period = max(r.period_start for r in rows)
    latest_by_app: dict = {}
    for r in rows:
        if r.period_start != latest_period:
            continue
        current = latest_by_app.get(r.app)
        if current is None or r.capture_date > current[0]:
            latest_by_app[r.app] = (r.capture_date, r.cumulative_usd)
    total = round(sum(usd for _capture_date, usd in latest_by_app.values()), 2)
    return latest_period, total


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
