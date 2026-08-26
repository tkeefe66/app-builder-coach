"""Restore drill: locate latest -> download -> decrypt -> pg_restore into a
scratch database -> verify row counts against the live source -> report
elapsed time.

Port of family-tree's scripts/restore-drill.ts, adapted to coach-web's
six app-owned tables.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import psycopg
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import r2
from .backup_nightly import load_encryption_key, scrub_url
from .r2 import require_env

log = logging.getLogger("restore_drill")

APP_TABLES = (
    "goals",
    "notes",
    "dismissals",
    "feature_checkoffs",
    "brief_recommendations",
    "briefs",
)
DEFAULT_PREFIX = "backups/nightly/"

# Postgres 17+ pg_dump embeds an unconditional `SET transaction_timeout = 0;`
# preamble in custom-format archives. A Postgres 16 pg_restore target does
# not recognize that session GUC and reports it as exactly one ignored
# error. That affects only the restore session's config, not the restored
# data, so it — and only it — is treated as non-fatal.
_BENIGN_GUC_RE = re.compile(r'unrecognized configuration parameter "transaction_timeout"')
# Anchored with \b so this matches exactly "...: 1" and never "...: 11" or
# "...: 137" -- an unanchored match here would let a catastrophically
# broken restore (dozens/hundreds of failed tables) get waved through as
# the single expected transaction_timeout GUC mismatch.
_BENIGN_IGNORED_RE = re.compile(r"errors ignored on restore: 1\b")


def decrypt_backup(encrypted: bytes, key: bytes) -> bytes:
    """Inverse of backup_nightly.encrypt_backup(): iv (12) || tag (16) ||
    ciphertext. AESGCM.decrypt() wants ciphertext||tag, so the tag is moved
    back to the end before decrypting."""
    iv, tag, ciphertext = encrypted[:12], encrypted[12:28], encrypted[28:]
    return AESGCM(key).decrypt(iv, ciphertext + tag, None)


def object_prefix() -> str:
    """Defaults to the real nightly-backup prefix; override via
    DRILL_OBJECT_PREFIX to point the drill at scratch objects without
    touching production backups."""
    return os.environ.get("DRILL_OBJECT_PREFIX") or DEFAULT_PREFIX


def latest_key(objects: list, prefix: str) -> str:
    if not objects:
        raise RuntimeError(f"no backups found in R2 under {prefix}")
    return max(objects, key=lambda o: o.last_modified).key


def is_benign_guc_mismatch(stderr: str) -> bool:
    return bool(_BENIGN_GUC_RE.search(stderr) and _BENIGN_IGNORED_RE.search(stderr))


def run_pg_restore(dump_path: Path, drill_url: str) -> None:
    """--dbname is passed as an argv element (drill_url), so -- same shape
    as backup_nightly.run_pg_dump -- any exception or log line built from
    subprocess output must have the URL scrubbed first. subprocess.run is
    called without check=True here (the returncode is handled explicitly
    below, to distinguish the benign GUC mismatch from a real failure), so
    no raw CalledProcessError can propagate; the stderr scrub is still
    applied defensively in case pg_restore ever echoes the DSN back."""
    result = subprocess.run(
        ["pg_restore", "--clean", "--if-exists", "--no-owner", "--dbname", drill_url, str(dump_path)],
        capture_output=True,
        text=True,
    )
    if result.stdout:
        log.info(scrub_url(result.stdout))
    if result.returncode != 0:
        stderr = scrub_url(result.stderr or "")
        if is_benign_guc_mismatch(stderr):
            log.info(
                'pg_restore reported 1 ignored error - "transaction_timeout" is a '
                "PostgreSQL 17+ session GUC that the source pg_dump embeds "
                "unconditionally; this restore target does not recognize it. This "
                "affects only the restore session, not restored data - treating as "
                "non-fatal and continuing to verification."
            )
        else:
            if stderr:
                log.error(stderr)
            raise RuntimeError(f"pg_restore exited {result.returncode}; see output above")
    elif result.stderr:
        log.info(scrub_url(result.stderr))


def table_row_counts(database_url: str, tables: tuple) -> dict:
    """Row count per table; a table missing entirely is recorded as None
    rather than raising, so the caller can report it distinctly."""
    counts: dict = {}
    with psycopg.connect(database_url, autocommit=True) as conn:
        with conn.cursor() as cur:
            for table in tables:
                try:
                    cur.execute(f'SELECT count(*) FROM "{table}"')
                    counts[table] = cur.fetchone()[0]
                except psycopg.Error:
                    counts[table] = None
    return counts


def check_row_counts(drill_counts: dict, source_counts: dict) -> None:
    """Print a comparison table. Ordinary mismatched counts are reported,
    not fatal (they may reflect writes made after the backup was taken).
    Two things ARE fatal: a table missing entirely from the restore, and a
    table that restored zero rows while the source has data -- the latter
    means the restore silently produced no content for that table (e.g. its
    COPY failed after the table was created), which a plain count mismatch
    check would otherwise wave through as "just a mismatch"."""
    print("\n=== row counts (drill vs. source) ===")
    any_missing = False
    any_mismatch = False
    any_zeroed = False
    for table in APP_TABLES:
        drill_n = drill_counts.get(table)
        source_n = source_counts.get(table)
        if drill_n is None:
            any_missing = True
            print(f"  {table:<24} MISSING FROM RESTORE")
            continue
        flag = "OK" if drill_n == source_n else "MISMATCH"
        if flag == "MISMATCH":
            any_mismatch = True
            if drill_n == 0 and source_n:
                any_zeroed = True
                flag = "MISMATCH (FATAL: restored 0 rows, source has data)"
        print(f"  {table:<24} drill={drill_n}  source={source_n}  {flag}")
    if any_mismatch:
        print("  NOTE: mismatches may reflect writes made after the backup was taken.")
    if any_missing:
        raise RuntimeError("one or more app tables are missing entirely from the restore")
    if any_zeroed:
        raise RuntimeError(
            "one or more app tables restored 0 rows while the source has data"
        )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    start = time.monotonic()

    database_url = require_env("DATABASE_URL")
    drill_url = require_env("DRILL_DATABASE_URL")
    require_env("R2_ACCOUNT_ID")
    require_env("R2_ACCESS_KEY_ID")
    require_env("R2_SECRET_ACCESS_KEY")
    require_env("R2_BUCKET")
    key = load_encryption_key()

    prefix = object_prefix()
    log.info("locating latest backup in R2 under %s", prefix)
    objects = r2.list_r2(prefix)
    key_name = latest_key(objects, prefix)
    log.info("latest backup is %s", key_name)

    log.info("downloading + decrypting")
    encrypted = r2.get_from_r2(key_name)
    dump = decrypt_backup(encrypted, key)
    log.info("decrypted dump is %d bytes", len(dump))

    with TemporaryDirectory(prefix="restore-drill-") as tmp_dir:
        dump_path = Path(tmp_dir) / "backup.dump"
        dump_path.write_bytes(dump)
        log.info("pg_restore --clean --if-exists --no-owner")
        run_pg_restore(dump_path, drill_url)

    drill_counts = table_row_counts(drill_url, APP_TABLES)
    source_counts = table_row_counts(database_url, APP_TABLES)
    check_row_counts(drill_counts, source_counts)

    elapsed = time.monotonic() - start
    print(f"\n=== drill complete: elapsed {elapsed:.1f}s ===")


if __name__ == "__main__":
    main()
