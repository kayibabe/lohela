"""Restore a pg_dump (custom format) into a target Postgres database.

Usage:
    python scripts/restore_db.py --file DUMP --target-url URL --yes [--clean]

Deliberately has no default target: a restore is destructive, so the target
must always be named explicitly on the command line. `--yes` is required or
the script refuses to run. Without `--clean`, pg_restore is run against
whatever is currently in the target and will error loudly on any conflicting
object rather than silently overwriting it — pass `--clean` only when you
mean to drop and recreate everything the dump contains.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit


def _to_libpq_url(url: str) -> str:
    return re.sub(r"^postgres(?:ql)?\+[a-z0-9_]+://", "postgresql://", url)


def _redact(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or "?"
    db = (parts.path or "/?").lstrip("/") or "?"
    return f"{host}/{db}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", required=True, help="Path to a pg_dump custom-format file")
    parser.add_argument("--target-url", required=True, help="Destination DATABASE_URL")
    parser.add_argument("--yes", action="store_true", help="Required: confirms the destructive restore")
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Drop existing objects first (pg_restore --clean --if-exists). Default: off.",
    )
    parser.add_argument("--jobs", type=int, default=1, help="Parallel restore jobs (pg_restore -j)")
    args = parser.parse_args()

    dump_path = Path(args.file)
    if not dump_path.is_file():
        print(f"error: dump file not found: {dump_path}", file=sys.stderr)
        return 2

    if shutil.which("pg_restore") is None:
        print("error: 'pg_restore' is not on PATH (install postgresql-client)", file=sys.stderr)
        return 2

    dsn = _to_libpq_url(args.target_url)
    target = _redact(dsn)

    print(f"Target: {target}")
    print(f"Dump:   {dump_path} ({dump_path.stat().st_size} bytes)")
    print(f"Mode:   {'CLEAN (drops existing objects first)' if args.clean else 'restore into current state (errors on conflicts)'}")

    if not args.yes:
        print(
            "\nRefusing to run without --yes. This restore is destructive to "
            f"'{target}'. Re-run with --yes once you have confirmed this is "
            "the intended target (never the live production database unless "
            "this restore IS the recovery action).",
            file=sys.stderr,
        )
        return 3

    cmd = [
        "pg_restore",
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        f"--jobs={args.jobs}",
        "--dbname",
        dsn,
    ]
    if args.clean:
        cmd += ["--clean", "--if-exists"]
    cmd.append(str(dump_path))

    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print("error: pg_restore failed", file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
        return proc.returncode

    print("Restore completed.")
    if proc.stderr.strip():
        # pg_restore writes non-fatal notices (e.g. skipped ACLs) to stderr
        # even on success; surface them for the record.
        print(proc.stderr, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
