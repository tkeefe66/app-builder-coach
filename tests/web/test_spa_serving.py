from pathlib import Path

from fastapi.testclient import TestClient

from apps.coach_web.auth import hash_password
from apps.coach_web.config import Settings
from apps.coach_web.main import create_app


def make_client(dist: Path | None):
    settings = Settings(database_url="sqlite+pysqlite:///:memory:",
                        ingest_token="t", password_hash=hash_password("p"),
                        secret_key="s")
    app = create_app(settings, spa_dist=dist)
    return TestClient(app, base_url="https://testserver")


def test_no_dist_root_404(tmp_path):
    c = make_client(None)
    assert c.get("/").status_code == 404
    assert c.get("/api/health").status_code == 200


def test_spa_serves_index_and_fallback(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>coach</html>")
    (dist / "assets" / "app.js").write_text("console.log(1)")
    c = make_client(dist)
    assert "coach" in c.get("/").text
    assert "coach" in c.get("/capabilities").text        # client route fallback
    assert c.get("/assets/app.js").text.startswith("console")
    assert c.get("/api/health").json() == {"status": "ok"}
    assert c.get("/api/nope").status_code == 404          # api never falls back


def test_spa_fallback_does_not_leak_dotfiles(tmp_path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>coach</html>")
    c = make_client(dist)
    resp = c.get("/../../etc/passwd")
    assert "coach" in resp.text or resp.status_code in (404, 403)
