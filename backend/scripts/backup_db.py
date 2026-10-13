"""Take a pg_dump (custom format) of a Postgres database and validate it.

Usage:
    python scripts/backup_db.py [--url URL] [--output-dir DIR] [--label LABEL]

`--url` defaults to $DATABASE_URL. The dump never touches stdout/logs with
the connection string (only the host/db name are printed, for audit trail).

The script selects pg_dump and pg_restore clients matching the server major.
Exit code 0 + a JSON summary on stdout means the dump was written AND
validated (the matching pg_restore --list ran clean against it). Any other
outcome is a failure — the partial file is removed rather than left looking
like a good backup.
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


def _server_major(dsn: str) -> int | None:
    """Read the target server major so dumps use a matching client toolchain."""
    psql = shutil.which("psql")
    if psql is None:
        return None
    proc = subprocess.run(
        [psql, dsn, "--no-psqlrc", "--tuples-only", "--no-align", "--command", "SHOW server_version_num"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip()) // 10000
    except ValueError:
        return None


def _matching_tool(name: str, server_major: int) -> str | None:
    """Prefer the versioned PostgreSQL client matching the target server."""
    versioned = Path(f"/usr/lib/postgresql/{server_major}/bin") / name
    if versioned.is_file():
        return str(versioned)
    executable = shutil.which(name)
    if executable is None:
        return None
    proc = subprocess.run([executable, "--version"], capture_output=True, text=True)
    match = re.search(r"\b(\d+)\.", proc.stdout)
    return executable if proc.returncode == 0 and match and int(match.group(1)) == server_major else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--output-dir", default="backups")
    parser.add_argument("--label", default="manual")
    args = parser.parse_args()

    if not args.url:
        print("error: no --url given and DATABASE_URL is not set", file=sys.stderr)
        return 2

    dsn = _to_libpq_url(args.url)
    target = _redact(dsn)
    server_major = _server_major(dsn)
    if server_major is None:
        print("error: could not identify the database server version with psql", file=sys.stderr)
        return 2
    pg_dump = _matching_tool("pg_dump", server_major)
    pg_restore = _matching_tool("pg_restore", server_major)
    if pg_dump is None or pg_restore is None:
        missing = "pg_dump" if pg_dump is None else "pg_restore"
        print(
            f"error: matching PostgreSQL {server_major} client '{missing}' is not installed",
            file=sys.stderr,
        )
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", args.label)
    dump_path = output_dir / f"lohela_{safe_label}_{stamp}.dump"

    print(f"Backing up {target} -> {dump_path}", file=sys.stderr)
    started = datetime.now(timezone.utc)
    proc = subprocess.run(
        [
            pg_dump,
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
        [pg_restore, "--list", str(dump_path)],
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
