from shared import snapshot as snap_mod


def make_payload(captured="2026-08-02T07:30:00+00:00", commits=2):
    body = {
        "schema_version": 1,
        "sweep": {"repos": 1, "new_commits": commits},
        "feature_units": [{"key": "h1:m", "kind": "commits", "repo": "alpha",
                           "date": "2026-08-01", "title": "alpha 2026-08",
                           "tags": ["auth"], "complexity": 3, "summary": "s",
                           "model": "m"}],
        "activity_daily": [{"date": "2026-08-01", "commits": commits,
                            "by_repo": {"alpha": commits}}],
        "adoption": [{"name": "plan mode", "lesson": "09-advanced-features",
                      "status": "never-touched", "last_used": None}],
    }
    return snap_mod.finalize_payload(body, captured)


AUTH = {"Authorization": "Bearer test-ingest-token"}


def test_ingest_requires_token(client):
    assert client.post("/api/ingest", json=make_payload()).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert client.post("/api/ingest", json=make_payload(), headers=bad).status_code == 401


def test_ingest_stores_snapshot(client):
    resp = client.post("/api/ingest", json=make_payload(), headers=AUTH)
    assert resp.status_code == 200
    data = resp.json()
    assert data["duplicate"] is False and data["snapshot_id"] >= 1


def test_ingest_is_idempotent(client):
    p = make_payload()
    first = client.post("/api/ingest", json=p, headers=AUTH).json()
    again = client.post("/api/ingest",
                        json=snap_mod.finalize_payload(
                            {k: v for k, v in p.items()
                             if k not in ("captured_at", "content_hash")},
                            "2026-08-03T07:30:00+00:00"),
                        headers=AUTH).json()
    assert again["duplicate"] is True
    assert again["snapshot_id"] == first["snapshot_id"]


def test_ingest_upserts_units_and_activity(client):
    client.post("/api/ingest", json=make_payload(), headers=AUTH)
    resp = client.post("/api/ingest", json=make_payload(commits=5), headers=AUTH)
    assert resp.status_code == 200
    # verify via db: one unit row, activity updated to 5 commits
    from sqlalchemy import select
    from sqlalchemy.orm import Session
    from apps.coach_web import models
    engine = client.app.state.engine
    with Session(engine) as s:
        assert len(s.scalars(select(models.FeatureUnit)).all()) == 1
        assert s.get(models.ActivityDaily, "2026-08-01").commits == 5
        assert s.get(models.FeatureCatalog, "plan mode") is not None


def test_ingest_rejects_malformed_unit_with_400_not_500(client):
    """Hash-correct but structurally wrong: a client bug, not a server crash."""
    p = make_payload()
    body = {k: v for k, v in p.items() if k not in ("captured_at", "content_hash")}
    body["feature_units"] = [{"key": "h1:m", "kind": "commits"}]  # missing fields
    p = snap_mod.finalize_payload(body, "2026-08-02T07:30:00+00:00")
    resp = client.post("/api/ingest", json=p, headers=AUTH)
    assert resp.status_code == 400
    assert "feature_units" in resp.json()["detail"]


def test_ingest_rejects_extra_unit_key_with_400(client):
    p = make_payload()
    body = {k: v for k, v in p.items() if k not in ("captured_at", "content_hash")}
    body["feature_units"] = [{**body["feature_units"][0], "dropped_column": "x"}]
    p = snap_mod.finalize_payload(body, "2026-08-02T07:30:00+00:00")
    resp = client.post("/api/ingest", json=p, headers=AUTH)
    assert resp.status_code == 400
    assert "dropped_column" in resp.json()["detail"]


def test_ingest_rejects_bad_schema_version(client):
    p = make_payload()
    p["schema_version"] = 99
    p = snap_mod.finalize_payload(
        {k: v for k, v in p.items() if k not in ("captured_at", "content_hash")},
        "2026-08-02T07:30:00+00:00")
    resp = client.post("/api/ingest", json=p, headers=AUTH)
    assert resp.status_code == 400
    assert "schema_version" in resp.json()["detail"]
