from apps.coach_web import changelog

SAMPLE = """# Changelog

## 2.1.228

- Fixed interactive sessions that could stop redrawing entirely
- Added a workspace trust prompt to `claude agents` for untrusted directories, matching the behavior of `claude`
- Improved slash-command menu: blue now marks only the selected row
- Changed the Write tool so newer models can overwrite an existing file

## 2.1.227

- Added gateway spend-limit support to Claude Code's usage warning; the limit-reached message now names the cap
- Removed the outdated note about auto mode sessions

## 2.1.226

- Bug fixes and reliability improvements

## not-a-version

- Added something from a malformed heading
"""


def test_parse_version_tuple_orders_numerically():
    assert changelog.parse_version("2.1.228") == (2, 1, 228)
    # String compare would put 2.1.99 above 2.1.228; tuple compare must not.
    assert changelog.parse_version("2.1.99") < changelog.parse_version("2.1.228")


def test_parse_version_rejects_garbage():
    assert changelog.parse_version("not-a-version") is None
    assert changelog.parse_version("2.1.x") is None
    assert changelog.parse_version("") is None


def test_derive_name_strips_verb_article_and_clause():
    assert changelog.derive_name(
        "Added a workspace trust prompt to `claude agents` for untrusted directories"
    ) == "workspace trust prompt to `claude agents` for untrusted directories"
    assert changelog.derive_name(
        "Added gateway spend-limit support to Claude Code's usage warning; the limit-reached message"
    ) == "gateway spend-limit support to Claude Code's usage warning"


def test_derive_name_truncates_to_column_width():
    name = changelog.derive_name("Added " + "x" * 400)
    assert len(name) == 120


def test_parse_takes_only_added_bullets():
    entries = changelog.parse(SAMPLE)
    names = [e["name"] for e in entries]
    assert len(entries) == 2
    assert any(n.startswith("workspace trust prompt") for n in names)
    assert any(n.startswith("gateway spend-limit support") for n in names)
    # Fixed / Improved / Changed / Removed are refinements, not new capabilities.
    assert not any("interactive sessions" in n for n in names)
    assert not any("slash-command menu" in n for n in names)
    assert not any("Write tool" in n for n in names)
    assert not any("outdated note" in n for n in names)


def test_parse_skips_malformed_heading_section():
    entries = changelog.parse(SAMPLE)
    # The bullet under `## not-a-version` must not be attributed to any version.
    assert not any("malformed heading" in e["name"] for e in entries)


def test_parse_attaches_the_right_version():
    entries = changelog.parse(SAMPLE)
    by_prefix = {e["name"][:9]: e for e in entries}
    assert by_prefix["workspace"]["version"] == (2, 1, 228)
    assert by_prefix["gateway s"]["version"] == (2, 1, 227)
    assert by_prefix["workspace"]["version_str"] == "2.1.228"


def test_parse_empty_input():
    assert changelog.parse("") == []


from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from apps.coach_web import models

NOW = datetime(2026, 8, 12, 7, 30, tzinfo=timezone.utc)


def make_db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/w.db")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def names_in(db):
    return sorted(r.name for r in db.scalars(
        select(models.FeatureCatalog).where(models.FeatureCatalog.source == "changelog")))


def test_first_run_records_watermark_and_inserts_nothing(tmp_path):
    # THE BOOTSTRAP TRAP. The real changelog has 487 `Added` bullets across 361
    # versions and Phase 5 owns dismissals, so a first-run flood is permanent.
    with make_db(tmp_path) as db:
        result = changelog.check(db, fetch=lambda: SAMPLE, now=NOW)
        db.commit()
        assert result["added"] == 0
        assert names_in(db) == []
        assert db.get(models.WatcherState, changelog.W_VERSION).value == "2.1.228"


def test_second_run_inserts_only_above_the_watermark(tmp_path):
    with make_db(tmp_path) as db:
        db.add(models.WatcherState(key=changelog.W_VERSION, value="2.1.227",
                                   updated_at=NOW.isoformat()))
        db.commit()
        result = changelog.check(db, fetch=lambda: SAMPLE, now=NOW)
        db.commit()
        assert result["added"] == 1
        assert [n[:9] for n in names_in(db)] == ["workspace"]
        assert db.get(models.WatcherState, changelog.W_VERSION).value == "2.1.228"


