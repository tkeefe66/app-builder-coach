"""Read endpoints for the SPA. Phase 1: summary only."""
import logging
import os
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.apps import display_map, load_apps

from . import aggregate, gaps, grade as grade_mod, models, rubric, taxonomy
from . import brief as brief_mod
from . import truecost as truecost_mod
from .auth import require_user
from .db import get_db
from .taxonomy import REPO_ROOT

router = APIRouter(dependencies=[Depends(require_user)])

logger = logging.getLogger(__name__)

# The Console figure is re-read monthly, so a 30-day threshold would flag as
# stale during the normal gap between readings. 35 gives that slack while still
# catching an anchor nobody has touched.
DRIFT_STALE_AFTER_DAYS = 35


def _parse_console_spend(raw: str) -> float | None:
    """Parse a dollar figure as shown on the Anthropic Console, e.g. "$8.90"."""
    try:
        return round(float(raw.replace("$", "").replace(",", "")), 2)
    except ValueError:
        return None


def _is_iso_date(raw: str) -> bool:
    """True when raw parses as YYYY-MM-DD -- the format row dates are stored
    and string-compared as, so anything else must not be treated as a window
    bound."""
    try:
        date.fromisoformat(raw)
        return True
    except ValueError:
        return False


@router.get("/api/summary")
def summary(db: Session = Depends(get_db)):
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    tag_counts: Counter = Counter()
    for (tags,) in db.execute(select(models.FeatureUnit.tags)):
        tag_counts.update(tags or [])
    adoption: dict[str, int] = {}
    if latest is not None:
        rows = db.execute(
            select(models.AdoptionHistory.status, func.count())
            .where(models.AdoptionHistory.snapshot_id == latest.id)
            .group_by(models.AdoptionHistory.status))
        adoption = {status: n for status, n in rows}
    return {
        "latest": ({"captured_at": latest.captured_at,
                    "sweep_stats": latest.sweep_stats}
                   if latest is not None else None),
        "unit_count": db.scalar(select(func.count(models.FeatureUnit.key))) or 0,
        "tag_counts": dict(tag_counts),
        "adoption": adoption,
    }


@router.get("/api/overview")
def overview(db: Session = Depends(get_db)):
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    today = date.today()
    monday = aggregate.week_start(today).isoformat()

    # Commit clusters are month-resolution units; they must never count
    # toward a week tile.
    units_this_week = db.scalar(
        select(func.count(models.FeatureUnit.key))
        .where(models.FeatureUnit.date >= monday,
               models.FeatureUnit.kind != "commits")) or 0
    activity_rows = [{"date": r.date, "commits": r.commits, "by_repo": r.by_repo,
                      "sessions": r.sessions}
                     for r in db.scalars(select(models.ActivityDaily))]
    commits_this_week = sum(r["commits"] for r in activity_rows
                            if r["date"] >= monday)
    stk = aggregate.streak(activity_rows, today)

    # A tile reads null (not 0) until the usage lane has ever shipped data.
    sessions_this_week = None
    if any(r["sessions"] is not None for r in activity_rows):
        sessions_this_week = sum(r["sessions"] or 0 for r in activity_rows
                                 if r["date"] >= monday)
    cost_rows = list(db.scalars(select(models.CostDaily)))
    cost_this_week = None
    if cost_rows:
        cost_this_week = round(sum(r.cost_usd for r in cost_rows
                                   if r.date >= monday), 2)

    gap = gaps.gap_lists(db, today)
    never_built = gap["never_built"]
    stale = gap["stale"]
    adoption_gaps = gap["adoption_gaps"]
    unit_rows = list(db.execute(
        select(models.FeatureUnit.repo, models.FeatureUnit.date,
               models.FeatureUnit.tags, models.FeatureUnit.complexity)
        .order_by(models.FeatureUnit.key)))
    active_goals = [
        {"id": g.id, "kind": g.kind, "target": g.target, "title": g.title,
         "target_date": g.target_date}
        for g in db.scalars(select(models.Goal)
                            .where(models.Goal.status == "active")
                            .order_by(models.Goal.id))]
    grade = grade_mod.compute_grade(unit_rows, rubric.load(), today)
    return {
        "freshness": {"captured_at": latest.captured_at if latest else None,
                      "received_at": latest.received_at.isoformat()
                      if latest else None},
        "tiles": {"units_this_week": units_this_week,
                  "commits_this_week": commits_this_week,
                  "streak_days": stk["days"],
                  "streak_last_active": stk["last_active"],
                  "sessions_this_week": sessions_this_week,
                  "cost_this_week": cost_this_week},
        "never_built": never_built,
        "stale": stale,
        "adoption_gaps": adoption_gaps,
        "active_goals": active_goals,
        "grade": grade,
    }


