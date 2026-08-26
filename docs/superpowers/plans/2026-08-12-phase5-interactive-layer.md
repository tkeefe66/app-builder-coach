# Phase 5 Implementation Plan — interactive layer + write-path hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the app its first write endpoints — goals, notes, dismissals, check-offs — and harden the write path before they exist.

**Architecture:** Four app-owned tables never touched by ingest. All writes sit behind the existing `require_user` cookie auth plus a new `require_same_origin` dependency that enforces only on unsafe methods and only on the cookie-authenticated router. Three of the four entities change behaviour the app already has: dismissals filter the brief's gap lists, check-offs override the Adoption board, goals surface on Overview.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy/Alembic, React+TS. No new dependencies.

Read `docs/superpowers/specs/2026-08-12-phase5-interactive-layer-design.md` first.

## Global Constraints

- **No new dependencies.**
- **No new repo-root file** — the Dockerfile `COPY` line needs no change, but do not remove anything from it (`tests/web/test_dockerfile_data_files.py` guards it).
- **`require_same_origin` enforces only on unsafe methods.** Browsers do **not** send `Origin` on same-origin `GET`; demanding it unconditionally 403s every read and blanks the dashboard.
- **It is a router-level dependency, never global middleware.** `/api/ingest` and `/api/usage` are bearer-token machine clients that send no `Origin`; covering them breaks the daily sweep.
- **`POST /api/login` stays exempt** (no session cookie to ride). `POST /api/logout` is covered.
- **CSP is `frame-ancestors 'none'` only.** The SPA uses React inline styles throughout; a `style-src` directive breaks every page.
- **App-owned tables are never written by ingest.** Nothing in `ingest.py` may touch them.
- Request bodies are Pydantic models with `max_length` matching column widths, so over-long input is a 422, not a truncation or a DB error.

## File Structure

```
apps/coach_web/models.py            (modify) Goal, FeatureCheckoff, Note, Dismissal
apps/coach_web/alembic/versions/    (new)    one revision, four tables
apps/coach_web/auth.py              (modify) require_same_origin, logout, limiter lock
apps/coach_web/main.py              (modify) security-headers middleware
apps/coach_web/config.py            (modify) allowed_origins setting
apps/coach_web/writes.py            (new)    CRUD router for the four entities
apps/coach_web/api.py               (modify) checked_off on board, active_goals on overview
apps/coach_web/brief.py             (modify) dismissal filtering in build_context
apps/coach_web/frontend/src/api.ts  (modify) post/patch/del helpers
apps/coach_web/frontend/src/pages/{Goals,Adoption,Capabilities,Overview}.tsx  (modify)
tests/web/test_security.py          (new)
tests/web/test_writes.py            (new)
tests/web/test_integrations.py      (new)
apps/coach_web/frontend/src/__tests__/Writes.test.tsx  (new)
```

Existing interfaces to build on (do not change signatures): `auth.require_user`,
`auth.require_ingest_token`, `auth.require_usage_token`, `auth.SESSION_COOKIE`,
`db.get_db`, `brief.build_context`, `api.adoption_board`, `api.overview`,
`config.Settings`.

---

### Task 1: Four tables and the migration

**Files:**
- Modify: `apps/coach_web/models.py`
- Create: alembic revision
- Test: `tests/web/test_models.py` (extend)

**Interfaces:**
- Produces: `models.Goal`, `models.FeatureCheckoff`, `models.Note`, `models.Dismissal`.

- [ ] **Step 1: Write the failing test**

Append to `tests/web/test_models.py`:

```python
def test_phase5_app_owned_tables(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from apps.coach_web import models

    engine = create_engine(f"sqlite:///{tmp_path}/p5.db")
    models.Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(models.Goal(kind="tag", target="auth", title="Ship auth",
                          target_date="2026-09-01", status="active",
                          created_at="2026-08-12T07:00:00+00:00"))
        s.add(models.FeatureCheckoff(feature_name="plan mode",
                                     checked_at="2026-08-12T07:00:00+00:00"))
        s.add(models.Note(subject_kind="brief", subject_id="1", body="useful",
                          created_at="2026-08-12T07:00:00+00:00"))
        s.add(models.Dismissal(kind="feature", target="hooks", reason="not now",
                               created_at="2026-08-12T07:00:00+00:00"))
        s.commit()
        assert s.query(models.Goal).one().status == "active"
        assert s.get(models.FeatureCheckoff, "plan mode").note == ""
        assert s.query(models.Note).one().body == "useful"
        assert s.query(models.Dismissal).one().target == "hooks"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/web/test_models.py -v -k phase5`
Expected: FAIL — `AttributeError: module 'apps.coach_web.models' has no attribute 'Goal'`

- [ ] **Step 3: Add the models**

Append to `apps/coach_web/models.py`:

```python
# --- App-owned tables (UI-written). Ingest must never touch these. ---

class Goal(Base):
    __tablename__ = "goals"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))          # tag | feature
    target: Mapped[str] = mapped_column(String(120))
    title: Mapped[str] = mapped_column(String(200))
    target_date: Mapped[str] = mapped_column(String(10), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_at: Mapped[str] = mapped_column(String(32))


class FeatureCheckoff(Base):
    """Manual "I learned this", independent of what the sweep can detect."""
    __tablename__ = "feature_checkoffs"
    feature_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    checked_at: Mapped[str] = mapped_column(String(32))
    note: Mapped[str] = mapped_column(String(500), default="")


class Note(Base):
    __tablename__ = "notes"
    id: Mapped[int] = mapped_column(primary_key=True)
    subject_kind: Mapped[str] = mapped_column(String(16), index=True)
    subject_id: Mapped[str] = mapped_column(String(120), index=True)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(32))


class Dismissal(Base):
    """A gap waved off. Filtered out of the brief, still visible on Overview."""
    __tablename__ = "dismissals"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    target: Mapped[str] = mapped_column(String(120), index=True)
    reason: Mapped[str] = mapped_column(String(500), default="")
    created_at: Mapped[str] = mapped_column(String(32))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/web/test_models.py -v -k phase5`
Expected: PASS

- [ ] **Step 5: Generate, inspect, and cycle the revision**

```bash
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini upgrade head
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini revision --autogenerate -m "phase5 app-owned tables"
```

`upgrade()` must contain ONLY four `create_table` calls (`goals`, `feature_checkoffs`, `notes`, `dismissals`) plus the indexes on `notes` and `dismissals`. Anything else means stop and report. Then, as separate commands (a shell loop over quoted multi-word alembic args does not work):

