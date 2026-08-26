from datetime import date, timedelta
from types import SimpleNamespace

from apps.coach_web import truecost
from tests.web.test_ingest import AUTH
from tests.web.test_usage_api import AUTH as USAGE_AUTH, body


def row(period, app, capture, usd):
    return SimpleNamespace(period_start=period, app=app,
                           capture_date=capture, cumulative_usd=usd)


def test_daily_infra_derives_deltas():
    rows = [row("2026-07-27", "a", "2026-08-09", 1.0),
            row("2026-07-27", "a", "2026-08-10", 2.5),
            row("2026-07-27", "a", "2026-08-11", 3.0)]
    daily = truecost.daily_infra(rows)
    assert daily["2026-08-09"]["a"] == 1.0   # first capture in period = cumulative
    assert daily["2026-08-10"]["a"] == 1.5
    assert daily["2026-08-11"]["a"] == 0.5


def test_daily_infra_handles_period_rollover():
    rows = [row("2026-07-27", "a", "2026-08-26", 12.0),
            row("2026-08-27", "a", "2026-08-27", 0.4)]
    daily = truecost.daily_infra(rows)
    assert daily["2026-08-26"]["a"] == 12.0
    assert daily["2026-08-27"]["a"] == 0.4   # new period, not a negative delta


def test_daily_infra_clamps_decreasing_cumulative_to_zero():
    # A same-period decrease is a Railway restatement, not a new series -- the
    # correct daily spend is 0.0, not the re-booked cumulative (4.0), which
    # would double-count the 5.0 already attributed to the prior capture.
    rows = [row("2026-07-27", "a", "2026-08-10", 5.0),
            row("2026-07-27", "a", "2026-08-11", 4.0)]
    assert truecost.daily_infra(rows)["2026-08-11"]["a"] == 0.0


def test_window_sums():
    daily = {"2026-08-01": {"a": 1.0}, "2026-08-05": {"a": 2.0, "b": 3.0},
             "2026-07-01": {"a": 99.0}}
    assert truecost.window_sums(daily, "2026-08-01", "2026-08-31") == {"a": 3.0, "b": 3.0}


def test_period_to_date_sums_latest_capture_per_app_in_latest_period():
    rows = [
        # app "a": two captures in the older period (must be ignored) and two
        # captures in the latest period -- only the latest (by capture_date)
        # counts. Its latest capture in the period is 2026-08-05.
        row("2026-06-27", "a", "2026-07-01", 50.0),
        row("2026-07-27", "a", "2026-08-01", 1.0),
        row("2026-07-27", "a", "2026-08-05", 4.0),
        # app "b": multiple captures in the latest period only, out of order,
        # with its latest capture on a DIFFERENT date (2026-08-10) than app
        # "a"'s. A buggy implementation that filters to a single global max
        # capture_date across the whole period (rather than per app) would
        # find no row for "a" on 2026-08-10 and drop its contribution,
        # producing 9.0 instead of 13.0.
        row("2026-07-27", "b", "2026-08-01", 2.0),
        row("2026-07-27", "b", "2026-08-10", 9.0),
    ]
    period_start, total = truecost.period_to_date(rows)
    assert period_start == "2026-07-27"
    assert total == 13.0   # a's latest (4.0) + b's latest (9.0)


def test_period_to_date_empty():
    assert truecost.period_to_date([]) == (None, 0.0)


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def test_truecost_empty(client):
    login(client)
    data = client.get("/api/truecost").json()
    assert data["apps"] == []
    assert data["totals"] == {"railway_usd": 0.0, "llm_usd": 0.0, "total_usd": 0.0}
    assert data["drift"] is None


def test_truecost_joins_both_lanes(client):
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    from shared import snapshot as snap_mod
    payload = snap_mod.finalize_payload({
        "schema_version": 3, "sweep": {}, "feature_units": [],
        "activity_daily": [], "adoption": [], "cost_daily": [],
        "infra_usage": [
            {"capture_date": yesterday, "period_start": yesterday,
             "app": "app-builder-coach", "cumulative_usd": 1.0},
            {"capture_date": today, "period_start": yesterday,
             "app": "app-builder-coach", "cumulative_usd": 3.0},
        ]}, f"{today}T07:30:00+00:00")
    client.post("/api/ingest", json=payload, headers=AUTH)
    client.post("/api/usage", json=body(ts=f"{today}T14:00:00Z"), headers=USAGE_AUTH)
    login(client)

    data = client.get("/api/truecost?days=30").json()
    entry = next(a for a in data["apps"] if a["app"] == "app-builder-coach")
    assert entry["railway_usd"] == 3.0        # 1.0 + 2.0 delta
    assert entry["llm_usd"] == 1.0            # 1 MTok haiku input
    assert entry["total_usd"] == 4.0
    assert entry["display"] == "App Builder Coach"
    assert data["totals"]["total_usd"] == 4.0
    assert data["cache"]["input_tokens"] == 1_000_000


