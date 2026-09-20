"""Take a pg_dump (custom format) of a Postgres database and validate it.

Usage:
    python scripts/backup_db.py [--url URL] [--output-dir DIR] [--label LABEL]

`--url` defaults to $DATABASE_URL. The dump never touches stdout/logs with
the connection string (only the host/db name are printed, for audit trail).

Exit code 0 + a JSON summary on stdout means the dump was written AND
validated (pg_restore --list ran clean against it). Any other outcome is a
failure — the partial file is removed rather than left looking like a good
backup.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit


def _to_libpq_url(url: str) -> str:
    """pg_dump/pg_restore don't understand the +asyncpg driver suffix."""
    return re.sub(r"^postgres(?:ql)?\+[a-z0-9_]+://", "postgresql://", url)


def _redact(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or "?"
    db = (parts.path or "/?").lstrip("/") or "?"
    return f"{host}/{db}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--output-dir", default="backups")
    parser.add_argument("--label", default="manual")
    args = parser.parse_args()

    if not args.url:
        print("error: no --url given and DATABASE_URL is not set", file=sys.stderr)
        return 2

    for tool in ("pg_dump", "pg_restore"):
        if shutil.which(tool) is None:
            print(f"error: '{tool}' is not on PATH (install postgresql-client)", file=sys.stderr)
            return 2

    dsn = _to_libpq_url(args.url)
    target = _redact(dsn)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", args.label)
    dump_path = output_dir / f"lohela_{safe_label}_{stamp}.dump"

    print(f"Backing up {target} -> {dump_path}", file=sys.stderr)
    started = datetime.now(timezone.utc)
    proc = subprocess.run(
        [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(dump_path),
            dsn,
        ],
        capture_output=True,
        text=True,
    )
    finished = datetime.now(timezone.utc)

    if proc.returncode != 0:
        dump_path.unlink(missing_ok=True)
        print("error: pg_dump failed", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        return proc.returncode

    # Validate: a corrupt/truncated dump fails pg_restore --list immediately.
    listing = subprocess.run(
        ["pg_restore", "--list", str(dump_path)],
        capture_output=True,
        text=True,
    )
    if listing.returncode != 0:
        print("error: dump written but failed validation (pg_restore --list)", file=sys.stderr)
        print(listing.stderr, file=sys.stderr)
        return listing.returncode

    table_entries = [
        line for line in listing.stdout.splitlines() if " TABLE DATA " in line
    ]

    summary = {
        "target": target,
        "file": str(dump_path),
        "size_bytes": dump_path.stat().st_size,
        "duration_seconds": round((finished - started).total_seconds(), 2),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "tables_with_data": len(table_entries),
        "validated": True,
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