```bash
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini upgrade head
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini downgrade -1
DATABASE_URL=sqlite:///alembic-dev.db .venv/bin/alembic -c apps/coach_web/alembic.ini upgrade head
.venv/bin/python -c "from pathlib import Path; Path('alembic-dev.db').unlink(missing_ok=True)"
```

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add apps/coach_web/models.py apps/coach_web/alembic tests/web/test_models.py
git commit -m "feat(web): app-owned tables for goals, notes, dismissals, check-offs"
```

---

### Task 2: `require_same_origin`

**Files:**
- Modify: `apps/coach_web/auth.py`, `apps/coach_web/config.py`
- Test: `tests/web/test_security.py` (new)

**Interfaces:**
- Produces: `Settings.allowed_origins: str = ""` (comma-separated) and `auth.require_same_origin(request) -> None`, which returns immediately for `GET`/`HEAD`/`OPTIONS` and raises 403 otherwise unless `Origin` (or `Referer`) matches.

This control exists for one specific case: a sibling subdomain of `tomkeefe.ai` is **same-site**, so `SameSite=Lax` does not stop it.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_security.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_security.py -v`
Expected: FAIL — `AttributeError: module 'apps.coach_web.auth' has no attribute 'require_same_origin'`

- [ ] **Step 3: Add the setting**

In `apps/coach_web/config.py`, add a field to `Settings` and read it in `settings_from_env`:

```python
    allowed_origins: str = ""
```

```python
        allowed_origins=os.environ.get("COACH_ALLOWED_ORIGINS", ""),
```

- [ ] **Step 4: Implement the dependency**

Append to `apps/coach_web/auth.py`:

```python
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def require_same_origin(request: Request) -> None:
    """Reject cross-origin state-changing requests on cookie-authed routes.

    SameSite=Lax already blocks classic cross-site CSRF, but SameSite is scoped
    to the registrable domain: a sibling subdomain of tomkeefe.ai counts as
    same-site and would carry the session cookie. This closes that.

    Two things this must NOT do:
      * enforce on GET -- browsers omit Origin on same-origin reads, so
        demanding it would 403 every read endpoint;
      * apply to /api/ingest or /api/usage -- those are bearer-token machine
        clients that send no Origin at all, and the daily sweep would break.
    Hence: unsafe methods only, and attached to the cookie-authed router only.
    """
    if request.method in SAFE_METHODS:
        return

    configured = [o.strip() for o in
                  (request.app.state.settings.allowed_origins or "").split(",")
                  if o.strip()]
    if configured:
        allowed = set(configured)
    else:
        # No configuration: trust this request's own host. Correct for both
        # Railway domains, and still rejects a sibling subdomain.
        host = request.headers.get("host", "")
        allowed = {f"https://{host}", f"http://{host}"}

    origin = request.headers.get("origin")
    if origin is None:
        referer = request.headers.get("referer")
        if referer:
            parts = referer.split("/")
            origin = "//".join([parts[0], parts[2]]) if len(parts) > 2 else None
    if origin is None:
        log.warning("write rejected: no Origin or Referer header")
        raise HTTPException(status_code=403,
                            detail="missing Origin on state-changing request")
    if origin not in allowed:
        log.warning("write rejected: origin %r not in %r", origin, sorted(allowed))
        raise HTTPException(status_code=403, detail="cross-origin write rejected")
```

Add `import logging` and `log = logging.getLogger("auth")` near the top of the file if not present.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_security.py -v`
Expected: PASS (7 tests)

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add apps/coach_web/auth.py apps/coach_web/config.py tests/web/test_security.py
git commit -m "feat(web): require_same_origin for cookie-authenticated writes"
```

---

### Task 3: Logout, security headers, rate-limiter lock

**Files:**
- Modify: `apps/coach_web/auth.py`, `apps/coach_web/main.py`
- Test: `tests/web/test_security.py` (extend)

**Interfaces:**
- Produces: `POST /api/logout` on the auth router; a security-headers middleware in `create_app`; a `threading.Lock` inside `LoginRateLimiter`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_security.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_security.py -v -k "headers or logout"`
Expected: FAIL — `KeyError: 'x-frame-options'` and 404 on `/api/logout`

- [ ] **Step 3: Add the lock and the logout route**

In `apps/coach_web/auth.py`, add `import threading` at the top. In `LoginRateLimiter.__init__` add `self._lock = threading.Lock()`, and wrap the body of `check` so the whole filter→compare→append sequence is atomic:

```python
    def check(self) -> None:
        """Record an attempt, or raise 429 if the window is already full.

        Locked: sync handlers run on a threadpool, so an unlocked
        filter->compare->append is a read-modify-write race. Measured at 40-way
        concurrency it happened to hold, but the race is real in principle.
        """
        with self._lock:
            now = self._clock()
            cutoff = now - self.window_seconds
            self._attempts = [t for t in self._attempts if t > cutoff]
            if len(self._attempts) >= self.max_attempts:
                raise HTTPException(
                    status_code=429,
                    detail=f"too many login attempts; retry in "
                           f"{self.window_seconds} seconds")
            self._attempts.append(now)
```

Also wrap `reset` with the same lock. Then add the logout route:

```python
@router.post("/api/logout", dependencies=[Depends(require_same_origin)])
def logout(response: Response):
    """Clear the session cookie.

    POST, never GET: SameSite=Lax attaches the cookie to top-level navigations,
    so a GET logout is triggerable from a plain link.

    The session is a stateless signed cookie, so this clears the client's copy
    and nothing more -- a copied cookie stays valid until it expires. A
    server-side session store is the upgrade if that ever matters.
    """
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=True,
                           samesite="lax")
    return {"status": "ok"}
```

Add `Depends` to the `fastapi` import in `auth.py`.

- [ ] **Step 4: Add the headers middleware**

In `apps/coach_web/main.py`, inside `create_app` after `app = FastAPI(...)`:

```python
    # Minimal but real. A full CSP is deliberately out of scope: the SPA uses
    # React inline styles everywhere, so a style-src directive breaks every
    # page. frame-ancestors is the exposure that matters -- without it anyone
    # can iframe the login and overlay a fake one.
    SECURITY_HEADERS = {
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": "frame-ancestors 'none'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "same-origin",
        "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    }

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
        return response
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_security.py -v`
Expected: PASS (12 tests)

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add apps/coach_web/auth.py apps/coach_web/main.py tests/web/test_security.py
git commit -m "feat(web): logout, security headers, locked rate limiter"
```

---

### Task 4: The writes router — goals and notes

**Files:**
- Create: `apps/coach_web/writes.py`
- Modify: `apps/coach_web/main.py`
- Test: `tests/web/test_writes.py` (new)

**Interfaces:**
- Consumes: `models.Goal`, `models.Note` from Task 1; `require_same_origin` from Task 2.
- Produces: `writes.router`, an `APIRouter(dependencies=[Depends(require_user), Depends(require_same_origin)])`, mounted in `create_app`. Endpoints: `GET/POST /api/goals`, `PATCH/DELETE /api/goals/{id}`, `GET/POST /api/notes`, `DELETE /api/notes/{id}`.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_writes.py`:

```python
ORIGIN = {"Origin": "https://testserver"}


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def test_goals_require_login(client):
    assert client.post("/api/goals", json={}, headers=ORIGIN).status_code == 401


def test_create_and_list_a_goal(client):
    login(client)
    resp = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "title": "Ship auth",
        "target_date": "2026-09-01"})
    assert resp.status_code == 200
    created = resp.json()
    assert created["status"] == "active" and created["id"] >= 1
    listed = client.get("/api/goals").json()["goals"]
    assert [g["title"] for g in listed] == ["Ship auth"]


