import json
from pathlib import Path

from src import usage


def w(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def transcript_rows():
    return [
        {"type": "user", "promptId": "p1", "sessionId": "s-1",
         "cwd": "/Users/x/Code Apps/alpha", "isSidechain": False,
         "timestamp": "2026-08-01T14:00:00.000Z"},
        {"type": "assistant", "sessionId": "s-1", "isSidechain": False,
         "timestamp": "2026-08-01T14:00:05.000Z",
         "message": {"model": "claude-sonnet-5",
                     "usage": {"input_tokens": 100, "output_tokens": 50,
                               "cache_read_input_tokens": 1000,
                               "cache_creation_input_tokens": 200}}},
        # tool-result user row: NOT a prompt
        {"type": "user", "toolUseResult": {"x": 1}, "sessionId": "s-1",
         "timestamp": "2026-08-01T14:00:10.000Z"},
        # sidechain prompt: NOT a prompt, but its assistant usage counts
        {"type": "user", "promptId": "p2", "sessionId": "s-1",
         "isSidechain": True, "timestamp": "2026-08-01T14:01:00.000Z"},
        {"type": "assistant", "sessionId": "s-1", "isSidechain": True,
         "timestamp": "2026-08-02T01:00:00.000Z",
         "message": {"model": "claude-haiku-4-5",
                     "usage": {"input_tokens": 10, "output_tokens": 5,
                               "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 0}}},
        "garbage-not-json",
        {"type": "ai-title"},  # no timestamp/usage: ignored
    ]


def test_parse_transcript(tmp_path):
    f = tmp_path / "abc.jsonl"
    f.write_text("".join(
        (json.dumps(r) if isinstance(r, dict) else r) + "\n"
        for r in transcript_rows()))
    out = usage.parse_transcript(f)
    assert out["session_id"] == "s-1"
    assert out["repo"] == "alpha"
    d1 = out["days"]["2026-08-01"]
    assert d1["prompts"] == 1  # main-chain prompt only
    assert d1["tokens"]["claude-sonnet-5"] == {
        "in": 100, "out": 50, "cache_read": 1000, "cache_create": 200}
    # sidechain assistant row lands on its own (UTC) date
    assert out["days"]["2026-08-02"]["tokens"]["claude-haiku-4-5"]["in"] == 10
    assert out["days"]["2026-08-02"]["prompts"] == 0


def make_home(tmp_path, name="proj-a", fname="s1.jsonl"):
    d = tmp_path / "home" / "projects" / name
    d.mkdir(parents=True, exist_ok=True)
    f = d / fname
    f.write_text("".join(
        (json.dumps(r) if isinstance(r, dict) else r) + "\n"
        for r in transcript_rows()))
    return tmp_path / "home", f


def test_scan_projects_cursors_skip_unchanged(tmp_path):
    home, f = make_home(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    rows1 = usage.scan_projects(home, data)
    assert len(rows1) == 1 and rows1[0]["repo"] == "alpha"
    # corrupt the file WITHOUT changing mtime/size -> cached row reused
    stat = f.stat()
    f.write_text("x" * stat.st_size)
    import os
    os.utime(f, (stat.st_atime, stat.st_mtime))
    rows2 = usage.scan_projects(home, data)
    assert rows2 == rows1  # cursor hit, no re-parse


def test_scan_projects_reparses_changed_and_drops_deleted(tmp_path):
    home, f = make_home(tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    usage.scan_projects(home, data)
    # append another prompt -> size changes -> re-parse
    with f.open("a") as fh:
        fh.write(json.dumps({"type": "user", "promptId": "p9",
                             "sessionId": "s-1", "isSidechain": False,
                             "timestamp": "2026-08-01T15:00:00.000Z"}) + "\n")
    rows = usage.scan_projects(home, data)
    assert rows[0]["days"]["2026-08-01"]["prompts"] == 2
    f.unlink()
    assert usage.scan_projects(home, data) == []


def test_scan_projects_missing_home(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    assert usage.scan_projects(tmp_path / "nope", data) == []


def test_daily_rollups():
    rows = [
        {"file": "a", "session_id": "s-1", "repo": "alpha",
         "days": {"2026-08-01": {"prompts": 3, "tokens": {
             "claude-haiku-4-5": {"in": 1_000_000, "out": 0,
                                  "cache_read": 0, "cache_create": 0}}}}},
        {"file": "b", "session_id": "s-2", "repo": "beta",
         "days": {"2026-08-01": {"prompts": 2, "tokens": {}},
                  "2026-08-02": {"prompts": 1, "tokens": {
                      "claude-sonnet-5": {"in": 0, "out": 1_000_000,
                                          "cache_read": 0, "cache_create": 0}}}}},
    ]
    out = usage.daily_rollups(rows)
    assert out["activity"]["2026-08-01"] == {"sessions": 2, "prompts": 5}
    assert out["activity"]["2026-08-02"] == {"sessions": 1, "prompts": 1}
    days = {c["date"]: c for c in out["cost"]}
    assert days["2026-08-01"]["input_tokens"] == 1_000_000
    assert days["2026-08-01"]["cost_usd"] == 1.0          # 1 MTok haiku input
    assert days["2026-08-01"]["by_model"] == {"claude-haiku-4-5": 1.0}
    assert days["2026-08-02"]["cost_usd"] == 15.0         # 1 MTok sonnet output
    assert [c["date"] for c in out["cost"]] == ["2026-08-01", "2026-08-02"]


def test_daily_rollups_empty():
    assert usage.daily_rollups([]) == {"activity": {}, "cost": []}
