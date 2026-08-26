import json
from pathlib import Path

from src import shipper
from tests.web.test_shipper import ADOPTION, CLS, LEDGER, write_jsonl


def client_post(client):
    def post(url, json=None, headers=None, timeout=None):
        return client.post("/api/ingest", json=json, headers=headers)
    return post


def test_sweep_data_ships_and_lands(client, tmp_path):
    write_jsonl(tmp_path / "ledger.jsonl", LEDGER)
    write_jsonl(tmp_path / "classifications.jsonl", CLS)
    payload = shipper.build_snapshot(tmp_path, ADOPTION, {"repos": 2},
                                     captured_at="2026-08-02T07:30:00+00:00")
    result = shipper.ship_all(payload, "/api/ingest", "test-ingest-token",
                              tmp_path / "outbox", post=client_post(client))
    assert result == {"shipped": 1, "queued": 0, "rejected": 0}

    client.post("/api/login", json={"password": "correct-horse"})
    data = client.get("/api/summary").json()
    assert data["unit_count"] == 1
    assert data["latest"]["sweep_stats"] == {"repos": 2}


def test_outbox_drains_into_app(client, tmp_path):
    payload = shipper.build_snapshot(tmp_path, ADOPTION, {"repos": 1},
                                     captured_at="2026-08-02T07:30:00+00:00")
    outbox = tmp_path / "outbox"
    outbox.mkdir()
    (outbox / "2026-08-01-aaaa.json").write_text(json.dumps(payload))
    second = shipper.build_snapshot(tmp_path, ADOPTION, {"repos": 2},
                                    captured_at="2026-08-02T08:30:00+00:00")
    result = shipper.ship_all(second, "/api/ingest", "test-ingest-token",
                              outbox, post=client_post(client))
    assert result == {"shipped": 2, "queued": 0, "rejected": 0}
    assert not list(outbox.glob("*.json"))
