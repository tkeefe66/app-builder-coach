import pytest

from shared import snapshot

BODY = {
    "schema_version": 1,
    "sweep": {"repos": 2, "new_commits": 5},
    "feature_units": [{"key": "abc:m", "kind": "commits", "repo": "r",
                       "date": "2026-08-01", "title": "r 2026-08",
                       "tags": ["auth"], "complexity": 3, "summary": "s",
                       "model": "heuristics"}],
    "activity_daily": [{"date": "2026-08-01", "commits": 5, "by_repo": {"r": 5}}],
    "adoption": [{"name": "plan mode", "lesson": "09-advanced-features",
                  "status": "never-touched", "last_used": None}],
}


def test_finalize_adds_hash_and_timestamp():
    p = snapshot.finalize_payload(dict(BODY), captured_at="2026-08-02T07:30:00+00:00")
    assert p["captured_at"] == "2026-08-02T07:30:00+00:00"
    assert len(p["content_hash"]) == 64
    snapshot.validate_payload(p)  # no raise


def test_hash_ignores_captured_at():
    a = snapshot.finalize_payload(dict(BODY), captured_at="2026-08-02T07:30:00+00:00")
    b = snapshot.finalize_payload(dict(BODY), captured_at="2026-08-03T07:30:00+00:00")
    assert a["content_hash"] == b["content_hash"]


def test_validate_rejects_wrong_version():
    p = snapshot.finalize_payload({**BODY, "schema_version": 5}, "2026-08-02T07:30:00+00:00")
    with pytest.raises(ValueError, match="schema_version"):
        snapshot.validate_payload(p)


def test_validate_rejects_tampered_payload():
    p = snapshot.finalize_payload(dict(BODY), "2026-08-02T07:30:00+00:00")
    p["feature_units"] = []
    with pytest.raises(ValueError, match="content_hash"):
        snapshot.validate_payload(p)


def test_validate_rejects_missing_key():
    p = snapshot.finalize_payload(dict(BODY), "2026-08-02T07:30:00+00:00")
    del p["adoption"]
    with pytest.raises(ValueError, match="adoption"):
        snapshot.validate_payload(p)


# --- nested item shapes -------------------------------------------------
# These bodies are re-finalized so the content_hash is correct: the point is
# that a well-signed payload with a malformed row is still rejected.

def _finalized(**overrides):
    return snapshot.finalize_payload({**BODY, **overrides},
                                     "2026-08-02T07:30:00+00:00")


def _unit(**overrides):
    return {**BODY["feature_units"][0], **overrides}


def test_validate_rejects_extra_key_in_unit():
    p = _finalized(feature_units=[_unit(sneaky="x")])
    with pytest.raises(ValueError, match="sneaky"):
        snapshot.validate_payload(p)


def test_validate_rejects_unit_missing_kind():
    unit = _unit()
    del unit["kind"]
    with pytest.raises(ValueError, match="kind"):
        snapshot.validate_payload(_finalized(feature_units=[unit]))


def test_validate_rejects_unit_tags_not_a_list():
    p = _finalized(feature_units=[_unit(tags="auth")])
    with pytest.raises(ValueError, match="tags"):
        snapshot.validate_payload(p)


def test_validate_rejects_unit_complexity_not_an_int():
    p = _finalized(feature_units=[_unit(complexity="3")])
    with pytest.raises(ValueError, match="complexity"):
        snapshot.validate_payload(p)


def test_validate_rejects_non_dict_unit():
    with pytest.raises(ValueError, match="feature_units"):
        snapshot.validate_payload(_finalized(feature_units=["nope"]))


def test_validate_error_names_the_offending_index():
    p = _finalized(feature_units=[_unit(), _unit(tags=None)])
    with pytest.raises(ValueError, match=r"feature_units\[1\]"):
        snapshot.validate_payload(p)


def test_validate_rejects_activity_missing_and_extra_keys():
    row = dict(BODY["activity_daily"][0])
    del row["by_repo"]
    with pytest.raises(ValueError, match="by_repo"):
        snapshot.validate_payload(_finalized(activity_daily=[row]))
    bad = {**BODY["activity_daily"][0], "junk": 1}
    with pytest.raises(ValueError, match="junk"):
        snapshot.validate_payload(_finalized(activity_daily=[bad]))


def test_validate_allows_optional_activity_keys():
    row = {**BODY["activity_daily"][0], "sessions": 2, "prompts": 9}
    snapshot.validate_payload(_finalized(activity_daily=[row]))  # no raise


def test_validate_rejects_activity_bad_types():
    bad_commits = {**BODY["activity_daily"][0], "commits": "5"}
    with pytest.raises(ValueError, match="commits"):
        snapshot.validate_payload(_finalized(activity_daily=[bad_commits]))
    bad_repo = {**BODY["activity_daily"][0], "by_repo": []}
    with pytest.raises(ValueError, match="by_repo"):
        snapshot.validate_payload(_finalized(activity_daily=[bad_repo]))


def test_validate_rejects_adoption_missing_and_extra_keys():
    row = dict(BODY["adoption"][0])
    del row["status"]
    with pytest.raises(ValueError, match="status"):
        snapshot.validate_payload(_finalized(adoption=[row]))
    bad = {**BODY["adoption"][0], "junk": 1}
    with pytest.raises(ValueError, match="junk"):
        snapshot.validate_payload(_finalized(adoption=[bad]))


def test_validate_allows_adoption_without_optional_keys():
    snapshot.validate_payload(
        _finalized(adoption=[{"name": "plan mode", "status": "never-touched"}]))
