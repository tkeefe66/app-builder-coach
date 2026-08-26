import pytest

from shared import apps as apps_mod

GOOD = """
apps:
  - name: b2b-ai-news
    display: "B2B AI News"
    railway_project_id: 5fd75529-08bf-4c58-8923-788dbc12b475
    anthropic_key_name: "B2B AI News"
    active: true
  - name: public-dynasty
    display: "Public Dynasty"
    railway_project_id: 834e0969-401d-44ee-8722-5d599a47013a
    active: false
"""


def write(tmp_path, text):
    p = tmp_path / "apps.yaml"
    p.write_text(text)
    return p


def test_load_apps(tmp_path):
    rows = apps_mod.load_apps(write(tmp_path, GOOD))
    assert [r["name"] for r in rows] == ["b2b-ai-news", "public-dynasty"]
    assert rows[0]["anthropic_key_name"] == "B2B AI News"


def test_helpers(tmp_path):
    rows = apps_mod.load_apps(write(tmp_path, GOOD))
    assert apps_mod.by_railway_id(rows)["5fd75529-08bf-4c58-8923-788dbc12b475"] == "b2b-ai-news"
    assert apps_mod.names(rows) == {"b2b-ai-news", "public-dynasty"}
    assert apps_mod.display_map(rows)["public-dynasty"] == "Public Dynasty"


def test_missing_required_key(tmp_path):
    bad = "apps:\n  - name: x\n    display: X\n    active: true\n"
    with pytest.raises(ValueError, match="railway_project_id"):
        apps_mod.load_apps(write(tmp_path, bad))


def test_unexpected_key(tmp_path):
    bad = GOOD + "    sneaky: 1\n"
    with pytest.raises(ValueError, match="sneaky"):
        apps_mod.load_apps(write(tmp_path, bad))


def test_duplicate_name(tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        apps_mod.load_apps(write(tmp_path, GOOD + GOOD.replace("apps:\n", "")))


def test_empty_list(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        apps_mod.load_apps(write(tmp_path, "apps: []\n"))
