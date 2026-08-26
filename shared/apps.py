"""App registry: joins Railway projects and Anthropic keys to one app identity.

Read by the local sweep (to map Railway project ids) and by the server (to
validate /api/usage payloads). Lives in shared/ because the Dockerfile copies
shared/ and apps/ but not src/.
"""
from pathlib import Path

import yaml

REQUIRED = {"name", "display", "railway_project_id", "active"}
ALLOWED = REQUIRED | {"anthropic_key_name"}


def load_apps(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text()) or {}
    rows = data.get("apps")
    if not isinstance(rows, list) or not rows:
        raise ValueError("apps.yaml: 'apps' must be a non-empty list")
    seen: set = set()
    out: list[dict] = []
    for i, row in enumerate(rows):
        where = f"apps.yaml: apps[{i}]"
        if not isinstance(row, dict):
            raise ValueError(f"{where} must be a mapping, got {type(row).__name__}")
        missing = sorted(REQUIRED - row.keys())
        if missing:
            raise ValueError(f"{where} missing required keys: {', '.join(missing)}")
        extra = sorted(row.keys() - ALLOWED)
        if extra:
            raise ValueError(f"{where} has unexpected keys: {', '.join(extra)}")
        if not isinstance(row["active"], bool):
            raise ValueError(f"{where}.active must be a boolean")
        if row["name"] in seen:
            raise ValueError(f"{where} duplicate app name: {row['name']!r}")
        seen.add(row["name"])
        out.append(row)
    return out


def by_railway_id(apps: list[dict]) -> dict:
    return {a["railway_project_id"]: a["name"] for a in apps}


def names(apps: list[dict]) -> set:
    return {a["name"] for a in apps}


def display_map(apps: list[dict]) -> dict:
    return {a["name"]: a["display"] for a in apps}
