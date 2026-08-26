import pytest

from shared import snapshot

V3_BODY = {
    "schema_version": 3,
    "sweep": {"repos": 1},
    "feature_units": [],
    "activity_daily": [{"date": "2026-08-01", "commits": 2, "by_repo": {"a": 2}}],
    "adoption": [],
    "cost_daily": [],
    "infra_usage": [{"capture_date": "2026-08-11", "period_start": "2026-07-27",
                     "app": "b2b-ai-news", "cumulative_usd": 2.885146}],
}


def fin(body):
    return snapshot.finalize_payload(dict(body), "2026-08-11T07:30:00+00:00")


def test_v3_valid():
    snapshot.validate_payload(fin(V3_BODY))


def test_v2_still_valid_without_infra():
    v2 = {k: v for k, v in V3_BODY.items() if k != "infra_usage"}
    v2["schema_version"] = 2
    snapshot.validate_payload(fin(v2))


def test_v1_still_valid():
    v1 = {k: v for k, v in V3_BODY.items()
          if k not in ("infra_usage", "cost_daily")}
    v1["schema_version"] = 1
    snapshot.validate_payload(fin(v1))


def test_v2_with_infra_rejected():
    v2 = dict(V3_BODY)
    v2["schema_version"] = 2
    with pytest.raises(ValueError, match="infra_usage"):
        snapshot.validate_payload(fin(v2))


def test_v3_missing_infra_rejected():
    v3 = {k: v for k, v in V3_BODY.items() if k != "infra_usage"}
    with pytest.raises(ValueError, match="infra_usage"):
        snapshot.validate_payload(fin(v3))


def test_v3_bad_infra_row():
    v3 = dict(V3_BODY)
    v3["infra_usage"] = [{"app": "x"}]
    with pytest.raises(ValueError, match="capture_date"):
        snapshot.validate_payload(fin(v3))


def test_v3_infra_cost_must_be_number():
    v3 = dict(V3_BODY)
    v3["infra_usage"] = [dict(V3_BODY["infra_usage"][0], cumulative_usd=True)]
    with pytest.raises(ValueError, match="cumulative_usd"):
        snapshot.validate_payload(fin(v3))


def test_v5_rejected():
    v5 = dict(V3_BODY)
    v5["schema_version"] = 5
    with pytest.raises(ValueError, match="schema_version"):
        snapshot.validate_payload(fin(v5))