def test_goal_rejects_bad_kind(client):
    login(client)
    resp = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "nonsense", "target": "auth", "title": "x"})
    assert resp.status_code == 422


def test_goal_rejects_overlong_title(client):
    login(client)
    resp = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "title": "x" * 500})
    assert resp.status_code == 422


def test_patch_goal_status(client):
    login(client)
    gid = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "title": "Ship auth"}).json()["id"]
    assert client.patch(f"/api/goals/{gid}", headers=ORIGIN,
                        json={"status": "done"}).status_code == 200
    assert client.get("/api/goals").json()["goals"][0]["status"] == "done"


def test_patch_missing_goal_is_404(client):
    login(client)
    assert client.patch("/api/goals/999", headers=ORIGIN,
                        json={"status": "done"}).status_code == 404


def test_delete_goal(client):
    login(client)
    gid = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "title": "Ship auth"}).json()["id"]
    assert client.delete(f"/api/goals/{gid}", headers=ORIGIN).status_code == 200
    assert client.get("/api/goals").json()["goals"] == []
    assert client.delete(f"/api/goals/{gid}", headers=ORIGIN).status_code == 404


def test_goal_write_rejects_cross_origin(client):
    login(client)
    resp = client.post("/api/goals", headers={"Origin": "https://evil.example"},
                       json={"kind": "tag", "target": "auth", "title": "x"})
    assert resp.status_code == 403


def test_goal_get_needs_no_origin(client):
    # Browsers omit Origin on same-origin GET; reads must not require it.
    login(client)
    assert client.get("/api/goals").status_code == 200


def test_notes_crud_and_filtering(client):
    login(client)
    client.post("/api/notes", headers=ORIGIN, json={
        "subject_kind": "tag", "subject_id": "auth", "body": "tag note"})
    client.post("/api/notes", headers=ORIGIN, json={
        "subject_kind": "brief", "subject_id": "1", "body": "brief note"})
    everything = client.get("/api/notes").json()["notes"]
    assert len(everything) == 2
    filtered = client.get("/api/notes?subject_kind=tag&subject_id=auth").json()["notes"]
    assert [n["body"] for n in filtered] == ["tag note"]
    nid = filtered[0]["id"]
    assert client.delete(f"/api/notes/{nid}", headers=ORIGIN).status_code == 200
    assert len(client.get("/api/notes").json()["notes"]) == 1


def test_note_rejects_bad_subject_kind(client):
    login(client)
    resp = client.post("/api/notes", headers=ORIGIN, json={
        "subject_kind": "nonsense", "subject_id": "x", "body": "y"})
    assert resp.status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_writes.py -v`
Expected: FAIL — 404 on `/api/goals` (the SPA fallback swallows unknown routes)

- [ ] **Step 3: Create the writes router**

`apps/coach_web/writes.py`:

```python
"""Write endpoints for app-owned tables.

Every route here is cookie-authenticated AND origin-checked. Machine clients
(/api/ingest, /api/usage) live on their own bearer-token routers and are
deliberately not covered -- they send no Origin header at all.
"""
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import models
from .auth import require_same_origin, require_user
from .db import get_db

router = APIRouter(dependencies=[Depends(require_user),
                                 Depends(require_same_origin)])

Kind = Literal["tag", "feature"]
SubjectKind = Literal["tag", "feature", "brief"]
Status = Literal["active", "done", "abandoned"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GoalIn(BaseModel):
    kind: Kind
    target: str = Field(max_length=120)
    title: str = Field(max_length=200)
    target_date: str = Field(default="", max_length=10)


class GoalPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    target_date: str | None = Field(default=None, max_length=10)
    status: Status | None = None


class NoteIn(BaseModel):
    subject_kind: SubjectKind
    subject_id: str = Field(max_length=120)
    body: str = Field(max_length=5000)


def _goal_json(g: models.Goal) -> dict:
    return {"id": g.id, "kind": g.kind, "target": g.target, "title": g.title,
            "target_date": g.target_date, "status": g.status,
            "created_at": g.created_at}


@router.get("/api/goals")
def list_goals(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.Goal).order_by(models.Goal.id))
    return {"goals": [_goal_json(g) for g in rows]}


@router.post("/api/goals")
def create_goal(body: GoalIn, db: Session = Depends(get_db)):
    goal = models.Goal(kind=body.kind, target=body.target, title=body.title,
                       target_date=body.target_date, status="active",
                       created_at=_now())
    db.add(goal)
    db.commit()
    return _goal_json(goal)


