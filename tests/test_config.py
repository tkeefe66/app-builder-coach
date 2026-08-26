from pathlib import Path
from src import config


def test_data_dir_is_inside_repo():
    assert config.DATA_DIR == config.REPO_ROOT / "data"


def test_code_apps_root_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("COACH_ROOT", str(tmp_path))
    assert config.code_apps_root() == tmp_path


def test_code_apps_root_default(monkeypatch):
    monkeypatch.delenv("COACH_ROOT", raising=False)
    assert config.code_apps_root() == Path.home() / "Code Apps"


def test_load_env_sets_vars(monkeypatch, tmp_path):
    monkeypatch.delenv("FOO_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text('# comment\n\nFOO_KEY=abc123\nQUOTED="hello"\n')
    monkeypatch.delenv("QUOTED", raising=False)
    config.load_env(env)
    import os
    assert os.environ["FOO_KEY"] == "abc123"
    assert os.environ["QUOTED"] == "hello"
    monkeypatch.delenv("FOO_KEY")
    monkeypatch.delenv("QUOTED")


def test_load_env_does_not_override_existing(monkeypatch, tmp_path):
    monkeypatch.setenv("FOO_KEY", "original")
    env = tmp_path / ".env"
    env.write_text("FOO_KEY=fromfile\n")
    config.load_env(env)
    import os
    assert os.environ["FOO_KEY"] == "original"


def test_load_env_missing_file_is_noop(tmp_path):
    config.load_env(tmp_path / "nope.env")
