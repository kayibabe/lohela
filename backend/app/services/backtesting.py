"""Strict no-lookahead backtesting and Monte Carlo validation gate."""

from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import cat_day_bounds_utc, settings
from app.models import BacktestRun, Match, MatchStatus, Prediction, RunStatus, SelectionResult
from app.services.performance import (
    _calibration_error,
    _has_pre_kickoff_information,
    _has_pre_kickoff_quote,
    _wilson_interval,
)
from app.services.settlement import evaluate_selection


class Backtester:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def run(
        self,
        period_start: date,
        period_end: date,
        model_version: str,
        simulations: int = 10_000,
    ) -> BacktestRun:
        run = BacktestRun(
            model_version=model_version,
            period_start=period_start,
            period_end=period_end,
            status=RunStatus.RUNNING,
            config_snapshot={
                "strict_no_lookahead": True,
                "closing_odds_only": True,
                "odds_slippage": 0.05,
                "kelly_fraction": 0.25,
                "maximum_bankroll_fraction": 0.02,
                "walk_forward": True,
                "monte_carlo_simulations": simulations,
            },
        )
        self.db.add(run)
        await self.db.flush()

        rows = await self._load_rows(period_start, period_end, model_version)
        latest: dict[tuple[int, str, str], tuple[Prediction, Match]] = {}
        leakage_count = 0
        missing_odds = 0
        for prediction, match in rows:
            if prediction.source_decimal_odds is None or prediction.source_odds_at is None:
                missing_odds += 1
                continue
            if not _has_pre_kickoff_information(prediction) or not _has_pre_kickoff_quote(prediction):
                leakage_count += 1
                continue
            key = (prediction.match_id, prediction.market, prediction.selection)
            previous = latest.get(key)
            prediction_time = _aware(prediction.as_of_at or prediction.created_at)
            previous_time = _aware(previous[0].as_of_at or previous[0].created_at) if previous else None
            if previous is None or prediction_time > previous_time:
                latest[key] = (prediction, match)

        records: list[dict] = []
        single_game_records: list[dict] = []
        bankroll = 100.0
        peak = bankroll
        max_drawdown = 0.0
        for prediction, match in sorted(latest.values(), key=lambda row: row[1].kickoff_at):
            try:
                outcome = evaluate_selection(prediction.market, match.home_goals, match.away_goals)
            except ValueError:
                continue
            if outcome == SelectionResult.VOID:
                continue
            slipped_odds = 1.0 + (prediction.source_decimal_odds - 1.0) * 0.95
            single_game_records.append(
                {
                    "date": match.kickoff_at.date().isoformat(),
                    "probability": prediction.model_probability,
                    "outcome": 1.0 if outcome == SelectionResult.WON else 0.0,
                    "odds": slipped_odds,
                    "stake": 1.0,
                    "profit": slipped_odds - 1.0 if outcome == SelectionResult.WON else -1.0,
                    "market": prediction.market,
                    "competition_id": match.competition_id,
                    "q_score_band": _q_score_band(prediction.q_score),
                    "spread_band": _spread_band(prediction.model_agreement),
                    "odds_band": _odds_band(slipped_odds),
                }
            )
            stake_fraction = _fractional_kelly(prediction.model_probability, slipped_odds)
            if stake_fraction <= 0:
                continue
            stake = bankroll * stake_fraction
            profit = stake * (slipped_odds - 1.0) if outcome == SelectionResult.WON else -stake
            bankroll += profit
            peak = max(peak, bankroll)
            max_drawdown = max(max_drawdown, peak - bankroll)
            records.append(
                {
                    "date": match.kickoff_at.date().isoformat(),
                    "probability": prediction.model_probability,
                    "outcome": 1.0 if outcome == SelectionResult.WON else 0.0,
                    "odds": slipped_odds,
                    "stake": stake,
                    "profit": profit,
                    "market": prediction.market,
                    "competition_id": match.competition_id,
                    "q_score_band": _q_score_band(prediction.q_score),
                    "spread_band": _spread_band(prediction.model_agreement),
                    "odds_band": _odds_band(slipped_odds),
                }
            )

        outcomes = [(row["probability"], row["outcome"]) for row in records]
        total_stake = sum(row["stake"] for row in records)
        total_profit = sum(row["profit"] for row in records)
        wins = sum(row["outcome"] == 1.0 for row in records)
        hit_rate = wins / len(records) if records else 0.0
        ci_low, ci_high = _wilson_interval(wins, len(records))
        brier = sum((p - y) ** 2 for p, y in outcomes) / len(outcomes) if outcomes else None
        metrics = {
            "sample_size": len(records),
            "candidate_rows": len(rows),
            "leakage_rows_rejected": leakage_count,
            "missing_closing_odds_rows": missing_odds,
            "ending_bankroll": bankroll,
            "profit_loss": total_profit,
            "roi": total_profit / total_stake if total_stake else 0.0,
            "yield": total_profit / total_stake if total_stake else 0.0,
            "hit_rate": hit_rate,
            "hit_rate_confidence_interval_95": [ci_low, ci_high],
            "brier_score": brier,
            "calibration_error": _calibration_error(outcomes),
            "max_drawdown": max_drawdown,
            "walk_forward": _walk_forward(records),
            "monte_carlo": _monte_carlo(records, simulations),
            "by_market": _group_metrics(records, "market"),
            "by_league": _group_metrics(records, "competition_id"),
            "by_q_score": _group_metrics(records, "q_score_band"),
            "by_model_spread": _group_metrics(records, "spread_band"),
            "by_odds_band": _group_metrics(records, "odds_band"),
            "single_game": _single_game_metrics(single_game_records),
            "single_game_by_market": _group_metrics(single_game_records, "market"),
            "focus_market_evaluation": _focus_market_evaluation(single_game_records),
            "research_policy": {
                "focus_markets": list(settings.research_focus_markets),
                "restricted_markets": list(settings.research_restricted_markets),
                "target_hit_rate": settings.research_target_hit_rate,
                "minimum_market_sample": settings.research_minimum_market_sample,
            },
            "losses": [
                {key: row[key] for key in ("date", "market", "competition_id", "probability", "odds", "q_score_band", "spread_band", "odds_band")}
                for row in records if row["outcome"] == 0.0
            ][:100],
        }
        run.metrics = metrics
        run.leakage_checks_passed = leakage_count == 0
        run.status = RunStatus.COMPLETED if leakage_count == 0 and records else RunStatus.FAILED
        run.error_details = (
            f"Strict no-lookahead gate rejected {leakage_count} rows"
            if leakage_count
            else ("No eligible closing-odds records" if not records else None)
        )
        run.completed_at = datetime.now(timezone.utc)
        await self.db.flush()
        return run

    async def _load_rows(self, start: date, end: date, model_version: str):
        result = await self.db.execute(
            select(Prediction, Match)
            .join(Match, Prediction.match_id == Match.id)
            .where(
                Prediction.model_version == model_version,
                Match.status == MatchStatus.FINISHED,
                Match.kickoff_at >= cat_day_bounds_utc(start)[0],
                Match.kickoff_at < cat_day_bounds_utc(end)[1],
                Match.home_goals.is_not(None),
                Match.away_goals.is_not(None),
            )
            .order_by(Match.kickoff_at, Prediction.created_at)
        )
        return result.all()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _fractional_kelly(probability: float, odds: float) -> float:
    net = odds - 1.0
    if net <= 0:
        return 0.0
    full_kelly = (net * probability - (1.0 - probability)) / net
    return min(0.02, max(0.0, full_kelly * 0.25))


