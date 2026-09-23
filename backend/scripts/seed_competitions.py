"""
Seed any missing competitions from app.services.seed.COMPETITIONS.

The app does this automatically on startup (and queues a history backfill for
new leagues); this script is the manual equivalent, without the backfill:
  python scripts/seed_competitions.py
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import AsyncSessionLocal
from app.services.seed import seed_missing_competitions


async def seed():
    async with AsyncSessionLocal() as db:
        added = await seed_missing_competitions(db)
    print(f"Seed complete. Added {len(added)} competitions: {added}")
    if added:
        print("Queue their history with POST /api/v1/admin/leagues/backfill.")


if __name__ == "__main__":
    asyncio.run(seed())
