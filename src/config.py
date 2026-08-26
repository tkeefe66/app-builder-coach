import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
ARCHIVE_PREFIX = "z"
STALE_AFTER_HOURS = 24


def code_apps_root() -> Path:
    return Path(os.environ.get("COACH_ROOT", str(Path.home() / "Code Apps")))


def load_env(path: Path = REPO_ROOT / ".env") -> None:
    """Load KEY=VALUE lines into os.environ; existing vars win."""
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
