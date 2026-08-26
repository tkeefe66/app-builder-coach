import json
import subprocess

from src import railway_cost

PAYLOAD = {
    "billingPeriod": {"start": "2026-07-27T16:07:00+00:00",
                      "end": "2026-08-27T16:07:00+00:00"},
    "projects": [
        {"id": "rw-1", "name": "B2B AI News", "currentUsageDollars": 2.885146364211531},
        {"id": "rw-2", "name": "Other", "currentUsageDollars": 1.5},
        {"id": "rw-unknown", "name": "Not In Registry", "currentUsageDollars": 9.9},
    ],
}
APPS = [
    {"name": "b2b-ai-news", "display": "B2B AI News",
     "railway_project_id": "rw-1", "active": True},
    {"name": "other", "display": "Other",
     "railway_project_id": "rw-2", "active": True},
]


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_infra_rows_maps_and_truncates_period():
    rows = railway_cost.infra_rows(PAYLOAD, APPS, "2026-08-11")
    assert [r["app"] for r in rows] == ["b2b-ai-news", "other"]  # unknown id dropped
    assert rows[0]["period_start"] == "2026-07-27"  # timestamp truncated to date
    assert rows[0]["capture_date"] == "2026-08-11"
    assert rows[0]["cumulative_usd"] == 2.885146  # rounded to 6dp


def test_infra_rows_none_payload():
    assert railway_cost.infra_rows(None, APPS, "2026-08-11") == []


def test_infra_rows_bad_period():
    bad = {"billingPeriod": {}, "projects": PAYLOAD["projects"]}
    assert railway_cost.infra_rows(bad, APPS, "2026-08-11") == []


def test_fetch_usage_ok():
    def run(cmd, **kw):
        assert cmd == ["railway", "usage", "projects", "--json"]
        return FakeProc(stdout=json.dumps(PAYLOAD))
    assert railway_cost.fetch_usage(run=run)["projects"][0]["id"] == "rw-1"


def test_fetch_usage_nonzero_exit():
    assert railway_cost.fetch_usage(run=lambda *a, **k: FakeProc(1, stderr="unauthorized")) is None


def test_fetch_usage_not_json():
    assert railway_cost.fetch_usage(run=lambda *a, **k: FakeProc(0, stdout="not json")) is None


def test_fetch_usage_missing_cli():
    def run(*a, **k):
        raise FileNotFoundError("railway")
    assert railway_cost.fetch_usage(run=run) is None


def test_fetch_usage_timeout():
    def run(*a, **k):
        raise subprocess.TimeoutExpired("railway", 120)
    assert railway_cost.fetch_usage(run=run) is None


PROJECT_PAYLOAD = {
    "billingPeriod": {"start": "2026-07-27T16:07:00+00:00"},
    "project": {"id": "rw-1", "name": "B2B AI News"},
    "services": [
        {"id": "svc-db", "name": "Postgres", "totalDollars": 1.860755721362364,
         "memoryDollars": 1.7546751778741823, "cpuDollars": 0.07843898919753087,
         "egressDollars": 1.182e-07, "volumeDollars": 0.027641436090650975,
         "backupDollars": -0.0},
        {"id": "svc-app", "name": "B2B AI News", "totalDollars": 1.0796139809329401,
         "memoryDollars": 1.0508426830634956, "cpuDollars": 0.017767793819444447,
         "egressDollars": 0.011003504050000001, "volumeDollars": -0.0,
         "backupDollars": -0.0},
    ],
}


def test_fetch_usage_appends_project_flag():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return FakeProc(stdout=json.dumps(PROJECT_PAYLOAD))

    railway_cost.fetch_usage(run=run, project="rw-1")
    assert seen["cmd"] == ["railway", "usage", "projects", "--project", "rw-1", "--json"]


