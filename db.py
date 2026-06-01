"""
Shared DB connection helpers. Reads credentials from .env (never hardcoded).

- get_local_engine():  SQLAlchemy engine for the LOCAL clone (use for all analysis).
- get_source_dsn():    psql/pg_dump-style DSN for the REMOTE source (read-only; clone only).

Nothing here connects on import — call the function you need.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing {key} in .env (see .env.example)")
    return val


def get_local_engine():
    """SQLAlchemy engine pointed at the LOCAL clone. Safe to read/write."""
    from sqlalchemy import create_engine

    user = _require("LOCAL_DB_USER")
    pw = _require("LOCAL_DB_PASSWORD")
    host = _require("LOCAL_DB_HOST")
    port = os.getenv("LOCAL_DB_PORT", "5432")
    name = _require("LOCAL_DB_NAME")
    return create_engine(f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{name}")


def get_source_dsn() -> str:
    """libpq connection string for the REMOTE source. READ-ONLY — clone use only."""
    return (
        f"host={_require('SOURCE_DB_HOST')} "
        f"port={os.getenv('SOURCE_DB_PORT', '5432')} "
        f"dbname={_require('SOURCE_DB_NAME')} "
        f"user={_require('SOURCE_DB_USER')} "
        f"password={_require('SOURCE_DB_PASSWORD')}"
    )
