import pytest

from shared import snapshot

SERVICE_ROW = {"capture_date": "2026-08-12", "period_start": "2026-07-27",
               "app": "b2b-ai-news", "service_id": "svc-db",
               "service_name": "Postgres", "cumulative_usd": 1.860756,
               "memory_usd": 1.754675, "cpu_usd": 0.078439,
               "egress_usd": 0.0, "volume_usd": 0.027641, "backup_usd": 0.0}

V4_BODY = {
    "schema_version": 4,
    "sweep": {"repos": 1},
    "feature_units": [],
    "activity_daily": [],
    "adoption": [],
    "cost_daily": [],
    "infra_usage": [],
    "infra_usage_services": [SERVICE_ROW],
}


def fin(body):
    return snapshot.finalize_payload(dict(body), "2026-08-12T07:30:00+00:00")


def test_schema_version_is_4():
    assert snapshot.SCHEMA_VERSION == 4


def test_v4_valid():
    snapshot.validate_payload(fin(V4_BODY))


def test_v3_still_valid_without_services():
    v3 = {k: v for k, v in V4_BODY.items() if k != "infra_usage_services"}
    v3["schema_version"] = 3
    snapshot.validate_payload(fin(v3))


def test_v2_still_valid():
    v2 = {k: v for k, v in V4_BODY.items()
          if k not in ("infra_usage_services", "infra_usage")}
    v2["schema_version"] = 2
    snapshot.validate_payload(fin(v2))


def test_v1_still_valid():
    v1 = {k: v for k, v in V4_BODY.items()
          if k not in ("infra_usage_services", "infra_usage", "cost_daily")}
    v1["schema_version"] = 1
    snapshot.validate_payload(fin(v1))


def test_v3_with_services_rejected():
    v3 = dict(V4_BODY)
    v3["schema_version"] = 3
    with pytest.raises(ValueError, match="infra_usage_services"):
        snapshot.validate_payload(fin(v3))


def test_v4_missing_services_rejected():
    v4 = {k: v for k, v in V4_BODY.items() if k != "infra_usage_services"}
    with pytest.raises(ValueError, match="infra_usage_services"):
        snapshot.validate_payload(fin(v4))


def test_v4_bad_service_row():
    v4 = dict(V4_BODY)
    v4["infra_usage_services"] = [{"app": "x"}]
    with pytest.raises(ValueError, match="capture_date"):
        snapshot.validate_payload(fin(v4))


def test_v4_service_dollars_must_be_number():
    v4 = dict(V4_BODY)
    v4["infra_usage_services"] = [dict(SERVICE_ROW, memory_usd=True)]
    with pytest.raises(ValueError, match="memory_usd"):
        snapshot.validate_payload(fin(v4))


def test_v5_rejected():
    v5 = dict(V4_BODY)
    v5["schema_version"] = 5
    with pytest.raises(ValueError, match="schema_version"):
        snapshot.validate_payload(fin(v5))
