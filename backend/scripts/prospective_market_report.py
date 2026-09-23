"""Read-only prospective audit of market-priced public selections.

Run from backend: python -m scripts.prospective_market_report --since 2026-09-23
Only publication-time snapshots and later, pre-kickoff closing quotes are used.
The same prediction appearing on two tickets counts once in probability scores.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
from collections import Counter
from datetime import date, datetime, timezone

from sqlalchemy import select, text

from app.database import AsyncSessionLocal
from app.models import AccumulatorTicket, Match, Prediction, SelectionResult, TicketSelection, TicketStatus, TicketType


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _scores(rows: list[tuple[float, int]]) -> dict:
    if not rows:
        return {"count": 0, "brier": None, "log_loss": None, "mean_probability": None,
                "win_rate": None, "signed_gap": None}
    n = len(rows)
    probabilities = [min(1 - 1e-9, max(1e-9, p)) for p, _ in rows]
    outcomes = [outcome for _, outcome in rows]
    return {
        "count": n,
        "brier": round(sum((p - y) ** 2 for p, y in zip(probabilities, outcomes)) / n, 6),
        "log_loss": round(-sum(y * math.log(p) + (1 - y) * math.log(1 - p)
                               for p, y in zip(probabilities, outcomes)) / n, 6),
        "mean_probability": round(sum(probabilities) / n, 6),
        "win_rate": round(sum(outcomes) / n, 6),
        "signed_gap": round((sum(outcomes) - sum(probabilities)) / n, 6),
    }


def summarize(rows: list[tuple]) -> dict:
    """Rows are (ticket, selection, prediction, match); no writes or fitting."""
    coverage: Counter[str] = Counter()
    unique: dict[int, tuple] = {}
    for ticket, selection, prediction, match in rows:
        coverage["published_selections"] += 1
        if selection.prediction_id in unique:
            coverage["duplicate_prediction_excluded"] += 1
            if (unique[selection.prediction_id][1].result == SelectionResult.PENDING
                    and selection.result != SelectionResult.PENDING):
                unique[selection.prediction_id] = (ticket, selection, prediction, match)
            continue
        unique[selection.prediction_id] = (ticket, selection, prediction, match)

    market_rows: list[tuple[float, int]] = []
    model_rows: list[tuple[float, int]] = []
    clv: list[float] = []
    by_market: dict[str, tuple[list[tuple[float, int]], list[tuple[float, int]]]] = {}
    for ticket, selection, prediction, match in unique.values():
        if not (_utc(prediction.created_at) <= _utc(ticket.published_at) < _utc(match.kickoff_at)
                and selection.source_odds_at is not None
                and _utc(selection.source_odds_at) <= _utc(ticket.published_at)):
            coverage["invalid_time_order_excluded"] += 1
            continue
        if not (0 < selection.probability_snapshot < 1 and 0 < prediction.model_probability < 1):
            coverage["invalid_probability_excluded"] += 1
            continue
        if selection.result == SelectionResult.PENDING:
            coverage["pending"] += 1
            continue
        if selection.result == SelectionResult.VOID:
            coverage["void_excluded"] += 1
            continue
        y = int(selection.result == SelectionResult.WON)
        market_rows.append((selection.probability_snapshot, y))
        model_rows.append((prediction.model_probability, y))
        market_group, model_group = by_market.setdefault(selection.market, ([], []))
        market_group.append((selection.probability_snapshot, y))
        model_group.append((prediction.model_probability, y))
        if (prediction.closing_decimal_odds is not None
                and prediction.closing_odds_at is not None
                and _utc(ticket.published_at) < _utc(prediction.closing_odds_at) <= _utc(match.kickoff_at)
                and selection.odds_snapshot > 1 and prediction.closing_decimal_odds > 1):
            clv.append(selection.odds_snapshot / prediction.closing_decimal_odds - 1)
        else:
            coverage["missing_later_pre_kickoff_close"] += 1

    return {
        "evidence": "prospective_published_market_tickets_only",
        "unique_predictions": len(unique),
        "coverage": dict(sorted(coverage.items())),
        "market": _scores(market_rows),
        "model_on_same_selections": _scores(model_rows),
        "by_market": {
            market: {"market": _scores(market_group), "model_on_same_selections": _scores(model_group)}
            for market, (market_group, model_group) in sorted(by_market.items())
        },
        "closing_line": {
            "count": len(clv),
            "mean_entry_vs_close": round(sum(clv) / len(clv), 6) if clv else None,
            "beat_close_rate": round(sum(value > 0 for value in clv) / len(clv), 6) if clv else None,
        },
        "interpretation": "Descriptive paper evidence only; no profitability or promotion claim.",
    }


async def run(since: date) -> dict:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        result = await db.execute(
            select(AccumulatorTicket, TicketSelection, Prediction, Match)
            .join(TicketSelection, TicketSelection.ticket_id == AccumulatorTicket.id)
            .join(Prediction, Prediction.id == TicketSelection.prediction_id)
            .join(Match, Match.id == TicketSelection.match_id)
            .where(AccumulatorTicket.pricing == "market",
                   AccumulatorTicket.target_date >= since,
                   AccumulatorTicket.ticket_type.in_((TicketType.SAFE, TicketType.BALANCED,
                                                      TicketType.AGGRESSIVE)),
                   AccumulatorTicket.status.in_((TicketStatus.PUBLISHED, TicketStatus.SETTLED,
                                                 TicketStatus.VOID)))
            .order_by(AccumulatorTicket.published_at, TicketSelection.id)
        )
        report = summarize(result.all())
        report["since_target_date"] = since.isoformat()
        report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        await db.rollback()
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=date.fromisoformat, default=date(2026, 9, 23))
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.since)), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
