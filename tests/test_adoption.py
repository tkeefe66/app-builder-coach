import json
from datetime import datetime, timezone
from src import adoption


def _write_history(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_parse_history_extracts_only_slash_commands(tmp_path):
    h = tmp_path / "history.jsonl"
    _write_history(h, [
        {"display": "/ss 2 look at this", "timestamp": "2026-07-01T10:00:00Z"},
        {"display": "/ss", "timestamp": "2026-07-05T10:00:00Z"},
        {"display": "my private prompt about health", "timestamp": "2026-07-02T10:00:00Z"},
        {"display": "/code-review", "timestamp": 1751500800},          # epoch seconds
    ])
    out, skipped = adoption.parse_history_commands(h)
    assert set(out) == {"/ss", "/code-review"}          # prose never appears
    assert out["/ss"]["count"] == 2
    assert out["/ss"]["last"] == "2026-07-05"
    assert skipped == 0


def test_parse_history_tolerates_garbage(tmp_path):
    h = tmp_path / "history.jsonl"
    h.write_text('not json\n{"display": "/loop"}\n')
    out, skipped = adoption.parse_history_commands(h)
    assert out["/loop"]["count"] == 1 and out["/loop"]["last"] is None
    assert skipped == 1


def test_parse_history_missing_file(tmp_path):
    assert adoption.parse_history_commands(tmp_path / "nope.jsonl") == ({}, 0)


def test_parse_history_skips_non_dict_rows(tmp_path):
    h = tmp_path / "history.jsonl"
    h.write_text('null\n42\n[1,2]\n{"display": "/skip"}\n')
    out, skipped = adoption.parse_history_commands(h)
    assert out == {"/skip": {"count": 1, "last": None}}
    assert skipped == 3


def test_parse_history_skips_bad_timestamps(tmp_path):
    h = tmp_path / "history.jsonl"
    h.write_text('{"display": "/before", "timestamp": "2026-01-01"}\n'
                 '{"display": "/bad", "timestamp": 1e300}\n'
                 '{"display": "/after", "timestamp": "2026-12-31"}\n')
    out, skipped = adoption.parse_history_commands(h)
    assert set(out) == {"/before", "/bad", "/after"}
    assert out["/before"]["last"] == "2026-01-01"
    assert out["/bad"]["last"] is None
    assert out["/after"]["last"] == "2026-12-31"
    assert skipped == 0


def test_parse_history_millis_timestamp(tmp_path):
    h = tmp_path / "history.jsonl"
    millis_ts = 1751702400000  # epoch millis
    expected_date = datetime.fromtimestamp(1751702400, tz=timezone.utc).date().isoformat()
    _write_history(h, [
        {"display": "/millis", "timestamp": millis_ts},
    ])
    out, skipped = adoption.parse_history_commands(h)
    assert out["/millis"]["last"] == expected_date
    assert skipped == 0


def test_inventory_config(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"hooks": {"Stop": [
        {"hooks": [{"type": "command", "command": "python3 x.py"}]}]}}))
    skills = tmp_path / "skills"
    (skills / "railway-cli").mkdir(parents=True)
    (skills / "railway-cli" / "SKILL.md").write_text("---\n---\n")
    (skills / "empty-dir").mkdir()
    inv = adoption.inventory_config(settings, skills)
    assert inv == {"hooks": ["python3 x.py"], "skills": ["railway-cli"]}


def test_inventory_config_missing_files(tmp_path):
    inv = adoption.inventory_config(tmp_path / "no.json", tmp_path / "no-skills")
    assert inv == {"hooks": [], "skills": []}
