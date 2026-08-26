import json
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

NOW = datetime(2026, 8, 11, 7, 0, tzinfo=timezone.utc)
TODAY = date(2026, 8, 11)


@pytest.fixture(autouse=True)
def _no_ambient_model_env(monkeypatch):
    # An exported COACH_ASSESSMENT_MODEL/COACH_BRIEF_MODEL in the ambient shell
    # would make the model-name assertions below spuriously fail.
    monkeypatch.delenv("COACH_ASSESSMENT_MODEL", raising=False)
    monkeypatch.delenv("COACH_BRIEF_MODEL", raising=False)


class FakeMessages:
    def __init__(self, reply, raises=None, stop_reason="end_turn"):
        self.reply, self.raises, self.kwargs = reply, raises, None
        self.stop_reason = stop_reason

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.raises:
            raise self.raises
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.reply)],
            stop_reason=self.stop_reason,
            usage=SimpleNamespace(input_tokens=500, output_tokens=250,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0))


class FakeClient:
    def __init__(self, reply, raises=None, stop_reason="end_turn"):
        self.messages = FakeMessages(reply, raises, stop_reason)


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/gen.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def seed(db):
    db.add(models.FeatureUnit(key="u1", kind="spec", repo="alpha",
                              date="2026-08-05", title="Login",
                              tags=["auth"], complexity=3, summary="did login",
                              model="m"))
    db.commit()


def payload(targets):
    return json.dumps({
        "summary": "You ship fast and deploy by hand.",
        "recommendations": [
            {"title": f"Do {t}", "kind": "tag", "target": t,
             "why": "because", "evidence": "1 unit, no containers"}
            for t in targets],
    })