@router.patch("/api/goals/{goal_id}")
def patch_goal(goal_id: int, body: GoalPatch, db: Session = Depends(get_db)):
    goal = db.get(models.Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    for field in ("title", "target_date", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(goal, field, value)
    db.commit()
    return _goal_json(goal)


@router.delete("/api/goals/{goal_id}")
def delete_goal(goal_id: int, db: Session = Depends(get_db)):
    goal = db.get(models.Goal, goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="goal not found")
    db.delete(goal)
    db.commit()
    return {"status": "ok"}


def _note_json(n: models.Note) -> dict:
    return {"id": n.id, "subject_kind": n.subject_kind,
            "subject_id": n.subject_id, "body": n.body,
            "created_at": n.created_at}


@router.get("/api/notes")
def list_notes(subject_kind: str | None = None, subject_id: str | None = None,
               db: Session = Depends(get_db)):
    stmt = select(models.Note).order_by(models.Note.id)
    if subject_kind is not None:
        stmt = stmt.where(models.Note.subject_kind == subject_kind)
    if subject_id is not None:
        stmt = stmt.where(models.Note.subject_id == subject_id)
    return {"notes": [_note_json(n) for n in db.scalars(stmt)]}


@router.post("/api/notes")
def create_note(body: NoteIn, db: Session = Depends(get_db)):
    note = models.Note(subject_kind=body.subject_kind, subject_id=body.subject_id,
                       body=body.body, created_at=_now())
    db.add(note)
    db.commit()
    return _note_json(note)


@router.delete("/api/notes/{note_id}")
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(models.Note, note_id)
    if note is None:
        raise HTTPException(status_code=404, detail="note not found")
    db.delete(note)
    db.commit()
    return {"status": "ok"}
```

- [ ] **Step 4: Mount it**

In `apps/coach_web/main.py`, beside the existing `app.include_router(api_router)`:

```python
    from .writes import router as writes_router
    app.include_router(writes_router)
```

Mount it **before** the SPA catch-all route, exactly like the other routers.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_writes.py -v`
Expected: PASS (11 tests)

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add apps/coach_web/writes.py apps/coach_web/main.py tests/web/test_writes.py
git commit -m "feat(web): goals and notes CRUD behind auth + origin check"
```

---

### Task 5: Dismissals and check-offs

**Files:**
- Modify: `apps/coach_web/writes.py`
- Test: `tests/web/test_writes.py` (extend)

**Interfaces:**
- Produces: `GET/POST /api/dismissals`, `DELETE /api/dismissals/{id}`, `POST /api/checkoffs`, `DELETE /api/checkoffs/{feature_name}`. Both creates are **idempotent** — a repeat returns 200 and the existing row, never a duplicate or an error.

- [ ] **Step 1: Write the failing tests**

Append to `tests/web/test_writes.py`:

```python
def test_dismissal_create_list_delete(client):
    login(client)
    made = client.post("/api/dismissals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "reason": "not now"}).json()
    assert made["target"] == "auth"
    assert len(client.get("/api/dismissals").json()["dismissals"]) == 1
    assert client.delete(f"/api/dismissals/{made['id']}",
                         headers=ORIGIN).status_code == 200
    assert client.get("/api/dismissals").json()["dismissals"] == []


def test_dismissal_is_idempotent(client):
    login(client)
    first = client.post("/api/dismissals", headers=ORIGIN,
                        json={"kind": "tag", "target": "auth"}).json()
    second = client.post("/api/dismissals", headers=ORIGIN,
                         json={"kind": "tag", "target": "auth"})
    assert second.status_code == 200
    assert second.json()["id"] == first["id"]
    assert len(client.get("/api/dismissals").json()["dismissals"]) == 1


def test_dismissal_rejects_bad_kind(client):
    login(client)
    assert client.post("/api/dismissals", headers=ORIGIN, json={
        "kind": "brief", "target": "x"}).status_code == 422


def test_checkoff_create_list_delete(client):
    login(client)
    assert client.post("/api/checkoffs", headers=ORIGIN, json={
        "feature_name": "plan mode"}).status_code == 200
    assert client.get("/api/checkoffs").json()["checkoffs"][0]["feature_name"] == "plan mode"
    assert client.delete("/api/checkoffs/plan mode",
                         headers=ORIGIN).status_code == 200
    assert client.get("/api/checkoffs").json()["checkoffs"] == []


def test_checkoff_is_idempotent(client):
    login(client)
    client.post("/api/checkoffs", headers=ORIGIN, json={"feature_name": "plan mode"})
    second = client.post("/api/checkoffs", headers=ORIGIN,
                         json={"feature_name": "plan mode"})
    assert second.status_code == 200
    assert len(client.get("/api/checkoffs").json()["checkoffs"]) == 1


def test_delete_missing_checkoff_is_404(client):
    login(client)
    assert client.delete("/api/checkoffs/nope", headers=ORIGIN).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_writes.py -v -k "dismissal or checkoff"`
Expected: FAIL — 404 on `/api/dismissals`

- [ ] **Step 3: Implement**

Append to `apps/coach_web/writes.py`:

```python
class DismissalIn(BaseModel):
    kind: Kind
    target: str = Field(max_length=120)
    reason: str = Field(default="", max_length=500)


class CheckoffIn(BaseModel):
    feature_name: str = Field(max_length=120)
    note: str = Field(default="", max_length=500)


def _dismissal_json(d: models.Dismissal) -> dict:
    return {"id": d.id, "kind": d.kind, "target": d.target,
            "reason": d.reason, "created_at": d.created_at}


@router.get("/api/dismissals")
def list_dismissals(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.Dismissal).order_by(models.Dismissal.id))
    return {"dismissals": [_dismissal_json(d) for d in rows]}


@router.post("/api/dismissals")
def create_dismissal(body: DismissalIn, db: Session = Depends(get_db)):
    # Idempotent: dismissing twice from two tabs is a no-op, not a duplicate
    # row that then needs deleting twice to actually un-dismiss.
    existing = db.scalar(select(models.Dismissal).where(
        models.Dismissal.kind == body.kind,
        models.Dismissal.target == body.target))
    if existing is not None:
        return _dismissal_json(existing)
    row = models.Dismissal(kind=body.kind, target=body.target,
                           reason=body.reason, created_at=_now())
    db.add(row)
    db.commit()
    return _dismissal_json(row)


@router.delete("/api/dismissals/{dismissal_id}")
def delete_dismissal(dismissal_id: int, db: Session = Depends(get_db)):
    row = db.get(models.Dismissal, dismissal_id)
    if row is None:
        raise HTTPException(status_code=404, detail="dismissal not found")
    db.delete(row)
    db.commit()
    return {"status": "ok"}


def _checkoff_json(c: models.FeatureCheckoff) -> dict:
    return {"feature_name": c.feature_name, "checked_at": c.checked_at,
            "note": c.note}


@router.get("/api/checkoffs")
def list_checkoffs(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.FeatureCheckoff)
                      .order_by(models.FeatureCheckoff.feature_name))
    return {"checkoffs": [_checkoff_json(c) for c in rows]}


@router.post("/api/checkoffs")
def create_checkoff(body: CheckoffIn, db: Session = Depends(get_db)):
    existing = db.get(models.FeatureCheckoff, body.feature_name)
    if existing is not None:
        return _checkoff_json(existing)
    row = models.FeatureCheckoff(feature_name=body.feature_name,
                                 checked_at=_now(), note=body.note)
    db.add(row)
    db.commit()
    return _checkoff_json(row)


@router.delete("/api/checkoffs/{feature_name}")
def delete_checkoff(feature_name: str, db: Session = Depends(get_db)):
    row = db.get(models.FeatureCheckoff, feature_name)
    if row is None:
        raise HTTPException(status_code=404, detail="checkoff not found")
    db.delete(row)
    db.commit()
    return {"status": "ok"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_writes.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add apps/coach_web/writes.py tests/web/test_writes.py
git commit -m "feat(web): dismissals and check-offs, both idempotent"
```

---

### Task 6: The three integrations

**Files:**
- Modify: `apps/coach_web/brief.py`, `apps/coach_web/api.py`
- Test: `tests/web/test_integrations.py` (new)

**Interfaces:**
- Consumes: `models.Dismissal`, `models.FeatureCheckoff`, `models.Goal`.
- Produces: `build_context` filters dismissed items from `never_built`, `stale`, `adoption_gaps`; `/api/adoption/board` features gain `checked_off: bool` and `detected_status: str` with `status` overridden to `"checked-off"`; `/api/overview` gains `active_goals`.

This is the task that makes the four tables worth having. Four tables of pure CRUD would be busywork.

- [ ] **Step 1: Write the failing tests**

`tests/web/test_integrations.py`:

```python
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from apps.coach_web import brief, models

TODAY = date(2026, 8, 12)
ORIGIN = {"Origin": "https://testserver"}


def login(client):
    client.post("/api/login", json={"password": "correct-horse"})


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/i.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def test_tag_dismissal_filters_never_built(tmp_path):
    with make_db(tmp_path) as db:
        before = brief.build_context(db, TODAY)["never_built"]
        assert len(before) > 0
        victim = before[0]
        db.add(models.Dismissal(kind="tag", target=victim, reason="",
                                created_at="2026-08-12T07:00:00+00:00"))
        db.commit()
        after = brief.build_context(db, TODAY)["never_built"]
        assert victim not in after
        assert len(after) == len(before) - 1


def test_tag_dismissal_filters_stale(tmp_path):
    with make_db(tmp_path) as db:
        db.add(models.FeatureUnit(key="old", kind="spec", repo="r",
                                  date="2025-01-01", title="t", tags=["auth"],
                                  complexity=1, summary="s", model="m"))
        db.commit()
        assert "auth" in brief.build_context(db, TODAY)["stale"]
        db.add(models.Dismissal(kind="tag", target="auth", reason="",
                                created_at="2026-08-12T07:00:00+00:00"))
        db.commit()
        assert "auth" not in brief.build_context(db, TODAY)["stale"]


def test_feature_dismissal_filters_adoption_gaps(tmp_path):
    with make_db(tmp_path) as db:
        snap = models.Snapshot(captured_at="2026-08-12T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.AdoptionHistory(snapshot_id=snap.id, feature_name="hooks",
                                      lesson="09", status="never-touched"))
        db.commit()
        assert brief.build_context(db, TODAY)["adoption_gaps"] == ["hooks"]
        db.add(models.Dismissal(kind="feature", target="hooks", reason="",
                                created_at="2026-08-12T07:00:00+00:00"))
        db.commit()
        assert brief.build_context(db, TODAY)["adoption_gaps"] == []


def test_a_tag_dismissal_does_not_filter_a_feature_gap(tmp_path):
    # kind must actually discriminate; a tag dismissal named "hooks" must not
    # silence the *feature* "hooks".
    with make_db(tmp_path) as db:
        snap = models.Snapshot(captured_at="2026-08-12T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.AdoptionHistory(snapshot_id=snap.id, feature_name="hooks",
                                      lesson="09", status="never-touched"))
        db.add(models.Dismissal(kind="tag", target="hooks", reason="",
                                created_at="2026-08-12T07:00:00+00:00"))
        db.commit()
        assert brief.build_context(db, TODAY)["adoption_gaps"] == ["hooks"]


