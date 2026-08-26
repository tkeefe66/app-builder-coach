"""Engine and session plumbing. SQLite (tests) needs StaticPool sharing."""
from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool


def normalize_database_url(url: str) -> str:
    """Point Postgres URLs at psycopg3, the only Postgres driver we install.

    Railway (like Heroku) hands out ``postgresql://`` and, historically,
    ``postgres://``. SQLAlchemy maps a bare ``postgresql://`` to psycopg2,
    which is not installed, so engine creation dies with ModuleNotFoundError.
    Anything already carrying an explicit ``+driver`` is left untouched.
    """
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


def make_engine(url: str):
    if not url:
        raise ValueError("DATABASE_URL is not set; coach-web cannot start without a database")
    url = normalize_database_url(url)
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False},
                             poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True)


def get_db(request: Request):
    with Session(request.app.state.engine) as session:
        yield session