def _walk_forward(records: list[dict]) -> list[dict]:
    if len(records) < 20:
        return []
    fold_size = max(5, len(records) // 5)
    folds = []
    for start in range(fold_size, len(records), fold_size):
        test = records[start : start + fold_size]
        if not test:
            continue
        stake = sum(row["stake"] for row in test)
        profit = sum(row["profit"] for row in test)
        folds.append({
            "train_size": start,
            "test_size": len(test),
            "test_start": test[0]["date"],
            "test_end": test[-1]["date"],
            "roi": profit / stake if stake else 0.0,
        })
    return folds


def _monte_carlo(records: list[dict], simulations: int) -> dict:
    if not records:
        return {"simulations": simulations, "roi_p05": None, "roi_median": None, "roi_p95": None}
    rng = random.Random(20260828)
    returns = [row["profit"] / row["stake"] for row in records if row["stake"] > 0]
    simulated = []
    for _ in range(simulations):
        sample = rng.choices(returns, k=len(returns))
        simulated.append(sum(sample) / len(sample))
    simulated.sort()
    return {
        "simulations": simulations,
        "roi_p05": simulated[int(simulations * 0.05)],
        "roi_median": simulated[int(simulations * 0.50)],
        "roi_p95": simulated[min(simulations - 1, int(simulations * 0.95))],
        "probability_positive_roi": sum(value > 0 for value in simulated) / simulations,
    }


def _q_score_band(value: float) -> str:
    if value >= 85:
        return "85–100"
    if value >= 80:
        return "80–85"
    if value >= 75:
        return "75–80"
    return "<75"


def _single_game_metrics(records: list[dict]) -> dict:
    outcomes = [(row["probability"], row["outcome"]) for row in records]
    wins = sum(row["outcome"] == 1.0 for row in records)
    stake = sum(row["stake"] for row in records)
    profit = sum(row["profit"] for row in records)
    low, high = _wilson_interval(wins, len(records))
    return {
        "sample_size": len(records),
        "wins": wins,
        "losses": len(records) - wins,
        "hit_rate": wins / len(records) if records else 0.0,
        "hit_rate_confidence_interval_95": [low, high],
        "brier_score": sum((p - y) ** 2 for p, y in outcomes) / len(outcomes) if outcomes else None,
        "calibration_error": _calibration_error(outcomes),
        "profit_loss": profit,
        "roi": profit / stake if stake else 0.0,
    }


def _focus_market_evaluation(records: list[dict]) -> list[dict]:
    output = []
    for market in settings.research_focus_markets:
        rows = [row for row in records if row["market"] == market]
        metrics = _single_game_metrics(rows)
        output.append({
            "market": market,
            **metrics,
            "target_hit_rate": settings.research_target_hit_rate,
            "minimum_sample": settings.research_minimum_market_sample,
            "sample_sufficient": metrics["sample_size"] >= settings.research_minimum_market_sample,
            "hit_rate_target_met": metrics["hit_rate"] >= settings.research_target_hit_rate,
        })
    return output


def _spread_band(value: float | None) -> str:
    if value is None:
        return "Unknown"
    if value <= 0.05:
        return "0–5 pp"
    if value <= 0.10:
        return "5–10 pp"
    if value <= 0.15:
        return "10–15 pp"
    return ">15 pp"


def _odds_band(value: float) -> str:
    if value < 1.50:
        return "1.01–1.49"
    if value < 2.00:
        return "1.50–1.99"
    if value < 3.00:
        return "2.00–2.99"
    return "3.00+"


def _group_metrics(records: list[dict], key: str) -> list[dict]:
    groups: dict[object, list[dict]] = {}
    for row in records:
        groups.setdefault(row[key], []).append(row)
    output = []
    for value, rows in groups.items():
        stake = sum(row["stake"] for row in rows)
        profit = sum(row["profit"] for row in rows)
        output.append({
            key: value,
            "sample_size": len(rows),
            "hit_rate": sum(row["outcome"] for row in rows) / len(rows),
            "roi": profit / stake if stake else 0.0,
        })
    return output
