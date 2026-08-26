from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import brief, models
from apps.coach_web.brief import MAX_TOKENS


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/b.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


from types import SimpleNamespace

from sqlalchemy import select

from apps.coach_web import usage_api


class FakeMessages:
    def __init__(self, reply="Ship the auth feature.", raises=None):
        self.reply, self.raises, self.kwargs = reply, raises, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if self.raises:
            raise self.raises
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.reply)],
            usage=SimpleNamespace(input_tokens=500, output_tokens=250,
                                  cache_read_input_tokens=0,
                                  cache_creation_input_tokens=0))


class FakeClient:
    def __init__(self, **kw):
        self.messages = FakeMessages(**kw)


def test_delta_sends_no_effort_thinking_or_cache_control(tmp_path):
    # Haiku 4.5 rejects `effort`, uses the older budget_tokens thinking form,
    # and has a 4096-token cache minimum this payload never reaches. It DOES
    # support output_config.format (structured outputs), which is how the brief
    # returns JSON -- so this pins the three forbidden things precisely rather
    # than banning the whole output_config key. Do not widen it back.
    with make_db(tmp_path) as db:
        assessment = models.Brief(created_at="2026-08-01T07:00:00+00:00",
                                  kind="assessment", day="2026-08-01",
                                  model="m", status="ok", body="standing read")
        db.add(assessment)
        db.commit()
        client = FakeClient(reply='{"summary": "s", "recommendations": []}')
        brief.generate_delta(db, assessment, client_factory=lambda: client)
        sent = client.messages.kwargs
        assert "thinking" not in sent
        assert "cache_control" not in sent
        assert "effort" not in sent.get("output_config", {})
        assert sent["output_config"]["format"]["type"] == "json_schema"
        assert sent["max_tokens"] == MAX_TOKENS


def test_upsert_llm_daily_accumulates(tmp_path):
    usage = {"input_tokens": 10, "output_tokens": 5,
             "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    with make_db(tmp_path) as db:
        usage_api.upsert_llm_daily(db, "2026-08-12", "a", "claude-haiku-4-5", usage)
        usage_api.upsert_llm_daily(db, "2026-08-12", "a", "claude-haiku-4-5", usage)
        db.commit()
        row = db.scalars(select(models.LlmDaily)).one()
        assert row.input_tokens == 20 and row.call_count == 2


from datetime import datetime, timezone

from apps.coach_web import ingest as ingest_mod
# Bound at import time, so it is the real function even while conftest's
# autouse fixture has the module attribute patched to a recorder.
from apps.coach_web.ingest import post_ingest as real_post_ingest
from tests.web.test_ingest import AUTH


def test_post_ingest_generates_a_brief_and_runs_the_watcher(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/p.db")
    models.Base.metadata.create_all(engine)
    result = real_post_ingest(engine, client_factory=lambda: FakeClient(),
                                    fetch=lambda: "## 1.0.0\n\n- Added a thing\n")
    # A fresh DB has no standing assessment, so the gate's first decision is
    # always "assessment" -- decide_and_generate now returns a decision kind,
    # not the row's status.
    assert result["brief"] == "assessment"
    assert result["changelog"]["status"] == "ok"
    with Session(engine) as db:
        assert db.scalars(select(models.Brief)).one().status == "ok"


def test_post_ingest_skips_the_watcher_when_not_due(tmp_path):
    from apps.coach_web import changelog as changelog_mod
    engine = create_engine(f"sqlite:///{tmp_path}/p2.db")
    models.Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(models.WatcherState(key=changelog_mod.W_CHECKED,
                                   value=datetime.now(timezone.utc).isoformat(),
                                   updated_at="x"))
        db.commit()

    def must_not_run():
        raise AssertionError("watcher ran while not due")

    result = real_post_ingest(engine, client_factory=lambda: FakeClient(),
                                    fetch=must_not_run)
    assert result["changelog"]["status"] == "skipped"
    assert result["brief"] == "assessment"      # the brief has no guard


def test_post_ingest_never_raises_when_everything_fails(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/p3.db")
    models.Base.metadata.create_all(engine)

    def boom():
        raise RuntimeError("down")

    result = real_post_ingest(
        engine, client_factory=lambda: FakeClient(raises=RuntimeError("429")),
        fetch=boom)
    # decide_and_generate never raises even when the underlying call fails --
    # it still attempted an assessment, it just stored a failed row. The
    # failure itself is visible on the row (see
    # test_a_failed_first_assessment_does_not_mark_the_change_seen in
    # test_brief_decide.py, which covers this exact assess() path), not in
    # this return value.
    assert result["brief"] == "assessment"
    assert result["changelog"]["status"] == "failed"


def test_ingest_schedules_the_background_task(client, background_calls):
    # The route's only new job is scheduling the task; post_ingest's own
    # failure handling is covered directly above. Asserting the schedule
    # happened keeps this test from passing vacuously under the autouse patch.
    from tests.web.test_ingest_v4 import v4_payload
    assert client.post("/api/ingest", json=v4_payload(), headers=AUTH).status_code == 200
    assert len(background_calls) == 1
    assert background_calls[0][0][0] is client.app.state.engine
