"""Write endpoints for app-owned tables.

Every route here is cookie-authenticated AND origin-checked. Machine clients
(/api/ingest, /api/usage) live on their own bearer-token routers and are
deliberately not covered -- they send no Origin header at all, so applying
require_same_origin to them would break the daily sweep.
"""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .auth import require_same_origin, require_user
from .db import get_db

router = APIRouter(dependencies=[Depends(require_user),
                                 Depends(require_same_origin)])

Kind = Literal["tag", "feature"]
SubjectKind = Literal["tag", "feature", "brief"]
Status = Literal["active", "done", "abandoned"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GoalIn(BaseModel):
    kind: Kind
    target: str = Field(max_length=120)
    title: str = Field(max_length=200)
    target_date: str = Field(default="", max_length=10)


class GoalPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    target_date: str | None = Field(default=None, max_length=10)
    status: Status | None = None


class NoteIn(BaseModel):
    subject_kind: SubjectKind
    subject_id: str = Field(max_length=120)
    body: str = Field(max_length=5000)


def _goal_json(g: models.Goal) -> dict:
    return {"id": g.id, "kind": g.kind, "target": g.target, "title": g.title,
            "target_date": g.target_date, "status": g.status,
            "created_at": g.created_at}


def _mark_recommendations(db, kind: str, target: str, outcome: str) -> None:
    """Close out every open recommendation for a target.

    Prior briefs are marked too, not just the newest -- every one of them was
    pitching the same thing, and the count of times-suggested is what feeds
    back into the next prompt.
    """
    for rec in db.scalars(select(models.BriefRecommendation).where(
            models.BriefRecommendation.kind == kind,
            models.BriefRecommendation.target == target,
            models.BriefRecommendation.outcome == "open")):
        rec.outcome = outcome
        rec.outcome_at = _now()


@router.get("/api/goals")
def list_goals(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.Goal).order_by(models.Goal.id))
    return {"goals": [_goal_json(g) for g in rows]}


@router.post("/api/goals")
def create_goal(body: GoalIn, db: Session = Depends(get_db)):
    # Idempotent like create_dismissal/create_checkoff: clicking "Add as
    # goal" twice must not produce two identical active goals.
    existing = db.scalar(select(models.Goal).where(
        models.Goal.kind == body.kind, models.Goal.target == body.target,
        models.Goal.status == "active"))
    if existing is not None:
        _mark_recommendations(db, body.kind, body.target, "converted")
        db.commit()
        return _goal_json(existing)
    goal = models.Goal(kind=body.kind, target=body.target, title=body.title,
                       target_date=body.target_date, status="active",
                       created_at=_now())
    db.add(goal)
    _mark_recommendations(db, body.kind, body.target, "converted")
    db.commit()
    return _goal_json(goal)


@router.patch("/api/goals/{goal_id}")
def patch_goal(goal_id: int, body: GoalPatch, db: Session = Depends(get_db)):
    goal = db.get(models.Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    for field in ("title", "target_date", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(goal, field, value)
    db.commit()
    return _goal_json(goal)


@router.delete("/api/goals/{goal_id}")
def delete_goal(goal_id: int, db: Session = Depends(get_db)):
    goal = db.get(models.Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    db.delete(goal)
    db.commit()
    return {"status": "ok"}


def _note_json(n: models.Note) -> dict:
    return {"id": n.id, "subject_kind": n.subject_kind,
            "subject_id": n.subject_id, "body": n.body,
            "created_at": n.created_at}


@router.get("/api/notes")
def list_notes(subject_kind: str | None = None, subject_id: str | None = None,
               db: Session = Depends(get_db)):
    stmt = select(models.Note).order_by(models.Note.id)
    if subject_kind is not None:
        stmt = stmt.where(models.Note.subject_kind == subject_kind)
    if subject_id is not None:
        stmt = stmt.where(models.Note.subject_id == subject_id)
    return {"notes": [_note_json(n) for n in db.scalars(stmt)]}


@router.post("/api/notes")
def create_note(body: NoteIn, db: Session = Depends(get_db)):
    note = models.Note(subject_kind=body.subject_kind, subject_id=body.subject_id,
                       body=body.body, created_at=_now())
    db.add(note)
    db.commit()
    return _note_json(note)


@router.delete("/api/notes/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(models.Note, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    db.delete(note)
    db.commit()
    return {"status": "ok"}


class DismissalIn(BaseModel):
    kind: Kind
    target: str = Field(max_length=120)
    reason: str = Field(default="", max_length=500)


class CheckoffIn(BaseModel):
    feature_name: str = Field(max_length=120)
    note: str = Field(default="", max_length=500)


def _dismissal_json(d: models.Dismissal) -> dict:
    return {"id": d.id, "kind": d.kind, "target": d.target,
            "reason": d.reason, "created_at": d.created_at}


@router.get("/api/dismissals")
def list_dismissals(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.Dismissal).order_by(models.Dismissal.id))
    return {"dismissals": [_dismissal_json(d) for d in rows]}


@router.post("/api/dismissals")
def create_dismissal(body: DismissalIn, db: Session = Depends(get_db)):
    # Idempotent: dismissing twice from two tabs is a no-op, not a duplicate
    # row that then needs deleting twice to actually un-dismiss.
    existing = db.scalar(select(models.Dismissal).where(
        models.Dismissal.kind == body.kind,
        models.Dismissal.target == body.target))
    if existing is not None:
        _mark_recommendations(db, body.kind, body.target, "dismissed")
        db.commit()
        return _dismissal_json(existing)
    row = models.Dismissal(kind=body.kind, target=body.target,
                           reason=body.reason, created_at=_now())
    db.add(row)
    _mark_recommendations(db, body.kind, body.target, "dismissed")
    db.commit()
    return _dismissal_json(row)


@router.delete("/api/dismissals/{dismissal_id}")
def delete_dismissal(dismissal_id: int, db: Session = Depends(get_db)):
    row = db.get(models.Dismissal, dismissal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="dismissal not found")
    db.delete(row)
    db.commit()
    return {"status": "ok"}


def _checkoff_json(c: models.FeatureCheckoff) -> dict:
    return {"feature_name": c.feature_name, "checked_at": c.checked_at,
            "note": c.note}


@router.get("/api/checkoffs")
def list_checkoffs(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.FeatureCheckoff)
                      .order_by(models.FeatureCheckoff.feature_name))
    return {"checkoffs": [_checkoff_json(c) for c in rows]}


@router.post("/api/checkoffs")
def create_checkoff(body: CheckoffIn, db: Session = Depends(get_db)):
    existing = db.get(models.FeatureCheckoff, body.feature_name)
    if existing is not None:
        return _checkoff_json(existing)
    row = models.FeatureCheckoff(feature_name=body.feature_name,
                                 checked_at=_now(), note=body.note)
    db.add(row)
    db.commit()
    return _checkoff_json(row)


@router.delete("/api/checkoffs/{feature_name}")
def delete_checkoff(feature_name: str, db: Session = Depends(get_db)):
    row = db.get(models.FeatureCheckoff, feature_name)
    if row is None:
        raise HTTPException(status_code=404, detail="checkoff not found")
    db.delete(row)
    db.commit()
    return {"status": "ok"}


@router.post("/api/reassess")
def reassess(db: Session = Depends(get_db)):
    """Force a fresh standing assessment, bypassing the change gate."""
    from . import brief as brief_mod
    result = brief_mod.decide_and_generate(db, force=True)
    db.commit()
    return {"status": result}
