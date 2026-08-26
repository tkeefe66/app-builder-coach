from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.coach_web import models
from shared import snapshot as snap_mod
from tests.web.test_ingest import AUTH


def v2_payload(cost_usd=0.5, sessions=3):
    body = {
        "schema_version": 2,
        "sweep": {"repos": 1},
        "feature_units": [],
        "activity_daily": [{"date": "2026-08-01", "commits": 2,
                            "by_repo": {"a": 2}, "sessions": sessions,
                            "prompts": 40}],
        "adoption": [],
        "cost_daily": [{"date": "2026-08-01", "input_tokens": 10,
                        "output_tokens": 5, "cache_read_tokens": 0,
                        "cache_creation_tokens": 0, "cost_usd": cost_usd,
                        "by_model": {"claude-sonnet-5": cost_usd}}],
    }
    return snap_mod.finalize_payload(body, f"2026-08-03T0{sessions}:00:00+00:00")


def test_v2_ingest_stores_cost_and_sessions(client):
    resp = client.post("/api/ingest", json=v2_payload(), headers=AUTH)
    assert resp.status_code == 200
    with Session(client.app.state.engine) as s:
        cost = s.get(models.CostDaily, "2026-08-01")
        assert cost.cost_usd == 0.5
        act = s.get(models.ActivityDaily, "2026-08-01")
        assert act.sessions == 3 and act.prompts == 40


def test_v2_ingest_upserts_cost(client):
    client.post("/api/ingest", json=v2_payload(cost_usd=0.5), headers=AUTH)
    client.post("/api/ingest", json=v2_payload(cost_usd=0.9, sessions=4), headers=AUTH)
    with Session(client.app.state.engine) as s:
        assert len(s.scalars(select(models.CostDaily)).all()) == 1
        assert s.get(models.CostDaily, "2026-08-01").cost_usd == 0.9
        assert s.get(models.ActivityDaily, "2026-08-01").sessions == 4


def test_v1_payload_still_ingests(client):
    from tests.web.test_ingest import make_payload
    resp = client.post("/api/ingest", json=make_payload(), headers=AUTH)
    assert resp.status_code == 200
    with Session(client.app.state.engine) as s:
        act = s.get(models.ActivityDaily, "2026-08-01")
        assert act.sessions is None  # untouched by v1
