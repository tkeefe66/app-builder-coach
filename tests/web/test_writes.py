ORIGIN = {"Origin": "https://testserver"}


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def test_goals_require_login(client):
    assert client.post("/api/goals", json={}, headers=ORIGIN).status_code == 401


def test_create_and_list_a_goal(client):
    login(client)
    resp = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "title": "Ship auth",
        "target_date": "2026-09-01"})
    assert resp.status_code == 200
    created = resp.json()
    assert created["status"] == "active" and created["id"] >= 1
    listed = client.get("/api/goals").json()["goals"]
    assert [g["title"] for g in listed] == ["Ship auth"]


def test_goal_rejects_bad_kind(client):
    login(client)
    resp = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "nonsense", "target": "auth", "title": "x"})
    assert resp.status_code == 422


def test_goal_rejects_overlong_title(client):
    login(client)
    resp = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "title": "x" * 500})
    assert resp.status_code == 422


def test_patch_goal_status(client):
    login(client)
    gid = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "title": "Ship auth"}).json()["id"]
    assert client.patch(f"/api/goals/{gid}", headers=ORIGIN,
                        json={"status": "done"}).status_code == 200
    assert client.get("/api/goals").json()["goals"][0]["status"] == "done"


def test_patch_missing_goal_is_404(client):
    login(client)
    assert client.patch("/api/goals/999", headers=ORIGIN,
                        json={"status": "done"}).status_code == 404


def test_delete_goal(client):
    login(client)
    gid = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "title": "Ship auth"}).json()["id"]
    assert client.delete(f"/api/goals/{gid}", headers=ORIGIN).status_code == 200
    assert client.get("/api/goals").json()["goals"] == []
    assert client.delete(f"/api/goals/{gid}", headers=ORIGIN).status_code == 404


def test_creating_a_goal_twice_is_idempotent(client):
    # Two clicks of "Add as goal" must not produce two identical active goals.
    login(client)
    body = {"kind": "tag", "target": "auth", "title": "Ship auth"}
    first = client.post("/api/goals", headers=ORIGIN, json=body).json()
    second = client.post("/api/goals", headers=ORIGIN, json=body)
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
    assert len(client.get("/api/goals").json()["goals"]) == 1


def test_goal_write_rejects_cross_origin(client):
    login(client)
    resp = client.post("/api/goals", headers={"Origin": "https://evil.example"},
                       json={"kind": "tag", "target": "auth", "title": "x"})
    assert resp.status_code == 403


def test_goal_get_needs_no_origin(client):
    # Browsers omit Origin on same-origin GET; reads must not require it.
    login(client)
    assert client.get("/api/goals").status_code == 200


def test_notes_crud_and_filtering(client):
    login(client)
    client.post("/api/notes", headers=ORIGIN, json={
        "subject_kind": "tag", "subject_id": "auth", "body": "tag note"})
    client.post("/api/notes", headers=ORIGIN, json={
        "subject_kind": "brief", "subject_id": "1", "body": "brief note"})
    everything = client.get("/api/notes").json()["notes"]
    assert len(everything) == 2
    filtered = client.get("/api/notes?subject_kind=tag&subject_id=auth").json()["notes"]
    assert [n["body"] for n in filtered] == ["tag note"]
    nid = filtered[0]["id"]
    assert client.delete(f"/api/notes/{nid}", headers=ORIGIN).status_code == 200
    assert len(client.get("/api/notes").json()["notes"]) == 1


def test_note_rejects_bad_subject_kind(client):
    login(client)
    resp = client.post("/api/notes", headers=ORIGIN, json={
        "subject_kind": "nonsense", "subject_id": "x", "body": "y"})
    assert resp.status_code == 422


