from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.coach_web import models
from shared import snapshot as snap_mod
from tests.web.test_ingest import AUTH


def v4_payload(memory=1.75, capture="2026-08-12"):
    body = {
        "schema_version": 4, "sweep": {"repos": 1}, "feature_units": [],
        "activity_daily": [], "adoption": [], "cost_daily": [], "infra_usage": [],
        "infra_usage_services": [
            {"capture_date": capture, "period_start": "2026-07-27",
             "app": "b2b-ai-news", "service_id": "svc-db", "service_name": "Postgres",
             "cumulative_usd": 1.86, "memory_usd": memory, "cpu_usd": 0.08,
             "egress_usd": 0.0, "volume_usd": 0.03, "backup_usd": 0.0}],
    }
    return snap_mod.finalize_payload(body, f"2026-08-12T0{len(capture) % 9}:00:00+00:00")


def test_v4_ingest_stores_service_row(client):
    assert client.post("/api/ingest", json=v4_payload(), headers=AUTH).status_code == 200
    with Session(client.app.state.engine) as s:
        row = s.get(models.InfraServiceUsage,
                    ("2026-07-27", "b2b-ai-news", "svc-db", "2026-08-12"))
        assert row.service_name == "Postgres"
        assert row.memory_usd == 1.75
        assert row.cumulative_usd == 1.86


def test_v4_ingest_upserts_same_capture(client):
    client.post("/api/ingest", json=v4_payload(memory=1.0), headers=AUTH)
    client.post("/api/ingest", json=v4_payload(memory=2.0), headers=AUTH)
    with Session(client.app.state.engine) as s:
        rows = s.scalars(select(models.InfraServiceUsage)).all()
        assert len(rows) == 1 and rows[0].memory_usd == 2.0


def test_v4_ingest_keeps_separate_captures(client):
    client.post("/api/ingest", json=v4_payload(capture="2026-08-11"), headers=AUTH)
    client.post("/api/ingest", json=v4_payload(capture="2026-08-12"), headers=AUTH)
    with Session(client.app.state.engine) as s:
        assert len(s.scalars(select(models.InfraServiceUsage)).all()) == 2


def test_v3_payload_still_ingests(client):
    from tests.web.test_ingest_v3 import v3_payload
    assert client.post("/api/ingest", json=v3_payload(), headers=AUTH).status_code == 200
