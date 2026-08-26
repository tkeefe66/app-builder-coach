import pytest

from apps.coach_web import rubric as rubric_mod
from apps.coach_web import taxonomy
from apps.coach_web.main import create_app
from apps.coach_web.rubric import RubricError, _parse


def _minimal_raw():
    """Smallest valid raw rubric covering every taxonomy tag."""
    tags = taxonomy.all_tags()
    return {
        "tiers": {"core": tags[:1], "standard": tags[1:2], "specialty": tags[2:]},
        "levels": [{"name": "newcomer", "label": "Newcomer"}],
        "pairs_with": {},
    }


def test_real_rubric_loads_and_covers_taxonomy():
    r = rubric_mod.load()
    assert set(r.tiers) == set(taxonomy.all_tags())
    assert [lv.name for lv in r.levels] == [
        "newcomer", "beginner", "junior", "mid", "senior"]
    core = [t for t, tier in r.tiers.items() if tier == "core"]
    assert len(core) == 10
    # Mid gates every core tag; senior adds recency + noncore requirements.
    mid = r.levels[3]
    assert set(mid.gates) == set(core)
    senior = r.levels[4]
    assert senior.noncore == (8, 3)
    assert all(g.within_days == 365 for g in senior.gates.values())
    for tag, related in r.pairs_with.items():
        assert tag in r.tiers
        assert all(t in r.tiers for t in related)


def test_unknown_tag_in_tiers_rejected():
    raw = _minimal_raw()
    raw["tiers"]["core"] = ["not-a-real-tag"]
    with pytest.raises(RubricError, match="unknown tag"):
        _parse(raw)


def test_taxonomy_tag_missing_a_tier_rejected():
    raw = _minimal_raw()
    raw["tiers"]["specialty"] = raw["tiers"]["specialty"][:-1]
    with pytest.raises(RubricError, match="missing a tier"):
        _parse(raw)


def test_tag_in_two_tiers_rejected():
    raw = _minimal_raw()
    raw["tiers"]["standard"] = raw["tiers"]["standard"] + raw["tiers"]["core"][:1]
    with pytest.raises(RubricError, match="two tiers"):
        _parse(raw)


def test_empty_levels_rejected():
    raw = _minimal_raw()
    raw["levels"] = []
    with pytest.raises(RubricError, match="non-empty"):
        _parse(raw)


def test_gate_on_unknown_tag_rejected():
    raw = _minimal_raw()
    raw["levels"] = [{"name": "x", "label": "X",
                      "gates": {"nope": {"min_count": 1}}}]
    with pytest.raises(RubricError, match="unknown tag"):
        _parse(raw)


def test_pairs_with_unknown_tag_rejected():
    raw = _minimal_raw()
    raw["pairs_with"] = {"nope": []}
    with pytest.raises(RubricError, match="pairs_with"):
        _parse(raw)


def test_gate_missing_min_count_rejected():
    raw = _minimal_raw()
    tag = taxonomy.all_tags()[0]
    raw["levels"] = [{"name": "x", "label": "X", "gates": {tag: {}}}]
    with pytest.raises(RubricError, match="min_count"):
        _parse(raw)


def test_level_missing_label_rejected():
    raw = _minimal_raw()
    raw["levels"] = [{"name": "x"}]
    with pytest.raises(RubricError, match="label"):
        _parse(raw)


def test_noncore_missing_min_count_rejected():
    raw = _minimal_raw()
    raw["levels"] = [{"name": "x", "label": "X", "noncore": {"tags": 2}}]
    with pytest.raises(RubricError, match="min_count"):
        _parse(raw)


def test_decreasing_min_count_between_levels_rejected():
    raw = _minimal_raw()
    tag = taxonomy.all_tags()[0]
    raw["levels"] = [
        {"name": "junior", "label": "Junior",
         "gates": {tag: {"min_count": 5}}},
        {"name": "mid", "label": "Mid",
         "gates": {tag: {"min_count": 3}}},
    ]
    with pytest.raises(RubricError, match="min_count"):
        _parse(raw)


def test_create_app_fails_fast_on_bad_rubric(monkeypatch, settings):
    def boom():
        raise RubricError("rubric.yaml: broken")
    monkeypatch.setattr(rubric_mod, "load", boom)
    with pytest.raises(RubricError, match="broken"):
        create_app(settings)
