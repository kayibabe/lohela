"""Read-only DB diagnostics and local paper snapshots; run from backend."""
import argparse
import asyncio
from datetime import date
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text
from app.config import CURRENT_MODEL_VERSION
from app.database import AsyncSessionLocal, engine
from app.services.singles_report import singles_report
from app.services.singles_ledger import freeze_snapshot, review_snapshot


async def main(args):
    engine.echo = False
    try:
        async with AsyncSessionLocal() as db:
            # Enforced by PostgreSQL as well as application code.
            await db.execute(text("SET TRANSACTION READ ONLY"))
            if args.command == "review":
                result = await review_snapshot(db, args.ledger)
            elif args.command == "freeze":
                result = await freeze_snapshot(db, args.ledger, args.start, args.end, args.model_version)
            else:
                result, _ = await singles_report(db, args.start, args.end, args.model_version)
            await db.rollback()
        output = json.dumps(result, indent=2, allow_nan=False, default=str)
        if args.output:
            with args.output.open("x", encoding="utf-8") as file:
                file.write(output + "\n")
        else:
            print(output)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("report", "freeze", "review"):
        p = sub.add_parser(command)
        p.add_argument("--output", type=Path)
        if command != "review":
            p.add_argument("--start", type=date.fromisoformat, required=True)
            p.add_argument("--end", type=date.fromisoformat, required=True)
            p.add_argument("--model-version", default=CURRENT_MODEL_VERSION)
        if command != "report":
            p.add_argument("--ledger", type=Path, required=True)
    asyncio.run(main(parser.parse_args()))
