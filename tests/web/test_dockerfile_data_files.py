"""Regression: the Docker image must ship every repo-root YAML data file
that `create_app` loads at startup (taxonomy.yaml, rubric.yaml, apps.yaml). A
file missing from the Dockerfile's COPY line will crash-loop the container
on deploy."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _copy_lines() -> list[str]:
    text = (REPO_ROOT / "Dockerfile").read_text()
    return [line for line in text.splitlines() if line.strip().startswith("COPY")]


def test_taxonomy_yaml_copied_into_image():
    assert any("taxonomy.yaml" in line for line in _copy_lines())


def test_rubric_yaml_copied_into_image():
    assert any("rubric.yaml" in line for line in _copy_lines())


def test_apps_yaml_copied_into_image():
    assert any("apps.yaml" in line for line in _copy_lines())
