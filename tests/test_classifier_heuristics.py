# tests/test_classifier_heuristics.py
from src import classifier, config

TAX_PATH = config.REPO_ROOT / "taxonomy.yaml"


def test_load_taxonomy():
    tax = classifier.load_taxonomy(TAX_PATH)
    assert "auth" in tax["tags"] and len(tax["tags"]) >= 20
    assert all(tag in tax["tags"] for tag in tax["heuristics"].values())


def test_all_taxonomy_tags_reachable_by_heuristics():
    tax = classifier.load_taxonomy(TAX_PATH)
    unreachable = set(tax["tags"]) - set(tax["heuristics"].values())
    assert unreachable == set()


def test_heuristic_tags_match_paths_and_message():
    tax = {"tags": ["auth", "db-migrations", "caching"],
           "heuristics": {"auth": "auth", "migration": "db-migrations"}}
    tags = classifier.heuristic_tags(
        ["api/app/auth.py", "migrations/0007_x.py"], "Add login flow", tax)
    assert tags == ["auth", "db-migrations"]


def test_heuristic_tags_empty_when_no_match():
    tax = {"tags": ["auth"], "heuristics": {"auth": "auth"}}
    assert classifier.heuristic_tags(["readme.md"], "docs", tax) == []


def test_heuristic_tags_needle_matching_is_case_insensitive():
    tax = {"tags": ["auth"], "heuristics": {"AUTH": "auth"}}
    assert classifier.heuristic_tags([], "Add Auth flow", tax) == ["auth"]
