"""Capability and adoption gaps over ingested data.

One source of truth for Overview and the coach. The two differ in exactly one
way, which is why `exclude_dismissed` is a parameter rather than two functions:
Overview keeps showing dismissed items so a dismissal never becomes invisible,
while the coach must stop re-suggesting them.

Not in aggregate.py -- that module promises "No DB access".
"""
from datetime import date, timedelta

from sqlalchemy import select

from . import models, taxonomy

STALE_DAYS = 180


def gap_lists(db, today: date, exclude_dismissed: bool = False) -> dict:
    last_by_tag: dict[str, str] = {}
    for tags, d in db.execute(select(models.FeatureUnit.tags,
                                     models.FeatureUnit.date)):
        for t in tags or []:
            if t not in last_by_tag or d > last_by_tag[t]:
                last_by_tag[t] = d

    never_built = [t for t in taxonomy.all_tags() if t not in last_by_tag]
    cutoff = (today - timedelta(days=STALE_DAYS)).isoformat()
    stale = [{"tag": t, "last_done": d} for t, d in sorted(last_by_tag.items())
             if d <= cutoff]

    adoption_gaps: list[str] = []
    latest = db.scalar(select(models.Snapshot)
                       .order_by(models.Snapshot.id.desc()).limit(1))
    if latest is not None:
        adoption_gaps = sorted(db.scalars(
            select(models.AdoptionHistory.feature_name)
            .where(models.AdoptionHistory.snapshot_id == latest.id,
                   models.AdoptionHistory.status == "never-touched")))

    if exclude_dismissed:
        dismissed_tags, dismissed_features = set(), set()
        for row in db.scalars(select(models.Dismissal)):
            if row.kind == "tag":
                dismissed_tags.add(row.target)
            elif row.kind == "feature":
                dismissed_features.add(row.target)
        never_built = [t for t in never_built if t not in dismissed_tags]
        stale = [s for s in stale if s["tag"] not in dismissed_tags]
        adoption_gaps = [f for f in adoption_gaps if f not in dismissed_features]

    return {"never_built": never_built, "stale": stale,
            "adoption_gaps": adoption_gaps}
