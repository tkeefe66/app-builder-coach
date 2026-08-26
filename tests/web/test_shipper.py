import json
from pathlib import Path

from shared import snapshot
from src import shipper

LEDGER = [
    {"repo": "alpha", "date": "2026-08-01T10:00:00-04:00", "message": "m1", "files": ["a.py"]},
    {"repo": "alpha", "date": "2026-08-01T11:00:00-04:00", "message": "m2", "files": ["b.py"]},
    {"repo": "beta", "date": "2026-08-02T09:00:00-04:00", "message": "m3", "files": ["c.py"]},
]
CLS = [
    {"key": "h1:h", "kind": "commits", "repo": "alpha", "date": "2026-08-01",
     "title": "alpha 2026-08", "tags": ["api-backend"], "complexity": 2,
     "summary": "s", "model": "heuristics"},
    {"key": "h1:m", "kind": "commits", "repo": "alpha", "date": "2026-08-01",
     "title": "alpha 2026-08", "tags": ["api-backend", "auth"], "complexity": 3,
     "summary": "s", "model": "claude-haiku-4-5-20251001"},
]
ADOPTION = [{"name": "plan mode", "lesson": "09-advanced-features",
             "status": "never-touched", "last_used": None}]


def write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_build_snapshot(tmp_path):
    write_jsonl(tmp_path / "ledger.jsonl", LEDGER)
    write_jsonl(tmp_path / "classifications.jsonl", CLS)
    p = shipper.build_snapshot(tmp_path, ADOPTION, {"repos": 2},
                               captured_at="2026-08-02T11:30:00+00:00")
    snapshot.validate_payload(p)
    # tiered rows collapse: :m wins over :h for same base hash
    assert len(p["feature_units"]) == 1
    assert p["feature_units"][0]["key"] == "h1:m"
    # activity is grouped per day with per-repo counts
    assert p["activity_daily"] == [
        {"date": "2026-08-01", "commits": 2, "by_repo": {"alpha": 2}},
        {"date": "2026-08-02", "commits": 1, "by_repo": {"beta": 1}},
    ]
    assert p["adoption"] == ADOPTION
    assert p["sweep"] == {"repos": 2}


def test_build_snapshot_empty_data_dir(tmp_path):
    p = shipper.build_snapshot(tmp_path, [], {}, captured_at="2026-08-02T11:30:00+00:00")
    snapshot.validate_payload(p)
    assert p["feature_units"] == [] and p["activity_daily"] == []


class FakeResponse:
    def __init__(self, status_code):
        self.status_code = status_code


def make_post(fail_times=0, log=None):
    calls = {"n": 0}
    def post(url, json=None, headers=None, timeout=None):
        calls["n"] += 1
        if log is not None:
            log.append(json["content_hash"])
        if calls["n"] <= fail_times:
            raise ConnectionError("down")
        return FakeResponse(200)
    return post


def _payload(tag, captured_at="2026-08-02T11:30:00+00:00"):
    return shipper.build_snapshot(
        Path("/nonexistent"), [], {"tag": tag}, captured_at=captured_at)


def test_ship_all_success(tmp_path):
    result = shipper.ship_all(_payload("a"), "http://x/api/ingest", "tok",
                              tmp_path / "outbox", post=make_post())
    assert result == {"shipped": 1, "queued": 0, "rejected": 0}
    assert not list((tmp_path / "outbox").glob("*.json"))


def test_ship_all_failure_queues_to_outbox(tmp_path):
    result = shipper.ship_all(_payload("a"), "http://x/api/ingest", "tok",
                              tmp_path / "outbox", post=make_post(fail_times=99))
    assert result == {"shipped": 0, "queued": 1, "rejected": 0}
    assert len(list((tmp_path / "outbox").glob("*.json"))) == 1


