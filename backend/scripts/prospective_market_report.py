"""Read-only prospective audit of market-priced public selections.

Run from backend: python -m scripts.prospective_market_report --since 2026-10-13 --until 2026-10-14
Only publication-time snapshots and later, pre-kickoff observed quotes are used.
The same prediction appearing on two tickets counts once in probability scores.
The exclusive upper date boundary freezes a cohort; outputs remain descriptive paper evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
from collections import Counter
from datetime import date, datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import aliased

from app.database import AsyncSessionLocal
from app.models import (
    AccumulatorTicket, Competition, Match, Prediction, SelectionResult,
    Team, TicketSelection, TicketStatus, TicketType,
)
from app.services.accumulator_builder import PUBLIC_TICKET_TYPES


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


def _paper_returns(rows: list[dict]) -> dict:
    """One flat unit per unique selection at its published price; descriptive only."""
    priced = [row for row in rows if row["odds"] and row["odds"] > 1]
    if not priced:
        return {"count": 0, "profit_units": None, "roi": None, "max_drawdown_units": None}
    cumulative = peak = max_drawdown = 0.0
    for row in sorted(priced, key=lambda item: (item["kickoff"], item["prediction_id"])):
        cumulative += row["odds"] - 1 if row["outcome"] else -1
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    profit = sum(row["odds"] - 1 if row["outcome"] else -1 for row in priced)
    return {"count": len(priced), "profit_units": round(profit, 4),
            "roi": round(profit / len(priced), 6),
            "max_drawdown_units": round(max_drawdown, 4)}


def _daily_bootstrap(rows: list[dict], iterations: int = 2000) -> dict:
    """Date-block interval for flat-unit ROI; dates, not picks, are resampled."""
    blocks: dict[str, list[dict]] = {}
    for row in rows:
        if row["odds"] and row["odds"] > 1:
            blocks.setdefault(row["target_date"], []).append(row)
    if len(blocks) < 2:
        return {"date_clusters": len(blocks), "roi_percentile_95": None,
                "iterations": iterations, "seed": 20261013}
    names = sorted(blocks)
    rng = random.Random(20261013)
    estimates = []
    for _ in range(iterations):
        sample = [row for _ in names for row in blocks[rng.choice(names)]]
        profit = sum(row["odds"] - 1 if row["outcome"] else -1 for row in sample)
        estimates.append(profit / len(sample))
    estimates.sort()
    return {"date_clusters": len(blocks),
            "roi_percentile_95": [round(estimates[int((iterations - 1) * q)], 6)
                                  for q in (0.025, 0.975)],
            "iterations": iterations, "seed": 20261013}


def summarize(rows: list[tuple], as_of: datetime | None = None,
             include_loss_audit: bool = False) -> dict:
    """Rows are (ticket, selection, prediction, match[, league]); no writes or fitting."""
    as_of = _utc(as_of or datetime.now(timezone.utc))
    coverage: Counter[str] = Counter()
    appearances = list(rows)
    unique: dict[int, tuple] = {}
    for row in rows:
        ticket, selection, prediction, match = row[:4]
        league = (row[4] if len(row) > 4 else
                  getattr(getattr(match, "competition", None), "name", "Unknown"))
        home_name = row[5] if len(row) > 5 else "Unknown"
        away_name = row[6] if len(row) > 6 else "Unknown"
        coverage["published_selections"] += 1
        if selection.prediction_id in unique:
            coverage["duplicate_prediction_excluded"] += 1
            if (unique[selection.prediction_id][1].result == SelectionResult.PENDING
                    and selection.result != SelectionResult.PENDING):
                unique[selection.prediction_id] = (ticket, selection, prediction, match, league,
                                                   home_name, away_name)
            continue
        unique[selection.prediction_id] = (ticket, selection, prediction, match, league,
                                           home_name, away_name)

    market_rows: list[tuple[float, int]] = []
    model_rows: list[tuple[float, int]] = []
    clv: list[float] = []
    clv_quote_ages: list[float] = []
    clv_eligible = 0
    by_market: dict[str, tuple[list[tuple[float, int]], list[tuple[float, int]]]] = {}
    by_league: dict[str, tuple[list[tuple[float, int]], list[tuple[float, int]]]] = {}
    scored: list[dict] = []
    for ticket, selection, prediction, match, league, home_name, away_name in unique.values():
        if not (_utc(prediction.created_at) <= _utc(ticket.published_at) < _utc(match.kickoff_at)
                and selection.source_odds_at is not None
                and _utc(selection.source_odds_at) <= _utc(ticket.published_at)):
            coverage["invalid_time_order_excluded"] += 1
            continue
        if not (0 < selection.probability_snapshot < 1 and 0 < prediction.model_probability < 1):
            coverage["invalid_probability_excluded"] += 1
            continue
        close_odds = None
        close_at = None
        if selection.odds_snapshot > 1:
            if _utc(match.kickoff_at) > as_of:
                coverage["awaiting_kickoff_for_close"] += 1
            else:
                clv_eligible += 1
            if (_utc(match.kickoff_at) <= as_of
                    and prediction.closing_decimal_odds is not None
                    and prediction.closing_odds_at is not None
                    and _utc(ticket.published_at) < _utc(prediction.closing_odds_at) <= _utc(match.kickoff_at)
                    and prediction.closing_decimal_odds > 1):
                clv.append(selection.odds_snapshot / prediction.closing_decimal_odds - 1)
                close_odds = prediction.closing_decimal_odds
                close_at = _utc(prediction.closing_odds_at)
                clv_quote_ages.append((_utc(match.kickoff_at) - close_at).total_seconds() / 60)
            elif _utc(match.kickoff_at) <= as_of:
                coverage["missing_later_pre_kickoff_close"] += 1
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
        league_group, league_model_group = by_league.setdefault(str(league or "Unknown"), ([], []))
        league_group.append((selection.probability_snapshot, y))
        league_model_group.append((prediction.model_probability, y))
        scored.append({"prediction_id": selection.prediction_id,
                       "match_id": getattr(selection, "match_id", getattr(match, "id", selection.prediction_id)),
                       "home": home_name, "away": away_name,
                       "market": selection.market,
                       "selection": getattr(selection, "selection", selection.market),
                       "league": str(league or "Unknown"),
                       "target_date": getattr(ticket, "target_date", match.kickoff_at.date()).isoformat(),
                       "kickoff": _utc(match.kickoff_at), "odds": selection.odds_snapshot,
                       "market_probability": selection.probability_snapshot,
                       "model_probability": prediction.model_probability,
                       "last_observed_pre_kickoff_odds": close_odds,
                       "last_observed_pre_kickoff_at": close_at, "outcome": y})
    report = {
        "evidence": "prospective_published_market_tickets_only",
        "unique_predictions": len(unique),
        "coverage": dict(sorted(coverage.items())),
        "market": _scores(market_rows),
        "model_on_same_selections": _scores(model_rows),
        "by_market": {
            market: {"market": _scores(market_group), "model_on_same_selections": _scores(model_group),
                     "paper_flat_unit_leg_return": _paper_returns(
                         [row for row in scored if row["market"] == market])}
            for market, (market_group, model_group) in sorted(by_market.items())
        },
        "by_league": {
            league: {"market": _scores(market_group),
                     "model_on_same_selections": _scores(model_group),
                     "paper_flat_unit_leg_return": _paper_returns(
                         [row for row in scored if row["league"] == league])}
            for league, (market_group, model_group) in sorted(by_league.items())
        },
        "paper_flat_unit_leg_return": _paper_returns(scored),
        "daily_bootstrap_roi_95": _daily_bootstrap(scored),
        "ticket_exposure": _ticket_exposure(appearances),
        "concentration": {
            "largest_market_share": round(
                max(Counter(row["market"] for row in scored).values()) / len(scored), 6
            )
            if scored else None,
            "largest_league_share": round(
                max(Counter(row["league"] for row in scored).values()) / len(scored), 6
            )
            if scored else None,
            "largest_fixture_share": round(
                max(Counter(row["match_id"] for row in scored).values()) / len(scored), 6
            )
            if scored else None,
        },
        "last_observed_pre_kickoff_quote": {
            "eligible_published_prices": clv_eligible,
            "count": len(clv),
            "coverage": round(len(clv) / clv_eligible, 6) if clv_eligible else None,
            "mean_entry_vs_observed_price": round(sum(clv) / len(clv), 6) if clv else None,
            "beat_observed_price_rate": round(sum(value > 0 for value in clv) / len(clv), 6) if clv else None,
            "quote_age_at_kickoff_minutes": {
                "count": len(clv_quote_ages),
                "median": round(statistics.median(clv_quote_ages), 2) if clv_quote_ages else None,
                "p90": round(sorted(clv_quote_ages)[int((len(clv_quote_ages) - 1) * 0.9)], 2)
                if clv_quote_ages else None,
                "max": round(max(clv_quote_ages), 2) if clv_quote_ages else None,
                "timestamp_basis": "local OddsSnapshot captured_at; provider cache age is not available",
            },
            "qualification": "DESCRIPTIVE_ONLY; close freshness threshold is not pre-registered",
            "unsettled_selections_included": True,
        },
        "interpretation": "Descriptive paper evidence only; no profitability or promotion claim.",
    }
    if include_loss_audit:
        losses = sorted((row for row in scored if row["outcome"] == 0),
                        key=lambda row: (max(row["market_probability"], row["model_probability"]),
                                         row["kickoff"]), reverse=True)
        report["loss_audit"] = {
            "count": len(losses),
            "sort": "highest probability assigned by either market or model first",
            "cases": [{**row, "kickoff": row["kickoff"].isoformat(),
                       "last_observed_pre_kickoff_at": (
                           row["last_observed_pre_kickoff_at"].isoformat()
                           if row["last_observed_pre_kickoff_at"] else None
                       ),
                       "last_observed_quote_age_minutes": round(
                           (row["kickoff"] - row["last_observed_pre_kickoff_at"]).total_seconds() / 60, 2
                       ) if row["last_observed_pre_kickoff_at"] else None}
                      for row in losses],
            "interpretation": "Diagnostic misses only; this retrospective sample is not a tuning or promotion set.",
        }
    return report


def _ticket_exposure(rows: list[tuple]) -> dict:
    """Reconcile every saved ticket-leg appearance without treating it as unique evidence."""
    counts: Counter[tuple] = Counter()
    fixture_picks: dict[int, set[tuple]] = {}
    ticket_ids: dict[tuple, set[int]] = {}
    target_dates: dict[tuple, set[str]] = {}
    labels: dict[tuple, tuple[str, str]] = {}
    scored_appearances: list[dict] = []
    pending = void = invalid_time = invalid_probability = 0

    for row in rows:
        ticket, selection, prediction, match = row[:4]
        match_id = getattr(selection, "match_id", getattr(match, "id", None))
        pick_key = (match_id, selection.market,
                    getattr(selection, "selection", selection.market))
        fixture_key = match_id
        counts[pick_key] += 1
        fixture_picks.setdefault(fixture_key, set()).add(pick_key)
        ticket_ids.setdefault(pick_key, set()).add(ticket.id)
        labels[pick_key] = (
            row[5] if len(row) > 5 else "Unknown",
            row[6] if len(row) > 6 else "Unknown",
        )
        target_dates.setdefault(pick_key, set()).add(
            str(getattr(ticket, "target_date", match.kickoff_at.date()))
        )

        if not (_utc(prediction.created_at) <= _utc(ticket.published_at) < _utc(match.kickoff_at)
                and selection.source_odds_at is not None
                and _utc(selection.source_odds_at) <= _utc(ticket.published_at)):
            invalid_time += 1
            continue
        if not (0 < selection.probability_snapshot < 1 and 0 < prediction.model_probability < 1):
            invalid_probability += 1
            continue
        if selection.result == SelectionResult.PENDING:
            pending += 1
            continue
        if selection.result == SelectionResult.VOID:
            void += 1
            continue
        scored_appearances.append({
            "prediction_id": selection.prediction_id,
            "match_id": match_id,
            "target_date": getattr(ticket, "target_date", match.kickoff_at.date()).isoformat(),
            "kickoff": _utc(match.kickoff_at),
            "odds": selection.odds_snapshot,
            "outcome": int(selection.result == SelectionResult.WON),
            "ticket_id": ticket.id,
        })

    repeated = [key for key, count in counts.items() if count > 1]
    repeated_picks = []
    for key in sorted(repeated, key=lambda item: (-counts[item], str(item)))[:20]:
        home, away = labels[key]
        repeated_picks.append({
            "match_id": key[0], "home": home, "away": away,
            "market": key[1], "selection": key[2],
            "appearances": counts[key], "ticket_ids": sorted(ticket_ids[key]),
            "target_dates": sorted(target_dates[key]),
        })

    fixture_overlap = sum(1 for picks in fixture_picks.values() if len(picks) > 1)
    return {
        "basis": "all saved published ticket versions; one simulated unit per settled leg appearance",
        "published_leg_appearances": len(rows),
        "distinct_ticket_records": len({row[0].id for row in rows}),
        "distinct_fixtures": len(fixture_picks),
        "unique_match_market_selection_picks": len(counts),
        "repeated_pick_keys": len(repeated),
        "repeated_leg_appearances": sum(counts[key] - 1 for key in repeated),
        "max_appearances_per_pick": max(counts.values(), default=0),
        "fixtures_with_multiple_distinct_picks": fixture_overlap,
        "settled_nonvoid_leg_appearances": len(scored_appearances),
        "pending_leg_appearances": pending,
        "void_leg_appearances": void,
        "invalid_time_order_appearances": invalid_time,
        "invalid_probability_appearances": invalid_probability,
        "paper_flat_unit_leg_appearance_return": _paper_returns(scored_appearances),
        "top_repeated_picks": repeated_picks,
        "interpretation": "Version history exposure arithmetic only; tickets may be superseded and are not assumed simultaneous or actually staked.",
    }


async def run(since: date, until: date | None = None,
              include_loss_audit: bool = False) -> dict:
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        home_team = aliased(Team)
        away_team = aliased(Team)
        result = await db.execute(
            select(AccumulatorTicket, TicketSelection, Prediction, Match, Competition.name,
                   home_team.name, away_team.name)
            .join(TicketSelection, TicketSelection.ticket_id == AccumulatorTicket.id)
            .join(Prediction, Prediction.id == TicketSelection.prediction_id)
            .join(Match, Match.id == TicketSelection.match_id)
            .join(Competition, Competition.id == Match.competition_id)
            .join(home_team, home_team.id == Match.home_team_id)
            .join(away_team, away_team.id == Match.away_team_id)
            .where(AccumulatorTicket.pricing == "market",
                   AccumulatorTicket.target_date >= since,
                   *([AccumulatorTicket.target_date < until] if until is not None else []),
                   AccumulatorTicket.ticket_type.in_(PUBLIC_TICKET_TYPES),
                   AccumulatorTicket.status.in_((TicketStatus.PUBLISHED, TicketStatus.SETTLED,
                                                 TicketStatus.VOID)))
            .order_by(AccumulatorTicket.published_at, TicketSelection.id)
        )
        report = summarize(result.all(), as_of=datetime.now(timezone.utc),
                            include_loss_audit=include_loss_audit)
        report["since_target_date"] = since.isoformat()
        report["until_target_date_exclusive"] = until.isoformat() if until else None
        report["generated_at_utc"] = datetime.now(timezone.utc).isoformat()
        await db.rollback()
        return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", type=date.fromisoformat, default=date(2026, 9, 23))
    parser.add_argument("--until", type=date.fromisoformat,
                        help="Exclusive target-date boundary to freeze a cohort")
    parser.add_argument("--include-loss-audit", action="store_true",
                        help="Include resolved losses and their captured evidence")
    args = parser.parse_args()
    if args.until is not None and args.until <= args.since:
        parser.error("--until must be later than --since")
    print(json.dumps(asyncio.run(run(args.since, args.until, args.include_loss_audit)),
                     indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
