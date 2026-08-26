"""Single-user auth: machine bearer token (ingest) + password session (human)."""
import hashlib
import logging
import threading
import secrets
import sys
import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from itsdangerous import BadSignature, TimestampSigner
from pydantic import BaseModel

log = logging.getLogger("auth")

SESSION_COOKIE = "coach_session"
SESSION_MAX_AGE = 30 * 86400

LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 60

router = APIRouter()


class LoginRateLimiter:
    """Sliding-window throttle for /api/login.

    Verifying a password costs 600k PBKDF2 iterations, so an unthrottled
    endpoint is both a brute-force oracle and a free CPU-burn DoS. This is
    a single-user app, so one global window is enough -- no per-IP keying,
    which an attacker could sidestep anyway. In-process state: it protects
    one worker, which is the deployment shape here.
    """

    def __init__(self, max_attempts: int = LOGIN_MAX_ATTEMPTS,
                 window_seconds: int = LOGIN_WINDOW_SECONDS,
                 clock=time.monotonic):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._clock = clock
        self._attempts: list[float] = []
        self._lock = threading.Lock()

    def check(self) -> None:
        """Record an attempt, or raise 429 if the window is already full.

        Locked: sync handlers run on a threadpool, so an unlocked
        filter->compare->append is a read-modify-write race. Measured at 40-way
        concurrency against production it happened to hold, but the race is
        real in principle and the lock costs nothing.
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

    def reset(self) -> None:
        with self._lock:
            self._attempts.clear()


def _login_limiter(request: Request) -> LoginRateLimiter:
    limiter = getattr(request.app.state, "login_limiter", None)
    if limiter is None:  # app built without the factory; fail closed, not open
        limiter = request.app.state.login_limiter = LoginRateLimiter()
    return limiter


def hash_password(pw: str, iterations: int = 600_000) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt),
                                 iterations).hex()
    return f"pbkdf2${iterations}${salt}${digest}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        scheme, iters, salt, digest = stored.split("$")
        if scheme != "pbkdf2":
            return False
        candidate = hashlib.pbkdf2_hmac("sha256", pw.encode(),
                                        bytes.fromhex(salt), int(iters)).hex()
        return secrets.compare_digest(candidate, digest)
    except (ValueError, AttributeError):
        return False


def require_ingest_token(request: Request) -> None:
    token = request.app.state.settings.ingest_token
    header = request.headers.get("Authorization", "")
    if not token or not secrets.compare_digest(header, f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="invalid ingest token")


def require_usage_token(request: Request) -> None:
    """Bearer auth for /api/usage.

    A separate token from COACH_INGEST_TOKEN: the ingest token lives only on the
    sweep host, and spreading it across every deployed app would widen the blast
    radius for no benefit.
    """
    token = request.app.state.settings.usage_token
    header = request.headers.get("Authorization", "")
    if not token or not secrets.compare_digest(header, f"Bearer {token}"):
        raise HTTPException(status_code=401, detail="invalid usage token")


def require_user(request: Request) -> None:
    secret = request.app.state.settings.secret_key
    if not secret:
        # An empty key still produces valid signatures, so anyone could mint
        # a session cookie. Refuse before unsigning rather than trust it.
        raise HTTPException(status_code=401, detail="login required")
    signer = TimestampSigner(secret)
    cookie = request.cookies.get(SESSION_COOKIE, "")
    try:
        signer.unsign(cookie, max_age=SESSION_MAX_AGE)
    except BadSignature:
        raise HTTPException(status_code=401, detail="login required")


class LoginBody(BaseModel):
    password: str


@router.post("/api/login")
def login(body: LoginBody, request: Request, response: Response):
    settings = request.app.state.settings
    limiter = _login_limiter(request)
    limiter.check()  # before any hashing work
    if not settings.password_hash or not verify_password(body.password,
                                                         settings.password_hash):
        raise HTTPException(status_code=401, detail="wrong password")
    limiter.reset()
    signer = TimestampSigner(settings.secret_key)
    response.set_cookie(SESSION_COOKIE, signer.sign(b"user").decode(),
                        max_age=SESSION_MAX_AGE, httponly=True,
                        secure=True, samesite="lax")
    return {"status": "ok"}


if __name__ == "__main__":
    print(hash_password(sys.argv[1]))


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
