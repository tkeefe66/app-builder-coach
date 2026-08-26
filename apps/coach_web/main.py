"""FastAPI app factory. Routers are added here as they are built."""
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from shared.apps import load_apps, names

from .auth import LoginRateLimiter
from .config import Settings, settings_from_env
from .taxonomy import REPO_ROOT


REQUIRED_PROD_SECRETS = (
    ("secret_key", "COACH_SECRET_KEY"),
    ("password_hash", "COACH_PASSWORD_HASH"),
    ("ingest_token", "COACH_INGEST_TOKEN"),
    ("usage_token", "COACH_USAGE_TOKEN"),
)


_AUTO_DIST = object()


def _check_prod_secrets(settings: Settings) -> None:
    """Fail fast on a real deployment missing credentials.

    Local/dev sqlite runs stay unguarded; a non-sqlite DATABASE_URL means
    this is serving real data and must not boot with empty secrets.
    """
    url = settings.database_url
    if not url or url.startswith("sqlite"):
        return
    missing = [env for attr, env in REQUIRED_PROD_SECRETS if not getattr(settings, attr)]
    if missing:
        raise ValueError(
            "coach-web cannot start: missing required environment "
            f"variable(s): {', '.join(missing)}")


def create_app(settings: Settings, spa_dist=_AUTO_DIST) -> FastAPI:
    _check_prod_secrets(settings)
    from . import rubric
    rubric.load()  # invalid rubric.yaml must prevent boot, not break requests

    app = FastAPI(title="coach-web", docs_url=None, redoc_url=None, openapi_url=None)

    # The SPA loads nothing external -- a same-origin favicon and the bundled
    # module, nothing more -- so 'self' everywhere holds.
    #
    # style-src keeps 'unsafe-inline' permanently: Recharts injects inline
    # styles at runtime, so moving the SPA's own style={{}} usages to classes
    # would not let this be tightened. It is not a placeholder for that work.
    CSP = ("default-src 'self'; script-src 'self'; "
           "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
           "connect-src 'self'; object-src 'none'; base-uri 'self'; "
           "form-action 'self'; frame-ancestors 'none'")

    SECURITY_HEADERS = {
        "X-Frame-Options": "DENY",
        "Content-Security-Policy": CSP,
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

    app.state.settings = settings
    app.state.app_names = names(load_apps(REPO_ROOT / "apps.yaml"))
    # Per-app instance so each test app (and each worker) has its own window.
    app.state.login_limiter = LoginRateLimiter()

    from . import models
    from .db import make_engine

    app.state.engine = make_engine(settings.database_url)
    if settings.database_url.startswith("sqlite"):
        models.Base.metadata.create_all(app.state.engine)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    from .auth import router as auth_router
    app.include_router(auth_router)

    from .ingest import router as ingest_router
    app.include_router(ingest_router)

    from . import usage_api
    app.include_router(usage_api.router)

    from .api import router as api_router
    app.include_router(api_router)

    from .writes import router as writes_router
    app.include_router(writes_router)

    if spa_dist is _AUTO_DIST:
        default_dist = Path(__file__).parent / "frontend" / "dist"
        spa_dist = default_dist if default_dist.is_dir() else None
    if spa_dist is not None and Path(spa_dist).is_dir():
        dist = Path(spa_dist).resolve()
        index = dist / "index.html"

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            if full_path.startswith("api/"):
                raise HTTPException(status_code=404)
            candidate = (dist / full_path).resolve()
            if (full_path and candidate.is_file()
                    and candidate.is_relative_to(dist)):
                return FileResponse(candidate)
            return FileResponse(index)

    return app


app = create_app(settings_from_env())
