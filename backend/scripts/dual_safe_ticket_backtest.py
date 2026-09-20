"""Research backtest: a second, non-overlapping SAFE (Conservative) ticket per day.

Motivating question (2026-09-20 chat): the SAFE/"Conservative" tier only ever
publishes the single best-scoring accumulator per day
(AccumulatorBuilder._find_best_ticket keeps one winner and discards every
other combination it evaluates -- see accumulator_builder.py:652-705). This
script asks: across the qualified leg pool that already existed on each
historical day, was there a *second* SAFE-eligible ticket, built from
completely different matches, and if the pipeline had also published it
alongside the primary, how would it have performed?

READ THIS BEFORE READING THE OUTPUT
====================================
1. Research only. Read-only against the live database: it re-derives ticket
   candidates from Prediction/Match/Odds rows and settles them by comparing
   to actual final scores. It writes nothing -- no AccumulatorTicket,
   TicketSelection, or TicketResult rows are created, and no production
   table is modified.
2. "Ticket 1" here is NOT guaranteed to be byte-identical to what was
   actually published that day. It is recomputed at full SAFE strength
   (TICKET_SPECS' SAFE spec, unmodified) using only the current-code
   eligibility gates (selection_rejection_reasons + the research-market
   exclusion), via the exact same _find_best_ticket search the pipeline
   uses. It does NOT replay: the bounded relaxation ladder
   (AccumulatorBuilder._apply_minimum_ticket_relaxation), or the
   cross-tier overlap constraints against that day's BALANCED/AGGRESSIVE
   tickets (irrelevant to the SAFE-vs-SAFE question this script asks, but
   it means a day where the real SAFE ticket was relaxed, or displaced by
   a BALANCED/AGGRESSIVE overlap veto, can differ here from history). It
   also excludes the STALE_ODDS and Q_SCORE_BELOW_TIER gate reasons from
   the raw eligibility check (re-applying min_q_score explicitly instead),
   because STALE_ODDS compares a leg's odds timestamp to wall-clock
   datetime.now() and would reject every archived leg on replay regardless
   of freshness at actual selection time -- the same exclusion already
   established in compare_candidate_pool_tiebreak.py._qualified_pool.
3. "Ticket 2" is Ticket 1's counterfactual sibling: the best SAFE-eligible
   ticket left after imposing a hard zero-shared-match constraint against
   Ticket 1 (max_shared_matches=0, via the same
   _within_ticket_overlap_limit machinery the pipeline already uses between
   tiers). Zero overlap, not just a low cap, was chosen so "different legs"
   is unambiguous -- no shared match, let alone shared leg.
4. Settlement uses today's calibration/model-version snapshot only for
   ticket construction (calibration gates are looked up per day, same as
   production); actual win/loss is decided purely from real final scores
   via app.services.settlement.evaluate_selection, the same function
   production settlement uses. A ticket is excluded from the settled
   metrics (not counted as a loss) if any leg's match has not finished with
   a recorded score yet.
5. Small-sample caveat applies as it does to every paper-ticket report in
   this repo: a short backtest window will not have enough settled tickets
   to distinguish skill from variance. Read the day count before trusting
   the headline ROI.

Usage:
  python backend/scripts/dual_safe_ticket_backtest.py --start-date 2026-08-01 --end-date 2026-09-19
  python backend/scripts/dual_safe_ticket_backtest.py --start-date 2026-08-01 --end-date 2026-09-19 --out docs/DUAL_SAFE_TICKET_BACKTEST_2026-08-01_2026-09-19.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.database import AsyncSessionLocal
from app.models import Match, MatchStatus, TicketType
from app.services.accumulator_builder import (
    TICKET_SPECS,
    AccumulatorBuilder,
    Ticket,
    _find_best_ticket,
    _research_market_rejection_reasons,
    selection_rejection_reasons,
    shared_match_count,
)
from app.services.settlement import evaluate_selection

SAFE_SPEC = next(spec for spec in TICKET_SPECS if spec.ticket_type == TicketType.SAFE)


def _historically_eligible(leg, spec, calibration) -> bool:
    """Eligibility check for replaying a PAST date, not a live selection day.

    selection_rejection_reasons() includes STALE_ODDS, which compares a leg's
    stored odds timestamp against wall-clock datetime.now() -- meaningless
    when the leg is being replayed long after the fact, since every archived
    leg would fail it regardless of how fresh the odds were at actual
    selection time. This mirrors the same exclusion already established in
    scripts/compare_candidate_pool_tiebreak.py._qualified_pool for the same
    reason. Q_SCORE_BELOW_TIER is stripped from the raw reasons and re-applied
    explicitly against `spec.min_q_score` below, which has the same net
    effect but keeps the tier threshold visible at the call site.
    """
    reasons = selection_rejection_reasons(leg, spec, calibration)
    reasons = [r for r in reasons if r not in ("STALE_ODDS", "Q_SCORE_BELOW_TIER")]
    return not reasons and leg.q_score >= spec.min_q_score


def _daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _ticket_summary(ticket: Ticket) -> dict:
    return {
        "combined_odds": ticket.combined_odds,
        "adjusted_probability": ticket.adjusted_probability,
        "expected_value": ticket.expected_value,
        "avg_q_score": ticket.avg_q_score,
        "relaxed": ticket.relaxed,
        "legs": [
            {
                "match_id": leg.match_id,
                "home_team": leg.home_team,
                "away_team": leg.away_team,
                "competition": leg.competition,
                "market": leg.market,
                "selection": leg.selection,
                "odds": leg.best_odds,
                "q_score": leg.q_score,
            }
            for leg in ticket.legs
        ],
    }


async def _settle(db, ticket: Ticket) -> dict | None:
    """Settle against real match results. None if any leg's match hasn't
    finished with a recorded score yet (excluded from metrics, not a loss)."""
    outcomes = []
    for leg in ticket.legs:
        match = await db.get(Match, leg.match_id)
        if (
            match is None
            or match.status != MatchStatus.FINISHED
            or match.home_goals is None
            or match.away_goals is None
        ):
            return None
        try:
            outcome = evaluate_selection(leg.market, match.home_goals, match.away_goals)
        except ValueError:
            return None
        outcomes.append(outcome.value)

    if "lost" in outcomes:
        result, returned = "lost", 0.0
    elif all(o == "void" for o in outcomes):
        result, returned = "void", 1.0
    else:
        result = "won"
        returned = 1.0
        for leg, outcome in zip(ticket.legs, outcomes):
            if outcome == "won":
                returned *= leg.best_odds
    return {
        "result": result,
        "return": round(returned, 6),
        "profit": round(returned - 1.0, 6),
        "leg_outcomes": outcomes,
    }


def _portfolio_metrics(rows: list[dict], key: str) -> dict:
    settled = [r[key] for r in rows if r.get(key) is not None]
    n = len(settled)
    if n == 0:
        return {"n_settled": 0}
    wins = sum(1 for r in settled if r["result"] == "won")
    voids = sum(1 for r in settled if r["result"] == "void")
    losses = n - wins - voids
    profit = sum(r["profit"] for r in settled)
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for r in settled:
        cumulative += r["profit"]
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    return {
        "n_settled": n,
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "hit_rate": round(wins / (wins + losses), 4) if (wins + losses) else None,
        "profit_units": round(profit, 4),
        "roi": round(profit / n, 4),
        "max_drawdown_units": round(max_drawdown, 4),
    }


async def main(start: date, end: date, out_path: str | None) -> None:
    async with AsyncSessionLocal() as db:
        builder = AccumulatorBuilder(db)
        daily_rows = []
        days_with_run = 0
        days_with_ticket1 = 0
        days_with_ticket2 = 0

        for target_date in _daterange(start, end):
            run = await builder._resolve_model_run(target_date, None)
            if run is None:
                continue
            days_with_run += 1

            pool = await builder._load_qualified_legs(target_date, run.id)
            learning = (run.config_snapshot or {}).get("learning", {})
            calibration = await builder._load_calibration_gates(
                target_date,
                run.model_version,
                learning.get("base_model_version") or learning.get("requested_model_version"),
            )
            coefficients = await builder._load_correlation_coefficients()
            eligible = [
                leg
                for leg in pool
                if not _research_market_rejection_reasons(leg)
                and _historically_eligible(leg, SAFE_SPEC, calibration)
            ]

            ticket1 = _find_best_ticket(eligible, SAFE_SPEC, coefficients)
            ticket2 = None
            if ticket1 is not None:
                days_with_ticket1 += 1
                ticket2 = _find_best_ticket(
                    eligible,
                    SAFE_SPEC,
                    coefficients,
                    prior_tickets=[ticket1],
                    max_shared_matches=0,
                )
                if ticket2 is not None:
                    days_with_ticket2 += 1
                    assert shared_match_count(ticket1, ticket2) == 0, (
                        "overlap constraint violated -- ticket2 shares a match with ticket1"
                    )

            row: dict = {
                "date": target_date.isoformat(),
                "model_run_id": run.id,
                "eligible_pool_size": len(eligible),
                "ticket1": _ticket_summary(ticket1) if ticket1 else None,
                "ticket2": _ticket_summary(ticket2) if ticket2 else None,
                "ticket1_settlement": await _settle(db, ticket1) if ticket1 else None,
                "ticket2_settlement": await _settle(db, ticket2) if ticket2 else None,
            }
            daily_rows.append(row)

        ticket1_metrics = _portfolio_metrics(daily_rows, "ticket1_settlement")
        ticket2_metrics = _portfolio_metrics(daily_rows, "ticket2_settlement")

        both_settled = [
            r for r in daily_rows
            if r["ticket1_settlement"] is not None and r["ticket2_settlement"] is not None
        ]
        both_won = sum(
            1 for r in both_settled
            if r["ticket1_settlement"]["result"] == "won" and r["ticket2_settlement"]["result"] == "won"
        )
        either_won = sum(
            1 for r in both_settled
            if r["ticket1_settlement"]["result"] == "won" or r["ticket2_settlement"]["result"] == "won"
        )
        combined_profit = sum(
            r["ticket1_settlement"]["profit"] + r["ticket2_settlement"]["profit"] for r in both_settled
        )

        report = {
            "scope": "RESEARCH BACKTEST ONLY -- non-decision-bearing, read-only against live DB",
            "window": {"start": start.isoformat(), "end": end.isoformat()},
            "days_with_completed_model_run": days_with_run,
            "days_with_ticket1": days_with_ticket1,
            "days_with_ticket2": days_with_ticket2,
            "ticket2_availability_rate": (
                round(days_with_ticket2 / days_with_ticket1, 4) if days_with_ticket1 else None
            ),
            "ticket1_metrics": ticket1_metrics,
            "ticket2_metrics": ticket2_metrics,
            "both_settled_days": len(both_settled),
            "both_won_days": both_won,
            "either_won_days": either_won,
            "combined_2_unit_stake_profit_units": round(combined_profit, 4) if both_settled else None,
            "combined_2_unit_stake_roi": (
                round(combined_profit / (2 * len(both_settled)), 4) if both_settled else None
            ),
            "limitations": [
                "Ticket 1 is recomputed at full SAFE strength; it may differ from the "
                "actual historical publication on days the real ticket was relaxed or "
                "displaced by a cross-tier overlap veto (see module docstring point 2).",
                "Ticket 2 is a research construct (zero-shared-match sibling of ticket 1); "
                "it was never actually published or exposed to real staking.",
                "Small-sample: check days_with_ticket2 / n_settled before trusting ROI deltas.",
            ],
            "daily_rows": daily_rows,
        }

        summary = {k: v for k, v in report.items() if k != "daily_rows"}
        print(json.dumps(summary, indent=2, default=str))

        if out_path:
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)
                f.write("\n")
            print(f"\nFull report (including per-day ticket detail) written to {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", type=date.fromisoformat, required=True)
    parser.add_argument("--end-date", type=date.fromisoformat, required=True)
    parser.add_argument("--out", type=str, default=None, help="Optional path to write the full JSON report")
    args = parser.parse_args()
    asyncio.run(main(args.start_date, args.end_date, args.out))
