from datetime import date

from tests.web.test_api_phase2 import login, make_rich_payload
from tests.web.test_ingest import AUTH


def test_overview_grade_null_when_no_units(client):
    login(client)
    assert client.get("/api/overview").json()["grade"] is None


def test_overview_grade_present_with_shape(client):
    today = date.today()
    client.post("/api/ingest", json=make_rich_payload(today), headers=AUTH)
    login(client)
    g = client.get("/api/overview").json()["grade"]
    # rich payload = 2 units (auth recent, caching 200d old): breadth 2,
    # below beginner's 3 -> newcomer, progressing toward beginner.
    assert g["level"] == "newcomer"
    assert g["level_label"] == "Newcomer"
    assert g["next_level"] == "beginner"
    assert 0 < g["percent_to_next"] < 100
    assert g["gaps"] == []  # beginner is breadth-only, no per-tag gates
