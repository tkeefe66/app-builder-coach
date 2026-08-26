from datetime import date

from apps.coach_web import aggregate

ROWS = [
    {"date": "2026-07-27", "commits": 3, "by_repo": {"a": 3}},  # Mon
    {"date": "2026-07-28", "commits": 2, "by_repo": {"a": 1, "b": 1}},
    {"date": "2026-07-30", "commits": 1, "by_repo": {"b": 1}},  # gap on 29th
    {"date": "2026-07-20", "commits": 5, "by_repo": {"a": 5}},  # prior week Mon
]
TODAY = date(2026, 7, 31)  # Friday


def test_week_start_is_monday():
    assert aggregate.week_start(date(2026, 7, 31)) == date(2026, 7, 27)
    assert aggregate.week_start(date(2026, 7, 27)) == date(2026, 7, 27)


def test_weekly_rollup_zero_fills_and_orders():
    weeks = aggregate.weekly_rollup(ROWS, weeks=3, today=TODAY)
    assert [w["start"] for w in weeks] == ["2026-07-13", "2026-07-20", "2026-07-27"]
    assert weeks[0]["commits"] == 0
    assert weeks[1]["commits"] == 5
    assert weeks[2]["commits"] == 6
    assert weeks[2]["by_repo"] == {"a": 4, "b": 2}


def test_streak_counts_back_from_last_active_over_gap():
    s = aggregate.streak(ROWS, today=TODAY)
    assert s == {"days": 1, "last_active": "2026-07-30"}  # 30th active, 29th gap


def test_streak_consecutive():
    rows = [{"date": "2026-07-29", "commits": 1, "by_repo": {}},
            {"date": "2026-07-30", "commits": 2, "by_repo": {}},
            {"date": "2026-07-31", "commits": 1, "by_repo": {}}]
    assert aggregate.streak(rows, today=TODAY) == {"days": 3, "last_active": "2026-07-31"}


def test_streak_empty():
    assert aggregate.streak([], today=TODAY) == {"days": 0, "last_active": None}


def test_weekday_totals_monday_first():
    totals = aggregate.weekday_totals(ROWS)
    assert totals[0] == 8   # both Mondays
    assert totals[1] == 2   # Tuesday
    assert totals[3] == 1   # Thursday
    assert sum(totals) == 11


def test_monthly_counts_zero_fills():
    months = aggregate.monthly_counts(
        ["2026-07-01", "2026-07-15", "2026-05-02"], months=4, today=TODAY)
    assert months == [{"month": "2026-04", "count": 0},
                      {"month": "2026-05", "count": 1},
                      {"month": "2026-06", "count": 0},
                      {"month": "2026-07", "count": 2}]
