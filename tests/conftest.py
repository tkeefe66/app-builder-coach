import subprocess
from pathlib import Path
import pytest


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
             "PATH": "/usr/bin:/bin", "HOME": str(repo)},
    )


@pytest.fixture
def make_repo(tmp_path):
    def _make(name: str = "demo-app", commits: int = 2) -> Path:
        repo = tmp_path / name
        repo.mkdir()
        _git(repo, "init", "-b", "main")
        for i in range(commits):
            f = repo / f"file{i}.py"
            f.write_text(f"print({i})\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", f"commit {i}")
        return repo
    return _make
