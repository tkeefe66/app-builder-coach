import json
from src import collector


def test_index_specs_reads_date_and_title(tmp_path):
    repo = tmp_path / "demo-app"
    specs = repo / "docs" / "superpowers" / "specs"
    specs.mkdir(parents=True)
    (specs / "2026-07-01-cool-feature-design.md").write_text("# Cool Feature\n\nBody.")
    rows = collector.index_specs(repo)
    assert rows == [{
        "repo": "demo-app",
        "spec_path": "docs/superpowers/specs/2026-07-01-cool-feature-design.md",
        "date": "2026-07-01", "title": "Cool Feature",
        "body": "# Cool Feature\n\nBody.",
    }]


def test_index_specs_no_dir(tmp_path):
    assert collector.index_specs(tmp_path / "bare") == []


def test_discover_repos_skips_archives_and_nonrepos(tmp_path, make_repo):
    make_repo("alpha")
    (tmp_path / "zOld").mkdir()          # archive prefix -> skipped
    (tmp_path / "notes").mkdir()         # no .git -> skipped
    names = [p.name for p in collector.discover_repos(tmp_path)]
    assert names == ["alpha"]


def test_sweep_is_incremental(tmp_path, make_repo):
    repo = make_repo("alpha")
    data = tmp_path / "coach-data"
    r1 = collector.sweep(tmp_path, data)
    assert r1["repos"] == 1 and r1["new_commits"] == 2 and r1["errors"] == []
    r2 = collector.sweep(tmp_path, data)
    assert r2["new_commits"] == 0
    ledger = [json.loads(x) for x in (data / "ledger.jsonl").read_text().splitlines()]
    assert len(ledger) == 2  # no duplicates


def test_sweep_error_isolation(tmp_path, make_repo):
    """One repo fails (empty .git), another succeeds. Sweep continues and persists good commits."""
    # Create a broken repo (empty .git dir, no log)
    broken = tmp_path / "broken-repo"
    broken.mkdir()
    (broken / ".git").mkdir()

    # Create a healthy repo
    healthy = make_repo("good-repo")

    # Sweep should not raise, should include error, and should persist good repo's commits
    data = tmp_path / "coach-data"
    result = collector.sweep(tmp_path, data)

    assert result["errors"], "Expected errors to be populated"
    assert any("broken-repo" in err for err in result["errors"]), "Error should mention broken repo"

    # Good repo's commits should still be in the ledger
    ledger = [json.loads(x) for x in (data / "ledger.jsonl").read_text().splitlines()]
    assert len(ledger) == 2, "Good repo's commits should be in ledger"
    assert all(c["repo"] == "good-repo" for c in ledger), "All ledger commits should be from good repo"


def test_sweep_recovers_from_stranded_cursor(tmp_path, make_repo):
    """A cursor sha that no longer resolves (e.g. history rewrite) should not
    error out the whole repo: sweep retries from full history, dedupes against
    the ledger, and repairs the cursor to real HEAD."""
    repo = make_repo("alpha")
    data = tmp_path / "coach-data"
    r1 = collector.sweep(tmp_path, data)
    assert r1["errors"] == [] and r1["new_commits"] == 2

    real_head = collector.read_cursors(data / "cursors.json")["alpha"]

    # Corrupt the cursor with a sha that doesn't exist in the repo.
    cursors = collector.read_cursors(data / "cursors.json")
    cursors["alpha"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    collector.write_cursors(data / "cursors.json", cursors)

    r2 = collector.sweep(tmp_path, data)
    assert r2["errors"] == []
    assert r2["new_commits"] == 0

    ledger = [json.loads(x) for x in (data / "ledger.jsonl").read_text().splitlines()]
    assert len(ledger) == 2  # no duplicates

    repaired = collector.read_cursors(data / "cursors.json")["alpha"]
    assert repaired == real_head