@router.get("/api/capabilities")
def capabilities(db: Session = Depends(get_db)):
    today = date.today()
    per_tag: dict[str, dict] = {}
    for tags, d, cx in db.execute(select(models.FeatureUnit.tags,
                                         models.FeatureUnit.date,
                                         models.FeatureUnit.complexity)):
        for t in tags or []:
            entry = per_tag.setdefault(t, {"dates": [], "cx": []})
            entry["dates"].append(d)
            entry["cx"].append(cx)
    out = []
    for t, e in per_tag.items():
        out.append({"tag": t, "count": len(e["dates"]),
                    "last_done": max(e["dates"]),
                    "avg_complexity": round(sum(e["cx"]) / len(e["cx"]), 1),
                    "monthly": aggregate.monthly_counts(e["dates"], 12, today)})
    out.sort(key=lambda r: (-r["count"], r["tag"]))
    return {"tags": out,
            "never_built": [t for t in taxonomy.all_tags() if t not in per_tag]}


@router.get("/api/activity")
def activity(weeks: int = 12, db: Session = Depends(get_db)):
    weeks = max(1, min(weeks, 52))
    today = date.today()
    activity = list(db.scalars(select(models.ActivityDaily)))
    rows = [{"date": r.date, "commits": r.commits, "by_repo": r.by_repo}
            for r in activity]
    oldest_start = aggregate.week_start(today) - timedelta(weeks=weeks - 1)
    windowed_rows = [r for r in rows if r["date"] >= oldest_start.isoformat()]
    weeks_out = aggregate.weekly_rollup(rows, weeks, today)
    sessions_available = any(r.sessions is not None for r in activity)
    if sessions_available:
        per_week: dict[str, int] = {}
        for r in activity:
            start = aggregate.week_start(date.fromisoformat(r.date)).isoformat()
            per_week[start] = per_week.get(start, 0) + (r.sessions or 0)
        for bucket in weeks_out:
            bucket["sessions"] = per_week.get(bucket["start"], 0)
    return {"weeks": weeks_out,
            "weekday_totals": aggregate.weekday_totals(windowed_rows),
            "streak": aggregate.streak(rows, today),
            "sessions_available": sessions_available}


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


@router.get("/api/adoption/board")
def adoption_board(db: Session = Depends(get_db)):
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    latest_rows: dict[str, models.AdoptionHistory] = {}
    if latest is not None:
        for row in db.scalars(select(models.AdoptionHistory)
                              .where(models.AdoptionHistory.snapshot_id == latest.id)):
            latest_rows[row.feature_name] = row
    history: dict[str, list] = {}
    for row, captured in db.execute(
            select(models.AdoptionHistory, models.Snapshot.captured_at)
            .join(models.Snapshot,
                  models.AdoptionHistory.snapshot_id == models.Snapshot.id)
            .order_by(models.Snapshot.id)):
        history.setdefault(row.feature_name, []).append(
            {"captured_at": captured, "status": row.status})
    checked = {c.feature_name for c in db.scalars(select(models.FeatureCheckoff))}
    dismissed_ids: dict[str, int] = {
        d.target: d.id for d in db.scalars(
            select(models.Dismissal).where(models.Dismissal.kind == "feature"))}
    features = []
    for cat in db.scalars(select(models.FeatureCatalog)):
        latest_row = latest_rows.get(cat.name)
        detected = latest_row.status if latest_row else "unknown"
        is_checked = cat.name in checked
        features.append({
            "name": cat.name, "lesson": cat.lesson, "source": cat.source,
            "discovered_at": cat.discovered_at,
            # A manual check-off beats a detector that cannot see the thing.
            "status": "checked-off" if is_checked else detected,
            "detected_status": detected,
            "checked_off": is_checked,
            # Dismissal only hides a feature from the coach's suggestions; the
            # Adoption board keeps showing it, so this must never touch status.
            "dismissed": cat.name in dismissed_ids,
            "dismissal_id": dismissed_ids.get(cat.name),
            "last_used": latest_row.last_used if latest_row else None,
            "history": history.get(cat.name, []),
        })
    features.sort(key=lambda f: (f["lesson"], f["name"]))
    return {"features": features}