def test_checkoff_overrides_the_adoption_board(client):
    login(client)
    with Session(client.app.state.engine) as db:
        snap = models.Snapshot(captured_at="2026-08-12T07:30:00+00:00",
                               content_hash="h", sweep_stats={})
        db.add(snap)
        db.commit()
        db.add(models.FeatureCatalog(name="plan mode", lesson="09",
                                     source="checklist", discovered_at="2026-01-01"))
        db.add(models.AdoptionHistory(snapshot_id=snap.id, feature_name="plan mode",
                                      lesson="09", status="never-touched"))
        db.commit()

    before = client.get("/api/adoption/board").json()["features"][0]
    assert before["status"] == "never-touched" and before["checked_off"] is False

    client.post("/api/checkoffs", headers=ORIGIN, json={"feature_name": "plan mode"})
    after = client.get("/api/adoption/board").json()["features"][0]
    assert after["status"] == "checked-off"
    assert after["checked_off"] is True
    # The detector's opinion is preserved, not destroyed.
    assert after["detected_status"] == "never-touched"


def test_overview_carries_active_goals(client):
    login(client)
    client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "auth", "title": "Ship auth"})
    gid = client.post("/api/goals", headers=ORIGIN, json={
        "kind": "tag", "target": "ui", "title": "Done one"}).json()["id"]
    client.patch(f"/api/goals/{gid}", headers=ORIGIN, json={"status": "done"})
    goals = client.get("/api/overview").json()["active_goals"]
    assert [g["title"] for g in goals] == ["Ship auth"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/web/test_integrations.py -v`
Expected: FAIL — dismissals not filtered; `KeyError: 'checked_off'`; `KeyError: 'active_goals'`

- [ ] **Step 3: Filter dismissals in `build_context`**

In `apps/coach_web/brief.py`, inside `build_context`, after `adoption_gaps` is computed and before the return, add:

```python
    # A dismissed gap is one Tom has already considered and rejected. Keep it
    # out of the coach's suggestions -- but note /api/overview deliberately
    # still shows it, so a dismissal never becomes invisible.
    dismissed_tags = set()
    dismissed_features = set()
    for row in db.scalars(select(models.Dismissal)):
        if row.kind == "tag":
            dismissed_tags.add(row.target)
        elif row.kind == "feature":
            dismissed_features.add(row.target)
    never_built = [t for t in never_built if t not in dismissed_tags]
    stale = [t for t in stale if t not in dismissed_tags]
    adoption_gaps = [f for f in adoption_gaps if f not in dismissed_features]
```

- [ ] **Step 4: Add `checked_off` to the board and `active_goals` to overview**

In `apps/coach_web/api.py`, in `adoption_board`, before the `for cat in ...` loop:

```python
    checked = {c.feature_name for c in db.scalars(select(models.FeatureCheckoff))}
```

and change the appended dict so the detected status is preserved rather than lost:

```python
        detected = latest_row.status if latest_row else "unknown"
        is_checked = cat.name in checked
        features.append({
            "name": cat.name, "lesson": cat.lesson, "source": cat.source,
            "discovered_at": cat.discovered_at,
            # A manual check-off beats a detector that cannot see the thing.
            "status": "checked-off" if is_checked else detected,
            "detected_status": detected,
            "checked_off": is_checked,
            "last_used": latest_row.last_used if latest_row else None,
            "history": history.get(cat.name, []),
        })
```

In `overview`, before the return:

```python
    active_goals = [
        {"id": g.id, "kind": g.kind, "target": g.target, "title": g.title,
         "target_date": g.target_date}
        for g in db.scalars(select(models.Goal)
                            .where(models.Goal.status == "active")
                            .order_by(models.Goal.id))]
```

and add `"active_goals": active_goals,` to the returned dict.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/web/test_integrations.py -v`
Expected: PASS (6 tests)

- [ ] **Step 6: Run full suite and commit**

```bash
.venv/bin/python -m pytest -q
git add apps/coach_web/brief.py apps/coach_web/api.py tests/web/test_integrations.py
git commit -m "feat(web): dismissals filter the brief, check-offs override the board, goals on overview"
```

---

### Task 7: Frontend write helpers

**Files:**
- Modify: `apps/coach_web/frontend/src/api.ts`
- Test: `apps/coach_web/frontend/src/__tests__/api.test.ts` (extend)

**Interfaces:**
- Produces: `post(path, body)`, `patch(path, body)`, `del(path)` — same 401-redirect and `ApiError` behaviour as the existing `get`.

- [ ] **Step 1: Write the failing tests**

Append to `apps/coach_web/frontend/src/__tests__/api.test.ts`, following the file's existing mocking style:

```ts
describe("write helpers", () => {
  it("posts JSON and returns the parsed body", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({ id: 1 }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    const { post } = await import("../api");
    expect(await post("/api/goals", { title: "x" })).toEqual({ id: 1 });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("same-origin");
    expect(JSON.parse(init.body)).toEqual({ title: "x" });
  });

  it("throws ApiError with the server detail", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(
      new Response(JSON.stringify({ detail: "cross-origin write rejected" }),
        { status: 403 }))));
    const { post, ApiError } = await import("../api");
    await expect(post("/api/goals", {})).rejects.toBeInstanceOf(ApiError);
  });

  it("deletes without a body", async () => {
    const fetchMock = vi.fn(() =>
      Promise.resolve(new Response(JSON.stringify({ status: "ok" }), { status: 200 })));
    vi.stubGlobal("fetch", fetchMock);
    const { del } = await import("../api");
    await del("/api/goals/1");
    expect(fetchMock.mock.calls[0][1].method).toBe("DELETE");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/coach_web/frontend && npx vitest run src/__tests__/api.test.ts`
Expected: FAIL — `post is not a function`

- [ ] **Step 3: Implement**

Append to `apps/coach_web/frontend/src/api.ts`:

```ts
async function write(path: string, method: string, body?: unknown): Promise<any> {
  const resp = await fetch(path, {
    method,
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (resp.status === 401) {
    location.assign("/login");
    throw new ApiError(401, "login required");
  }
  if (!resp.ok) {
    let detail = resp.statusText;
    try { detail = (await resp.json()).detail ?? detail; } catch { /* keep */ }
    throw new ApiError(resp.status, detail);
  }
  return resp.json();
}

export const post = (path: string, body: unknown) => write(path, "POST", body);
export const patch = (path: string, body: unknown) => write(path, "PATCH", body);
export const del = (path: string) => write(path, "DELETE");
```

The browser sets `Origin` automatically on these; nothing here needs to add it.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/coach_web/frontend && npx vitest run src/__tests__/api.test.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/frontend/src/api.ts apps/coach_web/frontend/src/__tests__/api.test.ts
git commit -m "feat(frontend): post/patch/del helpers"
```

---

### Task 8: Goals & Coach page — goals, dismissals, brief notes

**Files:**
- Modify: `apps/coach_web/frontend/src/pages/Goals.tsx`
- Test: `apps/coach_web/frontend/src/__tests__/Writes.test.tsx` (new)

**Interfaces:**
- Consumes: `post`/`patch`/`del` from Task 7; `/api/goals`, `/api/dismissals`, `/api/notes`.

- [ ] **Step 1: Write the test**

`apps/coach_web/frontend/src/__tests__/Writes.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import Goals from "../pages/Goals";

afterEach(() => vi.restoreAllMocks());

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status });
}