def test_run_with_nothing_new_inserts_nothing(tmp_path):
    with make_db(tmp_path) as db:
        db.add(models.WatcherState(key=changelog.W_VERSION, value="2.1.228",
                                   updated_at=NOW.isoformat()))
        db.commit()
        result = changelog.check(db, fetch=lambda: SAMPLE, now=NOW)
        db.commit()
        assert result["added"] == 0 and names_in(db) == []


def test_rows_land_as_changelog_source(tmp_path):
    with make_db(tmp_path) as db:
        db.add(models.WatcherState(key=changelog.W_VERSION, value="2.1.226",
                                   updated_at=NOW.isoformat()))
        db.commit()
        changelog.check(db, fetch=lambda: SAMPLE, now=NOW)
        db.commit()
        row = db.scalars(select(models.FeatureCatalog)).first()
        assert row.source == "changelog"
        assert row.discovered_at == "2026-08-12"
        assert row.lesson == ""


def test_existing_name_is_not_duplicated(tmp_path):
    with make_db(tmp_path) as db:
        db.add(models.WatcherState(key=changelog.W_VERSION, value="2.1.227",
                                   updated_at=NOW.isoformat()))
        # Take the name from the parser itself rather than rebuilding it by hand:
        # derive_name cuts at ";" but not ",", so a hand-written copy drifts.
        existing = next(e["name"] for e in changelog.parse(SAMPLE)
                        if e["name"].startswith("workspace"))
        db.add(models.FeatureCatalog(name=existing, lesson="", source="checklist",
                                     discovered_at="2026-01-01"))
        db.commit()
        result = changelog.check(db, fetch=lambda: SAMPLE, now=NOW)
        db.commit()
        assert result["added"] == 0
        # The pre-existing row keeps its own source; it is not rewritten.
        assert db.get(models.FeatureCatalog, existing).source == "checklist"


def test_fetch_failure_leaves_watermark_untouched(tmp_path):
    def boom():
        raise RuntimeError("network down")

    with make_db(tmp_path) as db:
        db.add(models.WatcherState(key=changelog.W_VERSION, value="2.1.226",
                                   updated_at=NOW.isoformat()))
        db.commit()
        result = changelog.check(db, fetch=boom, now=NOW)
        db.commit()
        assert result["status"] == "failed" and result["added"] == 0
        assert db.get(models.WatcherState, changelog.W_VERSION).value == "2.1.226"


def test_unparseable_document_leaves_watermark_untouched(tmp_path):
    with make_db(tmp_path) as db:
        db.add(models.WatcherState(key=changelog.W_VERSION, value="2.1.226",
                                   updated_at=NOW.isoformat()))
        db.commit()
        result = changelog.check(db, fetch=lambda: "garbage, no headings", now=NOW)
        db.commit()
        assert result["status"] == "failed"
        assert db.get(models.WatcherState, changelog.W_VERSION).value == "2.1.226"


def test_due_is_false_within_seven_days_and_true_after(tmp_path):
    with make_db(tmp_path) as db:
        assert changelog.due(db, NOW) is True          # never checked
        db.add(models.WatcherState(key=changelog.W_CHECKED,
                                   value=(NOW - timedelta(days=3)).isoformat(),
                                   updated_at=NOW.isoformat()))
        db.commit()
        assert changelog.due(db, NOW) is False
        db.get(models.WatcherState, changelog.W_CHECKED).value = (
            NOW - timedelta(days=8)).isoformat()
        db.commit()
        assert changelog.due(db, NOW) is True


def test_check_stamps_last_checked_on_success(tmp_path):
    with make_db(tmp_path) as db:
        changelog.check(db, fetch=lambda: SAMPLE, now=NOW)
        db.commit()
        assert db.get(models.WatcherState, changelog.W_CHECKED).value == NOW.isoformat()
