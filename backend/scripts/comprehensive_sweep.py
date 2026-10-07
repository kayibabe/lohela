"""Comprehensive parameter sweep for singles research policy.

Sweeps a much wider grid than odds_floor_sweep.py:
  - Odds floors:       1.50 .. 4.00  (step 0.10, 26 values)
  - Probability floors: 0.45 .. 0.90  (step 0.05, 10 values)
  - EV floors:         0.00, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25  (8 values)
  - Haircuts:          0.00, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10  (7 values)
  - Quote ages (h):    0.5, 1.0, 2.0, 3.0, 6.0, 12.0  (6 values)

Grid size: 26 × 10 × 8 × 7 × 6 = 87,360 combinations.

Read-only diagnostic. Never writes to the database or authorises staking.
Results written to --out (JSON) for further analysis / visualisation.
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


ODDS_FLOORS        = [round(1.50 + 0.10 * i, 2) for i in range(26)]  # 1.50..4.00
PROBABILITY_FLOORS = [round(0.45 + 0.05 * i, 2) for i in range(10)]  # 0.45..0.90
EV_FLOORS          = [0.00, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.25]
HAIRCUTS           = [0.00, 0.01, 0.02, 0.03, 0.05, 0.08, 0.10]
QUOTE_AGES         = [0.5, 1.0, 2.0, 3.0, 6.0, 12.0]


def score(candidates, outcomes, start: date, end: date, policy: Policy) -> dict:
    if not candidates:
        return {"selected": 0, "resolved": 0, "wins": 0, "losses": 0,
                "profit": 0.0, "roi": None, "days": 0}
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
            c.probability, c.market, str(c.id))))
    rows = [
        c for c in picks
        if start <= c.kickoff_at.date() <= end
        and outcomes.get(c.id) in {"win", "loss", "void"}
    ]
    wins = sum(outcomes[c.id] == "win" for c in rows)
    losses = sum(outcomes[c.id] == "loss" for c in rows)
    profit = sum(c.odds - 1 if outcomes[c.id] == "win" else -1.0
                 for c in rows)
    return {
        "selected": len(picks),
        "resolved": len(rows),
        "wins": wins,
        "losses": losses,
        "profit": round(profit, 8),
        "roi": round(profit / len(rows), 8) if rows else None,
        "hit_rate": round(wins / (wins + losses), 4) if (wins + losses) else None,
        "days": len({c.kickoff_at.date() for c in rows}),
    }


async def main(args):
    engine.echo = False
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        rows = await load_rows(db, args.start, args.end, args.model_version)
        candidates, outcomes, adapter_rejections = adapt_rows(rows)
        train = [c for c in candidates if args.train_start <= c.kickoff_at.date() <= args.train_end]
        test  = [c for c in candidates if args.test_start  <= c.kickoff_at.date() <= args.test_end]

        total = len(ODDS_FLOORS) * len(PROBABILITY_FLOORS) * len(EV_FLOORS) * len(HAIRCUTS) * len(QUOTE_AGES)
        print(f"Sweeping {total:,} parameter combinations...", file=sys.stderr)

        results = []
        for odds in ODDS_FLOORS:
            for probability in PROBABILITY_FLOORS:
                for ev in EV_FLOORS:
                    for haircut in HAIRCUTS:
                        for age in QUOTE_AGES:
                            try:
                                policy = Policy(
                                    version="comprehensive-sweep",
                                    min_probability=probability,
                                    min_odds=odds,
                                    price_haircut=haircut,
                                    min_ev=ev,
                                    max_quote_age_hours=age,
                                )
                            except ValueError:
                                continue
                            train_m = score(train, outcomes, args.train_start, args.train_end, policy)
                            test_m  = score(test,  outcomes, args.test_start,  args.test_end,  policy)
                            results.append({
                                "min_odds":          odds,
                                "min_probability":   probability,
                                "min_ev":            ev,
                                "price_haircut":     haircut,
                                "max_quote_age_h":   age,
                                "train":             train_m,
                                "test":              test_m,
                            })

        await db.rollback()

    # Filter to minimum train resolved count
    eligible = [r for r in results if r["train"]["resolved"] >= args.min_train]

    # Ranked slices
    by_train_roi  = sorted(eligible, key=lambda r: (r["train"]["roi"] or -999, r["train"]["resolved"]), reverse=True)
    by_test_roi   = sorted(eligible, key=lambda r: (r["test"]["roi"]  or -999, r["test"]["resolved"]),  reverse=True)
    by_train_prof = sorted(eligible, key=lambda r: (r["train"]["profit"],       r["train"]["resolved"]), reverse=True)

    # Profitable-in-both slice
    profitable_both = [
        r for r in eligible
        if (r["train"]["roi"] or -999) > 0 and (r["test"]["roi"] or -999) > 0
    ]
    profitable_both_sorted = sorted(
        profitable_both,
        key=lambda r: ((r["train"]["roi"] or 0) + (r["test"]["roi"] or 0)) / 2,
        reverse=True,
    )

    output = {
        "status": "research_only",
        "model_version": args.model_version,
        "candidate_database_rows": len(rows),
        "adapted_candidates": len(candidates),
        "adapter_rejections": adapter_rejections,
        "train_candidates": len(train),
        "test_candidates": len(test),
        "grid_total": total,
        "grid_evaluated": len(results),
        "grid_eligible": len(eligible),
        "profitable_in_both_windows": len(profitable_both),
        "train_window": [args.train_start.isoformat(), args.train_end.isoformat()],
        "test_window":  [args.test_start.isoformat(),  args.test_end.isoformat()],
        "minimum_train_resolved": args.min_train,
        "parameter_ranges": {
            "min_odds":        [ODDS_FLOORS[0], ODDS_FLOORS[-1]],
            "min_probability": [PROBABILITY_FLOORS[0], PROBABILITY_FLOORS[-1]],
            "min_ev":          [EV_FLOORS[0], EV_FLOORS[-1]],
            "price_haircut":   [HAIRCUTS[0], HAIRCUTS[-1]],
            "max_quote_age_h": [QUOTE_AGES[0], QUOTE_AGES[-1]],
        },
        "top20_by_train_roi":            by_train_roi[:20],
        "top20_by_test_roi":             by_test_roi[:20],
        "top20_by_train_profit":         by_train_prof[:20],
        "top20_profitable_in_both":      profitable_both_sorted[:20],
        "all_eligible":                  eligible,
        "limitations": [
            "All rows are retrospective replays of stored predictions, not a frozen prospective trial.",
            "Grid search creates selection bias; test results are descriptive only.",
            "Short cohort: results are highly sensitive to individual fixture outcomes.",
            "Quoted prices do not prove executable fills, account limits, or future profitability.",
            "No threshold is promoted or recommended for live staking by this script.",
        ],
    }

    out_path = args.out or str(Path(__file__).resolve().parent / "comprehensive_sweep_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, allow_nan=False, default=str)
    print(f"Results written to: {out_path}", file=sys.stderr)

    # Print summary to stdout
    summary = {k: v for k, v in output.items() if k != "all_eligible"}
    print(json.dumps(summary, indent=2, allow_nan=False, default=str))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start",       type=date.fromisoformat, default=date(2026, 9, 20))
    parser.add_argument("--end",         type=date.fromisoformat, default=date(2026, 9, 28))
    parser.add_argument("--train-start", type=date.fromisoformat, default=date(2026, 9, 20))
    parser.add_argument("--train-end",   type=date.fromisoformat, default=date(2026, 9, 24))
    parser.add_argument("--test-start",  type=date.fromisoformat, default=date(2026, 9, 25))
    parser.add_argument("--test-end",    type=date.fromisoformat, default=date(2026, 9, 28))
    parser.add_argument("--model-version", default=CURRENT_MODEL_VERSION)
    parser.add_argument("--min-train",   type=int, default=3)
    parser.add_argument("--top",         type=int, default=20)
    parser.add_argument("--out",         type=str, default=None)
    args = parser.parse_args()
    asyncio.run(main(args))
