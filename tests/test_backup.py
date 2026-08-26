"""Unit tests for the nightly-backup-to-R2 system. No network, no live
database — everything is faked or monkeypatched.
"""
import base64
import os
import subprocess
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.exceptions import InvalidTag

from src import backup_nightly, r2, restore_drill

PLAINTEXT = b"pg_dump output goes here, pretend this is a custom-format archive"


def _key() -> bytes:
    return os.urandom(32)


# --- 1. Encryption round-trip -------------------------------------------

def test_encrypt_decrypt_round_trip():
    key = _key()
    encrypted = backup_nightly.encrypt_backup(PLAINTEXT, key)
    assert restore_drill.decrypt_backup(encrypted, key) == PLAINTEXT


# --- 2. Byte layout, pinned independently of the round-trip -------------

def test_byte_layout_is_iv_then_tag_then_ciphertext(monkeypatch):
    """iv (12 bytes) || tag (16 bytes) || ciphertext — NOT iv + ct + tag,
    which is what AESGCM.encrypt() returns on its own (ct||tag).

    Fixes the iv and independently recomputes the expected tag/ciphertext
    via AESGCM directly, so this test fails if encrypt_backup's reorder is
    wrong even if decrypt_backup has a matching-but-wrong inverse (which
    the round-trip test alone would not catch).
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _key()
    fixed_iv = b"\x11" * 12
    monkeypatch.setattr(backup_nightly.os, "urandom", lambda n: fixed_iv)

    encrypted = backup_nightly.encrypt_backup(PLAINTEXT, key)

    blob = AESGCM(key).encrypt(fixed_iv, PLAINTEXT, None)
    expected_ciphertext, expected_tag = blob[:-16], blob[-16:]

    assert encrypted[:12] == fixed_iv
    assert encrypted[12:28] == expected_tag
    assert encrypted[28:] == expected_ciphertext


# --- 3. Wrong key fails to decrypt, does not return garbage -------------

def test_wrong_key_raises_not_garbage():
    encrypted = backup_nightly.encrypt_backup(PLAINTEXT, _key())
    with pytest.raises(InvalidTag):
        restore_drill.decrypt_backup(encrypted, _key())


# --- 4. Non-32-byte key raises a clear error -----------------------------

def test_load_encryption_key_rejects_wrong_length(monkeypatch):
    short_key_b64 = base64.b64encode(os.urandom(16)).decode()
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", short_key_b64)
    with pytest.raises(ValueError, match="32 bytes"):
        backup_nightly.load_encryption_key()


def test_load_encryption_key_accepts_32_bytes(monkeypatch):
    key_b64 = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", key_b64)
    assert backup_nightly.load_encryption_key() == base64.b64decode(key_b64)


# --- 5. Database guard: fake query result, no live DB --------------------

def test_guard_raises_when_tables_missing():
    with pytest.raises(RuntimeError, match="refusing to back up"):
        backup_nightly.check_database_guard("railway", tables_found=0)


def test_guard_raises_when_only_some_tables_present():
    with pytest.raises(RuntimeError, match="found 2 of 3"):
        backup_nightly.check_database_guard("railway", tables_found=2)


def test_guard_passes_when_all_tables_present():
    backup_nightly.check_database_guard("coach_web", tables_found=3)  # no raise


# --- 6. Prune boundary: 29 days kept, 31 days deleted --------------------

def test_prune_boundary_29_kept_31_deleted(monkeypatch):
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    kept = r2.R2Object(key="backups/nightly/2026-07-14.dump.enc",
                        last_modified=now - timedelta(days=29))
    deleted = r2.R2Object(key="backups/nightly/2026-07-12.dump.enc",
                           last_modified=now - timedelta(days=31))

    monkeypatch.setattr(r2, "list_r2", lambda prefix: [kept, deleted])
    deleted_keys = []
    monkeypatch.setattr(r2, "delete_r2", lambda key: deleted_keys.append(key))

    backup_nightly.prune_old_backups(now)

    assert deleted_keys == [deleted.key]
    assert kept.key not in deleted_keys


def test_prune_keeps_everything_when_all_recent(monkeypatch):
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    recent = r2.R2Object(key="backups/nightly/2026-08-11.dump.enc",
                          last_modified=now - timedelta(days=1))
    monkeypatch.setattr(r2, "list_r2", lambda prefix: [recent])
    deleted_keys = []
    monkeypatch.setattr(r2, "delete_r2", lambda key: deleted_keys.append(key))

    backup_nightly.prune_old_backups(now)

    assert deleted_keys == []


# --- 7. Missing required env var raises, naming the variable -------------

def test_require_env_names_missing_var(monkeypatch):
    monkeypatch.delenv("R2_BUCKET", raising=False)
    with pytest.raises(RuntimeError, match="R2_BUCKET"):
        r2.require_env("R2_BUCKET")


def test_load_encryption_key_names_missing_var(monkeypatch):
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="BACKUP_ENCRYPTION_KEY"):
        backup_nightly.load_encryption_key()


# --- extra coverage: restore-drill helpers, no network/DB ----------------

def test_latest_key_picks_most_recently_modified():
    now = datetime(2026, 8, 12, tzinfo=timezone.utc)
    older = r2.R2Object(key="backups/nightly/2026-08-10.dump.enc", last_modified=now - timedelta(days=2))
    newer = r2.R2Object(key="backups/nightly/2026-08-12.dump.enc", last_modified=now)
    assert restore_drill.latest_key([older, newer], "backups/nightly/") == newer.key


def test_latest_key_raises_when_empty():
    with pytest.raises(RuntimeError, match="no backups found"):
        restore_drill.latest_key([], "backups/nightly/")


def test_object_prefix_defaults_and_overrides(monkeypatch):
    monkeypatch.delenv("DRILL_OBJECT_PREFIX", raising=False)
    assert restore_drill.object_prefix() == "backups/nightly/"
    monkeypatch.setenv("DRILL_OBJECT_PREFIX", "backups/drill-scratch/")
    assert restore_drill.object_prefix() == "backups/drill-scratch/"


def test_benign_guc_mismatch_detected_precisely():
    benign = (
        'pg_restore: warning: errors ignored on restore: 1\n'
        'pg_restore: error: could not execute query: ERROR:  unrecognized '
        'configuration parameter "transaction_timeout"\n'
    )
    assert restore_drill.is_benign_guc_mismatch(benign) is True


def test_other_restore_errors_are_not_treated_as_benign():
    other = 'pg_restore: error: could not execute query: ERROR:  relation "goals" does not exist\n'
    assert restore_drill.is_benign_guc_mismatch(other) is False


def test_check_row_counts_raises_when_table_missing_from_restore():
    drill_counts = {t: 0 for t in restore_drill.APP_TABLES}
    drill_counts["briefs"] = None  # missing entirely
    source_counts = {t: 0 for t in restore_drill.APP_TABLES}
    with pytest.raises(RuntimeError, match="missing entirely"):
        restore_drill.check_row_counts(drill_counts, source_counts)


def test_check_row_counts_reports_but_does_not_raise_on_mismatch(capsys):
    drill_counts = {t: 5 for t in restore_drill.APP_TABLES}
    source_counts = {t: 5 for t in restore_drill.APP_TABLES}
    source_counts["goals"] = 7  # a write made after the backup was taken
    restore_drill.check_row_counts(drill_counts, source_counts)  # no raise
    out = capsys.readouterr().out
    assert "MISMATCH" in out


# --- CRITICAL 1: the benign-ignored-error regex must be anchored ----------

def test_benign_ignored_count_rejects_eleven():
    text = (
        'pg_restore: warning: errors ignored on restore: 11\n'
        'pg_restore: error: could not execute query: ERROR:  unrecognized '
        'configuration parameter "transaction_timeout"\n'
    )
    assert restore_drill.is_benign_guc_mismatch(text) is False


def test_benign_ignored_count_rejects_one_hundred_thirty_seven():
    text = (
        'pg_restore: warning: errors ignored on restore: 137\n'
        'pg_restore: error: could not execute query: ERROR:  unrecognized '
        'configuration parameter "transaction_timeout"\n'
    )
    assert restore_drill.is_benign_guc_mismatch(text) is False


def test_benign_ignored_count_rejects_one_thousand():
    text = (
        'pg_restore: warning: errors ignored on restore: 1000\n'
        'pg_restore: error: could not execute query: ERROR:  unrecognized '
        'configuration parameter "transaction_timeout"\n'
    )
    assert restore_drill.is_benign_guc_mismatch(text) is False


def test_benign_ignored_count_accepts_exactly_one():
    text = (
        'pg_restore: warning: errors ignored on restore: 1\n'
        'pg_restore: error: could not execute query: ERROR:  unrecognized '
        'configuration parameter "transaction_timeout"\n'
    )
    assert restore_drill.is_benign_guc_mismatch(text) is True


# --- IMPORTANT 2(d): both conjuncts of the benign carve-out are required --

def test_benign_mismatch_requires_guc_text_not_just_count():
    text = 'pg_restore: warning: errors ignored on restore: 1\n'
    assert restore_drill.is_benign_guc_mismatch(text) is False


def test_benign_mismatch_requires_count_text_not_just_guc():
    text = (
        'pg_restore: error: could not execute query: ERROR:  unrecognized '
        'configuration parameter "transaction_timeout"\n'
    )
    assert restore_drill.is_benign_guc_mismatch(text) is False


# --- IMPORTANT 3: restoring 0 rows against a non-empty source is fatal ----

def test_check_row_counts_raises_when_restored_zero_but_source_has_data():
    drill_counts = {t: 5 for t in restore_drill.APP_TABLES}
    drill_counts["briefs"] = 0
    source_counts = {t: 5 for t in restore_drill.APP_TABLES}
    source_counts["briefs"] = 5000
    with pytest.raises(RuntimeError, match="restored 0 rows"):
        restore_drill.check_row_counts(drill_counts, source_counts)


def test_check_row_counts_zero_vs_zero_is_ok_not_fatal():
    """Both sides legitimately empty is not the same as the restore
    dropping all the data -- must not be flagged fatal."""
    drill_counts = {t: 0 for t in restore_drill.APP_TABLES}
    source_counts = {t: 0 for t in restore_drill.APP_TABLES}
    restore_drill.check_row_counts(drill_counts, source_counts)  # no raise


# --- CRITICAL 2 / IMPORTANT 2(b): pg_dump failure must not leak the URL ---

def test_run_pg_dump_raises_on_nonzero_exit(monkeypatch):
    """Mirrors real subprocess.run: only raises CalledProcessError when
    check=True is actually passed through -- so this fails (not just with
    a different message, but by not raising the expected error at all) if
    `check=True` is ever dropped from the pg_dump call."""
    def fake_run(cmd, check=False, **kwargs):
        if check:
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr=b"pg_dump: error: connection failed")
        return subprocess.CompletedProcess(args=cmd, returncode=1, stdout=b"", stderr=b"pg_dump: error: connection failed")

    monkeypatch.setattr(backup_nightly.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="pg_dump exited 1"):
        backup_nightly.run_pg_dump("postgresql://backupuser:SuperSecret123@db.example.com:5432/coach_web")


def test_run_pg_dump_error_never_contains_password_or_url(monkeypatch):
    url = "postgresql://backupuser:SuperSecret123@db.example.com:5432/coach_web"

    def fake_run(cmd, **kwargs):
        # argv (containing the URL) is what CalledProcessError.__str__ embeds
        raise subprocess.CalledProcessError(returncode=1, cmd=cmd, stderr=b"pg_dump: error: connection failed")

    monkeypatch.setattr(backup_nightly.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as exc_info:
        backup_nightly.run_pg_dump(url)
    message = str(exc_info.value)
    assert "SuperSecret123" not in message
    assert url not in message


def test_run_pg_dump_scrubs_url_if_echoed_in_stderr(monkeypatch):
    """Defensive scrub: even if pg_dump's own stderr text echoes the DSN
    back (not just argv), it must not survive into the raised message."""
    url = "postgresql://backupuser:SuperSecret123@db.example.com:5432/coach_web"

    def fake_run(cmd, **kwargs):
        raise subprocess.CalledProcessError(
            returncode=1, cmd=cmd,
            stderr=f"pg_dump: error: could not connect using {url}".encode(),
        )

    monkeypatch.setattr(backup_nightly.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError) as exc_info:
        backup_nightly.run_pg_dump(url)
    message = str(exc_info.value)
    assert "SuperSecret123" not in message
    assert url not in message


# --- IMPORTANT 1: zero-byte / non-PGDMP dumps must not upload -------------

def test_validate_pg_dump_output_rejects_empty():
    with pytest.raises(RuntimeError, match=r"got 0 bytes"):
        backup_nightly.validate_pg_dump_output(b"")


def test_validate_pg_dump_output_rejects_small_non_pgdmp_blob():
    with pytest.raises(RuntimeError):
        backup_nightly.validate_pg_dump_output(b"not a real dump, just some text padding" * 30)


def test_validate_pg_dump_output_accepts_valid_looking_archive():
    data = b"PGDMP" + b"\x00" * 2000
    assert backup_nightly.validate_pg_dump_output(data) == data


def test_run_pg_dump_rejects_empty_output_end_to_end(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(backup_nightly.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match=r"got 0 bytes"):
        backup_nightly.run_pg_dump("postgresql://u:p@h/db")


# --- IMPORTANT 2(a): the database guard must run BEFORE the upload --------

def test_guard_runs_before_upload(monkeypatch):
    call_order: list[str] = []

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host/db")
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setenv("R2_BUCKET", "bucket")
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", base64.b64encode(os.urandom(32)).decode())

    def fake_assert(database_url):
        call_order.append("guard")

    def fake_run_pg_dump(database_url):
        return b"PGDMP" + b"\x00" * 2000

    def fake_upload(key, body):
        call_order.append("upload")

    monkeypatch.setattr(backup_nightly, "assert_dumping_the_right_database", fake_assert)
    monkeypatch.setattr(backup_nightly, "run_pg_dump", fake_run_pg_dump)
    monkeypatch.setattr(backup_nightly.r2, "upload_to_r2", fake_upload)
    monkeypatch.setattr(backup_nightly.r2, "list_r2", lambda prefix: [])

    backup_nightly.main()

    assert "guard" in call_order and "upload" in call_order
    assert call_order.index("guard") < call_order.index("upload")


# --- IMPORTANT 2(c): prune must scan the exact PRUNE_PREFIX ---------------

def test_prune_passes_exact_prefix_to_list_r2(monkeypatch):
    seen_prefixes: list[str] = []

    def fake_list_r2(prefix):
        seen_prefixes.append(prefix)
        return []

    monkeypatch.setattr(r2, "list_r2", fake_list_r2)
    backup_nightly.prune_old_backups(datetime(2026, 8, 12, tzinfo=timezone.utc))

    # Hardcoded literal, not backup_nightly.PRUNE_PREFIX -- comparing
    # against the constant itself would still pass if the constant were
    # wrongly changed to "", since both sides would move together.
    assert seen_prefixes == ["backups/nightly/"]
