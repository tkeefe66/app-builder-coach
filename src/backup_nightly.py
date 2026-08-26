"""Nightly backup: assert-right-database -> pg_dump -> encrypt -> upload -> prune.

Port of family-tree's scripts/backup-nightly.ts, adapted to coach-web's
schema. Two details are ported byte-for-byte from that production system
because getting either wrong produces a backup that looks fine and cannot
be restored:

  1. The AES-256-GCM byte layout is iv (12) || tag (16) || ciphertext.
     Python's AESGCM.encrypt() returns ciphertext||tag (tag appended), so
     encrypt_backup() below splits and reorders it. See the byte-layout
     test in tests/test_backup.py, which pins this independently of the
     round-trip test.
  2. assert_dumping_right_database() must run before anything is written
     to R2 — see its docstring for the failure this prevents.
"""
from __future__ import annotations

import base64
import logging
import os
import re
import subprocess
from datetime import datetime, timedelta, timezone

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import r2
from .r2 import require_env

log = logging.getLogger("backup_nightly")

REQUIRED_TABLES = ("briefs", "goals", "snapshots")
PRUNE_PREFIX = "backups/nightly/"
PRUNE_AFTER_DAYS = 30

# pg_dump's custom-format archives start with this 5-byte magic string. A
# zero-byte or truncated dump (e.g. a client/server version mismatch that
# still exits 0, or a network hiccup) must never be treated as a good
# backup -- it would upload and silently overwrite the day's last-known-good
# object.
MIN_DUMP_BYTES = 1024
PGDUMP_MAGIC = b"PGDMP"

# Defensively strips any postgres/postgresql URL -- which may embed a
# password -- out of subprocess output before it can reach a log line or an
# exception message. `subprocess.CalledProcessError.__str__` embeds the
# whole argv (including a --dbname URL), so callers must never let a raw
# CalledProcessError propagate; this also guards against the URL showing up
# echoed back in stderr text itself.
_URL_RE = re.compile(r"postgres(?:ql)?://\S*")


def scrub_url(text: str) -> str:
    return _URL_RE.sub("postgresql://<redacted>", text)


def load_encryption_key() -> bytes:
    raw = require_env("BACKUP_ENCRYPTION_KEY")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise ValueError(
            f"BACKUP_ENCRYPTION_KEY must decode to exactly 32 bytes, got {len(key)}"
        )
    return key


def encrypt_backup(plain: bytes, key: bytes) -> bytes:
    """AES-256-GCM, laid out as iv (12 bytes) || tag (16 bytes) || ciphertext.

    AESGCM.encrypt() returns ciphertext with the tag appended (ct||tag), not
    the iv||tag||ct layout family-tree uses, so the tag is split off the end
    and moved ahead of the ciphertext. NOT iv + ct + tag.
    """
    iv = os.urandom(12)
    blob = AESGCM(key).encrypt(iv, plain, None)
    ciphertext, tag = blob[:-16], blob[-16:]
    return iv + tag + ciphertext


def check_database_guard(db: str, tables_found: int) -> None:
    """Raise unless all of REQUIRED_TABLES were found in `public`.

    Split out from assert_dumping_right_database() so tests can exercise
    the guard logic with a fake query result — no live database needed.
    """
    if tables_found < len(REQUIRED_TABLES):
        raise RuntimeError(
            f'refusing to back up "{db}": expected the app\'s tables '
            f"({', '.join(REQUIRED_TABLES)}), found {tables_found} of "
            f"{len(REQUIRED_TABLES)}. DATABASE_URL is almost certainly "
            "pointed at the wrong database."
        )
    log.info('source database "%s" looks like this app', db)