def test_truecost_drift_when_configured(client, monkeypatch):
    today = date.today().isoformat()
    monkeypatch.setenv("COACH_CONSOLE_FROM", today)
    monkeypatch.setenv("COACH_CONSOLE_TO", today)
    monkeypatch.setenv("COACH_CONSOLE_SPEND", "5.00")
    client.post("/api/usage", json=body(ts=f"{today}T14:00:00Z"), headers=USAGE_AUTH)
    login(client)
    drift = client.get("/api/truecost").json()["drift"]
    assert drift["console_usd"] == 5.0
    assert drift["tracked_usd"] == 1.0
    assert drift["gap_usd"] == 4.0


def test_truecost_drift_strips_dollar_sign_and_commas(client, monkeypatch):
    # The Anthropic Console renders spend as e.g. "$8.90" -- an operator
    # copying that figure verbatim must not 500 the endpoint.
    today = date.today().isoformat()
    monkeypatch.setenv("COACH_CONSOLE_FROM", today)
    monkeypatch.setenv("COACH_CONSOLE_TO", today)
    monkeypatch.setenv("COACH_CONSOLE_SPEND", "$1,008.90")
    client.post("/api/usage", json=body(ts=f"{today}T14:00:00Z"), headers=USAGE_AUTH)
    login(client)
    resp = client.get("/api/truecost")
    assert resp.status_code == 200
    assert resp.json()["drift"]["console_usd"] == 1008.90


def test_truecost_drift_none_on_malformed_date(client, monkeypatch):
    today = date.today().isoformat()
    monkeypatch.setenv("COACH_CONSOLE_FROM", "Aug 1, 2026")
    monkeypatch.setenv("COACH_CONSOLE_TO", today)
    monkeypatch.setenv("COACH_CONSOLE_SPEND", "5.00")
    client.post("/api/usage", json=body(ts=f"{today}T14:00:00Z"), headers=USAGE_AUTH)
    login(client)
    resp = client.get("/api/truecost")
    assert resp.status_code == 200
    assert resp.json()["drift"] is None


def svc(period, app, sid, capture, total, memory, name="Postgres"):
    return SimpleNamespace(period_start=period, app=app, service_id=sid,
                           capture_date=capture, cumulative_usd=total,
                           memory_usd=memory, service_name=name)


def test_daily_infra_services_derives_deltas_per_service():
    rows = [svc("2026-07-27", "a", "s1", "2026-08-10", 1.0, 0.9),
            svc("2026-07-27", "a", "s1", "2026-08-11", 2.5, 2.4),
            svc("2026-07-27", "a", "s2", "2026-08-11", 4.0, 3.9)]
    daily = truecost.daily_infra_services(rows)
    assert daily["2026-08-10"][("a", "s1")] == 1.0
    assert daily["2026-08-11"][("a", "s1")] == 1.5
    assert daily["2026-08-11"][("a", "s2")] == 4.0   # first capture = cumulative


def test_daily_infra_services_clamps_decrease_and_handles_rollover():
    rows = [svc("2026-07-27", "a", "s1", "2026-08-10", 5.0, 5.0),
            svc("2026-07-27", "a", "s1", "2026-08-11", 4.0, 4.0),
            svc("2026-08-27", "a", "s1", "2026-08-27", 0.4, 0.4)]
    daily = truecost.daily_infra_services(rows)
    assert daily["2026-08-11"][("a", "s1")] == 0.0   # decrease clamps, never negative
    assert daily["2026-08-27"][("a", "s1")] == 0.4   # new period, not a delta


def test_daily_infra_services_selects_field():
    rows = [svc("2026-07-27", "a", "s1", "2026-08-10", 10.0, 9.0)]
    assert truecost.daily_infra_services(rows, "memory_usd")["2026-08-10"][("a", "s1")] == 9.0


def test_window_sums_works_unmodified_with_tuple_keys():
    # Pins the claim that window_sums is key-agnostic, so daily_infra_services
    # can hand it composite keys without touching it.
    daily = {"2026-08-01": {("a", "s1"): 1.0, ("a", "s2"): 2.0},
             "2026-07-01": {("a", "s1"): 99.0}}
    assert truecost.window_sums(daily, "2026-08-01", "2026-08-31") == {
        ("a", "s1"): 1.0, ("a", "s2"): 2.0}


