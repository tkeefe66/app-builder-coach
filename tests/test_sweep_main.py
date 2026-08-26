# tests/test_sweep_main.py
from src import sweep


def test_main_end_to_end_without_api_key(tmp_path, make_repo, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    make_repo("alpha")
    data = tmp_path / "coach-data"
    rc = sweep.main(root=tmp_path, data_dir=data)
    assert rc == 0
    assert (data / "profile.md").exists()
    out = capsys.readouterr().out
    assert "repos=1" in out and "new_commits=2" in out


def test_main_survives_empty_root(tmp_path, capsys):
    rc = sweep.main(root=tmp_path / "empty", data_dir=tmp_path / "d")
    assert rc == 0


def test_main_survives_stage_failure(tmp_path, make_repo, monkeypatch, capsys):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    make_repo("alpha")

    def _boom(*args, **kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(sweep.classifier, "load_taxonomy", _boom)
    rc = sweep.main(root=tmp_path, data_dir=tmp_path / "coach-data")
    assert rc == 0
    out = capsys.readouterr().out
    assert "FAILED" in out
