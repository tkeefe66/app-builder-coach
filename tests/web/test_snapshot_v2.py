import pytest

from shared import snapshot

V2_BODY = {
    "schema_version": 2,
    "sweep": {"repos": 1},
    "feature_units": [],
    "activity_daily": [{"date": "2026-08-01", "commits": 2,
                        "by_repo": {"a": 2}, "sessions": 3, "prompts": 40}],
    "adoption": [],
    "cost_daily": [{"date": "2026-08-01", "input_tokens": 5, "output_tokens": 2,
                    "cache_read_tokens": 100, "cache_creation_tokens": 10,
                    "cost_usd": 0.12, "by_model": {"claude-sonnet-5": 0.12}}],
}


def fin(body):
    return snapshot.finalize_payload(dict(body), "2026-08-03T07:30:00+00:00")


def test_v2_valid():
    snapshot.validate_payload(fin(V2_BODY))


def test_v1_still_valid_without_cost():
    v1 = {k: v for k, v in V2_BODY.items() if k != "cost_daily"}
    v1["schema_version"] = 1
    v1["activity_daily"] = [{"date": "2026-08-01", "commits": 2, "by_repo": {"a": 2}}]
    snapshot.validate_payload(fin(v1))


def test_v1_with_cost_daily_rejected():
    v1 = dict(V2_BODY)
    v1["schema_version"] = 1
    with pytest.raises(ValueError, match="cost_daily"):
        snapshot.validate_payload(fin(v1))


def test_v2_missing_cost_daily_rejected():
    v2 = {k: v for k, v in V2_BODY.items() if k != "cost_daily"}
    with pytest.raises(ValueError, match="cost_daily"):
        snapshot.validate_payload(fin(v2))


def test_v2_bad_cost_row():
    v2 = dict(V2_BODY)
    v2["cost_daily"] = [{"date": "2026-08-01"}]
    with pytest.raises(ValueError, match="input_tokens"):
        snapshot.validate_payload(fin(v2))


def test_v5_rejected():
    v5 = dict(V2_BODY)
    v5["schema_version"] = 5
    with pytest.raises(ValueError, match="schema_version"):
        snapshot.validate_payload(fin(v5))
