from src.collector import detect_languages, parse_git_log

RAW = (
    "\x1eaaa111\x1f2026-07-01T10:00:00-06:00\x1fAdd auth login\n"
    "10\t2\tapi/app/auth.py\n"
    "5\t0\tweb/login.tsx\n"
    "\x1ebbb222\x1f2026-07-02T11:00:00-06:00\x1fBinary asset\n"
    "-\t-\tlogo.png\n"
)


def test_detect_languages_counts_by_extension():
    assert detect_languages(["a.py", "b/c.py", "d.tsx", "Dockerfile", "x.png"]) == {
        "python": 2, "typescript": 1, "docker": 1,
    }


def test_parse_git_log_two_commits():
    rows = parse_git_log(RAW)
    assert [r["sha"] for r in rows] == ["aaa111", "bbb222"]
    first = rows[0]
    assert first["message"] == "Add auth login"
    assert first["files"] == ["api/app/auth.py", "web/login.tsx"]
    assert first["insertions"] == 15 and first["deletions"] == 2
    assert first["languages"] == {"python": 1, "typescript": 1}


def test_parse_git_log_binary_numstat_dashes():
    rows = parse_git_log(RAW)
    assert rows[1]["insertions"] == 0 and rows[1]["deletions"] == 0
    assert rows[1]["files"] == ["logo.png"]


def test_parse_git_log_empty_input():
    assert parse_git_log("") == []
