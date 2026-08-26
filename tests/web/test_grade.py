from datetime import date, timedelta

from apps.coach_web.grade import (_level_fractions, best_fit_repo, compute_grade,
                                  gate_fraction, tag_stats)
from apps.coach_web.rubric import Gate, Level, Rubric

TODAY = date(2026, 8, 3)
RECENT = (TODAY - timedelta(days=10)).isoformat()
STALE = (TODAY - timedelta(days=200)).isoformat()


def make_rubric():
    return Rubric(
        tiers={"auth": "core", "api-backend": "core",
               "caching": "standard", "websockets-sse": "specialty"},
        levels=(
            Level(name="newcomer", label="Newcomer"),
            Level(name="junior", label="Junior", breadth=2,
                  gates={"api-backend": Gate(min_count=2)}),
            Level(name="mid", label="Mid", breadth=3,
                  gates={"auth": Gate(min_count=4, min_avg_complexity=3.0),
                         "api-backend": Gate(min_count=4)}),
            Level(name="senior", label="Senior", breadth=4, noncore=(2, 2),
                  gates={"auth": Gate(min_count=6, within_days=365)}),
        ),
        pairs_with={"websockets-sse": ["api-backend"]},
    )


def test_tag_stats_counts_avg_and_last_done():
    rows = [("a", "2026-01-01", ["auth"], 2),
            ("a", "2026-03-01", ["auth", "caching"], 5),
            ("b", "2026-02-01", ["auth"], 4)]
    s = tag_stats(rows)
    assert s["auth"]["count"] == 3
    assert s["auth"]["avg_complexity"] == 3.7  # (2+5+4)/3 rounded
    assert s["auth"]["last_done"] == "2026-03-01"
    assert s["caching"]["count"] == 1


def test_gate_fraction_partial_count():
    s = tag_stats([("a", RECENT, ["auth"], 3), ("a", RECENT, ["auth"], 3)])
    assert gate_fraction(Gate(min_count=4), s.get("auth"), TODAY) == 0.5


def test_gate_fraction_complexity_shortfall():
    s = tag_stats([("a", RECENT, ["auth"], 2), ("a", RECENT, ["auth"], 1)])
    # count 2/2 = 1.0, avg cx 1.5/3.0 = 0.5
    g = Gate(min_count=2, min_avg_complexity=3.0)
    assert gate_fraction(g, s.get("auth"), TODAY) == 0.5


def test_gate_fraction_stale_halves():
    s = tag_stats([("a", STALE, ["auth"], 3), ("a", STALE, ["auth"], 3)])
    assert gate_fraction(Gate(min_count=2), s.get("auth"), TODAY) == 0.5


def test_gate_fraction_missing_tag_is_zero():
    assert gate_fraction(Gate(min_count=1), None, TODAY) == 0.0


def test_gate_fraction_stacks_stale_and_within_days():
    old = (TODAY - timedelta(days=400)).isoformat()
    s = tag_stats([("a", old, ["auth"], 3)] * 6)
    # older than both STALE_DAYS (180) and within_days (365): both halvings apply
    g = Gate(min_count=6, within_days=365)
    assert gate_fraction(g, s.get("auth"), TODAY) == 0.25


def test_compute_grade_empty_rows_is_none():
    assert compute_grade([], make_rubric(), TODAY) is None


def test_attains_junior_with_progress_and_sorted_gaps():
    rows = [("alpha", RECENT, ["api-backend"], 3),
            ("alpha", RECENT, ["api-backend"], 3),
            ("beta", RECENT, ["auth"], 3)]
    g = compute_grade(rows, make_rubric(), TODAY)
    assert g["level"] == "junior"
    assert g["next_level"] == "mid"
    # mid fractions: auth 1/4*1=.25, api 2/4=.5, breadth 2/3=.667 -> 47%
    assert g["percent_to_next"] == 47
    assert [x["tag"] for x in g["gaps"]] == ["auth", "api-backend"]  # worst first
    assert g["gaps"][0]["have"]["count"] == 1
    assert g["gaps"][0]["need"]["min_count"] == 4


def test_stale_core_demotes():
    rows = [("alpha", STALE, ["api-backend"], 3),
            ("alpha", STALE, ["api-backend"], 3),
            ("beta", RECENT, ["auth"], 3)]
    g = compute_grade(rows, make_rubric(), TODAY)
    assert g["level"] == "newcomer"  # junior gate at 1.0*0.5 = 0.5 < 1