def test_ship_all_drains_outbox_oldest_first(tmp_path):
    outbox = tmp_path / "outbox"
    # distinct captured_at per tag so outbox filenames (and therefore drain
    # order) reflect arrival order, not payload content-hash
    payload_a = _payload("a", captured_at="2026-08-02T11:30:00+00:00")
    payload_b = _payload("b", captured_at="2026-08-02T11:31:00+00:00")
    payload_c = _payload("c", captured_at="2026-08-02T11:32:00+00:00")
    # queue two payloads while "offline"
    shipper.ship_all(payload_a, "http://x/api/ingest", "tok", outbox,
                     post=make_post(fail_times=99))
    shipper.ship_all(payload_b, "http://x/api/ingest", "tok", outbox,
                     post=make_post(fail_times=99))
    shipped_hashes = []
    result = shipper.ship_all(payload_c, "http://x/api/ingest", "tok", outbox,
                              post=make_post(log=shipped_hashes))
    assert result == {"shipped": 3, "queued": 0, "rejected": 0}
    assert not list(outbox.glob("*.json"))
    assert shipped_hashes == [
        payload_a["content_hash"], payload_b["content_hash"], payload_c["content_hash"]]


def test_ship_all_quarantines_corrupt_outbox_entry(tmp_path):
    outbox = tmp_path / "outbox"
    outbox.mkdir(parents=True)
    corrupt = outbox / "0-corrupt.json"
    corrupt.write_text("{not valid json")
    result = shipper.ship_all(_payload("a"), "http://x/api/ingest", "tok",
                              outbox, post=make_post())
    assert result == {"shipped": 1, "queued": 0, "rejected": 0}
    assert not list(outbox.glob("*.json"))
    assert (outbox / "0-corrupt.json.corrupt").exists()


def test_ship_all_http_error_status_queues(tmp_path):
    def post(url, json=None, headers=None, timeout=None):
        return FakeResponse(500)
    result = shipper.ship_all(_payload("a"), "http://x/api/ingest", "tok",
                              tmp_path / "outbox", post=post)
    assert result == {"shipped": 0, "queued": 1, "rejected": 0}


def status_post(status_by_hash=None, default=200):
    def post(url, json=None, headers=None, timeout=None):
        return FakeResponse((status_by_hash or {}).get(json["content_hash"], default))
    return post


def test_ship_all_quarantines_terminally_rejected_pending_entry(tmp_path):
    """A payload the server permanently rejects must not block the queue."""
    outbox = tmp_path / "outbox"
    outbox.mkdir(parents=True)
    bad = _payload("bad", captured_at="2026-08-02T11:30:00+00:00")
    good = _payload("good", captured_at="2026-08-02T11:31:00+00:00")
    (outbox / "0-bad.json").write_text(json.dumps(bad))
    (outbox / "1-good.json").write_text(json.dumps(good))

    result = shipper.ship_all(
        _payload("current", captured_at="2026-08-02T11:32:00+00:00"),
        "http://x/api/ingest", "tok", outbox,
        post=status_post({bad["content_hash"]: 400}))

    assert result == {"shipped": 2, "queued": 0, "rejected": 1}
    assert (outbox / "0-bad.json.rejected").exists()
    # the newer pending entry and the current payload both got through
    assert not list(outbox.glob("*.json"))


def test_ship_all_auth_failure_is_retryable(tmp_path):
    """401/403 are config problems: keep the payload, do not quarantine it."""
    for status in (401, 403):
        outbox = tmp_path / f"outbox-{status}"
        result = shipper.ship_all(_payload("a"), "http://x/api/ingest", "tok",
                                  outbox, post=status_post(default=status))
        assert result == {"shipped": 0, "queued": 1, "rejected": 0}
        assert len(list(outbox.glob("*.json"))) == 1
        assert not list(outbox.glob("*.rejected"))


def test_ship_all_rejects_current_payload_on_terminal_status(tmp_path):
    outbox = tmp_path / "outbox"
    result = shipper.ship_all(_payload("a"), "http://x/api/ingest", "tok",
                              outbox, post=status_post(default=400))
    assert result == {"shipped": 0, "queued": 0, "rejected": 1}
    # must not be re-picked-up by a later run
    assert not list(outbox.glob("*.json"))
    assert len(list(outbox.glob("*.rejected"))) == 1


