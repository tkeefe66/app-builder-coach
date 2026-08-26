import json

import pytest

from shared import apps as apps_registry
from src import classifier, config

TAX = {"tags": ["auth", "caching", "llm-integration"], "heuristics": {"auth": "auth"}}


class FakeContent:
    def __init__(self, text): self.text = text


class FakeResponse:
    def __init__(self, text, tin=100, tout=50):
        self.content = [FakeContent(text)]
        self.usage = type("U", (), {"input_tokens": tin, "output_tokens": tout})()


class FakeClient:
    def __init__(self, text): self._text, self.calls = text, 0
    @property
    def messages(self): return self
    def create(self, **kwargs):
        self.calls += 1
        return FakeResponse(self._text)


class FakeClientRaisingThenOk:
    def __init__(self):
        self.calls = 0
    @property
    def messages(self): return self
    def create(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("API error")
        return FakeResponse('{"tags": ["auth"], "complexity": 2, "summary": "s"}')


def test_read_jsonl_skips_malformed_lines(tmp_path):
    path = tmp_path / "rows.jsonl"
    path.write_text(
        json.dumps({"a": 1}) + "\n" +
        "{not valid json\n" +
        json.dumps({"a": 2}) + "\n"
    )
    rows = classifier.read_jsonl(path)
    assert rows == [{"a": 1}, {"a": 2}]


def test_content_hash_stable():
    assert classifier.content_hash("abc") == classifier.content_hash("abc")
    assert len(classifier.content_hash("abc")) == 16


def test_build_units_spec_repos_and_commit_clusters():
    ledger = [
        {"repo": "has-specs", "date": "2026-07-01T10:00:00Z", "message": "m1", "files": ["a.py"]},
        {"repo": "no-specs", "date": "2026-07-03T10:00:00Z", "message": "m2", "files": ["b.py"]},
        {"repo": "no-specs", "date": "2026-07-20T10:00:00Z", "message": "m3", "files": ["c.py"]},
        {"repo": "no-specs", "date": "2026-08-01T10:00:00Z", "message": "m4", "files": ["d.py"]},
    ]
    specs = [{"repo": "has-specs", "spec_path": "docs/superpowers/specs/x.md",
              "date": "2026-07-01", "title": "Feature X"}]
    units = classifier.build_units(ledger, specs)
    kinds = sorted((u["kind"], u["repo"], u["date"]) for u in units)
    assert kinds == [("commits", "no-specs", "2026-07-01"),
                     ("commits", "no-specs", "2026-08-01"),
                     ("spec", "has-specs", "2026-07-01")]


def test_build_units_excludes_given_month():
    ledger = [
        {"repo": "r", "date": "2026-07-03T10:00:00Z", "message": "m2", "files": ["b.py"]},
        {"repo": "r", "date": "2026-08-01T10:00:00Z", "message": "m4", "files": ["d.py"]},
    ]
    units = classifier.build_units(ledger, [], exclude_month="2026-08")
    kinds = sorted((u["kind"], u["repo"], u["date"]) for u in units)
    assert kinds == [("commits", "r", "2026-07-01")]   # August cluster excluded


def test_build_units_spec_text_includes_body():
    specs_a = [{"repo": "r", "spec_path": "docs/x.md", "date": "2026-07-01",
                "title": "Feature X", "body": "Body one"}]
    specs_b = [{"repo": "r", "spec_path": "docs/x.md", "date": "2026-07-01",
                "title": "Feature X", "body": "Body two"}]
    unit_a = classifier.build_units([], specs_a)[0]
    unit_b = classifier.build_units([], specs_b)[0]
    assert unit_a["text"] != unit_b["text"]
    assert classifier.content_hash(unit_a["text"]) != classifier.content_hash(unit_b["text"])


def test_classify_unit_parses_json_and_validates_tags():
    client = FakeClient('{"tags": ["auth", "bogus-tag"], "complexity": 3, "summary": "s"}')
    out = classifier.classify_unit("some text", TAX, client)
    assert out["tags"] == ["auth"]           # bogus dropped
    assert out["complexity"] == 3 and out["input_tokens"] == 100


def test_classify_unit_reports_usage_to_coach_web(monkeypatch):
    sent = []
    monkeypatch.setattr(classifier, "report",
                        lambda app, model, usage: sent.append((app, model, usage)))
    client = FakeClient('{"tags": ["auth"], "complexity": 2, "summary": "s"}')
    classifier.classify_unit("some text", TAX, client)
    assert len(sent) == 1
    app, model, usage = sent[0]
    assert (app, model) == (classifier.APP_SLUG, classifier.HAIKU_MODEL)
    assert (usage.input_tokens, usage.output_tokens) == (100, 50)


def test_classify_unit_reports_even_when_reply_is_unparseable(monkeypatch):
    """The tokens were spent whether or not the reply was usable."""
    sent = []
    monkeypatch.setattr(classifier, "report",
                        lambda app, model, usage: sent.append(app))
    client = FakeClient("{not valid json}")
    with pytest.raises(json.JSONDecodeError):
        classifier.classify_unit("some text", TAX, client)
    assert sent == [classifier.APP_SLUG]


def test_app_slug_is_registered_in_apps_yaml():
    """An unregistered slug is rejected by /api/usage with a 400."""
    registered = apps_registry.names(
        apps_registry.load_apps(config.REPO_ROOT / "apps.yaml"))
    assert classifier.APP_SLUG in registered


def test_run_classifier_caches_forever(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(json.dumps(
        {"repo": "r", "date": "2026-07-01T00:00:00Z", "message": "add auth", "files": ["auth.py"]}) + "\n")
    (tmp_path / "specs.jsonl").write_text("")
    client = FakeClient('{"tags": ["auth"], "complexity": 2, "summary": "s"}')
    r1 = classifier.run_classifier(tmp_path, TAX, lambda: client)
    assert r1 == {"classified": 1, "cached": 0, "failed": 0}
    r2 = classifier.run_classifier(tmp_path, TAX, lambda: client)
    assert r2 == {"classified": 0, "cached": 1, "failed": 0}
    assert client.calls == 1                 # zero API calls on second run
    costs = (tmp_path / "llm_costs.jsonl").read_text().splitlines()
    assert len(costs) == 1


def test_run_classifier_no_client_falls_back_to_heuristics(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(json.dumps(
        {"repo": "r", "date": "2026-07-01T00:00:00Z", "message": "add auth", "files": ["auth.py"]}) + "\n")
    (tmp_path / "specs.jsonl").write_text("")
    r = classifier.run_classifier(tmp_path, TAX, lambda: None)
    assert r["classified"] == 1
    row = json.loads((tmp_path / "classifications.jsonl").read_text())
    assert row["model"] == "heuristics" and row["tags"] == ["auth"]


def test_run_classifier_dedupes_identical_content_within_batch(tmp_path):
    # Two repos, both with no specs, same month, identical message+file → identical content hash
    (tmp_path / "ledger.jsonl").write_text(
        json.dumps({"repo": "repo-a", "date": "2026-07-01T00:00:00Z", "message": "add auth", "files": ["auth.py"]}) + "\n" +
        json.dumps({"repo": "repo-b", "date": "2026-07-01T00:00:00Z", "message": "add auth", "files": ["auth.py"]}) + "\n"
    )
    (tmp_path / "specs.jsonl").write_text("")
    client = FakeClient('{"tags": ["auth"], "complexity": 2, "summary": "s"}')
    r = classifier.run_classifier(tmp_path, TAX, lambda: client)
    assert r == {"classified": 1, "cached": 1, "failed": 0}  # First classified, second cached (same content hash)
    assert client.calls == 1  # Only one API call
    rows = (tmp_path / "classifications.jsonl").read_text().splitlines()
    assert len(rows) == 1  # Only one row in classifications.jsonl


def test_run_classifier_api_failure_path(tmp_path):
    # Two distinct units (fixed past months, never "current month"): first
    # fails with RuntimeError, second succeeds.
    (tmp_path / "ledger.jsonl").write_text(
        json.dumps({"repo": "r", "date": "2020-01-01T00:00:00Z", "message": "add auth", "files": ["auth.py"]}) + "\n" +
        json.dumps({"repo": "r", "date": "2020-02-01T00:00:00Z", "message": "add caching", "files": ["cache.py"]}) + "\n"
    )
    (tmp_path / "specs.jsonl").write_text("")
    client = FakeClientRaisingThenOk()
    r = classifier.run_classifier(tmp_path, TAX, lambda: client)
    assert r == {"classified": 1, "cached": 0, "failed": 1}  # One succeeded, one failed
    rows = (tmp_path / "classifications.jsonl").read_text().splitlines()
    assert len(rows) == 1  # Only the successful unit's row
    row = json.loads(rows[0])
    assert row["tags"] == ["auth"] and row["model"] == "claude-haiku-4-5-20251001"


def test_run_classifier_tiered_cache_keyless_then_keyed_reclassifies(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(json.dumps(
        {"repo": "r", "date": "2026-07-01T00:00:00Z", "message": "add auth", "files": ["auth.py"]}) + "\n")
    (tmp_path / "specs.jsonl").write_text("")

    r1 = classifier.run_classifier(tmp_path, TAX, lambda: None)
    assert r1 == {"classified": 1, "cached": 0, "failed": 0}

    client = FakeClient('{"tags": ["auth"], "complexity": 2, "summary": "s"}')
    r2 = classifier.run_classifier(tmp_path, TAX, lambda: client)
    assert r2 == {"classified": 1, "cached": 0, "failed": 0}
    assert client.calls == 1  # keyed run actually called the API, not poisoned by keyless cache

    rows = [json.loads(x) for x in (tmp_path / "classifications.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    suffixes = sorted(r["key"].rsplit(":", 1)[1] for r in rows)
    assert suffixes == ["h", "m"]

    eff = classifier.effective_rows(rows)
    assert len(eff) == 1
    assert eff[0]["key"].endswith(":m")
    assert eff[0]["model"] == "claude-haiku-4-5-20251001"


def test_run_classifier_tiered_cache_keyed_then_keyless_stays_cached(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(json.dumps(
        {"repo": "r", "date": "2026-07-01T00:00:00Z", "message": "add auth", "files": ["auth.py"]}) + "\n")
    (tmp_path / "specs.jsonl").write_text("")

    client = FakeClient('{"tags": ["auth"], "complexity": 2, "summary": "s"}')
    r1 = classifier.run_classifier(tmp_path, TAX, lambda: client)
    assert r1 == {"classified": 1, "cached": 0, "failed": 0}

    r2 = classifier.run_classifier(tmp_path, TAX, lambda: None)
    assert r2 == {"classified": 0, "cached": 1, "failed": 0}

    rows = (tmp_path / "classifications.jsonl").read_text().splitlines()
    assert len(rows) == 1  # keyless run never downgraded the model row


def test_run_classifier_heuristic_tags_scoped_to_unit_month(tmp_path):
    (tmp_path / "ledger.jsonl").write_text(
        json.dumps({"repo": "r", "date": "2020-01-01T00:00:00Z", "message": "add auth", "files": ["auth.py"]}) + "\n" +
        json.dumps({"repo": "r", "date": "2020-02-01T00:00:00Z", "message": "add cache layer", "files": ["cache.py"]}) + "\n"
    )
    (tmp_path / "specs.jsonl").write_text("")
    tax = {"tags": ["auth", "caching"], "heuristics": {"auth": "auth", "cache": "caching"}}
    r = classifier.run_classifier(tmp_path, tax, lambda: None)
    assert r["classified"] == 2
    rows = {json.loads(x)["date"]: json.loads(x) for x in
            (tmp_path / "classifications.jsonl").read_text().splitlines()}
    assert rows["2020-01-01"]["tags"] == ["auth"]      # not poisoned by February's "caching"
    assert rows["2020-02-01"]["tags"] == ["caching"]   # not poisoned by January's "auth"