def test_top_level_has_no_next():
    rows = ([("a", RECENT, ["auth"], 4)] * 6
            + [("a", RECENT, ["api-backend"], 4)] * 4
            + [("a", RECENT, ["caching"], 4)] * 2
            + [("a", RECENT, ["websockets-sse"], 4)] * 2)
    g = compute_grade(rows, make_rubric(), TODAY)
    assert g["level"] == "senior"
    assert g["next_level"] is None and g["next_label"] is None
    assert g["percent_to_next"] == 100
    assert g["gaps"] == []


def test_never_built_gap_shape():
    rows = [("alpha", RECENT, ["api-backend"], 3),
            ("alpha", RECENT, ["api-backend"], 3),
            ("beta", RECENT, ["caching"], 3)]
    g = compute_grade(rows, make_rubric(), TODAY)
    auth_gap = next(x for x in g["gaps"] if x["tag"] == "auth")
    assert auth_gap["have"] == {"count": 0, "avg_complexity": None,
                                "last_done": None}


def test_best_fit_prefers_repo_with_most_recent_related_work():
    rubric = make_rubric()
    rows = [("alpha", RECENT, ["api-backend"], 3),
            ("alpha", RECENT, ["api-backend"], 3),
            ("beta", RECENT, ["api-backend"], 3)]
    assert best_fit_repo("websockets-sse", rubric, rows, TODAY) == "alpha"


def test_best_fit_tie_breaks_on_recency():
    rubric = make_rubric()
    older = (TODAY - timedelta(days=20)).isoformat()
    rows = [("alpha", older, ["api-backend"], 3),
            ("beta", RECENT, ["api-backend"], 3)]
    assert best_fit_repo("websockets-sse", rubric, rows, TODAY) == "beta"


def test_best_fit_falls_back_to_most_recent_repo():
    rubric = make_rubric()
    rows = [("alpha", "2026-01-01", ["caching"], 3),
            ("beta", RECENT, ["caching"], 3)]
    # auth has no pairs_with entry -> fallback
    assert best_fit_repo("auth", rubric, rows, TODAY) == "beta"


def test_best_fit_scored_tie_breaks_on_repo_name_deterministically():
    rubric = make_rubric()
    rows = [("alpha", RECENT, ["api-backend"], 3),
            ("beta", RECENT, ["api-backend"], 3)]  # equal count, equal date
    assert best_fit_repo("websockets-sse", rubric, rows, TODAY) == "beta"
    assert (best_fit_repo("websockets-sse", rubric, list(reversed(rows)), TODAY)
            == "beta")


def test_best_fit_fallback_tie_breaks_on_repo_name_deterministically():
    rubric = make_rubric()
    rows = [("alpha", RECENT, ["caching"], 3),
            ("beta", RECENT, ["caching"], 3)]  # equal date, no pairs -> fallback
    assert best_fit_repo("auth", rubric, rows, TODAY) == "beta"
    assert best_fit_repo("auth", rubric, list(reversed(rows)), TODAY) == "beta"


def test_percent_capped_at_99_when_not_fully_attained():
    rubric = Rubric(
        tiers={"auth": "core"},
        levels=(Level(name="newcomer", label="Newcomer"),
                Level(name="mid", label="Mid",
                      gates={"auth": Gate(min_count=1000)})),
        pairs_with={},
    )
    rows = [("a", RECENT, ["auth"], 3)] * 995  # 995/1000 = 0.995 -> round() gives
    g = compute_grade(rows, rubric, TODAY)     # 100 uncapped; must be capped to 99
    assert g["level"] == "newcomer"
    assert g["percent_to_next"] == 99


def test_unknown_tag_does_not_inflate_breadth_or_noncore():
    rubric = make_rubric()
    senior = rubric.levels[3]  # breadth=4, noncore=(2, 2)
    base_rows = ([("a", RECENT, ["auth"], 4)] * 6
                 + [("a", RECENT, ["caching"], 4)] * 2)
    stats_without = tag_stats(base_rows)
    stats_with = tag_stats(base_rows + [("a", RECENT, ["mystery-tag"], 4)] * 5)
    fracs_without = _level_fractions(senior, stats_without, rubric, TODAY)
    fracs_with = _level_fractions(senior, stats_with, rubric, TODAY)
    assert fracs_without == fracs_with