@router.get("/api/truecost")
def truecost(days: int = 30, db: Session = Depends(get_db)):
    days = max(1, min(days, 365))
    today = date.today()
    start = (today - timedelta(days=days - 1)).isoformat()
    end = today.isoformat()

    infra_rows = list(db.scalars(select(models.InfraUsage)))
    daily = truecost_mod.daily_infra(infra_rows)
    railway_by_app = truecost_mod.window_sums(daily, start, end)

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

    llm_rows = list(db.scalars(select(models.LlmDaily)))
    llm_by_app: dict = {}
    cache_read = cache_creation = input_tokens = 0
    for row in llm_rows:
        if start <= row.date <= end:
            llm_by_app[row.app] = round(llm_by_app.get(row.app, 0.0) + row.cost_usd, 6)
            cache_read += row.cache_read_tokens or 0
            cache_creation += row.cache_creation_tokens or 0
            input_tokens += row.input_tokens or 0

    displays = display_map(load_apps(REPO_ROOT / "apps.yaml"))
    apps_out = []
    for app in sorted(set(railway_by_app) | set(llm_by_app)):
        railway_usd = round(railway_by_app.get(app, 0.0), 2)
        llm_usd = round(llm_by_app.get(app, 0.0), 2)
        apps_out.append({"app": app, "display": displays.get(app, app),
                         "railway_usd": railway_usd, "llm_usd": llm_usd,
                         "total_usd": round(railway_usd + llm_usd, 2),
                         "services": services_map.get(app, [])})
    grand_total = round(sum(a["total_usd"] for a in apps_out), 2)
    for entry in apps_out:
        entry["share"] = (round(entry["total_usd"] / grand_total, 4)
                          if grand_total else 0.0)
    apps_out.sort(key=lambda a: -a["total_usd"])

    # Railway's billing period and Anthropic's calendar month are different
    # spans; each single-source figure is reported with its own window and only
    # the trailing-`days` numbers above are ever added together.
    latest_period, period_to_date_usd = truecost_mod.period_to_date(infra_rows)
    month_start = today.replace(day=1).isoformat()
    llm_mtd = round(sum(r.cost_usd for r in llm_rows if r.date >= month_start), 2)

    drift = None
    console_spend = os.environ.get("COACH_CONSOLE_SPEND")
    console_from = os.environ.get("COACH_CONSOLE_FROM")
    console_to = os.environ.get("COACH_CONSOLE_TO")
    if console_spend and console_from and console_to:
        console_usd = _parse_console_spend(console_spend)
        if console_usd is None:
            logger.warning("COACH_CONSOLE_SPEND is not a valid dollar amount: %r",
                           console_spend)
        elif not _is_iso_date(console_from):
            logger.warning("COACH_CONSOLE_FROM is not a valid ISO date: %r", console_from)
        elif not _is_iso_date(console_to):
            logger.warning("COACH_CONSOLE_TO is not a valid ISO date: %r", console_to)
        else:
            tracked = round(sum(r.cost_usd for r in llm_rows
                                if console_from <= r.date <= console_to), 2)
            age_days = (today - date.fromisoformat(console_to)).days
            drift = {"from": console_from, "to": console_to,
                     "console_usd": console_usd, "tracked_usd": tracked,
                     "gap_usd": round(console_usd - tracked, 2),
                     "age_days": age_days,
                     "stale": age_days > DRIFT_STALE_AFTER_DAYS or age_days < 0}

    return {
        "window_days": days,
        "window": {"start": start, "end": end},
        "apps": apps_out,
        "totals": {
            "railway_usd": round(sum(a["railway_usd"] for a in apps_out), 2),
            "llm_usd": round(sum(a["llm_usd"] for a in apps_out), 2),
            "total_usd": grand_total,
        },
        "railway_period": {"period_start": latest_period,
                           "to_date_usd": period_to_date_usd},
        "llm_month_to_date_usd": llm_mtd,
        "cache": {"input_tokens": input_tokens, "cache_read_tokens": cache_read,
                  "cache_creation_tokens": cache_creation,
                  "read_ratio": (round(cache_read / (cache_read + input_tokens), 4)
                                 if (cache_read + input_tokens) else 0.0)},
        "drift": drift,
    }


def _rec_json(r) -> dict:
    return {"id": r.id, "title": r.title, "kind": r.kind, "target": r.target,
            "why": r.why, "evidence": r.evidence, "outcome": r.outcome}


def _brief_json(row) -> dict:
    return {"created_at": row.created_at, "day": row.day or row.created_at[:10],
            "kind": row.kind, "status": row.status, "summary": row.body,
            "error": row.error,
            "recommendations": [_rec_json(r) for r in
                                sorted(row.recommendations, key=lambda r: r.ord)]}


@router.get("/api/briefs")
def briefs(limit: int = 30, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 100))
    # `limit` bounds the fetch before the assessment/deltas/history split, so a
    # very deep history could in principle push the newest assessment out of the
    # window and report it as absent. Inherited from the old endpoint's archive
    # limiting; harmless at single-user scale with the default of 30.
    rows = list(db.scalars(select(models.Brief)
                           .order_by(models.Brief.created_at.desc())
                           .limit(limit)))

    # A failed newest assessment must not blank the page: fall back to the last
    # good one and say plainly that it is stale, carrying the failure's message.
    newest_assessment = next((r for r in rows if r.kind == "assessment"), None)
    good = next((r for r in rows
                 if r.kind == "assessment" and r.status == "ok"), None)
    assessment = None
    if good is not None:
        assessment = _brief_json(good)
        assessment["stale"] = newest_assessment is not good
        assessment["error"] = (newest_assessment.error
                               if newest_assessment is not good else "")

    cutoff = good.created_at if good is not None else None
    deltas, history = [], []
    for r in rows:
        if r is good:
            continue
        if cutoff is not None and r.created_at > cutoff and r.kind == "delta":
            deltas.append(_brief_json(r))
        else:
            history.append(_brief_json(r))

    return {"assessment": assessment, "deltas": deltas, "history": history,
            "recurring": brief_mod.recommendation_history(db)}
