import pytest
from fastapi import HTTPException

from apps.coach_web import auth


class FakeRequest:
    def __init__(self, method="POST", origin=None, referer=None,
                 host="testserver", allowed=""):
        self.method = method
        self.headers = {}
        if origin is not None:
            self.headers["origin"] = origin
        if referer is not None:
            self.headers["referer"] = referer
        self.headers["host"] = host
        self.app = type("A", (), {"state": type("S", (), {
            "settings": type("C", (), {"allowed_origins": allowed})()})()})()
        self.url = type("U", (), {"scheme": "https"})()


def test_safe_methods_skip_the_check():
    # Browsers do NOT send Origin on same-origin GET. Demanding it here would
    # 403 every read endpoint and blank the whole dashboard.
    for method in ("GET", "HEAD", "OPTIONS"):
        auth.require_same_origin(FakeRequest(method=method))


def test_matching_origin_passes():
    auth.require_same_origin(
        FakeRequest(origin="https://testserver", host="testserver"))


def test_foreign_origin_rejected():
    with pytest.raises(HTTPException) as exc:
        auth.require_same_origin(
            FakeRequest(origin="https://evil.example", host="testserver"))
    assert exc.value.status_code == 403


def test_sibling_subdomain_rejected():
    # The case this control exists for: SameSite=Lax treats a sibling subdomain
    # as same-site, so the session cookie would ride along.
    with pytest.raises(HTTPException) as exc:
        auth.require_same_origin(
            FakeRequest(origin="https://other.tomkeefe.ai",
                        host="code-coach.tomkeefe.ai"))
    assert exc.value.status_code == 403


def test_missing_origin_and_referer_rejected():
    with pytest.raises(HTTPException) as exc:
        auth.require_same_origin(FakeRequest())
    assert exc.value.status_code == 403


def test_referer_is_accepted_when_origin_absent():
    auth.require_same_origin(
        FakeRequest(referer="https://testserver/goals", host="testserver"))


def test_allowed_origins_setting_is_honoured():
    auth.require_same_origin(
        FakeRequest(origin="https://code-coach.tomkeefe.ai", host="whatever",
                    allowed="https://code-coach.tomkeefe.ai,https://x.up.railway.app"))


ORIGIN = {"Origin": "https://testserver"}


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def test_security_headers_present_on_api(client):
    resp = client.get("/api/health")
    assert resp.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["content-security-policy"]
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["referrer-policy"] == "same-origin"
    assert "max-age=" in resp.headers["strict-transport-security"]


def test_logout_clears_the_session(client):
    login(client)
    assert client.get("/api/overview").status_code == 200
    assert client.post("/api/logout", headers=ORIGIN).status_code == 200
    assert client.get("/api/overview").status_code == 401


def test_logout_is_post_only(client):
    login(client)
    # A state-changing GET is reachable from a plain link, and SameSite=Lax
    # DOES attach the cookie to top-level navigations.
    assert client.get("/api/logout").status_code in (404, 405)
    assert client.get("/api/overview").status_code == 200


def test_logout_rejects_cross_origin(client):
    login(client)
    resp = client.post("/api/logout", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403
    assert client.get("/api/overview").status_code == 200


def test_rate_limiter_is_lock_protected():
    import threading
    limiter = auth.LoginRateLimiter(max_attempts=5, window_seconds=60)
    errors = []

    def hammer():
        try:
            limiter.check()
        except HTTPException:
            errors.append(1)

    threads = [threading.Thread(target=hammer) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # Exactly 5 may pass; the rest must be throttled.
    assert len(errors) == 35


# Every directive is listed individually so a partial regression names the
# directive that vanished, rather than failing on one opaque string compare.
CSP_DIRECTIVES = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
]


@pytest.mark.parametrize("directive", CSP_DIRECTIVES)
def test_csp_carries_every_directive(client, directive):
    csp = client.get("/api/health").headers["content-security-policy"]
    assert directive in csp


def test_csp_keeps_unsafe_inline_for_styles_only(client):
    # Recharts injects inline styles at runtime, so 'unsafe-inline' in
    # style-src is permanent -- but it must never leak into script-src,
    # which is the directive actually holding back injected script.
    csp = client.get("/api/health").headers["content-security-policy"]
    script = next(d for d in csp.split(";") if d.strip().split()[0] == "script-src")
    assert "unsafe-inline" not in script
    assert "unsafe-eval" not in script
