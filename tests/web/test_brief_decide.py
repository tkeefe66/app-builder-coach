import json
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

NOW = datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc)


class Recorder:
    """Counts calls so 'no model call at all' is provable, not assumed."""
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=json.dumps(
                {"summary": "s", "recommendations": []}))],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0))


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/d.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def seed(db, key="u1", day="2026-08-05"):
    db.add(models.FeatureUnit(key=key, kind="spec", repo="alpha", date=day,
                              title="t", tags=["auth"], complexity=3,
                              summary="s", model="m"))
    db.commit()


def test_first_run_generates_an_assessment(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "assessment"
        db.commit()
        assert rec.calls == 1
        assert db.scalars(select(models.Brief)).one().kind == "assessment"


def test_unchanged_fingerprint_makes_no_model_call(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "skipped"
        db.commit()
        assert rec.calls == 1                       # no second call
        assert len(db.scalars(select(models.Brief)).all()) == 1   # no second row


def test_a_changed_fingerprint_generates_a_delta(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        seed(db, key="u2", day="2026-08-09")
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "delta"
        db.commit()
        kinds = [b.kind for b in db.scalars(select(models.Brief)
                                            .order_by(models.Brief.id))]
        assert kinds == ["assessment", "delta"]


def test_five_deltas_trigger_a_reassessment(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        for i in range(brief.MAX_DELTAS_BEFORE_REASSESS):
            seed(db, key=f"x{i}", day="2026-08-09")
            assert brief.decide_and_generate(db, lambda: rec, NOW) == "delta"
            db.commit()
        seed(db, key="final", day="2026-08-09")
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "assessment"
        db.commit()
        assert brief.get_state(db, brief.DELTA_COUNT_KEY) == "0"


def test_force_reassesses_regardless_of_fingerprint(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        assert brief.decide_and_generate(db, lambda: rec, NOW, force=True) == "assessment"
        db.commit()
        assert rec.calls == 2


class Boom:
    def __init__(self):
        self.messages = self

    def create(self, **kwargs):
        raise RuntimeError("429")


def test_a_failed_delta_still_records_a_row(tmp_path):
    # A run of failed rows is the only visible signal of a misconfigured key.
    with make_db(tmp_path) as db:
        seed(db)
        brief.decide_and_generate(db, lambda: Recorder(), NOW)
        db.commit()
        seed(db, key="u2", day="2026-08-09")
        brief.decide_and_generate(db, lambda: Boom(), NOW)
        db.commit()
        newest = db.scalars(select(models.Brief)
                            .order_by(models.Brief.id.desc())).first()
        assert newest.status == "failed"


def test_a_failed_generation_does_not_consume_the_change(tmp_path):
    # If a failure advanced the fingerprint, the change it was meant to report
    # would be skipped forever. It must retry on the next ingest.
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        seed(db, key="u2", day="2026-08-09")
        brief.decide_and_generate(db, lambda: Boom(), NOW)
        db.commit()
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "delta"
        db.commit()
        newest = db.scalars(select(models.Brief)
                            .order_by(models.Brief.id.desc())).first()
        assert newest.status == "ok"


def test_a_quiet_stretch_does_not_trigger_reassessment(tmp_path):
    # The skip check outranks the delta counter: five deltas followed by
    # silence must stay silent.
    with make_db(tmp_path) as db:
        seed(db)
        rec = Recorder()
        brief.decide_and_generate(db, lambda: rec, NOW)
        db.commit()
        for i in range(brief.MAX_DELTAS_BEFORE_REASSESS):
            seed(db, key=f"y{i}", day="2026-08-09")
            brief.decide_and_generate(db, lambda: rec, NOW)
            db.commit()
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "skipped"


def test_a_failed_first_assessment_does_not_mark_the_change_seen(tmp_path):
    # The delta branch's guard is covered by
    # test_a_failed_generation_does_not_consume_the_change. This covers the
    # assess() branch, which is reached only when no assessment exists yet.
    # Without its guard the very first ingest would mark the change seen and
    # the corpus would never be assessed at all.
    with make_db(tmp_path) as db:
        seed(db)
        assert brief.decide_and_generate(db, lambda: Boom(), NOW) == "assessment"
        db.commit()
        assert brief.get_state(db, brief.FP_KEY) == ""
        assert brief.get_state(db, brief.DELTA_COUNT_KEY) == ""

        rec = Recorder()
        assert brief.decide_and_generate(db, lambda: rec, NOW) == "assessment"
        db.commit()
        assert rec.calls == 1
