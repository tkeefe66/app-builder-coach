"""Engine plumbing: Postgres URL normalization to the installed psycopg3 driver."""
import pytest
from sqlalchemy import create_engine

from apps.coach_web import db

PSYCOPG = "postgresql+psycopg://u:p@h/dbname"


@pytest.mark.parametrize("raw", [
    "postgres://u:p@h/dbname",          # legacy Railway/Heroku form
    "postgresql://u:p@h/dbname",        # modern form -> defaults to psycopg2
    "postgresql+psycopg://u:p@h/dbname",  # already explicit: unchanged
])
def test_normalize_database_url_targets_psycopg3(raw):
    assert db.normalize_database_url(raw) == PSYCOPG


def test_normalize_leaves_other_urls_alone():
    for raw in ("sqlite+pysqlite:///:memory:", "", "mysql://u:p@h/d",
                "postgresql+psycopg2://u:p@h/d"):
        assert db.normalize_database_url(raw) == raw


def test_normalized_url_resolves_a_dialect_without_psycopg2():
    """create_engine imports the DBAPI eagerly; psycopg2 is not installed."""
    engine = create_engine(PSYCOPG)  # construction only, never connects
    assert engine.dialect.driver == "psycopg"


def test_make_engine_normalizes_postgres_urls():
    engine = db.make_engine("postgres://u:p@h/dbname")
    assert engine.dialect.driver == "psycopg"
    assert str(engine.url) == "postgresql+psycopg://u:***@h/dbname"


def test_make_engine_still_rejects_empty_url():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        db.make_engine("")