def test_services_by_app_shapes_and_sorts():
    totals = {("a", "s1"): 4.0, ("a", "s2"): 6.0, ("b", "s3"): 1.0}
    memory = {("a", "s1"): 3.0, ("a", "s2"): 5.5, ("b", "s3"): 0.5}
    names = {("a", "s1"): "web", ("a", "s2"): "Postgres", ("b", "s3"): "web"}
    out = truecost.services_by_app(totals, memory, names)
    assert [s["service"] for s in out["a"]] == ["Postgres", "web"]   # desc by total
    assert out["a"][0]["total_usd"] == 6.0
    assert out["a"][0]["memory_usd"] == 5.5
    assert out["a"][0]["share_of_app"] == 0.6                        # 6.0 / 10.0
    assert round(sum(s["share_of_app"] for s in out["a"]), 4) == 1.0


def test_services_by_app_zero_total():
    out = truecost.services_by_app({("a", "s1"): 0.0}, {("a", "s1"): 0.0},
                                   {("a", "s1"): "web"})
    assert out["a"][0]["share_of_app"] == 0.0    # no ZeroDivisionError


def test_truecost_includes_services(client):
    today = date.today().isoformat()
    from shared import snapshot as snap_mod
    # Both infra lanes ship together, as the sweep sends them: an app enters the
    # table via its app-level railway/llm spend, and `services` is the drill-down
    # on that row. A services-only fixture would test a shape the sweep never
    # produces -- and an app listed at $0.00 beside $3.00 of services would be a
    # worse answer than the app being absent.
    payload = snap_mod.finalize_payload({
        "schema_version": 4, "sweep": {}, "feature_units": [], "activity_daily": [],
        "adoption": [], "cost_daily": [],
        "infra_usage": [{"capture_date": today, "period_start": today,
                         "app": "app-builder-coach", "cumulative_usd": 3.0}],
        "infra_usage_services": [
            {"capture_date": today, "period_start": today, "app": "app-builder-coach",
             "service_id": "svc-db", "service_name": "Postgres", "cumulative_usd": 3.0,
             "memory_usd": 2.8, "cpu_usd": 0.2, "egress_usd": 0.0,
             "volume_usd": 0.0, "backup_usd": 0.0}]}, f"{today}T07:30:00+00:00")
    client.post("/api/ingest", json=payload, headers=AUTH)
    login(client)
    data = client.get("/api/truecost?days=30").json()
    entry = next(a for a in data["apps"] if a["app"] == "app-builder-coach")
    assert entry["services"][0]["service"] == "Postgres"
    assert entry["services"][0]["memory_usd"] == 2.8


def test_truecost_omits_services_when_absent(client):
    login(client)
    assert client.get("/api/truecost").json()["apps"] == []


def _anchor(monkeypatch, days_old, spend="5.00"):
    """Pin the Console window to a period ending `days_old` days ago."""
    end = (date.today() - timedelta(days=days_old)).isoformat()
    start = (date.today() - timedelta(days=days_old + 30)).isoformat()
    monkeypatch.setenv("COACH_CONSOLE_FROM", start)
    monkeypatch.setenv("COACH_CONSOLE_TO", end)
    monkeypatch.setenv("COACH_CONSOLE_SPEND", spend)


def test_drift_age_days_counts_from_the_anchor_end(client, monkeypatch):
    _anchor(monkeypatch, 10)
    login(client)
    drift = client.get("/api/truecost").json()["drift"]
    assert drift["age_days"] == 10
    assert drift["stale"] is False


def test_drift_is_not_stale_at_the_threshold(client, monkeypatch):
    # 35 days, not 30: the anchor is re-set from a monthly Console figure, so a
    # 30-day threshold would flag as stale during the normal window between one
    # month's reading and the next.
    _anchor(monkeypatch, 35)
    login(client)
    drift = client.get("/api/truecost").json()["drift"]
    assert drift["age_days"] == 35
    assert drift["stale"] is False


def test_drift_is_stale_one_day_past_the_threshold(client, monkeypatch):
    _anchor(monkeypatch, 36)
    login(client)
    drift = client.get("/api/truecost").json()["drift"]
    assert drift["age_days"] == 36
    assert drift["stale"] is True


def test_drift_is_stale_at_production_neglect_magnitude(client, monkeypatch):
    # A forgotten anchor is months old, not days. Fixtures must span the real
    # magnitude -- a boundary test alone would pass against a broken unit.
    _anchor(monkeypatch, 180)
    login(client)
    drift = client.get("/api/truecost").json()["drift"]
    assert drift["age_days"] == 180
    assert drift["stale"] is True


def test_drift_is_stale_when_anchor_is_in_the_future(client, monkeypatch):
    # A typo'd COACH_CONSOLE_TO in the future must not silently disable the
    # stale check forever: negative age is a misconfiguration, not freshness.
    _anchor(monkeypatch, -30)
    login(client)
    drift = client.get("/api/truecost").json()["drift"]
    assert drift["age_days"] < 0
    assert drift["stale"] is True
