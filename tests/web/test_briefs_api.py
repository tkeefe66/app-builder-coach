from sqlalchemy.orm import Session

from apps.coach_web import models


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def _add(client, day, kind, status="ok", body="s", targets=()):
    with Session(client.app.state.engine) as db:
        row = models.Brief(created_at=f"{day}T07:00:00+00:00", kind=kind,
                           day=day, model="m", status=status, body=body)
        db.add(row)
        db.commit()
        for i, t in enumerate(targets):
            db.add(models.BriefRecommendation(brief_id=row.id, ord=i,
                                              title=f"Do {t}", kind="tag",
                                              target=t, why="w", evidence="e"))
        db.commit()
        return row.id


def test_briefs_requires_login(client):
    assert client.get("/api/briefs").status_code == 401


def test_briefs_empty(client):
    login(client)
    body = client.get("/api/briefs").json()
    assert body["assessment"] is None
    assert body["deltas"] == []
    assert body["history"] == []
    assert body["recurring"] == []


def test_assessment_carries_its_recommendations(client):
    login(client)
    _add(client, "2026-08-01", "assessment", targets=("deploy-docker",))
    body = client.get("/api/briefs").json()
    assert body["assessment"]["day"] == "2026-08-01"
    assert body["assessment"]["recommendations"][0]["target"] == "deploy-docker"
    assert body["assessment"]["stale"] is False


def test_deltas_are_those_after_the_assessment(client):
    login(client)
    _add(client, "2026-08-01", "delta", body="old news")
    _add(client, "2026-08-05", "assessment", body="standing read")
    _add(client, "2026-08-09", "delta", body="new news")
    body = client.get("/api/briefs").json()
    assert [d["summary"] for d in body["deltas"]] == ["new news"]
    assert [h["summary"] for h in body["history"]] == ["old news"]


def test_a_failed_assessment_keeps_the_last_good_one_flagged_stale(client):
    login(client)
    _add(client, "2026-08-01", "assessment", body="standing read")
    _add(client, "2026-08-09", "assessment", status="failed", body="")
    body = client.get("/api/briefs").json()
    assert body["assessment"]["summary"] == "standing read"
    assert body["assessment"]["stale"] is True


def test_recurring_rollup_counts_repeats(client):
    login(client)
    _add(client, "2026-08-01", "assessment", targets=("deploy-docker",))
    _add(client, "2026-08-05", "delta", targets=("deploy-docker",))
    body = client.get("/api/briefs").json()
    entry = body["recurring"][0]
    assert entry["target"] == "deploy-docker"
    assert entry["times"] == 2


def test_legacy_prose_briefs_still_render(client):
    # The ten pre-existing rows have kind "delta", no day, no recommendations.
    # The day assertion pins the `row.day or row.created_at[:10]` fallback in
    # _brief_json: deleting that fallback must turn this test red.
    login(client)
    with Session(client.app.state.engine) as db:
        db.add(models.Brief(created_at="2026-08-12T07:00:00+00:00", model="m",
                            status="ok", body="a wall of prose"))
        db.commit()
    body = client.get("/api/briefs").json()
    assert body["assessment"] is None
    assert body["history"][0]["summary"] == "a wall of prose"
    assert body["history"][0]["day"] == "2026-08-12"
    assert body["history"][0]["recommendations"] == []
