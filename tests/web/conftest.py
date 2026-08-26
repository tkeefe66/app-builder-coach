import os
import pytest
from fastapi.testclient import TestClient

# Set DATABASE_URL before importing main (which creates app at module level)
if not os.environ.get("DATABASE_URL"):
    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"

from apps.coach_web.auth import hash_password
from apps.coach_web.config import Settings
from apps.coach_web.main import create_app


@pytest.fixture
def settings():
    return Settings(
        database_url="sqlite+pysqlite:///:memory:",
        ingest_token="test-ingest-token",
        password_hash=hash_password("correct-horse"),
        secret_key="test-secret",
        usage_token="test-usage-token",
    )


@pytest.fixture(autouse=True)
def background_calls(monkeypatch):
    """Keep the post-ingest background task off the network, and record it.

    TestClient runs BackgroundTasks synchronously after the response, so without
    this every /api/ingest test would fetch the real Claude Code changelog over
    HTTP (and, wherever a key is present, call the Anthropic API). The task's own
    behaviour is exercised directly with injected fakes in test_brief.py; this
    fixture yields the recorded calls so a route test can still prove the task
    was scheduled rather than silently asserting nothing.
    """
    from apps.coach_web import ingest as ingest_mod
    calls: list = []

    def recorder(*args, **kwargs):
        calls.append((args, kwargs))
        return {"brief": "skipped", "changelog": {"status": "skipped", "added": 0}}

    monkeypatch.setattr(ingest_mod, "post_ingest", recorder)
    return calls


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app, base_url="https://testserver") as c:
        yield c
