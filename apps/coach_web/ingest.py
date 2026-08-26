"""Idempotent snapshot ingest. One transaction per snapshot."""
import logging
from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm import Session as _Session

from shared.snapshot import validate_payload
from . import models
from .auth import require_ingest_token
from .db import get_db

log = logging.getLogger("ingest")
router = APIRouter(dependencies=[Depends(require_ingest_token)])


def apply_snapshot(db: Session, payload: dict) -> dict:
    existing = db.scalar(select(models.Snapshot).where(
        models.Snapshot.content_hash == payload["content_hash"]))
    if existing is not None:
        existing.captured_at = payload["captured_at"]
        db.commit()
        return {"snapshot_id": existing.id, "duplicate": True}

    snap = models.Snapshot(content_hash=payload["content_hash"],
                           captured_at=payload["captured_at"],
                           sweep_stats=payload["sweep"])
    db.add(snap)
    db.flush()

    for u in payload["feature_units"]:
        row = db.get(models.FeatureUnit, u["key"])
        if row is None:
            db.add(models.FeatureUnit(**u))
        else:
            for field in ("kind", "repo", "date", "title", "tags",
                          "complexity", "summary", "model"):
                setattr(row, field, u[field])

    for a in payload["activity_daily"]:
        row = db.get(models.ActivityDaily, a["date"])
        if row is None:
            db.add(models.ActivityDaily(**a))
        else:
            row.commits = a["commits"]
            row.by_repo = a["by_repo"]
            # v1 payloads carry neither key; guard so they never null out
            # sessions/prompts already stored by an earlier v2 snapshot.
            if "sessions" in a:
                row.sessions = a["sessions"]
            if "prompts" in a:
                row.prompts = a["prompts"]

    today = date.today().isoformat()
    for r in payload["adoption"]:
        db.add(models.AdoptionHistory(snapshot_id=snap.id,
                                      feature_name=r["name"],
                                      lesson=r.get("lesson", ""),
                                      status=r["status"],
                                      last_used=r.get("last_used")))
        if db.get(models.FeatureCatalog, r["name"]) is None:
            db.add(models.FeatureCatalog(name=r["name"],
                                         lesson=r.get("lesson", ""),
                                         source="checklist",
                                         discovered_at=today))
    for c in payload.get("cost_daily", []):
        row = db.get(models.CostDaily, c["date"])
        if row is None:
            db.add(models.CostDaily(**c))
        else:
            for field in ("input_tokens", "output_tokens", "cache_read_tokens",
                          "cache_creation_tokens", "cost_usd", "by_model"):
                setattr(row, field, c[field])

    for row in payload.get("infra_usage", []):
        key = (row["period_start"], row["app"], row["capture_date"])
        existing_row = db.get(models.InfraUsage, key)
        if existing_row is None:
            db.add(models.InfraUsage(**row))
        else:
            existing_row.cumulative_usd = row["cumulative_usd"]

    for s in payload.get("infra_usage_services", []):
        key = (s["period_start"], s["app"], s["service_id"], s["capture_date"])
        existing_service = db.get(models.InfraServiceUsage, key)
        if existing_service is None:
            db.add(models.InfraServiceUsage(**s))
        else:
            for field in ("service_name", "cumulative_usd", "memory_usd", "cpu_usd",
                          "egress_usd", "volume_usd", "backup_usd"):
                setattr(existing_service, field, s[field])

    db.commit()
    return {"snapshot_id": snap.id, "duplicate": False}


def post_ingest(engine, client_factory=None, fetch=None, now=None) -> dict:
    """Post-ingest background work. Opens its own session; never raises.

    Runs after the response has already gone out, so a failure here can only
    lose a brief -- it can never fail an ingest or lose swept data.
    """
    from . import brief as brief_mod, changelog as changelog_mod

    out = {"brief": "failed", "changelog": {"status": "skipped", "added": 0}}
    try:
        with _Session(engine) as db:
            kwargs = {} if client_factory is None else {"client_factory": client_factory}
            out["brief"] = brief_mod.decide_and_generate(db, now=now, **kwargs)
            when = now or datetime.now(timezone.utc)
            if changelog_mod.due(db, when):
                kw = {} if fetch is None else {"fetch": fetch}
                out["changelog"] = changelog_mod.check(db, now=when, **kw)
            db.commit()
    except Exception:
        log.exception("post-ingest background work failed")
    return out


@router.post("/api/ingest")
def ingest(payload: dict, background: BackgroundTasks,
           request: Request, db: Session = Depends(get_db)):
    try:
        validate_payload(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    result = apply_snapshot(db, payload)
    background.add_task(post_ingest, request.app.state.engine)
    return result