def test_dismissal_create_list_delete(client):
    login(client)
    made = client.post("/api/dismissals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "reason": "not now"}).json()
    assert made["target"] == "auth"
    assert len(client.get("/api/dismissals").json()["dismissals"]) == 1
    assert client.delete(f"/api/dismissals/{made['id']}",
                         headers=ORIGIN).status_code == 200
    assert client.get("/api/dismissals").json()["dismissals"] == []


def test_dismissal_is_idempotent(client):
    login(client)
    first = client.post("/api/dismissals", headers=ORIGIN,
                        json={"kind": "tag", "target": "auth"}).json()
    second = client.post("/api/dismissals", headers=ORIGIN,
                         json={"kind": "tag", "target": "auth"})
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
    assert len(client.get("/api/dismissals").json()["dismissals"]) == 1


def test_dismissal_rejects_bad_kind(client):
    login(client)
    assert client.post("/api/dismissals", headers=ORIGIN, json={
        "kind": "brief", "target": "x"}).status_code == 422


def test_checkoff_create_list_delete(client):
    login(client)
    assert client.post("/api/checkoffs", headers=ORIGIN, json={
        "feature_name": "plan mode"}).status_code == 200
    assert client.get("/api/checkoffs").json()["checkoffs"][0]["feature_name"] == "plan mode"
    assert client.delete("/api/checkoffs/plan mode",
                         headers=ORIGIN).status_code == 200
    assert client.get("/api/checkoffs").json()["checkoffs"] == []


def test_checkoff_is_idempotent(client):
    login(client)
    client.post("/api/checkoffs", headers=ORIGIN, json={"feature_name": "plan mode"})
    second = client.post("/api/checkoffs", headers=ORIGIN,
                         json={"feature_name": "plan mode"})
    assert second.status_code == 200
    assert len(client.get("/api/checkoffs").json()["checkoffs"]) == 1


def test_delete_missing_checkoff_is_404(client):
    login(client)
    assert client.delete("/api/checkoffs/nope", headers=ORIGIN).status_code == 404


from sqlalchemy.orm import Session

from apps.coach_web import models


def _seed_recommendation(client, target="deploy-docker", kind="tag"):
    """Insert a brief + open recommendation directly, via the app's engine."""
    with Session(client.app.state.engine) as db:
        b = models.Brief(created_at="2026-08-01T07:00:00+00:00", kind="assessment",
                         day="2026-08-01", model="m", status="ok", body="s")
        db.add(b)
        db.commit()
        rec = models.BriefRecommendation(brief_id=b.id, ord=0, title="t",
                                         kind=kind, target=target, why="w",
                                         evidence="e")
        db.add(rec)
        db.commit()
        return rec.id


def _outcome(client, rec_id):
    with Session(client.app.state.engine) as db:
        return db.get(models.BriefRecommendation, rec_id).outcome


def test_creating_a_goal_converts_matching_recommendations(client):
    login(client)
    rec_id = _seed_recommendation(client)
    resp = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "deploy-docker", "title": "Containerize"})
    assert resp.status_code == 200
    assert _outcome(client, rec_id) == "converted"


def test_creating_an_unrelated_goal_leaves_recommendations_open(client):
    login(client)
    rec_id = _seed_recommendation(client)
    client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "scraping", "title": "Scrape"})
    assert _outcome(client, rec_id) == "open"


def test_dismissing_a_target_dismisses_its_recommendations(client):
    login(client)
    rec_id = _seed_recommendation(client)
    resp = client.post("/api/dismissals", headers=ORIGIN, json={
        "kind": "tag", "target": "deploy-docker", "reason": "not now"})
    assert resp.status_code == 200
    assert _outcome(client, rec_id) == "dismissed"


def test_dismissing_twice_still_marks_recommendations(client):
    # The idempotent early-return path must mark too, or a second tab's
    # dismissal silently leaves the recommendation open.
    login(client)
    body = {"kind": "tag", "target": "deploy-docker", "reason": ""}
    client.post("/api/dismissals", headers=ORIGIN, json=body)
    rec_id = _seed_recommendation(client)
    client.post("/api/dismissals", headers=ORIGIN, json=body)
    assert _outcome(client, rec_id) == "dismissed"


def test_reassess_requires_login(client):
    assert client.post("/api/reassess", headers=ORIGIN,
                       json={}).status_code == 401


def test_reassess_rejects_a_foreign_origin(client):
    login(client)
    resp = client.post("/api/reassess", json={},
                       headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
