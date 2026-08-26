"""Ship snapshot payloads to coach-web. Never raises out of ship_all."""
import logging
from collections import defaultdict
from pathlib import Path

from shared.snapshot import SCHEMA_VERSION, finalize_payload
from . import classifier

log = logging.getLogger("shipper")

UNIT_FIELDS = ("key", "kind", "repo", "date", "title",
               "tags", "complexity", "summary", "model")


def build_snapshot(data_dir: Path, adoption_rows: list[dict],
                   sweep_stats: dict, captured_at: str,
                   usage: dict | None = None,
                   infra: list[dict] | None = None,
                   services: list[dict] | None = None) -> dict:
    ledger = classifier.read_jsonl(data_dir / "ledger.jsonl")
    units = classifier.effective_rows(
        classifier.read_jsonl(data_dir / "classifications.jsonl"))
    daily: dict[str, dict] = defaultdict(lambda: {"commits": 0, "by_repo": {}})
    for row in ledger:
        day = row["date"][:10]
        daily[day]["commits"] += 1
        daily[day]["by_repo"][row["repo"]] = daily[day]["by_repo"].get(row["repo"], 0) + 1
    act = {d: {"date": d, **v} for d, v in sorted(daily.items())}
    if usage:
        # A day with Claude Code activity but no commits still gets a row.
        for d, u in usage["activity"].items():
            row = act.setdefault(d, {"date": d, "commits": 0, "by_repo": {}})
            row["sessions"] = u["sessions"]
            row["prompts"] = u["prompts"]
    body = {
        "schema_version": SCHEMA_VERSION,
        "sweep": sweep_stats,
        "feature_units": [{f: u.get(f) for f in UNIT_FIELDS} for u in units],
        "activity_daily": [act[d] for d in sorted(act)],
        "adoption": adoption_rows,
        "cost_daily": (usage or {}).get("cost", []),
        "infra_usage": infra or [],
        "infra_usage_services": services or [],
    }
    return finalize_payload(body, captured_at)


OK, TERMINAL, RETRYABLE = "ok", "terminal", "retryable"


def _try_post(payload: dict, url: str, token: str, post) -> str:
    """Post one payload. Returns OK, TERMINAL (never retry) or RETRYABLE.

    A 4xx means the server will reject this payload every time, so retrying
    it forever would wedge the outbox behind a poison pill. 401/403 are the
    exception: they signal a bad or missing token, a config problem that
    fixes itself once the token is corrected, so those stay retryable.
    """
    try:
        resp = post(url, json=payload,
                    headers={"Authorization": f"Bearer {token}"}, timeout=30)
        if 200 <= resp.status_code < 300:
            return OK
        if 400 <= resp.status_code < 500 and resp.status_code not in (401, 403):
            log.warning("ship rejected: HTTP %s from %s (permanent)",
                        resp.status_code, url)
            return TERMINAL
        log.warning("ship failed: HTTP %s from %s", resp.status_code, url)
    except Exception as exc:
        log.warning("ship failed: %s: %s", type(exc).__name__, exc)
    return RETRYABLE


def ship_all(payload: dict, url: str, token: str,
             outbox_dir: Path, post=None) -> dict:
    """Ship pending outbox payloads (oldest first) then the current one.
    Retryable failures queue the payload; a retryable failure stops draining
    to preserve order. A payload the server permanently rejects (4xx other
    than 401/403) is quarantined as *.rejected and the drain continues, so
    one bad payload can never wedge every later one behind it.
    Never raises: every filesystem operation is guarded. A pending outbox
    entry that can't be read/parsed is quarantined (renamed to *.corrupt)
    and skipped without blocking the drain; any other unexpected filesystem
    error is logged and the affected payload is counted as queued."""
    import json as _json
    if post is None:
        import httpx
        post = httpx.post

    try:
        outbox_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        log.warning("ship_all: could not create outbox dir %s: %s: %s",
                    outbox_dir, type(exc).__name__, exc)
        return {"shipped": 0, "queued": 1, "rejected": 0}

    try:
        pending_paths = sorted(outbox_dir.glob("*.json"))
    except Exception as exc:
        log.warning("ship_all: could not list outbox dir %s: %s: %s",
                    outbox_dir, type(exc).__name__, exc)
        pending_paths = []

    shipped = queued = rejected = 0
    blocked = False
    for path in pending_paths:
        try:
            pending = _json.loads(path.read_text())
        except Exception as exc:
            log.warning("ship_all: corrupt outbox entry %s: %s: %s -- quarantining",
                        path, type(exc).__name__, exc)
            _quarantine(path, ".corrupt")
            continue

        outcome = RETRYABLE if blocked else _try_post(pending, url, token, post)
        if outcome == OK:
            try:
                path.unlink()
            except Exception as exc:
                log.warning("ship_all: shipped %s but could not remove outbox "
                            "entry: %s: %s", path, type(exc).__name__, exc)
            shipped += 1
        elif outcome == TERMINAL:
            # Permanently unacceptable to the server -- park it and keep
            # draining rather than retrying it on every future sweep.
            log.warning("ship_all: outbox entry %s permanently rejected by "
                        "server -- quarantining", path)
            _quarantine(path, ".rejected")
            rejected += 1
        else:
            blocked = True
            queued += 1

    outcome = RETRYABLE if blocked else _try_post(payload, url, token, post)
    if outcome == OK:
        shipped += 1
    else:
        suffix = ".json.rejected" if outcome == TERMINAL else ".json"
        if outcome == TERMINAL:
            log.warning("ship_all: current payload permanently rejected by "
                        "server -- quarantining, not queueing")
        try:
            stem = (f"{payload['captured_at'].replace(':', '')}"
                    f"-{payload['content_hash'][:8]}")
            (outbox_dir / (stem + suffix)).write_text(_json.dumps(payload))
        except Exception as exc:
            log.warning("ship_all: could not write payload to outbox: %s: %s",
                        type(exc).__name__, exc)
        if outcome == TERMINAL:
            rejected += 1
        else:
            queued += 1
    return {"shipped": shipped, "queued": queued, "rejected": rejected}


def _quarantine(path: Path, suffix: str) -> None:
    try:
        path.rename(path.with_name(path.name + suffix))
    except Exception as exc:
        log.warning("ship_all: could not quarantine outbox entry %s: %s: %s",
                    path, type(exc).__name__, exc)
