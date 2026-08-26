import json
from src import collector


def test_collect_repo_full_history(make_repo):
    repo = make_repo()
    rows = collector.collect_repo(repo, since_sha=None)
    assert len(rows) == 2
    assert rows[0]["message"] == "commit 0"          # --reverse: oldest first
    assert all(r["repo"] == "demo-app" for r in rows)


def test_collect_repo_incremental(make_repo):
    repo = make_repo()
    first = collector.collect_repo(repo, since_sha=None)
    newer = collector.collect_repo(repo, since_sha=first[0]["sha"])
    assert [r["message"] for r in newer] == ["commit 1"]


def test_collect_repo_bad_path_raises(tmp_path):
    import pytest
    with pytest.raises(collector.CollectError):
        collector.collect_repo(tmp_path / "not-a-repo", since_sha=None)


def test_cursors_roundtrip_and_ledger_append(tmp_path):
    cpath = tmp_path / "cursors.json"
    assert collector.read_cursors(cpath) == {}
    collector.write_cursors(cpath, {"demo-app": "abc"})
    assert collector.read_cursors(cpath) == {"demo-app": "abc"}

    lpath = tmp_path / "ledger.jsonl"
    collector.append_ledger(lpath, [{"sha": "a"}, {"sha": "b"}])
    collector.append_ledger(lpath, [{"sha": "c"}])
    lines = [json.loads(x) for x in lpath.read_text().splitlines()]
    assert [r["sha"] for r in lines] == ["a", "b", "c"]
