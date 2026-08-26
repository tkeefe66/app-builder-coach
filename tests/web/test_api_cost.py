from datetime import date, timedelta

from shared import snapshot as snap_mod
from tests.web.test_ingest import AUTH


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def payload_with(units=(), activity=(), cost=(), captured="2026-08-03T07:00:00+00:00"):
    body = {"schema_version": 2, "sweep": {"repos": 1},
            "feature_units": list(units), "activity_daily": list(activity),
            "adoption": [], "cost_daily": list(cost)}
    return snap_mod.finalize_payload(body, captured)


def cost_row(d, usd):
    return {"date": d, "input_tokens": 1, "output_tokens": 1,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "cost_usd": usd, "by_model": {"m": usd}}


def test_cost_endpoint_empty(client):
    login(client)
    data = client.get("/api/cost").json()
    assert data["available"] is False
    assert data["days"] == [] and data["total_usd_window"] == 0


def test_cost_endpoint_windows_and_totals(client):
    today = date.today()
    old = (today - timedelta(days=120)).isoformat()
    recent = today.isoformat()
    client.post("/api/ingest", json=payload_with(
        cost=[cost_row(old, 9.0), cost_row(recent, 2.5)]), headers=AUTH)
    login(client)
    data = client.get("/api/cost?weeks=4").json()
    assert data["available"] is True
    assert [d["date"] for d in data["days"]] == [recent]
    assert data["total_usd_window"] == 2.5
    assert data["by_model_window"] == {"m": 2.5}
    assert sum(w["cost_usd"] for w in data["weekly"]) == 2.5
    assert len(data["weekly"]) == 4


def test_overview_tiles_fill_when_data_exists(client):
    today = date.today().isoformat()
    client.post("/api/ingest", json=payload_with(
        activity=[{"date": today, "commits": 1, "by_repo": {"a": 1},
                   "sessions": 2, "prompts": 10}],
        cost=[cost_row(today, 1.25)]), headers=AUTH)
    login(client)
    tiles = client.get("/api/overview").json()["tiles"]
    assert tiles["sessions_this_week"] == 2
    assert tiles["cost_this_week"] == 1.25


def test_overview_tiles_null_without_usage_data(client):
    login(client)
    tiles = client.get("/api/overview").json()["tiles"]
    assert tiles["sessions_this_week"] is None
    assert tiles["cost_this_week"] is None


def test_units_this_week_excludes_commit_clusters(client):
    today = date.today()
    monday = (today - timedelta(days=today.weekday())).isoformat()
    units = [
        {"key": "c1:m", "kind": "commits", "repo": "a", "date": monday,
         "title": "a cluster", "tags": ["auth"], "complexity": 2,
         "summary": "s", "model": "m"},
        {"key": "s1:m", "kind": "spec", "repo": "a", "date": monday,
         "title": "real", "tags": ["auth"], "complexity": 3,
         "summary": "s", "model": "m"},
    ]
    client.post("/api/ingest", json=payload_with(units=units), headers=AUTH)
    login(client)
    assert client.get("/api/overview").json()["tiles"]["units_this_week"] == 1


def test_activity_sessions_available_and_weekly_sessions(client):
    today = date.today().isoformat()
    client.post("/api/ingest", json=payload_with(
        activity=[{"date": today, "commits": 1, "by_repo": {"a": 1},
                   "sessions": 3, "prompts": 12}]), headers=AUTH)
    login(client)
    data = client.get("/api/activity?weeks=4").json()
    assert data["sessions_available"] is True
    assert data["weeks"][-1]["sessions"] == 3


def test_activity_sessions_unavailable_without_usage(client):
    login(client)
    data = client.get("/api/activity").json()
    assert data["sessions_available"] is False
    assert "sessions" not in data["weeks"][-1]
