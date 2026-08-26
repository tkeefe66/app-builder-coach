"""Grow the adoption checklist from the Claude Code changelog.

Conservative by construction: only `Added ` bullets under a well-formed
`## N.N.N` heading become catalog rows. Everything else is skipped, because
Phase 5 owns dismissals -- until it ships, a false positive is stuck in the
catalog with no way to wave it off.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from . import models

log = logging.getLogger("changelog")

CHANGELOG_URL = (
    "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md")
# feature_catalog.name is String(120); a longer name would be truncated by the
# database on some backends and rejected on others. Truncate deliberately.
NAME_MAX = 120
_VERSION_RE = re.compile(r"^\d+(?:\.\d+)*$")
_ARTICLES = ("a ", "an ", "the ")

W_VERSION = "changelog.version_watermark"
W_CHECKED = "changelog.last_checked_at"
CHECK_INTERVAL_DAYS = 7


def parse_version(heading: str) -> tuple | None:
    """(2, 1, 228) for '2.1.228', else None.

    Tuple-of-ints, never a string: '2.1.99' sorts ABOVE '2.1.228' as a string,
    which would let one old version freeze the watermark forever.
    """
    heading = heading.strip()
    if not _VERSION_RE.match(heading):
        return None
    return tuple(int(part) for part in heading.split("."))


def derive_name(bullet: str) -> str:
    """Turn 'Added a foo to bar; baz' into 'foo to bar'."""
    text = bullet.strip()
    if text.lower().startswith("added "):
        text = text[len("added "):]
    text = text.split(";")[0].strip()
    lowered = text.lower()
    for article in _ARTICLES:
        if lowered.startswith(article):
            text = text[len(article):]
            break
    return text.strip()[:NAME_MAX]


def parse(markdown: str) -> list[dict]:
    """Every `Added` bullet under a parseable version heading, in file order."""
    entries: list[dict] = []
    version = None
    version_str = ""
    for line in markdown.splitlines():
        if line.startswith("## "):
            version_str = line[3:].strip()
            version = parse_version(version_str)
            if version is None:
                log.debug("changelog: skipping unparseable heading %r", version_str)
            continue
        if version is None or not line.startswith("- Added "):
            continue
        name = derive_name(line[2:])
        if name:
            entries.append({"version": version, "version_str": version_str,
                            "name": name})
    return entries


def _fetch() -> str:
    import httpx
    resp = httpx.get(CHANGELOG_URL, timeout=30, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _get_state(db, key: str) -> str | None:
    row = db.get(models.WatcherState, key)
    return row.value if row is not None else None


def _set_state(db, key: str, value: str, now: datetime) -> None:
    row = db.get(models.WatcherState, key)
    if row is None:
        db.add(models.WatcherState(key=key, value=value,
                                   updated_at=now.isoformat()))
    else:
        row.value = value
        row.updated_at = now.isoformat()


def due(db, now: datetime) -> bool:
    """True when the watcher has never run, or last ran >= 7 days ago."""
    last = _get_state(db, W_CHECKED)
    if not last:
        return True
    try:
        when = datetime.fromisoformat(last)
    except ValueError:
        log.warning("changelog: unreadable last_checked_at %r; treating as due", last)
        return True
    return (now - when) >= timedelta(days=CHECK_INTERVAL_DAYS)


def check(db, fetch=_fetch, now: datetime | None = None) -> dict:
    """Fetch, diff against the watermark, insert new rows. Never raises.

    Does not commit -- the caller owns the transaction.
    """
    now = now or datetime.now(timezone.utc)
    watermark_str = _get_state(db, W_VERSION)
    try:
        entries = parse(fetch())
    except Exception as exc:
        log.warning("changelog: fetch failed: %s: %s", type(exc).__name__, exc)
        return {"status": "failed", "added": 0, "watermark": watermark_str or ""}

    if not entries:
        # Either the format drifted or the fetch returned something unusable.
        # Advancing the watermark here would silently skip real entries forever.
        log.warning("changelog: parsed zero entries; leaving watermark untouched")
        return {"status": "failed", "added": 0, "watermark": watermark_str or ""}

    newest = max(entries, key=lambda e: e["version"])
    watermark = parse_version(watermark_str) if watermark_str else None

    added = 0
    if watermark is None:
        # FIRST RUN: 361 versions of history is not news. Record where we are
        # and insert nothing; the next run reports genuinely new entries.
        log.info("changelog: first run, watermark set to %s, no rows inserted",
                 newest["version_str"])
    else:
        today = now.date().isoformat()
        for entry in entries:
            if entry["version"] <= watermark:
                continue
            if db.get(models.FeatureCatalog, entry["name"]) is not None:
                continue
            db.add(models.FeatureCatalog(name=entry["name"], lesson="",
                                         source="changelog", discovered_at=today))
            added += 1

    _set_state(db, W_VERSION, newest["version_str"], now)
    _set_state(db, W_CHECKED, now.isoformat(), now)
    return {"status": "ok", "added": added, "watermark": newest["version_str"]}
