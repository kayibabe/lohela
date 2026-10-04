"""Refuse to start a worker against a schema older than this image expects."""

from __future__ import annotations

import os
import subprocess
import sys


def ensure_schema_is_current() -> None:
    """Use Alembic's revision graph rather than a duplicated hard-coded head."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "current", "--check-heads"],
        check=False,
    )
    if result.returncode:
        raise SystemExit(
            "Worker startup blocked: database schema is not at this image's Alembic head. "
            "Run the web pre-deploy migration successfully before starting worker code."
        )


def main() -> None:
    ensure_schema_is_current()
    os.execvp("celery", ["celery", "-A", "app.tasks.pipeline", "worker", "--loglevel=info"])


if __name__ == "__main__":
    main()
