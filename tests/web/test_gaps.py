from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import gaps, models

TODAY = date(2026, 8, 11)
OLD = "2026-01-01"      # older than STALE_DAYS before TODAY


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/gaps.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def add_unit(db, key, tags, day):
    db.add(models.FeatureUnit(key=key, kind="spec", repo="r", date=day,
                              title="t", tags=tags, complexity=3, summary="s",
                              model="m"))


def test_never_built_is_the_taxonomy_minus_what_was_used(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"], TODAY.isoformat())
        db.commit()
        out = gaps.gap_lists(db, TODAY)
        assert "auth" not in out["never_built"]
        assert "deploy-docker" in out["never_built"]


def test_stale_carries_the_last_done_date(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"], OLD)
        db.commit()
        out = gaps.gap_lists(db, TODAY)
        assert {"tag": "auth", "last_done": OLD} in out["stale"]


def test_adoption_gaps_come_from_the_latest_snapshot(tmp_path):
    with make_db(tmp_path) as db:
        snap = models.Snapshot(captured_at="2026-08-11T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.AdoptionHistory(snapshot_id=snap.id,
                                      feature_name="plan mode", lesson="l",
                                      status="never-touched", last_used=None))
        db.add(models.AdoptionHistory(snapshot_id=snap.id,
                                      feature_name="hooks", lesson="l",
                                      status="used", last_used="2026-08-01"))
        db.commit()
        out = gaps.gap_lists(db, TODAY)
        assert out["adoption_gaps"] == ["plan mode"]


def test_dismissals_are_kept_by_default_and_dropped_on_request(tmp_path):
    # Overview deliberately still shows dismissed items so a dismissal never
    # becomes invisible; the coach must stop re-suggesting them. That one
    # difference is the whole reason this takes a flag.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"], TODAY.isoformat())
        db.add(models.Dismissal(kind="tag", target="deploy-docker",
                                reason="no need",
                                created_at="2026-08-01T00:00:00+00:00"))
        db.commit()
        assert "deploy-docker" in gaps.gap_lists(db, TODAY)["never_built"]
        assert "deploy-docker" not in gaps.gap_lists(
            db, TODAY, exclude_dismissed=True)["never_built"]


def test_feature_dismissals_only_filter_adoption_gaps(tmp_path):
    with make_db(tmp_path) as db:
        snap = models.Snapshot(captured_at="2026-08-11T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.AdoptionHistory(snapshot_id=snap.id,
                                      feature_name="plan mode", lesson="l",
                                      status="never-touched", last_used=None))
        db.add(models.Dismissal(kind="feature", target="plan mode", reason="",
                                created_at="2026-08-01T00:00:00+00:00"))
        db.commit()
        out = gaps.gap_lists(db, TODAY, exclude_dismissed=True)
        assert out["adoption_gaps"] == []


def test_empty_database_yields_the_whole_taxonomy(tmp_path):
    with make_db(tmp_path) as db:
        out = gaps.gap_lists(db, TODAY)
        assert len(out["never_built"]) > 0
        assert out["stale"] == []
        assert out["adoption_gaps"] == []
