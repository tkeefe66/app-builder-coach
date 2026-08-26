from tests.web.test_ingest import AUTH, make_payload
from shared import snapshot as snap_mod


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def test_summary_requires_login(client):
    assert client.get("/api/summary").status_code == 401


def test_summary_empty_db(client):
    login(client)
    data = client.get("/api/summary").json()
    assert data == {"latest": None, "unit_count": 0, "tag_counts": {},
                    "adoption": {}}


def test_summary_after_ingest(client):
    client.post("/api/ingest", json=make_payload(), headers=AUTH)
    login(client)
    data = client.get("/api/summary").json()
    assert data["latest"]["captured_at"] == "2026-08-02T07:30:00+00:00"
    assert data["unit_count"] == 1
    assert data["tag_counts"] == {"auth": 1}
    assert data["adoption"] == {"never-touched": 1}


def test_summary_adoption_scopes_to_latest_snapshot(client):
    # Ingest first snapshot with never-touched status
    client.post("/api/ingest", json=make_payload(), headers=AUTH)
    # Ingest second snapshot with used status (different adoption data)
    second_body = {
        "schema_version": 1,
        "sweep": {"repos": 1, "new_commits": 5},
        "feature_units": [{"key": "h1:m", "kind": "commits", "repo": "alpha",
                           "date": "2026-08-01", "title": "alpha 2026-08",
                           "tags": ["auth"], "complexity": 3, "summary": "s",
                           "model": "m"}],
        "activity_daily": [{"date": "2026-08-01", "commits": 5,
                            "by_repo": {"alpha": 5}}],
        "adoption": [{"name": "plan mode", "lesson": "09-advanced-features",
                      "status": "used", "last_used": "2026-08-01"}],
    }
    second_payload = snap_mod.finalize_payload(second_body, "2026-08-03T07:30:00+00:00")
    client.post("/api/ingest", json=second_payload, headers=AUTH)
    login(client)
    data = client.get("/api/summary").json()
    # Verify adoption only contains latest snapshot's status, not the first one
    assert data["adoption"] == {"used": 1}