def test_fetch_usage_without_project_unchanged():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return FakeProc(stdout=json.dumps(PAYLOAD))

    railway_cost.fetch_usage(run=run)
    assert seen["cmd"] == ["railway", "usage", "projects", "--json"]


def test_service_rows_parses_and_normalizes():
    rows = railway_cost.service_rows(PROJECT_PAYLOAD, "b2b-ai-news", "2026-08-11", "rw-1")
    assert [r["service_id"] for r in rows] == ["svc-app", "svc-db"]  # sorted by service_id
    db = next(r for r in rows if r["service_id"] == "svc-db")
    assert db["app"] == "b2b-ai-news"
    assert db["service_name"] == "Postgres"
    assert db["period_start"] == "2026-07-27"
    assert db["cumulative_usd"] == 1.860756
    assert db["memory_usd"] == 1.754675
    assert db["egress_usd"] == 0.0          # 1.182e-07 rounds to zero
    # -0.0 must be normalized: repr(-0.0) is "-0.0" and it poisons downstream sums
    assert str(db["backup_usd"]) == "0.0"
    app_row = next(r for r in rows if r["service_id"] == "svc-app")
    assert str(app_row["volume_usd"]) == "0.0"


def test_service_rows_rejects_project_id_mismatch():
    assert railway_cost.service_rows(PROJECT_PAYLOAD, "b2b", "2026-08-11", "other-id") is None


def test_service_rows_none_payload():
    assert railway_cost.service_rows(None, "b2b", "2026-08-11", "rw-1") is None


def test_service_rows_bad_period():
    bad = dict(PROJECT_PAYLOAD, billingPeriod={})
    assert railway_cost.service_rows(bad, "b2b", "2026-08-11", "rw-1") is None


def test_service_rows_valid_empty_services():
    """A valid project with zero services must return [], not None, so it counts as OK."""
    payload = {
        "billingPeriod": {"start": "2026-07-27T16:07:00+00:00"},
        "project": {"id": "rw-1", "name": "Empty Project"},
        "services": [],
    }
    rows = railway_cost.service_rows(payload, "empty", "2026-08-11", "rw-1")
    assert rows == []  # empty list, not None


def test_collect_service_rows_partial_failure():
    apps = [
        {"name": "a", "display": "A", "railway_project_id": "rw-1", "active": True},
        {"name": "b", "display": "B", "railway_project_id": "rw-2", "active": True},
    ]

    def run(cmd, **kw):
        if "rw-2" in cmd:
            return FakeProc(1, stderr="boom")
        return FakeProc(stdout=json.dumps(PROJECT_PAYLOAD))

    rows, ok, attempted = railway_cost.collect_service_rows(apps, "2026-08-11", run=run)
    assert ok == 1 and attempted == 2
    assert {r["app"] for r in rows} == {"a"}   # the successful project still ships


def test_collect_service_rows_all_fail():
    apps = [{"name": "a", "display": "A", "railway_project_id": "rw-1", "active": True}]
    rows, ok, attempted = railway_cost.collect_service_rows(
        apps, "2026-08-11", run=lambda *a, **k: FakeProc(1))
    assert rows == [] and ok == 0 and attempted == 1


def test_collect_service_rows_counts_empty_project_as_ok():
    """Regression test: an empty but valid Railway project must count as OK.

    Before the fix, collect_service_rows used `if got:`, which is False for both
    None (unusable payload) and [] (empty services). This caused an empty project
    to be miscounted as failed, permanently reporting infra_services=partial(k/n).
    With `if got is not None:`, empty projects correctly count as OK.
    """
    apps = [{"name": "a", "display": "A", "railway_project_id": "rw-1", "active": True}]
    empty = dict(PROJECT_PAYLOAD, services=[])
    rows, ok, attempted = railway_cost.collect_service_rows(
        apps, "2026-08-11", run=lambda *a, **k: FakeProc(stdout=json.dumps(empty)))
    assert rows == []
    assert ok == 1 and attempted == 1