function routes(overrides: Record<string, unknown> = {}) {
  const table: Record<string, unknown> = {
    "/api/briefs": { latest: null, archive: [] },
    "/api/goals": { goals: [{ id: 1, kind: "tag", target: "auth",
      title: "Ship auth", target_date: "2026-09-01", status: "active",
      created_at: "2026-08-12T07:00:00+00:00" }] },
    "/api/dismissals": { dismissals: [{ id: 7, kind: "tag", target: "auth",
      reason: "not now", created_at: "2026-08-12T07:00:00+00:00" }] },
    "/api/notes": { notes: [] },
    ...overrides,
  };
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    const key = Object.keys(table).find((k) => url.startsWith(k));
    return Promise.resolve(json(key ? table[key] : { status: "ok" }));
  });
}

describe("Goals & Coach writes", () => {
  it("lists active goals and dismissals", async () => {
    vi.stubGlobal("fetch", routes());
    render(<Goals />);
    expect(await screen.findByText("Ship auth")).toBeInTheDocument();
    // Match on the reason, not /auth/ -- "Ship auth" contains "auth" and a
    // loose regex would match two elements and throw.
    expect(await screen.findByText(/not now/)).toBeInTheDocument();
  });

  it("creates a goal", async () => {
    const fetchMock = routes();
    vi.stubGlobal("fetch", fetchMock);
    render(<Goals />);
    await screen.findByText("Ship auth");
    fireEvent.change(screen.getByPlaceholderText(/goal/i),
      { target: { value: "Learn hooks" } });
    fireEvent.change(screen.getByPlaceholderText(/tag or feature/i),
      { target: { value: "hooks" } });
    fireEvent.click(screen.getByRole("button", { name: /add goal/i }));
    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(
        ([u, i]: any) => String(u) === "/api/goals" && i?.method === "POST");
      expect(posted).toBeTruthy();
      expect(JSON.parse((posted as any)[1].body).title).toBe("Learn hooks");
    });
  });

  it("un-dismisses", async () => {
    const fetchMock = routes();
    vi.stubGlobal("fetch", fetchMock);
    render(<Goals />);
    await screen.findByText(/not now/);
    fireEvent.click(screen.getByRole("button", { name: /un-dismiss/i }));
    await waitFor(() => {
      const deleted = fetchMock.mock.calls.find(
        ([u, i]: any) => String(u) === "/api/dismissals/7" && i?.method === "DELETE");
      expect(deleted).toBeTruthy();
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/coach_web/frontend && npx vitest run src/__tests__/Writes.test.tsx`
Expected: FAIL — no "Ship auth" text, no "add goal" button

- [ ] **Step 3: Extend `Goals.tsx`**

Keep the existing brief card and archive exactly as they are. Add to the imports:

```tsx
import { del, get, patch, post } from "../api";
```

Add state and loaders alongside the existing brief state:

```tsx
  type Goal = { id: number; kind: string; target: string; title: string;
    target_date: string; status: string; created_at: string };
  type Dismissal = { id: number; kind: string; target: string; reason: string;
    created_at: string };

  const [goals, setGoals] = useState<Goal[]>([]);
  const [dismissals, setDismissals] = useState<Dismissal[]>([]);
  const [title, setTitle] = useState("");
  const [target, setTarget] = useState("");

  const loadGoals = () => get("/api/goals").then((d) => setGoals(d.goals)).catch(() => {});
  const loadDismissals = () =>
    get("/api/dismissals").then((d) => setDismissals(d.dismissals)).catch(() => {});
  useEffect(() => { loadGoals(); loadDismissals(); }, []);

  async function addGoal() {
    if (!title.trim() || !target.trim()) return;
    await post("/api/goals", { kind: "tag", target: target.trim(), title: title.trim() });
    setTitle(""); setTarget("");
    loadGoals();
  }
```

Render after the brief archive:

```tsx
      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 15 }}>Goals</h2>
        {goals.filter((g) => g.status === "active").length === 0 && (
          <p className="muted" style={{ fontSize: 13 }}>No active goals.</p>
        )}
        {goals.filter((g) => g.status === "active").map((g) => (
          <div key={g.id} style={{ display: "flex", gap: 8, alignItems: "baseline",
            marginBottom: 6 }}>
            <span className="ink2" style={{ fontSize: 13, flex: 1 }}>
              {g.title} <span className="muted">({g.target}
              {g.target_date ? ` · by ${g.target_date}` : ""})</span>
            </span>
            <button type="button" onClick={async () => {
              await patch(`/api/goals/${g.id}`, { status: "done" }); loadGoals();
            }}>Done</button>
            <button type="button" onClick={async () => {
              await del(`/api/goals/${g.id}`); loadGoals();
            }}>Delete</button>
          </div>
        ))}
        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <input placeholder="New goal" value={title}
            onChange={(e) => setTitle(e.target.value)} style={{ flex: 2 }} />
          <input placeholder="tag or feature" value={target}
            onChange={(e) => setTarget(e.target.value)} style={{ flex: 1 }} />
          <button type="button" onClick={addGoal}>Add goal</button>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0, fontSize: 15 }}>Dismissed</h2>
        <p className="muted" style={{ fontSize: 12, marginTop: -4 }}>
          Hidden from the coach's suggestions. Still counted in the gap lists on Overview.
        </p>
        {dismissals.length === 0 && (
          <p className="muted" style={{ fontSize: 13 }}>Nothing dismissed.</p>
        )}
        {dismissals.map((d) => (
          <div key={d.id} style={{ display: "flex", gap: 8, alignItems: "baseline",
            marginBottom: 6 }}>
            <span className="ink2" style={{ fontSize: 13, flex: 1 }}>
              {d.target} <span className="muted">({d.kind}
              {d.reason ? ` · ${d.reason}` : ""})</span>
            </span>
            <button type="button" onClick={async () => {
              await del(`/api/dismissals/${d.id}`); loadDismissals();
            }}>Un-dismiss</button>
          </div>
        ))}
      </div>
