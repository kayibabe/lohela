"""Print the selected test-window picks for one research sweep policy."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.config import CURRENT_MODEL_VERSION
from app.database import AsyncSessionLocal, engine
from app.services.singles_research import Policy, select_candidates
from app.services.singles_report import adapt_rows, load_rows


async def main(args):
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        rows = await load_rows(db, args.start, args.end, args.model_version)
        candidates, outcomes, rejected = adapt_rows(rows)
        matches = {str(match.id): match for _, match in rows}
        candidates = [c for c in candidates if args.start <= c.kickoff_at.date() <= args.end]
        policy = Policy(
            version="odds-floor-sweep",
            min_probability=0.60,
            min_odds=2.10,
            price_haircut=0.00,
            min_ev=0.00,
            max_quote_age_hours=6.0,
        )
        selection = select_candidates(candidates, as_of=datetime.now(timezone.utc), policy=policy)
        output = []
        for candidate in selection.picks:
            match = matches[candidate.match_id]
            output.append({
                "prediction_id": candidate.id,
                "match_id": candidate.match_id,
                "kickoff_at": candidate.kickoff_at.isoformat(),
                "market": candidate.market,
                "probability": candidate.probability,
                "odds": candidate.odds,
                "home_goals": match.home_goals,
                "away_goals": match.away_goals,
                "result": outcomes.get(candidate.id, "pending"),
            })
        await db.rollback()
    print(json.dumps({
        "policy": {
            "min_odds": policy.min_odds,
            "min_probability": policy.min_probability,
            "min_ev": policy.min_ev,
            "price_haircut": policy.price_haircut,
            "max_quote_age_hours": policy.max_quote_age_hours,
        },
        "window": [args.start.isoformat(), args.end.isoformat()],
        "selected": len(output),
        "rows": output,
        "adapter_rejections": rejected,
    }, indent=2, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 9, 25))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 28))
    parser.add_argument("--model-version", default=CURRENT_MODEL_VERSION)
    asyncio.run(main(parser.parse_args()))
