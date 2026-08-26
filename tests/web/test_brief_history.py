from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

TODAY = date(2026, 8, 11)


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/h.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def add_brief(db, day, kind="delta"):
    row = models.Brief(created_at=f"{day}T07:00:00+00:00", kind=kind, day=day,
                       model="m", status="ok", body="prose")
    db.add(row)
    db.commit()
    return row


def add_rec(db, brief_row, target, outcome="open", kind="tag"):
    rec = models.BriefRecommendation(
        brief_id=brief_row.id, ord=0, title=f"Do {target}", kind=kind,
        target=target, why="w", evidence="e", outcome=outcome)
    db.add(rec)
    db.commit()
    return rec


def test_history_counts_repeat_suggestions(tmp_path):
    with make_db(tmp_path) as db:
        for day in ("2026-08-01", "2026-08-05", "2026-08-09"):
            add_rec(db, add_brief(db, day), "deploy-docker")
        hist = {h["target"]: h for h in brief.recommendation_history(db)}
        assert hist["deploy-docker"]["times"] == 3
        assert hist["deploy-docker"]["first"] == "2026-08-01"
        assert hist["deploy-docker"]["last"] == "2026-08-09"
        assert hist["deploy-docker"]["outcome"] == "open"


def test_history_reports_the_strongest_outcome(tmp_path):
    # Converted beats dismissed beats open: what matters is whether it ever
    # led anywhere.
    with make_db(tmp_path) as db:
        add_rec(db, add_brief(db, "2026-08-01"), "deploy-docker", "superseded")
        add_rec(db, add_brief(db, "2026-08-05"), "deploy-docker", "converted")
        hist = {h["target"]: h for h in brief.recommendation_history(db)}
        assert hist["deploy-docker"]["outcome"] == "converted"


def test_history_is_empty_with_no_recommendations(tmp_path):
    with make_db(tmp_path) as db:
        assert brief.recommendation_history(db) == []


def test_delta_prompt_names_ignored_recommendations(tmp_path):
    # The rule that makes the coach stop repeating itself.
    with make_db(tmp_path) as db:
        for day in ("2026-08-01", "2026-08-05", "2026-08-09"):
            add_rec(db, add_brief(db, day), "deploy-docker")
        assessment = add_brief(db, "2026-08-01", kind="assessment")
        ctx = brief.build_delta_context(db, TODAY, assessment)
        text = brief.render_delta_prompt(ctx)
        assert "deploy-docker" in text
        assert "suggested 3x" in text
        assert "never acted on" in text


def test_delta_context_carries_the_assessment(tmp_path):
    with make_db(tmp_path) as db:
        assessment = add_brief(db, "2026-08-01", kind="assessment")
        assessment.body = "You build fast and deploy by hand."
        add_rec(db, assessment, "deploy-docker")
        db.commit()
        ctx = brief.build_delta_context(db, TODAY, assessment)
        assert ctx["assessment_summary"] == "You build fast and deploy by hand."
        assert ctx["assessment_day"] == "2026-08-01"
        assert ctx["assessment_recommendations"][0]["target"] == "deploy-docker"


def test_history_ignores_empty_day_in_span(tmp_path):
    # A recommendation with one day="" brief and one real-day brief
    # should report the real date, not "" as first/last.
    with make_db(tmp_path) as db:
        brief_empty = models.Brief(
            created_at="2026-08-01T07:00:00+00:00", kind="delta", day="",
            model="m", status="ok", body="prose")
        db.add(brief_empty)
        db.commit()
        add_rec(db, brief_empty, "deploy-docker")
        add_rec(db, add_brief(db, "2026-08-05"), "deploy-docker")
        hist = {h["target"]: h for h in brief.recommendation_history(db)}
        assert hist["deploy-docker"]["first"] == "2026-08-05"
        assert hist["deploy-docker"]["last"] == "2026-08-05"
        assert hist["deploy-docker"]["times"] == 2


def test_describe_change_filters_goals_and_checkoffs(tmp_path):
    # describe_change should include only active goals and recent check-offs.
    with make_db(tmp_path) as db:
        now_ts = "2026-08-11T00:00:00+00:00"
        # Create a done goal (should be excluded)
        done_goal = models.Goal(
            title="Old goal", kind="feature", target="old-feat", status="done",
            created_at=now_ts)
        db.add(done_goal)
        # Create an active goal (should be included)
        active_goal = models.Goal(
            title="Current goal", kind="feature", target="new-feat",
            status="active", created_at=now_ts)
        db.add(active_goal)
        # Create an old check-off from before the window (should be excluded)
        old_cutoff = TODAY - timedelta(days=brief.RECENT_WINDOW_DAYS + 1)
        old_checkoff = models.FeatureCheckoff(
            feature_name="old-feature",
            checked_at=f"{old_cutoff}T10:00:00+00:00")
        db.add(old_checkoff)
        # Create a recent check-off within the window (should be included)
        recent_cutoff = TODAY - timedelta(days=brief.RECENT_WINDOW_DAYS - 1)
        recent_checkoff = models.FeatureCheckoff(
            feature_name="recent-feature",
            checked_at=f"{recent_cutoff}T10:00:00+00:00")
        db.add(recent_checkoff)
        db.commit()

        changes = brief.describe_change(db, TODAY)
        text = "\n".join(changes)

        # Should include active goal, not done goal
        assert "Current goal" in text
        assert "Old goal" not in text

        # Should include recent check-off, not old one
        assert "recent-feature" in text
        assert "old-feature" not in text


def test_corpus_context_includes_recommendation_history(tmp_path):
    # The assessment path must see the same history the delta path does --
    # otherwise Sonnet re-recommends what Haiku was already told to drop.
    with make_db(tmp_path) as db:
        add_rec(db, add_brief(db, "2026-08-01"), "deploy-docker")
        ctx = brief.build_corpus_context(db, TODAY)
        assert ctx["history"] == brief.recommendation_history(db)
        assert ctx["history"]


def test_corpus_prompt_names_ignored_recommendations(tmp_path):
    # Same rule as the delta path: a repeatedly-ignored target must be named,
    # with its count and fate, in the prompt Sonnet actually sees.
    with make_db(tmp_path) as db:
        for day in ("2026-08-01", "2026-08-05", "2026-08-09"):
            add_rec(db, add_brief(db, day), "deploy-docker")
        ctx = brief.build_corpus_context(db, TODAY)
        text = brief.render_corpus_prompt(ctx)
        assert "deploy-docker" in text
        assert "suggested 3x" in text
        assert "never acted on" in text


def test_assessment_system_carries_the_repeat_rule():
    # DELTA_SYSTEM has always carried this rule; ASSESSMENT_SYSTEM must too,
    # since reassessment is the most frequent path to a fresh recommendation
    # list and needs the same guard against re-pitching ignored targets.
    assert ("made three or more times and was never acted on"
            in brief.ASSESSMENT_SYSTEM)
