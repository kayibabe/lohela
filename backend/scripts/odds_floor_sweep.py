"""Read-only train/test sweep for singles research policy parameters.

This is deliberately a research diagnostic. It never writes to the database,
publishes picks, or authorizes staking. The training window ranks policies;
the later window is evaluated only after that ranking is complete.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from datetime import date, datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from app.config import CURRENT_MODEL_VERSION
from app.database import AsyncSessionLocal, engine
from app.services.singles_research import Policy
from app.services.singles_report import adapt_rows, load_rows


ODDS_FLOORS = [1.50 + 0.10 * i for i in range(16)]  # 1.50 .. 3.00
PROBABILITY_FLOORS = [0.50 + 0.05 * i for i in range(8)]  # .50 .. .85
EV_FLOORS = [0.00, 0.05, 0.10, 0.20]
HAIRCUTS = [0.00, 0.02, 0.05]
QUOTE_AGES = [1.0, 2.0, 6.0]


def score(candidates, outcomes, start: date, end: date, policy: Policy) -> dict:
    if not candidates:
        return {"selected": 0, "resolved": 0, "wins": 0, "profit": 0.0,
                "roi": None, "days": 0}
    # Equivalent to the research selector for already-adapted rows, but kept
    # local so a large diagnostic grid does not spend minutes rebuilding the
    # same rejection counters for every cell.
    as_of = datetime.now(timezone.utc)
    eligible = []
    for c in candidates:
        if c.historical or c.decision_at > as_of:
            continue
        if c.decision_at - c.quote_at > timedelta(hours=policy.max_quote_age_hours):
            continue
        if c.odds <= policy.min_odds or c.probability < policy.min_probability:
            continue
        if c.probability * policy.conservative_odds(c.odds) - 1 <= policy.min_ev:
            continue
        eligible.append(c)
    by_match = defaultdict(list)
    for c in eligible:
        by_match[c.match_id].append(c)
    picks = []
    for rows in by_match.values():
        earliest = min(c.decision_at for c in rows)
        first = [c for c in rows if c.decision_at == earliest]
        picks.append(max(first, key=lambda c: (
            c.probability * policy.conservative_odds(c.odds) - 1,
            c.probability, c.market, str(c.id)))
        )
    rows = [
        c for c in picks
        if start <= c.kickoff_at.date() <= end
        and outcomes.get(c.id) in {"win", "loss", "void"}
    ]
    wins = sum(outcomes[c.id] == "win" for c in rows)
    profit = sum(c.odds - 1 if outcomes[c.id] == "win" else -1.0
                 for c in rows)
    return {
        "selected": len(picks),
        "resolved": len(rows),
        "wins": wins,
        "losses": sum(outcomes[c.id] == "loss" for c in rows),
        "profit": round(profit, 8),
        "roi": round(profit / len(rows), 8) if rows else None,
        "days": len({c.kickoff_at.date() for c in rows}),
    }


async def main(args):
    engine.echo = False
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        rows = await load_rows(db, args.start, args.end, args.model_version)
        candidates, outcomes, adapter_rejections = adapt_rows(rows)
        # Bind each candidate to a train/test window before sweeping. Outcomes
        # remain outside selection_candidates and are read only after selection.
        train = [c for c in candidates if args.train_start <= c.kickoff_at.date() <= args.train_end]
        test = [c for c in candidates if args.test_start <= c.kickoff_at.date() <= args.test_end]
        results = []
        for odds in ODDS_FLOORS:
            for probability in PROBABILITY_FLOORS:
                for ev in EV_FLOORS:
                    for haircut in HAIRCUTS:
                        for age in QUOTE_AGES:
                            policy = Policy(
                                version="odds-floor-sweep",
                                min_probability=probability,
                                min_odds=round(odds, 2),
                                price_haircut=haircut,
                                min_ev=ev,
                                max_quote_age_hours=age,
                            )
                            train_metrics = score(train, outcomes, args.train_start, args.train_end, policy)
                            test_metrics = score(test, outcomes, args.test_start, args.test_end, policy)
                            results.append({
                                "parameters": asdict(policy),
                                "train": train_metrics,
                                "test": test_metrics,
                            })
        eligible = [r for r in results if r["train"]["resolved"] >= args.min_train]
        ranked = sorted(
            eligible,
            key=lambda r: (r["train"]["roi"], r["train"]["resolved"]),
            reverse=True,
        )
        robust = sorted(
            eligible,
            key=lambda r: (
                r["test"]["roi"] if r["test"]["roi"] is not None else -999,
                r["test"]["resolved"],
            ),
            reverse=True,
        )
        await db.rollback()
    output = {
        "status": "research_only",
        "model_version": args.model_version,
        "candidate_database_rows": len(rows),
        "adapted_candidates": len(candidates),
        "adapter_rejections": adapter_rejections,
        "grid_size": len(results),
        "train_window": [args.train_start.isoformat(), args.train_end.isoformat()],
        "test_window": [args.test_start.isoformat(), args.test_end.isoformat()],
        "minimum_train_resolved": args.min_train,
        "ranking": "train ROI, then train resolved count; test is untouched evaluation",
        "top_by_train_roi": ranked[:args.top],
        "top_by_test_roi_among_train_eligible": robust[:args.top],
        "limitations": [
            "All rows are retrospective replays of stored predictions, not a frozen prospective trial.",
            "The grid search itself creates selection bias; test results are descriptive only.",
            "This cohort is short and observations can share fixtures, dates, markets, and model runs.",
            "Quoted prices do not prove executable fills, account limits, or future profitability.",
            "No threshold is promoted or recommended for live staking by this script.",
        ],
    }
    print(json.dumps(output, indent=2, allow_nan=False, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=date.fromisoformat, default=date(2026, 9, 20))
    parser.add_argument("--end", type=date.fromisoformat, default=date(2026, 9, 28))
    parser.add_argument("--train-start", type=date.fromisoformat, default=date(2026, 9, 20))
    parser.add_argument("--train-end", type=date.fromisoformat, default=date(2026, 9, 24))
    parser.add_argument("--test-start", type=date.fromisoformat, default=date(2026, 9, 25))
    parser.add_argument("--test-end", type=date.fromisoformat, default=date(2026, 9, 28))
    parser.add_argument("--model-version", default=CURRENT_MODEL_VERSION)
    parser.add_argument("--min-train", type=int, default=10)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()
    asyncio.run(main(args))
