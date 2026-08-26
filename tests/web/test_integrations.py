from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

TODAY = date(2026, 8, 12)
ORIGIN = {"Origin": "https://testserver"}


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/i.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def test_tag_dismissal_filters_never_built(tmp_path):
    with make_db(tmp_path) as db:
        before, _, _ = brief._gap_lists(db, TODAY)
        assert len(before) > 0
        victim = before[0]
        db.add(models.Dismissal(kind="tag", target=victim, reason="",
                                created_at="2026-08-12T07:00:00+00:00"))
        db.commit()
        after, _, _ = brief._gap_lists(db, TODAY)
        assert victim not in after
        assert len(after) == len(before) - 1


def test_tag_dismissal_filters_stale(tmp_path):
    with make_db(tmp_path) as db:
        db.add(models.FeatureUnit(key="old", kind="spec", repo="r",
                                  date="2025-01-01", title="t", tags=["auth"],
                                  complexity=1, summary="s", model="m"))
        db.commit()
        assert "auth" in brief._gap_lists(db, TODAY)[1]
        db.add(models.Dismissal(kind="tag", target="auth", reason="",
                                created_at="2026-08-12T07:00:00+00:00"))
        db.commit()
        assert "auth" not in brief._gap_lists(db, TODAY)[1]


def test_feature_dismissal_filters_adoption_gaps(tmp_path):
    with make_db(tmp_path) as db:
        snap = models.Snapshot(captured_at="2026-08-12T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.AdoptionHistory(snapshot_id=snap.id, feature_name="hooks",
                                      lesson="09", status="never-touched"))
        db.commit()
        assert brief._gap_lists(db, TODAY)[2] == ["hooks"]
        db.add(models.Dismissal(kind="feature", target="hooks", reason="",
                                created_at="2026-08-12T07:00:00+00:00"))
        db.commit()
        assert brief._gap_lists(db, TODAY)[2] == []


def test_a_tag_dismissal_does_not_filter_a_feature_gap(tmp_path):
    # kind must actually discriminate; a tag dismissal named "hooks" must not
    # silence the *feature* "hooks".
    with make_db(tmp_path) as db:
        snap = models.Snapshot(captured_at="2026-08-12T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.AdoptionHistory(snapshot_id=snap.id, feature_name="hooks",
                                      lesson="09", status="never-touched"))
        db.add(models.Dismissal(kind="tag", target="hooks", reason="",
                                created_at="2026-08-12T07:00:00+00:00"))
        db.commit()
        assert brief._gap_lists(db, TODAY)[2] == ["hooks"]


def test_checkoff_overrides_the_adoption_board(client):
    login(client)
    with Session(client.app.state.engine) as db:
        snap = models.Snapshot(captured_at="2026-08-12T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.FeatureCatalog(name="plan mode", lesson="09",
                                     source="checklist", discovered_at="2026-01-01"))
        db.add(models.AdoptionHistory(snapshot_id=snap.id, feature_name="plan mode",
                                      lesson="09", status="never-touched"))
        db.commit()

    before = client.get("/api/adoption/board").json()["features"][0]
    assert before["status"] == "never-touched" and before["checked_off"] is False

    client.post("/api/checkoffs", headers=ORIGIN, json={"feature_name": "plan mode"})
    after = client.get("/api/adoption/board").json()["features"][0]
    assert after["status"] == "checked-off"
    assert after["checked_off"] is True
    # The detector's opinion is preserved, not destroyed.
    assert after["detected_status"] == "never-touched"


def test_overview_carries_active_goals(client):
    login(client)
    client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "title": "Ship auth"})
    gid = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "ui", "title": "Done one"}).json()["id"]
    client.patch(f"/api/goals/{gid}", headers=ORIGIN, json={"status": "done"})
    goals = client.get("/api/overview").json()["active_goals"]
    assert [g["title"] for g in goals] == ["Ship auth"]