def test_assessment_stores_summary_and_recommendations(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient(payload(["deploy-docker", "websockets-sse"]))
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        assert row.status == "ok"
        assert row.kind == "assessment"
        assert row.day == "2026-08-11"
        assert row.body == "You ship fast and deploy by hand."
        assert row.model == brief.ASSESSMENT_MODEL
        recs = list(db.scalars(select(models.BriefRecommendation)
                               .order_by(models.BriefRecommendation.ord)))
        assert [r.target for r in recs] == ["deploy-docker", "websockets-sse"]
        assert recs[0].evidence == "1 unit, no containers"
        assert recs[0].outcome == "open"


def test_assessment_sends_effort_and_adaptive_thinking(tmp_path):
    # The assessment runs twice a month over ~25k tokens; it is the one call
    # where quality shows. Sonnet 5 accepts both.
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient(payload(["deploy-docker"]))
        brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        sent = client.messages.kwargs
        assert sent["thinking"] == {"type": "adaptive"}
        assert sent["output_config"]["effort"] == "medium"
        assert sent["output_config"]["format"]["type"] == "json_schema"


def test_out_of_vocabulary_target_is_dropped_and_siblings_survive(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient(payload(["deploy-docker", "not-a-real-tag"]))
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        recs = list(db.scalars(select(models.BriefRecommendation)))
        assert [r.target for r in recs] == ["deploy-docker"]
        assert row.status == "ok"


def test_unparseable_json_degrades_instead_of_failing(tmp_path):
    # A degraded brief beats no brief; `failed` stays reserved for call failures,
    # where a run of failed rows is the only signal of a misconfigured key.
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient("Build a Docker pipeline first.")
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        assert row.status == "ok"
        assert row.body == "Build a Docker pipeline first."
        assert db.scalars(select(models.BriefRecommendation)).all() == []


def test_call_failure_records_a_failed_row_and_does_not_raise(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient("", raises=RuntimeError("429"))
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        assert row.status == "failed"
        assert "RuntimeError: 429" in row.error


def test_missing_api_key_records_a_failed_row(tmp_path):
    with make_db(tmp_path) as db:
        row = brief.generate_assessment(db, client_factory=lambda: None, now=NOW)
        db.commit()
        assert row.status == "failed"
        assert "ANTHROPIC_API_KEY" in row.error


def test_reissuing_a_target_supersedes_the_prior_open_row(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        first = brief.generate_assessment(
            db, client_factory=lambda: FakeClient(payload(["deploy-docker"])), now=NOW)
        db.commit()
        old = db.scalars(select(models.BriefRecommendation)
                         .where(models.BriefRecommendation.brief_id == first.id)).one()
        brief.generate_delta(
            db, first,
            client_factory=lambda: FakeClient(payload(["deploy-docker"])), now=NOW)
        db.commit()
        db.refresh(old)
        assert old.outcome == "superseded"
        live = db.scalars(select(models.BriefRecommendation)
                          .where(models.BriefRecommendation.outcome == "open")).all()
        assert len(live) == 1


def test_generation_reports_its_spend_to_llm_daily(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        brief.generate_assessment(
            db, client_factory=lambda: FakeClient(payload(["deploy-docker"])), now=NOW)
        db.commit()
        rows = list(db.scalars(select(models.LlmDaily)))
        assert rows and rows[0].app == "app-builder-coach"
        assert rows[0].cost_usd > 0


def test_assessment_honours_the_model_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("COACH_ASSESSMENT_MODEL", "claude-opus-5")
    with make_db(tmp_path) as db:
        seed(db)
        row = brief.generate_assessment(
            db, client_factory=lambda: FakeClient(payload(["deploy-docker"])), now=NOW)
        assert row.model == "claude-opus-5"


def test_recommendation_kind_is_derived_from_vocabulary_not_response(tmp_path):
    # "hooks" is an adoption gap (a feature), but the model mislabels it "tag".
    # _store must derive kind from the vocabulary it built, not trust the
    # model's own field -- otherwise a wrong kind here strands the row where
    # (kind, target)-keyed outcome tracking would never find it.
    with make_db(tmp_path) as db:
        seed(db)
        snap = models.Snapshot(captured_at="2026-08-01T00:00:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.AdoptionHistory(snapshot_id=snap.id, feature_name="hooks",
                                      lesson="09", status="never-touched"))
        db.commit()
        reply = json.dumps({
            "summary": "s",
            "recommendations": [
                {"title": "Use hooks", "kind": "tag", "target": "hooks",
                 "why": "because", "evidence": "e"},
            ],
        })
        client = FakeClient(reply)
        brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        rec = db.scalars(select(models.BriefRecommendation)).one()
        assert rec.target == "hooks"
        assert rec.kind == "feature"


def test_truncated_response_records_a_failed_row_but_still_reports_spend(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        client = FakeClient(payload(["deploy-docker"]), stop_reason="max_tokens")
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        assert row.status == "failed"
        assert "truncat" in row.error.lower()
        rows = list(db.scalars(select(models.LlmDaily)))
        assert rows and rows[0].cost_usd > 0
        assert db.scalars(select(models.BriefRecommendation)).all() == []


def test_recommendations_as_dict_degrades_to_prose_with_no_rows(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        reply = json.dumps({"summary": "s", "recommendations": {"a": 1}})
        client = FakeClient(reply)
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        assert row.status == "ok"
        assert row.body == reply
        assert db.scalars(select(models.BriefRecommendation)).all() == []


def test_recommendations_as_string_degrades_to_prose_with_no_rows(tmp_path):
    with make_db(tmp_path) as db:
        seed(db)
        reply = json.dumps({"summary": "s", "recommendations": "abc"})
        client = FakeClient(reply)
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        assert row.status == "ok"
        assert row.body == reply
        assert db.scalars(select(models.BriefRecommendation)).all() == []


def test_malformed_entry_mid_list_leaves_no_half_written_brief(tmp_path):
    # recommendations is a valid list (passes _parse's shape check), but one
    # entry is not a dict. _store must validate every entry before adding any
    # row, so the valid sibling never ends up committed alongside a brief that
    # the outer handler is about to mark failed.
    with make_db(tmp_path) as db:
        seed(db)
        reply = json.dumps({
            "summary": "s",
            "recommendations": [
                {"title": "Do deploy-docker", "kind": "tag",
                 "target": "deploy-docker", "why": "because", "evidence": "e"},
                "not-a-dict",
            ],
        })
        client = FakeClient(reply)
        row = brief.generate_assessment(db, client_factory=lambda: client, now=NOW)
        db.commit()
        assert row.status == "ok"
        recs = list(db.scalars(select(models.BriefRecommendation)))
        assert [r.target for r in recs] == ["deploy-docker"]