def _fetch_guard_row(database_url: str) -> tuple[str, int]:
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT current_database() AS db,
                       (SELECT count(*) FROM information_schema.tables
                         WHERE table_schema = 'public'
                           AND table_name = ANY(%s)) AS tables
                """,
                (list(REQUIRED_TABLES),),
            )
            db, tables = cur.fetchone()
    return db, int(tables)


def assert_dumping_the_right_database(database_url: str) -> None:
    """Refuse to dump a database that isn't this app's, BEFORE anything is
    written to R2.

    The failure this exists for is silent: a managed platform's
    ``${{Postgres.DATABASE_URL}}``-style reference can point at the
    platform's DEFAULT database rather than the one the app created.
    pg_dump against it succeeds, produces a valid small file, uploads it
    over the day's key, and the job reports success — you find out during
    a restore.

    The check is "do the app's tables exist", not "is there data". A
    legitimately empty install still has a schema; a wrong database has
    none of these tables at all. Failing here is safe: date-stamped keys
    mean a refused run leaves every prior backup untouched.
    """
    db, tables = _fetch_guard_row(database_url)
    check_database_guard(db, tables)


def validate_pg_dump_output(data: bytes) -> bytes:
    """Refuse anything that isn't plausibly a real custom-format archive:
    at least MIN_DUMP_BYTES and starting with the PGDMP magic. A zero-exit,
    zero-byte (or truncated/wrong-format) dump must never be treated as
    good -- it would upload and overwrite the day's last-known-good backup.
    """
    if len(data) < MIN_DUMP_BYTES or not data.startswith(PGDUMP_MAGIC):
        raise RuntimeError(
            f"pg_dump output does not look like a valid custom-format "
            f"archive (got {len(data)} bytes, expected >= {MIN_DUMP_BYTES} "
            f"bytes starting with {PGDUMP_MAGIC!r}); refusing to upload it"
        )
    return data


def run_pg_dump(database_url: str) -> bytes:
    """--format=custom --no-owner: compressed, supports selective/parallel
    restore. Plain SQL does not.

    The URL is passed as an argv element, and CalledProcessError.__str__
    embeds the whole argv -- so a raw CalledProcessError must never
    propagate uncaught, or the connection string (password included) ends
    up in the service log. Re-raise with only the exit code and
    (URL-scrubbed) stderr.
    """
    try:
        result = subprocess.run(
            ["pg_dump", "--dbname", database_url, "--format", "custom", "--no-owner"],
            check=True,
            capture_output=True,
        )
    except subprocess.CalledProcessError as exc:
        stderr = scrub_url((exc.stderr or b"").decode("utf-8", "replace"))
        raise RuntimeError(f"pg_dump exited {exc.returncode}: {stderr}") from None
    return validate_pg_dump_output(result.stdout)


def dump_object_key(now: datetime) -> str:
    return f"{PRUNE_PREFIX}{now.strftime('%Y-%m-%d')}.dump.enc"


def prune_old_backups(now: datetime) -> None:
    """Delete objects under PRUNE_PREFIX older than PRUNE_AFTER_DAYS.

    Callers must not let a failure here fail the run: by the time this is
    called the day's backup is already safely uploaded.
    """
    cutoff = now - timedelta(days=PRUNE_AFTER_DAYS)
    for obj in r2.list_r2(PRUNE_PREFIX):
        if obj.last_modified < cutoff:
            r2.delete_r2(obj.key)
            log.info("pruned %s", obj.key)


def main() -> None:
    logging.basicConfig(level=logging.INFO)

    database_url = require_env("DATABASE_URL")
    require_env("R2_ACCOUNT_ID")
    require_env("R2_ACCESS_KEY_ID")
    require_env("R2_SECRET_ACCESS_KEY")
    require_env("R2_BUCKET")
    key = load_encryption_key()

    log.info("checking source database before touching R2")
    assert_dumping_the_right_database(database_url)

    log.info("running pg_dump")
    dump = run_pg_dump(database_url)
    log.info("pg_dump produced %d bytes plain", len(dump))

    now = datetime.now(timezone.utc)
    object_key = dump_object_key(now)
    encrypted = encrypt_backup(dump, key)
    r2.upload_to_r2(object_key, encrypted)
    log.info("uploaded %s (%d bytes plain)", object_key, len(dump))

    try:
        prune_old_backups(now)
    except Exception as exc:  # a prune failure must not fail the run
        log.error("prune failed (continuing, backup already uploaded): %s", exc)


if __name__ == "__main__":
    main()
