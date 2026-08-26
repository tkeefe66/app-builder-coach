from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

TODAY = date(2026, 8, 11)


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/g.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def add_unit(db, key, tags, day=TODAY.isoformat(), complexity=3):
    db.add(models.FeatureUnit(key=key, kind="spec", repo="r", date=day,
                              title=f"title {key}", tags=tags,
                              complexity=complexity, summary=f"summary {key}",
                              model="m"))


def test_fingerprint_is_stable_for_unchanged_state(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.commit()
        assert brief.fingerprint(db, TODAY) == brief.fingerprint(db, TODAY)


def test_fingerprint_moves_when_a_new_tag_is_used(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.commit()
        before = brief.fingerprint(db, TODAY)
        add_unit(db, "u2", ["deploy-docker"])
        db.commit()
        assert brief.fingerprint(db, TODAY) != before


def test_fingerprint_moves_when_a_goal_is_added(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.commit()
        before = brief.fingerprint(db, TODAY)
        db.add(models.Goal(kind="tag", target="deploy-docker", title="Containerize",
                           target_date="", status="active",
                           created_at="2026-08-11T00:00:00+00:00"))
        db.commit()
        assert brief.fingerprint(db, TODAY) != before


def test_spend_never_moves_the_fingerprint_at_any_magnitude(tmp_path):
    # Spend was a component until 2026-08-12, rounded to whole dollars on the
    # theory that cents were the noise floor. Production disagreed: trailing
    # Claude Code spend runs in the thousands and moves by hundreds between
    # sweeps, so the hash changed on every single ingest and a delta fired
    # every time -- exactly what this gate exists to prevent. Spend is also an
    # outcome of building, which `units` and `tags` already detect.
    #
    # The magnitudes below are deliberate: cents, a whole dollar, and a
    # three-thousand-dollar day. If someone reintroduces spend with ANY
    # rounding, at least one of them moves the hash and this test goes red.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.add(models.CostDaily(date=TODAY.isoformat(), input_tokens=1,
                                output_tokens=1, cache_read_tokens=0,
                                cache_creation_tokens=0, cost_usd=2.10,
                                by_model={}))
        db.commit()
        before = brief.fingerprint(db, TODAY)

        for amount in (2.40, 9.90, 3277.90):
            db.query(models.CostDaily).filter_by(date=TODAY.isoformat()).update(
                {"cost_usd": amount})
            db.commit()
            assert brief.fingerprint(db, TODAY) == before, (
                f"spend of {amount} moved the fingerprint; it must not be a component")


def test_state_round_trips(tmp_path):
    with make_db(tmp_path) as db:
        assert brief.get_state(db, brief.FP_KEY) == ""
        brief.set_state(db, brief.FP_KEY, "abc")
        db.commit()
        assert brief.get_state(db, brief.FP_KEY) == "abc"
        brief.set_state(db, brief.FP_KEY, "def")
        db.commit()
        assert brief.get_state(db, brief.FP_KEY) == "def"


def test_fingerprint_moves_when_adopted_status_changes(tmp_path):
    # Adopted features (status != "never-touched") must move the fingerprint.
    # This isolates the adopted query: it must fail if someone flips != to ==.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        snapshot = models.Snapshot(content_hash="abc123",
                                   captured_at="2026-08-11T00:00:00+00:00")
        db.add(snapshot)
        db.flush()
        db.add(models.AdoptionHistory(snapshot_id=snapshot.id,
                                      feature_name="feature1",
                                      status="never-touched"))
        db.commit()
        before = brief.fingerprint(db, TODAY)
        # Change status to "used" (now status != "never-touched")
        db.query(models.AdoptionHistory).filter_by(feature_name="feature1").update(
            {"status": "used"})
        db.commit()
        assert brief.fingerprint(db, TODAY) != before


def test_fingerprint_moves_when_unit_count_changes(tmp_path):
    # Unit count changes must move the fingerprint.
    # Use an already-seen tag to isolate the units component; new tags would
    # also trigger via the tags component.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.commit()
        before = brief.fingerprint(db, TODAY)
        # Add another unit with the same tag
        add_unit(db, "u2", ["auth"])
        db.commit()
        assert brief.fingerprint(db, TODAY) != before


def test_fingerprint_moves_when_changelog_state_changes(tmp_path):
    # Changelog state must move the fingerprint.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        db.commit()
        before = brief.fingerprint(db, TODAY)
        brief.set_state(db, "changelog.last_checked_at", "2026-08-11T00:00:00+00:00")
        db.commit()
        assert brief.fingerprint(db, TODAY) != before


def test_never_touched_features_stay_out_of_the_fingerprint(tmp_path):
    # Pins the `!=` in the adopted query. An inequality assertion across a
    # status flip is symmetric and cannot catch `!=` becoming `==`; this one
    # is asymmetric, because under `==` a never-touched feature would enter
    # the adopted set and move the hash. AdoptionHistory feeds no other
    # fingerprint component, so nothing else can move it here.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", ["auth"])
        snap = models.Snapshot(captured_at="2026-08-11T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.AdoptionHistory(snapshot_id=snap.id, feature_name="hooks",
                                      lesson="l", status="used",
                                      last_used="2026-08-01"))
        db.commit()
        before = brief.fingerprint(db, TODAY)

        db.add(models.AdoptionHistory(snapshot_id=snap.id,
                                      feature_name="plan mode", lesson="l",
                                      status="never-touched", last_used=None))
        db.commit()
        assert brief.fingerprint(db, TODAY) == before
