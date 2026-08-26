from src import profile

CLS = [
    {"tags": ["auth"], "complexity": 3, "date": "2026-07-01", "repo": "a", "title": "t1"},
    {"tags": ["auth", "caching"], "complexity": 5, "date": "2025-01-01", "repo": "b", "title": "t2"},
]
TAGS = ["auth", "caching", "webhooks"]


def test_build_matrix_counts_last_and_gaps():
    m = profile.build_matrix(CLS, TAGS, today="2026-08-02")
    rows = {r["tag"]: r for r in m["rows"]}
    assert rows["auth"]["count"] == 2
    assert rows["auth"]["last"] == "2026-07-01"
    assert rows["auth"]["avg_complexity"] == 4.0
    assert m["never"] == ["webhooks"]
    assert m["stale"] == ["caching"]          # last done 2025-01-01, >180d


def test_render_contains_sections():
    m = profile.build_matrix(CLS, TAGS, today="2026-08-02")
    adoption = [{"name": "plan mode", "lesson": "09", "status": "never-touched", "last_used": None}]
    text = profile.render(m, adoption, {"generated": "2026-08-02", "commits": 10, "repos": 2,
                                        "history_skipped": 3})
    assert "## Capability matrix" in text
    assert "## Never built" in text and "webhooks" in text
    assert "## Claude Code feature adoption" in text and "plan mode" in text
    assert "2026-08-02" in text
    assert "history lines skipped: 3" in text
    assert "profile is stale if this date is >1 day old." in text


def test_write_profile_creates_file(tmp_path):
    profile.write_profile(tmp_path / "data", "# hello\n")
    assert (tmp_path / "data" / "profile.md").read_text() == "# hello\n"


def test_build_matrix_handles_null_tags():
    cls_with_null = [
        {"tags": None, "complexity": 2, "date": "2026-07-01", "repo": "a", "title": "t1"},
        {"tags": ["auth"], "complexity": 3, "date": "2026-07-01", "repo": "b", "title": "t2"},
    ]
    tags = ["auth"]
    m = profile.build_matrix(cls_with_null, tags, today="2026-08-02")
    rows = {r["tag"]: r for r in m["rows"]}
    assert rows["auth"]["count"] == 1
    assert rows["auth"]["avg_complexity"] == 3.0
