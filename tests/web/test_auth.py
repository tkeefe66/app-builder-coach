import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner

from apps.coach_web import auth as auth_mod
from apps.coach_web.auth import (SESSION_COOKIE, LoginRateLimiter,
                                 hash_password, verify_password)
from apps.coach_web.config import Settings
from apps.coach_web.main import create_app


def test_password_hash_roundtrip():
    h = hash_password("s3cret")
    assert h.startswith("pbkdf2$")
    assert verify_password("s3cret", h)
    assert not verify_password("wrong", h)
    assert not verify_password("s3cret", "garbage")


def test_login_sets_session_cookie(client):
    resp = client.post("/api/login", json={"password": "correct-horse"})
    assert resp.status_code == 200
    assert "coach_session" in resp.cookies


def test_login_rejects_bad_password(client):
    resp = client.post("/api/login", json={"password": "nope"})
    assert resp.status_code == 401
    assert "coach_session" not in resp.cookies


def test_empty_secret_key_cannot_forge_a_session():
    """An unset COACH_SECRET_KEY must not make every signature valid."""
    app = create_app(Settings(database_url="sqlite+pysqlite:///:memory:",
                              ingest_token="tok",
                              password_hash=hash_password("correct-horse"),
                              secret_key=""))
    forged = TimestampSigner("").sign(b"user").decode()
    with TestClient(app, base_url="https://testserver") as c:
        c.cookies.set(SESSION_COOKIE, forged)
        assert c.get("/api/summary").status_code == 401


def test_create_app_rejects_missing_prod_secrets():
    with pytest.raises(ValueError, match="COACH_SECRET_KEY"):
        create_app(Settings(database_url="postgresql+psycopg://u:p@h/db",
                            ingest_token="tok",
                            password_hash=hash_password("correct-horse"),
                            secret_key=""))


def test_create_app_names_every_missing_prod_secret():
    with pytest.raises(ValueError) as exc:
        create_app(Settings(database_url="postgresql+psycopg://u:p@h/db",
                            ingest_token="", password_hash="", secret_key=""))
    for name in ("COACH_SECRET_KEY", "COACH_PASSWORD_HASH", "COACH_INGEST_TOKEN"):
        assert name in str(exc.value)


# --- login rate limiting ------------------------------------------------
# PBKDF2 at 600k iterations is a free CPU-burn oracle if unthrottled.

def test_sixth_login_attempt_is_throttled_without_hashing(client, monkeypatch):
    for _ in range(5):
        assert client.post("/api/login", json={"password": "nope"}).status_code == 401

    calls = []
    monkeypatch.setattr(auth_mod, "verify_password",
                        lambda pw, stored: calls.append(pw) or False)
    resp = client.post("/api/login", json={"password": "nope"})
    assert resp.status_code == 429
    assert calls == [], "throttled request must not reach the password hash"


def test_throttle_blocks_even_the_correct_password(client):
    for _ in range(5):
        client.post("/api/login", json={"password": "nope"})
    resp = client.post("/api/login", json={"password": "correct-horse"})
    assert resp.status_code == 429
    assert SESSION_COOKIE not in resp.cookies


def test_successful_login_resets_the_window(client):
    for _ in range(4):
        assert client.post("/api/login", json={"password": "nope"}).status_code == 401
    assert client.post("/api/login",
                       json={"password": "correct-horse"}).status_code == 200
    # window cleared -> a full budget of attempts is available again
    for _ in range(5):
        assert client.post("/api/login", json={"password": "nope"}).status_code == 401


def test_each_app_starts_with_a_clean_window(client, settings):
    for _ in range(5):
        client.post("/api/login", json={"password": "nope"})
    assert client.post("/api/login", json={"password": "nope"}).status_code == 429

    with TestClient(create_app(settings), base_url="https://testserver") as fresh:
        assert fresh.post("/api/login", json={"password": "nope"}).status_code == 401


def test_limiter_window_slides_with_injected_clock():
    now = [1000.0]
    limiter = LoginRateLimiter(max_attempts=5, window_seconds=60,
                               clock=lambda: now[0])
    for _ in range(5):
        limiter.check()
    with pytest.raises(HTTPException) as exc:
        limiter.check()
    assert exc.value.status_code == 429

    now[0] += 61  # the whole window has aged out
    limiter.check()


def test_limiter_reset_clears_attempts():
    limiter = LoginRateLimiter(max_attempts=2)
    limiter.check()
    limiter.check()
    limiter.reset()
    limiter.check()  # no raise


def test_create_app_allows_sqlite_without_secrets():
    """Local/dev sqlite runs stay frictionless; only prod is gated."""
    create_app(Settings(database_url="sqlite+pysqlite:///:memory:",
                        ingest_token="", password_hash="", secret_key=""))
