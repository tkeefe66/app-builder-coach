from datetime import date, timedelta

from fastapi.testclient import TestClient

from apps.coach_web.auth import hash_password
from apps.coach_web.config import Settings
from apps.coach_web.main import create_app
from shared import snapshot as snap_mod
from tests.web.test_ingest import AUTH


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def _iso(d):
    return d.isoformat()


def make_rich_payload(today):
    monday = today - timedelta(days=today.weekday())
    old = today - timedelta(days=200)
    body = {
        "schema_version": 1,
        "sweep": {"repos": 2, "new_commits": 4},
        "feature_units": [
            {"key": "u1:m", "kind": "spec", "repo": "alpha", "date": _iso(monday),
             "title": "this week", "tags": ["auth"], "complexity": 4,
             "summary": "s", "model": "m"},
            {"key": "u2:m", "kind": "spec", "repo": "alpha", "date": _iso(old),
             "title": "old", "tags": ["caching"], "complexity": 2,
             "summary": "s", "model": "m"},
        ],
        "activity_daily": [
            {"date": _iso(monday), "commits": 3, "by_repo": {"alpha": 3}},
            {"date": _iso(monday + timedelta(days=1)), "commits": 2,
             "by_repo": {"beta": 2}},
        ],
        "adoption": [
            {"name": "plan mode", "lesson": "09-advanced-features",
             "status": "never-touched", "last_used": None},
            {"name": "MCP servers", "lesson": "05-mcp",
             "status": "used", "last_used": _iso(monday)},
        ],
    }
    return snap_mod.finalize_payload(body, f"{_iso(today)}T07:30:00+00:00")


def test_api_docs_are_disabled():
    # Explicit spa_dist=None: with no SPA dist configured, docs endpoints
    # must 404 rather than fall through to a catch-all route.
    settings = Settings(database_url="sqlite+pysqlite:///:memory:",
                        ingest_token="t", password_hash=hash_password("p"),
                        secret_key="s")
    app = create_app(settings, spa_dist=None)
    no_dist_client = TestClient(app, base_url="https://testserver")
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert no_dist_client.get(path).status_code == 404, path


def test_all_read_endpoints_require_login(client):
    for path in ("/api/overview", "/api/capabilities",
                 "/api/activity", "/api/adoption/board"):
        assert client.get(path).status_code == 401, path


def test_overview(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    data = client.get("/api/overview").json()
    assert data["freshness"]["captured_at"].startswith(_iso(today))
    assert data["tiles"]["units_this_week"] == 1
    assert data["tiles"]["commits_this_week"] == 5
    assert data["tiles"]["streak_days"] >= 1
    assert data["tiles"]["sessions_this_week"] is None
    assert data["tiles"]["cost_this_week"] is None
    assert "websockets-sse" in data["never_built"]
    assert "auth" not in data["never_built"]
    assert {"tag": "caching", "last_done": _iso(today - timedelta(days=200))} in data["stale"]
    assert data["adoption_gaps"] == ["plan mode"]


def test_overview_empty_db(client):
    login(client)
    data = client.get("/api/overview").json()
    assert data["freshness"] == {"captured_at": None, "received_at": None}
    assert data["tiles"]["units_this_week"] == 0
    assert data["tiles"]["streak_days"] == 0


def test_capabilities(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    data = client.get("/api/capabilities").json()
    tags = {t["tag"]: t for t in data["tags"]}
    assert tags["auth"]["count"] == 1
    assert tags["auth"]["avg_complexity"] == 4.0
    assert len(tags["auth"]["monthly"]) == 12
    assert tags["auth"]["monthly"][-1]["count"] == 1
    assert "websockets-sse" in data["never_built"]


def test_activity(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    data = client.get("/api/activity?weeks=4").json()
    assert len(data["weeks"]) == 4
    assert data["weeks"][-1]["commits"] == 5
    assert sum(data["weekday_totals"]) == 5
    assert data["streak"]["days"] >= 1
    assert data["sessions_available"] is False


def test_activity_weekday_totals_windowed_to_weeks(client):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    old = today - timedelta(days=60)
    body = {
        "schema_version": 1,
        "sweep": {"repos": 1, "new_commits": 10},
        "feature_units": [],
        "activity_daily": [
            {"date": _iso(monday), "commits": 3, "by_repo": {"alpha": 3}},
            {"date": _iso(monday + timedelta(days=1)), "commits": 2,
             "by_repo": {"beta": 2}},
            {"date": _iso(old), "commits": 7, "by_repo": {"alpha": 7}},
        ],
        "adoption": [],
    }
    payload = snap_mod.finalize_payload(body, f"{_iso(today)}T07:30:00+00:00")
    client.post("/api/ingest", json=payload, headers=AUTH)
    login(client)
    data = client.get("/api/activity?weeks=4").json()
    # old day (60 days ago) is outside the 4-week window: weekday_totals
    # must only reflect the in-window commits, not the old day's 7.
    assert sum(data["weekday_totals"]) == 5
    # weeks rollup was already windowed correctly before this fix.
    assert sum(w["commits"] for w in data["weeks"]) == 5
    # streak must still be computed on the FULL history, not truncated
    # by the display window.
    assert data["streak"]["days"] >= 1
    assert data["streak"]["last_active"] == _iso(monday + timedelta(days=1))


def test_adoption_board(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    data = client.get("/api/adoption/board").json()
    by_name = {f["name"]: f for f in data["features"]}
    assert by_name["plan mode"]["status"] == "never-touched"
    assert by_name["MCP servers"]["status"] == "used"
    assert len(by_name["plan mode"]["history"]) == 1
    lessons = [f["lesson"] for f in data["features"]]
    assert lessons == sorted(lessons)


ORIGIN = {"Origin": "https://testserver"}


def test_adoption_board_feature_without_dismissal_reports_not_dismissed(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    by_name = {f["name"]: f for f in client.get("/api/adoption/board").json()["features"]}
    assert by_name["plan mode"]["dismissed"] is False
    assert by_name["plan mode"]["dismissal_id"] is None


def test_adoption_board_feature_with_dismissal_reports_dismissed(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    made = client.post("/api/dismissals", headers=ORIGIN,
                       json={"kind": "feature", "target": "plan mode"}).json()
    by_name = {f["name"]: f for f in client.get("/api/adoption/board").json()["features"]}
    assert by_name["plan mode"]["dismissed"] is True
    assert by_name["plan mode"]["dismissal_id"] == made["id"]
    # A dismissal of a different kind (e.g. a tag) must not leak onto a feature.
    assert by_name["MCP servers"]["dismissed"] is False


def test_adoption_board_dismissal_does_not_change_status(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    before = {f["name"]: f for f in client.get("/api/adoption/board").json()["features"]}
    client.post("/api/dismissals", headers=ORIGIN,
               json={"kind": "feature", "target": "plan mode"})
    after = {f["name"]: f for f in client.get("/api/adoption/board").json()["features"]}
    assert after["plan mode"]["status"] == before["plan mode"]["status"] == "never-touched"
    assert (after["plan mode"]["detected_status"]
           == before["plan mode"]["detected_status"] == "never-touched")
