"""Direct usage ingest from deployed apps.

Apps POST the Anthropic `usage` block after each call. Rows are priced and
aggregated into llm_daily on write: no per-call table, so growth stays bounded
while cache-hit rate stays derivable.
"""
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from shared.apps import load_apps, names
from shared.pricing import price_for
from . import models
from .auth import require_usage_token
from .db import get_db

router = APIRouter(dependencies=[Depends(require_usage_token)])

REQUIRED = {"app", "model", "ts", "input_tokens", "output_tokens",
            "cache_read_input_tokens", "cache_creation_input_tokens"}
TOKEN_FIELDS = ("input_tokens", "output_tokens",
                "cache_read_input_tokens", "cache_creation_input_tokens")
REPO_ROOT = Path(__file__).resolve().parents[2]


def _validate(payload: dict, known_apps: set) -> None:
    missing = sorted(REQUIRED - payload.keys())
    if missing:
        raise HTTPException(400, f"usage missing required keys: {', '.join(missing)}")
    extra = sorted(payload.keys() - REQUIRED)
    if extra:
        raise HTTPException(400, f"usage has unexpected keys: {', '.join(extra)}")
    for field in TOKEN_FIELDS:
        value = payload[field]
        # bool is an int subclass; a flag is never a valid token count.
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise HTTPException(400, f"usage.{field} must be a non-negative int")
    ts = payload["ts"]
    if not isinstance(ts, str) or len(ts) < 10 or ts[4] != "-" or ts[7] != "-":
        raise HTTPException(400, "usage.ts must be an ISO 8601 timestamp")
    if payload["app"] not in known_apps:
        raise HTTPException(400, f"unknown app: {payload['app']}")


def cost_for(model: str, payload: dict) -> float:
    pin, pout, pread, pwrite = price_for(model)
    return round((payload["input_tokens"] * pin
                  + payload["output_tokens"] * pout
                  + payload["cache_read_input_tokens"] * pread
                  + payload["cache_creation_input_tokens"] * pwrite) / 1_000_000, 6)


def upsert_llm_daily(db, date: str, app: str, model: str, usage: dict) -> None:
    """Accumulate one priced call into llm_daily. Caller commits.

    Shared by /api/usage and the server's own brief calls so a single call can
    never be priced two different ways.
    """
    key = (date, app, model)
    row = db.get(models.LlmDaily, key)
    if row is None:
        row = models.LlmDaily(date=date, app=app, model=model)
        db.add(row)
    row.input_tokens = (row.input_tokens or 0) + usage["input_tokens"]
    row.output_tokens = (row.output_tokens or 0) + usage["output_tokens"]
    row.cache_read_tokens = (row.cache_read_tokens or 0) + usage["cache_read_input_tokens"]
    row.cache_creation_tokens = (
        (row.cache_creation_tokens or 0) + usage["cache_creation_input_tokens"])
    row.cost_usd = round((row.cost_usd or 0.0) + cost_for(model, usage), 6)
    row.call_count = (row.call_count or 0) + 1


@router.post("/api/usage")
def record_usage(payload: dict, request: Request, db: Session = Depends(get_db)):
    known = getattr(request.app.state, "app_names", None)
    if known is None:
        known = names(load_apps(REPO_ROOT / "apps.yaml"))
    _validate(payload, known)

    date = payload["ts"][:10]
    model = str(payload["model"])
    upsert_llm_daily(db, date, payload["app"], model, payload)
    db.commit()
    return {"ok": True, "date": date}
