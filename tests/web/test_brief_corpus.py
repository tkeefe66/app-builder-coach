from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

TODAY = date(2026, 8, 11)


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/c.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def add_unit(db, key, repo, day, tags, complexity, title, summary):
    db.add(models.FeatureUnit(key=key, kind="spec", repo=repo, date=day,
                              title=title, tags=tags, complexity=complexity,
                              summary=summary, model="m"))


def test_corpus_rolls_up_repos(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", "alpha", "2026-01-05", ["auth"], 2, "Login", "did login")
        add_unit(db, "u2", "alpha", "2026-03-05", ["auth", "testing-depth"], 4,
                 "Tests", "did tests")
        add_unit(db, "u3", "beta", "2026-02-05", ["frontend-spa"], 3, "UI", "did ui")
        db.commit()
        ctx = brief.build_corpus_context(db, TODAY)
        by_name = {r["repo"]: r for r in ctx["repos"]}
        assert by_name["alpha"]["units"] == 2
        assert by_name["alpha"]["first"] == "2026-01-05"
        assert by_name["alpha"]["last"] == "2026-03-05"
        assert by_name["alpha"]["mean_complexity"] == 3.0
        assert sorted(by_name["alpha"]["tags"]) == ["auth", "testing-depth"]
        assert by_name["beta"]["units"] == 1


def test_corpus_carries_titles_and_summaries(tmp_path):
    # The whole point: the model has never seen the prose before.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", "alpha", "2026-01-05", ["auth"], 2,
                 "Add magic-link login", "Swapped passwords for emailed links.")
        db.commit()
        ctx = brief.build_corpus_context(db, TODAY)
        assert ctx["work"][0]["title"] == "Add magic-link login"
        assert ctx["work"][0]["summary"] == "Swapped passwords for emailed links."
        assert ctx["work"][0]["complexity"] == 2


def test_corpus_states_its_truncation(tmp_path):
    # Silent truncation reads to the model as a complete corpus and produces
    # confidently wrong conclusions about coverage.
    with make_db(tmp_path) as db:
        for i in range(brief.MAX_RECENT_UNITS + brief.MAX_COMPLEX_UNITS + 40):
            day = f"2026-01-{(i % 28) + 1:02d}"
            add_unit(db, f"u{i}", "alpha", day, ["auth"], (i % 5) + 1,
                     f"t{i}", f"s{i}")
        db.commit()
        ctx = brief.build_corpus_context(db, TODAY)
        assert len(ctx["work"]) <= brief.MAX_RECENT_UNITS + brief.MAX_COMPLEX_UNITS
        assert "of" in ctx["work_note"]
        assert str(brief.MAX_RECENT_UNITS + brief.MAX_COMPLEX_UNITS + 40) in ctx["work_note"]
        assert ctx["work_note"] in brief.render_corpus_prompt(ctx)


def test_corpus_note_is_empty_when_nothing_was_dropped(tmp_path):
    with make_db(tmp_path) as db:
        add_unit(db, "u1", "alpha", "2026-01-05", ["auth"], 2, "t", "s")
        db.commit()
        assert brief.build_corpus_context(db, TODAY)["work_note"] == ""


def test_corpus_includes_the_grade_and_its_best_fit_repos(tmp_path):
    # compute_grade already works out which repo should host a missing tag.
    # It has never been shown to the model.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", "alpha", "2026-08-01", ["auth"], 3, "t", "s")
        db.commit()
        ctx = brief.build_corpus_context(db, TODAY)
        assert ctx["grade"] is not None
        assert "gaps" in ctx["grade"]


def test_corpus_names_dismissals_explicitly(tmp_path):
    # A dismissed gap was considered and waved off. The model must know that,
    # not just see it missing.
    with make_db(tmp_path) as db:
        add_unit(db, "u1", "alpha", "2026-08-01", ["auth"], 3, "t", "s")
        db.add(models.Dismissal(kind="tag", target="scraping", reason="no need",
                                created_at="2026-08-01T00:00:00+00:00"))
        db.commit()
        ctx = brief.build_corpus_context(db, TODAY)
        assert {"kind": "tag", "target": "scraping",
                "reason": "no need"} in ctx["commitments"]["dismissed"]
        assert "scraping" not in ctx["never_built"]


def test_corpus_on_empty_database(tmp_path):
    with make_db(tmp_path) as db:
        ctx = brief.build_corpus_context(db, TODAY)
        assert ctx["repos"] == []
        assert ctx["work"] == []
        assert ctx["grade"] is None
        assert brief.render_corpus_prompt(ctx)  # renders without raising
