"""Compare public-table row counts between a source and restored database."""

import argparse
import json
import os
import re
import sys
from urllib.parse import urlsplit

import psycopg2
from psycopg2 import sql


def _dsn(url: str) -> str:
    return re.sub(r"^postgres(?:ql)?\+[a-z0-9_]+://", "postgresql://", url)


def _target_name(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.hostname or '?'}/{(parsed.path or '/?').lstrip('/') or '?'}"


def _row_counts(url: str) -> dict[str, int]:
    with psycopg2.connect(_dsn(url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
            )
            tables = [row[0] for row in cursor.fetchall()]
            counts = {}
            for table in tables:
                cursor.execute(
                    sql.SQL("SELECT count(*) FROM {}.{}").format(
                        sql.Identifier("public"), sql.Identifier(table)
                    )
                )
                counts[table] = cursor.fetchone()[0]
            return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-url", default=os.environ.get("DATABASE_URL"))
    parser.add_argument("--target-url", required=True)
    args = parser.parse_args()
    if not args.source_url:
        parser.error("--source-url or DATABASE_URL is required")

    source = _row_counts(args.source_url)
    restored = _row_counts(args.target_url)
    matches = source == restored
    print(
        json.dumps(
            {
                "source": _target_name(args.source_url),
                "target": _target_name(args.target_url),
                "tables_compared": len(source),
                "source_counts": source,
                "restored_counts": restored,
                "match": matches,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
