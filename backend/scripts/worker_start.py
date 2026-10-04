"""Refuse to start a worker against a schema older than this image expects."""

from __future__ import annotations

import os
import sys

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.config import settings


def schema_heads() -> tuple[set[str], set[str]]:
    """Read migration heads through Alembic's API, supported by all pins."""
    config = Config("alembic.ini")
    expected = set(ScriptDirectory.from_config(config).get_heads())
    database_url = settings.database_url.replace(
        "postgresql+asyncpg://", "postgresql+psycopg2://", 1
    )
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            actual = set(MigrationContext.configure(connection).get_current_heads())
    finally:
        engine.dispose()
    return actual, expected


def ensure_schema_is_current() -> None:
    """Use Alembic's revision graph rather than a duplicated hard-coded head."""
    try:
        actual, expected = schema_heads()
    except Exception as exc:
        raise SystemExit(
            "Worker startup blocked: could not verify database schema before consuming tasks "
            f"({type(exc).__name__})."
        ) from exc
    if actual != expected:
        raise SystemExit(
            "Worker startup blocked: database schema is not at this image's Alembic head. "
            f"Expected {sorted(expected)}, found {sorted(actual)}. "
            "Run the web pre-deploy migration successfully before starting worker code."
        )


def main() -> None:
    ensure_schema_is_current()
    os.execvp("celery", ["celery", "-A", "app.tasks.pipeline", "worker", "--loglevel=info"])


if __name__ == "__main__":
    main()
