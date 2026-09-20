"""Read-only evidence export; run in the application's configured environment."""
import asyncio
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import text
from app.database import AsyncSessionLocal


async def main():
    evidence = {"captured_at": datetime.now(timezone.utc).isoformat(),
                "source": "Railway production web / PostgreSQL", "tables": {}}
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        evidence["read_only"] = (await db.execute(text("SHOW transaction_read_only"))).scalar()
        for table in ("predictions", "accumulator_tickets", "ticket_selections", "ticket_results",
                      "competitions", "teams"):
            evidence["tables"][table] = list((await db.execute(text(
                f"SELECT row_to_json(t) FROM {table} t ORDER BY id"))).scalars())
        evidence["tables"]["matches"] = list((await db.execute(text(
            "SELECT row_to_json(t) FROM matches t WHERE id IN "
            "(SELECT match_id FROM predictions UNION SELECT match_id FROM ticket_selections) ORDER BY id"
        ))).scalars())
        await db.rollback()
    print("EVIDENCE_JSON=" + json.dumps(evidence, default=str, allow_nan=False))


if __name__ == "__main__":
    asyncio.run(main())
