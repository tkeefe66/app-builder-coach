import pytest
from sqlalchemy import create_engine, select

from apps.coach_web import models
from apps.coach_web.db import make_engine
from sqlalchemy.orm import Session


def test_models_create_and_roundtrip():
    engine = make_engine("sqlite+pysqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        snap = models.Snapshot(content_hash="c" * 64,
                               captured_at="2026-08-02T07:30:00+00:00",
                               sweep_stats={"repos": 7})
        s.add(snap)
        s.flush()
        s.add(models.FeatureUnit(key="h1:m", kind="commits", repo="alpha",
                                 date="2026-08-01", title="alpha 2026-08",
                                 tags=["auth"], complexity=3, summary="s",
                                 model="claude-haiku-4-5-20251001"))
        s.add(models.ActivityDaily(date="2026-08-01", commits=2,
                                   by_repo={"alpha": 2}))
        s.add(models.AdoptionHistory(snapshot_id=snap.id, feature_name="plan mode",
                                     lesson="09-advanced-features",
                                     status="never-touched", last_used=None))
        s.add(models.FeatureCatalog(name="plan mode",
                                    lesson="09-advanced-features",
                                    source="checklist",
                                    discovered_at="2026-08-02"))
        s.commit()
        assert s.scalar(select(models.FeatureUnit)).tags == ["auth"]
        assert s.scalar(select(models.Snapshot)).received_at is not None


def test_make_engine_requires_database_url():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        make_engine("")


def test_brief_and_watcher_state_tables(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from apps.coach_web import models

    engine = create_engine(f"sqlite:///{tmp_path}/m.db")
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(models.Brief(created_at="2026-08-12T07:30:00+00:00", body="hi",
                           model="claude-haiku-4-5", input_tokens=10,
                           output_tokens=20, cost_usd=0.0001, status="ok"))
        s.add(models.WatcherState(key="changelog.version_watermark",
                                  value="2.1.228", updated_at="2026-08-12T07:30:00+00:00"))
        s.commit()
        brief = s.query(models.Brief).one()
        assert brief.id >= 1 and brief.status == "ok" and brief.error == ""
        assert s.get(models.WatcherState, "changelog.version_watermark").value == "2.1.228"


def test_phase5_app_owned_tables(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from apps.coach_web import models

    engine = create_engine(f"sqlite:///{tmp_path}/p5.db")
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(models.Goal(kind="tag", target="auth", title="Ship auth",
                          target_date="2026-09-01", status="active",
                          created_at="2026-08-12T07:00:00+00:00"))
        s.add(models.FeatureCheckoff(feature_name="plan mode",
                                     checked_at="2026-08-12T07:00:00+00:00"))
        s.add(models.Note(subject_kind="brief", subject_id="1", body="useful",
                          created_at="2026-08-12T07:00:00+00:00"))
        s.add(models.Dismissal(kind="feature", target="hooks", reason="not now",
                               created_at="2026-08-12T07:00:00+00:00"))
        s.commit()
        assert s.query(models.Goal).one().status == "active"
        assert s.get(models.FeatureCheckoff, "plan mode").note == ""
        assert s.query(models.Note).one().body == "useful"
        assert s.query(models.Dismissal).one().target == "hooks"


def test_brief_defaults_to_a_legacy_delta():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as db:
        row = models.Brief(created_at="2026-08-11T07:00:00+00:00", model="m")
        db.add(row)
        db.commit()
        assert row.kind == "delta"
        assert row.day == ""
        assert row.fingerprint == ""


def test_brief_recommendation_round_trips():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as db:
        brief_row = models.Brief(created_at="2026-08-11T07:00:00+00:00",
                                 kind="assessment", day="2026-08-11", model="m")
        db.add(brief_row)
        db.commit()
        rec = models.BriefRecommendation(
            brief_id=brief_row.id, ord=0, title="Containerize purchase-inventory",
            kind="tag", target="deploy-docker", why="because", evidence="14 units, no containers")
        db.add(rec)
        db.commit()
        assert rec.outcome == "open"
        assert rec.outcome_at == ""
        assert rec.brief_id == brief_row.id