```

- [ ] **Step 4: Typecheck, test, build**

```bash
cd apps/coach_web/frontend && npx tsc --noEmit && npx vitest run && npm run build
```
Expected: tsc clean, all tests pass, build succeeds.

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/frontend/src/pages/Goals.tsx \
        apps/coach_web/frontend/src/__tests__/Writes.test.tsx
git commit -m "feat(frontend): goal management and dismissal list on Goals & Coach"
```

---

### Task 9: Adoption check-off and dismiss controls

**Files:**
- Modify: `apps/coach_web/frontend/src/pages/Adoption.tsx`
- Test: `apps/coach_web/frontend/src/__tests__/Writes.test.tsx` (extend)

**Interfaces:**
- Consumes: `checked_off` / `detected_status` from Task 6; `/api/checkoffs`, `/api/dismissals`.

- [ ] **Step 1: Write the test**

Append to `Writes.test.tsx`:

```tsx
import Adoption from "../pages/Adoption";

describe("Adoption writes", () => {
  function board() {
    return vi.fn((input: RequestInfo | URL, init?: any) => {
      const url = String(input);
      if (url.startsWith("/api/adoption/board") && (!init || !init.method)) {
        return Promise.resolve(json({ features: [{
          name: "plan mode", lesson: "09", source: "checklist",
          discovered_at: "2026-01-01", status: "never-touched",
          detected_status: "never-touched", checked_off: false,
          last_used: null, history: [] }] }));
      }
      return Promise.resolve(json({ status: "ok" }));
    });
  }

  it("checks a feature off", async () => {
    const fetchMock = board();
    vi.stubGlobal("fetch", fetchMock);
    render(<Adoption />);
    await screen.findByText("plan mode");
    fireEvent.click(screen.getByRole("button", { name: /check off/i }));
    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(
        ([u, i]: any) => String(u) === "/api/checkoffs" && i?.method === "POST");
      expect(posted).toBeTruthy();
      expect(JSON.parse((posted as any)[1].body).feature_name).toBe("plan mode");
    });
  });

  it("dismisses a feature", async () => {
    const fetchMock = board();
    vi.stubGlobal("fetch", fetchMock);
    render(<Adoption />);
    await screen.findByText("plan mode");
    fireEvent.click(screen.getByRole("button", { name: /dismiss/i }));
    await waitFor(() => {
      const posted = fetchMock.mock.calls.find(
        ([u, i]: any) => String(u) === "/api/dismissals" && i?.method === "POST");
      expect(posted).toBeTruthy();
      expect(JSON.parse((posted as any)[1].body).kind).toBe("feature");
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/coach_web/frontend && npx vitest run src/__tests__/Writes.test.tsx`
Expected: FAIL — no "check off" button

- [ ] **Step 3: Extend `Adoption.tsx`**

Change the import to `import { del, get, post } from "../api";`. Extend the type:

```tsx
type Feature = { name: string; lesson: string; status: string;
  last_used: string | null; source: string; discovered_at: string;
  checked_off?: boolean; detected_status?: string;
  history: { captured_at: string; status: string }[] };
```

Replace the `useEffect` with a named reloader so the buttons can refresh:

```tsx
  const reload = () => get("/api/adoption/board").then(setData).catch((e) => setErr(String(e)));
  useEffect(() => { reload(); }, []);
```

Replace the existing three-cell `<tr>` body with four cells — the last carries the controls:

```tsx
                <tr key={f.name} style={{ borderTop: "1px solid var(--grid)" }}>
                  <td style={{ padding: "8px 0", width: "40%" }}>{f.name}</td>
                  <td><StatusChip status={f.status} /></td>
                  <td className="ink2" style={{ textAlign: "right" }}>
                    {f.last_used ? `last used ${fmtDate(f.last_used)}` : ""}
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    <button type="button" onClick={async () => {
                      if (f.checked_off) {
                        await del(`/api/checkoffs/${encodeURIComponent(f.name)}`);
                      } else {
                        await post("/api/checkoffs", { feature_name: f.name });
                      }
                      reload();
                    }}>{f.checked_off ? "Undo check off" : "Check off"}</button>{" "}
                    <button type="button" onClick={async () => {
                      await post("/api/dismissals", { kind: "feature", target: f.name });
                    }}>Dismiss</button>
                  </td>
                </tr>
```

The existing `StatusChip` renders `checked-off` from `f.status` with no change, which is enough visual distinction. Do not drop `detected_status` from the API payload — it is what preserves the detector's opinion when a check-off overrides it.

- [ ] **Step 4: Typecheck, test, build**

```bash
cd apps/coach_web/frontend && npx tsc --noEmit && npx vitest run && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/frontend/src/pages/Adoption.tsx \
        apps/coach_web/frontend/src/__tests__/Writes.test.tsx
git commit -m "feat(frontend): check-off and dismiss controls on Adoption"
```

---

### Task 10: Overview active goals

**Files:**
- Modify: `apps/coach_web/frontend/src/pages/Overview.tsx`
- Test: `apps/coach_web/frontend/src/__tests__/Writes.test.tsx` (extend)

**Interfaces:**
- Consumes: `active_goals` from Task 6.

