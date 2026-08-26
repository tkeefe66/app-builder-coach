from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.coach_web import models

AUTH = {"Authorization": "Bearer test-usage-token"}


def body(app="app-builder-coach", model="claude-haiku-4-5", ts="2026-08-11T14:00:00Z",
         inp=1_000_000, out=0):
    return {"app": app, "model": model, "ts": ts,
            "input_tokens": inp, "output_tokens": out,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}


def test_usage_requires_token(client):
    assert client.post("/api/usage", json=body()).status_code == 401


def test_usage_rejects_bad_token(client):
    resp = client.post("/api/usage", json=body(),
                       headers={"Authorization": "Bearer nope"})
    assert resp.status_code == 401


def test_usage_stores_priced_row(client):
    resp = client.post("/api/usage", json=body(), headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["date"] == "2026-08-11"
    with Session(client.app.state.engine) as s:
        row = s.get(models.LlmDaily, ("2026-08-11", "app-builder-coach", "claude-haiku-4-5"))
        assert row.input_tokens == 1_000_000
        assert row.cost_usd == 1.0          # 1 MTok haiku input at $1.00/MTok
        assert row.call_count == 1


def test_usage_aggregates_same_day_model(client):
    client.post("/api/usage", json=body(), headers=AUTH)
    client.post("/api/usage", json=body(inp=0, out=1_000_000), headers=AUTH)
    with Session(client.app.state.engine) as s:
        rows = s.scalars(select(models.LlmDaily)).all()
        assert len(rows) == 1
        assert rows[0].call_count == 2
        assert rows[0].input_tokens == 1_000_000
        assert rows[0].output_tokens == 1_000_000
        assert rows[0].cost_usd == 6.0      # $1.00 input + $5.00 haiku output


def test_usage_unknown_app_rejected(client):
    resp = client.post("/api/usage", json=body(app="not-a-real-app"), headers=AUTH)
    assert resp.status_code == 400
    assert "not-a-real-app" in resp.json()["detail"]


def test_usage_missing_key_rejected(client):
    payload = body()
    del payload["input_tokens"]
    resp = client.post("/api/usage", json=payload, headers=AUTH)
    assert resp.status_code == 400
    assert "input_tokens" in resp.json()["detail"]


def test_usage_extra_key_rejected(client):
    resp = client.post("/api/usage", json=dict(body(), sneaky=1), headers=AUTH)
    assert resp.status_code == 400
    assert "sneaky" in resp.json()["detail"]


def test_usage_bad_timestamp_rejected(client):
    resp = client.post("/api/usage", json=body(ts="nope"), headers=AUTH)
    assert resp.status_code == 400
    assert "ts" in resp.json()["detail"]
