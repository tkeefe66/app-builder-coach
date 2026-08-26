from pathlib import Path
from src import adoption, config

CHECKLIST_PATH = config.REPO_ROOT / "feature-checklist.yaml"


def test_load_checklist_real_file():
    features = adoption.load_checklist(CHECKLIST_PATH)
    assert len(features) >= 25
    assert all("name" in f and "lesson" in f and "detect" in f for f in features)


def test_evaluate_statuses():
    features = [
        {"name": "cmd feature", "lesson": "01", "detect": {"commands": ["/x"]}},
        {"name": "hook feature", "lesson": "06", "detect": {"hook_substring": "gate.py"}},
        {"name": "skill feature", "lesson": "03", "detect": {"skill": "cool-skill"}},
        {"name": "untouched", "lesson": "09", "detect": {}},
    ]
    usage = {"/x": {"count": 3, "last": "2026-07-01"}}
    inventory = {"hooks": ["python3 gate.py"], "skills": ["cool-skill"]}
    rows = adoption.evaluate_checklist(features, usage, inventory)
    by_name = {r["name"]: r for r in rows}
    assert by_name["cmd feature"]["status"] == "used"
    assert by_name["cmd feature"]["last_used"] == "2026-07-01"
    assert by_name["hook feature"]["status"] == "configured-but-unused"
    assert by_name["skill feature"]["status"] == "configured-but-unused"
    assert by_name["untouched"]["status"] == "never-touched"


def test_evaluate_precedence_used_wins():
    features = [
        {"name": "combo", "lesson": "x", "detect": {"commands": ["/x"], "hook_substring": "gate.py", "skill": "cool-skill"}}
    ]
    usage = {"/x": {"count": 1, "last": "2026-07-01"}}
    inventory = {"hooks": ["python3 gate.py"], "skills": ["cool-skill"]}
    rows = adoption.evaluate_checklist(features, usage, inventory)
    assert rows[0]["status"] == "used"
    assert rows[0]["last_used"] == "2026-07-01"


def test_evaluate_precedence_hook_beats_never():
    features = [
        {"name": "combo", "lesson": "x", "detect": {"commands": ["/x"], "hook_substring": "gate.py", "skill": "cool-skill"}}
    ]
    usage = {}
    inventory = {"hooks": ["python3 gate.py"], "skills": ["cool-skill"]}
    rows = adoption.evaluate_checklist(features, usage, inventory)
    assert rows[0]["status"] == "configured-but-unused"
