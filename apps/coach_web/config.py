"""Server settings. All values come from env in prod; tests construct directly."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    ingest_token: str
    password_hash: str
    secret_key: str
    usage_token: str = ""
    allowed_origins: str = ""


def settings_from_env() -> Settings:
    return Settings(
        database_url=os.environ.get("DATABASE_URL", ""),
        ingest_token=os.environ.get("COACH_INGEST_TOKEN", ""),
        password_hash=os.environ.get("COACH_PASSWORD_HASH", ""),
        secret_key=os.environ.get("COACH_SECRET_KEY", ""),
        usage_token=os.environ.get("COACH_USAGE_TOKEN", ""),
        allowed_origins=os.environ.get("COACH_ALLOWED_ORIGINS", ""),
    )
