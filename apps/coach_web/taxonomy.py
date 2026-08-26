"""Capability taxonomy, read from the repo's taxonomy.yaml (single source)."""
from functools import lru_cache
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@lru_cache(maxsize=1)
def all_tags() -> list[str]:
    tax = yaml.safe_load((REPO_ROOT / "taxonomy.yaml").read_text())
    return sorted(tax["tags"])