- [ ] **Step 1: Write the test**

Append to `Writes.test.tsx`:

```tsx
import Overview from "../pages/Overview";

describe("Overview goals", () => {
  it("shows active goals", async () => {
    vi.stubGlobal("fetch", vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/briefs")) {
        return Promise.resolve(json({ latest: null, archive: [] }));
      }
      return Promise.resolve(json({
        freshness: { captured_at: "2026-08-12T07:00:00+00:00", received_at: null },
        tiles: { units_this_week: 1, commits_this_week: 2, streak_days: 3,
          streak_last_active: "2026-08-12", sessions_this_week: 4,
          cost_this_week: 1.5 },
        never_built: [], stale: [], adoption_gaps: [],
        active_goals: [{ id: 1, kind: "tag", target: "auth",
          title: "Ship auth", target_date: "2026-09-01" }],
        grade: { level: "Junior Engineer", pct_to_next: 72, next_level: "Mid-Level",
          gates: [] },
      }));
    }));
    render(<Overview />);
    expect(await screen.findByText("Ship auth")).toBeInTheDocument();
  });
});
```

If the `grade` shape above does not match `GradeCard`'s `Grade` type, copy the shape from the existing `GradeCard.test.tsx` fixture rather than inventing one.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/coach_web/frontend && npx vitest run src/__tests__/Writes.test.tsx`
Expected: FAIL — "Ship auth" not found

- [ ] **Step 3: Render goals on Overview**

Add `active_goals: { id: number; kind: string; target: string; title: string; target_date: string }[];` to the `Overview` type, and render a card after the tile row:

```tsx
      {data.active_goals?.length > 0 && (
        <div className="card" style={{ marginTop: 16 }}>
          <h2 style={{ marginTop: 0, fontSize: 15 }}>Active goals</h2>
          <ul className="ink2" style={{ fontSize: 13 }}>
            {data.active_goals.map((g) => (
              <li key={g.id}>{g.title}{" "}
                <span className="muted">({g.target}
                {g.target_date ? ` · by ${g.target_date}` : ""})</span></li>
            ))}
          </ul>
        </div>
      )}
```

- [ ] **Step 4: Typecheck, test, build**

```bash
cd apps/coach_web/frontend && npx tsc --noEmit && npx vitest run && npm run build
```

- [ ] **Step 5: Commit**

```bash
git add apps/coach_web/frontend/src/pages/Overview.tsx \
        apps/coach_web/frontend/src/__tests__/Writes.test.tsx
git commit -m "feat(frontend): active goals on Overview"
```

---

### Task 11: Deploy

**Files:** none new (uses `.claude/skills/deploy-coach-web/SKILL.md`)

- [ ] **Step 1: Finish the branch.** Final whole-branch review, then merge to main. Confirm the Python suite and the frontend typecheck/build are green on the merge commit.

- [ ] **Step 2: Deploy.** No new required environment variable — `COACH_ALLOWED_ORIGINS` is optional and unset falls back to the request `Host`.

```bash
railway up --service coach-web --detach
```
Poll `railway deployment list --service coach-web --limit 1 --json` until terminal; run the poll as a **background** task (foreground `sleep` is blocked).

- [ ] **Step 3: Verify the migration and headers.**

```bash
curl -s https://coach-web-production-1f04.up.railway.app/api/health
curl -sI https://coach-web-production-1f04.up.railway.app/ | grep -icE 'x-frame|content-security|strict-transport|x-content-type|referrer-policy'
railway logs --service coach-web | grep -iE "Running upgrade|goals|dismissals"
```
Expected: `{"status":"ok"}`, header count **5**, and a line showing the migration ran.

- [ ] **Step 4: Verify the sweep still works — this is the regression that matters.**

```bash
make sweep
```
Expected: `shipped=1 queued=0`. The sweep sends **no `Origin` header**; if `require_same_origin` leaked onto the ingest router this is where it breaks, and a queued/rejected payload is the signal.

- [ ] **Step 5: Verify the tables live.**

```bash
railway ssh --service coach-web "python -c \"
import os
from sqlalchemy import create_engine, text
e = create_engine(os.environ['DATABASE_URL'].replace('postgresql://','postgresql+psycopg://'))
with e.connect() as c:
    for t in ('goals','notes','dismissals','feature_checkoffs'):
        print(t, c.execute(text('select count(*) from ' + t)).scalar())
\""
```
Expected: all four tables present, each `0`.

- [ ] **Step 6: Confirm cross-origin writes are actually rejected in production.**

```bash
curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  https://coach-web-production-1f04.up.railway.app/api/goals \
  -H 'Origin: https://evil.example' -H 'Content-Type: application/json' -d '{}'
```
Expected: **401** (no session) — and with a valid session cookie it must be **403**. Since you cannot log in, 401 is the check available; note that the unit tests cover the 403 path.

Then have the owner exercise the UI: add a goal, check a feature off, dismiss a gap, and confirm the next brief stops mentioning the dismissed item.

**Do not stop at "it compiles" or "the deploy succeeded."** Every previous stage of this project was declared done on a green build and then found broken.

- [ ] **Step 7: Update `docs/HANDOFF.md`** — record that Phase 5 shipped, that all phases are now complete, the measured security baseline, the two accepted limitations (cookie-clearing logout, `frame-ancestors`-only CSP), and the standing rule that `require_same_origin` must never be applied to the ingest/usage routers.

---

## Self-Review Notes

- **Spec coverage:** four tables ✓ T1; Origin check with safe-method and machine-client carve-outs ✓ T2; logout, headers, limiter lock ✓ T3; goals + notes CRUD ✓ T4; dismissals + check-offs, idempotent ✓ T5; all three integrations ✓ T6; write helpers ✓ T7; UI ✓ T8–T10; deploy + live verification ✓ T11.
- **Placeholder scan:** none. One conditional instruction in T10 Step 1 (copy the `grade` fixture from `GradeCard.test.tsx` if the shape differs) — an instruction to check an existing file, not a TBD.
- **Type consistency:** `kind` is `tag|feature` on both `Goal` and `Dismissal`; `subject_kind` is `tag|feature|brief` on `Note` only. `checked_off`/`detected_status` are produced in T6 and consumed in T9. `post`/`patch`/`del` are produced in T7 and used in T8–T9. Goal JSON keys match across T4 producer, T6 overview consumer, and T8/T10 TS types.
- **Known risk for the executor:** T2's dependency is attached at router level in T4. If anyone later moves it to `app.add_middleware`, `/api/ingest` and `/api/usage` start returning 403 and the daily sweep silently queues forever. T11 Step 4 is the test that catches it; keep it.
- **Deliberate deviation:** the spec listed tag notes on Capabilities. This plan ships the notes **API** for all three subject kinds (T4) but wires UI only for briefs on Goals & Coach; tag and feature note UI is left for a follow-up rather than adding two more page rewrites to an already 11-task plan. The API needs no change when that UI arrives.
