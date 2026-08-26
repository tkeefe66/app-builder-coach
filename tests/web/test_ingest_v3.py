from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.coach_web import models
from shared import snapshot as snap_mod
from tests.web.test_ingest import AUTH


def v3_payload(usd=2.885146, capture="2026-08-11"):
    body = {
        "schema_version": 3,
        "sweep": {"repos": 1},
        "feature_units": [],
        "activity_daily": [],
        "adoption": [],
        "cost_daily": [],
        "infra_usage": [{"capture_date": capture, "period_start": "2026-07-27",
                         "app": "b2b-ai-news", "cumulative_usd": usd}],
    }
    return snap_mod.finalize_payload(body, f"2026-08-11T0{len(capture) % 9}:00:00+00:00")


def test_v3_ingest_stores_infra(client):
    resp = client.post("/api/ingest", json=v3_payload(), headers=AUTH)
    assert resp.status_code == 200
    with Session(client.app.state.engine) as s:
        row = s.get(models.InfraUsage, ("2026-07-27", "b2b-ai-news", "2026-08-11"))
        assert row.cumulative_usd == 2.885146


def test_v3_ingest_upserts_same_capture(client):
    client.post("/api/ingest", json=v3_payload(usd=2.0), headers=AUTH)
    client.post("/api/ingest", json=v3_payload(usd=3.5), headers=AUTH)
    with Session(client.app.state.engine) as s:
        rows = s.scalars(select(models.InfraUsage)).all()
        assert len(rows) == 1
        assert rows[0].cumulative_usd == 3.5


def test_v3_ingest_keeps_separate_capture_dates(client):
    client.post("/api/ingest", json=v3_payload(usd=2.0, capture="2026-08-10"), headers=AUTH)
    client.post("/api/ingest", json=v3_payload(usd=3.5, capture="2026-08-11"), headers=AUTH)
    with Session(client.app.state.engine) as s:
        assert len(s.scalars(select(models.InfraUsage)).all()) == 2


def test_v1_payload_still_ingests(client):
    from tests.web.test_ingest import make_payload
    assert client.post("/api/ingest", json=make_payload(), headers=AUTH).status_code == 200