def test_sweep_ships_when_url_set(monkeypatch, tmp_path):
    from src import sweep
    root = tmp_path / "root"; root.mkdir()
    data = tmp_path / "data"
    monkeypatch.setenv("COACH_INGEST_URL", "http://x/api/ingest")
    monkeypatch.setenv("COACH_INGEST_TOKEN", "tok")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert sweep.main(root=root, data_dir=data) == 0
    # no server reachable -> payload must be queued in the outbox
    assert len(list((data / "outbox").glob("*.json"))) == 1


def test_build_snapshot_v2_merges_usage(tmp_path):
    write_jsonl(tmp_path / "ledger.jsonl", LEDGER)
    write_jsonl(tmp_path / "classifications.jsonl", CLS)
    usage = {"activity": {"2026-08-01": {"sessions": 2, "prompts": 30},
                          "2026-08-05": {"sessions": 1, "prompts": 4}},
             "cost": [{"date": "2026-08-01", "input_tokens": 1, "output_tokens": 2,
                       "cache_read_tokens": 3, "cache_creation_tokens": 4,
                       "cost_usd": 0.5, "by_model": {"m": 0.5}}]}
    p = shipper.build_snapshot(tmp_path, ADOPTION, {"repos": 2},
                               captured_at="2026-08-06T11:30:00+00:00",
                               usage=usage)
    snapshot.validate_payload(p)
    assert p["schema_version"] == 4
    by_date = {a["date"]: a for a in p["activity_daily"]}
    assert by_date["2026-08-01"]["sessions"] == 2
    assert by_date["2026-08-01"]["prompts"] == 30
    assert by_date["2026-08-01"]["commits"] == 2          # from LEDGER
    assert by_date["2026-08-05"] == {"date": "2026-08-05", "commits": 0,
                                     "by_repo": {}, "sessions": 1, "prompts": 4}
    assert p["cost_daily"][0]["cost_usd"] == 0.5


def test_build_snapshot_no_usage_ships_empty_cost(tmp_path):
    p = shipper.build_snapshot(tmp_path, [], {}, captured_at="2026-08-06T11:30:00+00:00")
    snapshot.validate_payload(p)
    assert p["cost_daily"] == []


def test_build_snapshot_v3_carries_infra(tmp_path):
    infra = [{"capture_date": "2026-08-11", "period_start": "2026-07-27",
              "app": "b2b-ai-news", "cumulative_usd": 2.885146}]
    p = shipper.build_snapshot(tmp_path, [], {}, captured_at="2026-08-11T07:30:00+00:00",
                               infra=infra)
    snapshot.validate_payload(p)
    assert p["schema_version"] == 4
    assert p["infra_usage"] == infra


def test_build_snapshot_no_infra_ships_empty(tmp_path):
    p = shipper.build_snapshot(tmp_path, [], {}, captured_at="2026-08-11T07:30:00+00:00")
    snapshot.validate_payload(p)
    assert p["infra_usage"] == []


def test_build_snapshot_v4_carries_services(tmp_path):
    services = [{"capture_date": "2026-08-12", "period_start": "2026-07-27",
                 "app": "b2b-ai-news", "service_id": "svc-db",
                 "service_name": "Postgres", "cumulative_usd": 1.86,
                 "memory_usd": 1.75, "cpu_usd": 0.08, "egress_usd": 0.0,
                 "volume_usd": 0.03, "backup_usd": 0.0}]
    p = shipper.build_snapshot(tmp_path, [], {}, captured_at="2026-08-12T07:30:00+00:00",
                               services=services)
    snapshot.validate_payload(p)
    assert p["schema_version"] == 4
    assert p["infra_usage_services"] == services


def test_build_snapshot_no_services_ships_empty(tmp_path):
    p = shipper.build_snapshot(tmp_path, [], {}, captured_at="2026-08-12T07:30:00+00:00")
    snapshot.validate_payload(p)
    assert p["infra_usage_services"] == []
