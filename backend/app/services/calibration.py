"""Derived model, league, and market-correlation research snapshots."""

from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CorrelationCoefficient,
    EdgeBandCalibration,
    LeaguePerformance,
    Match,
    MatchStatus,
    ModelPerformance,
    Prediction,
    SelectionResult,
)
from app.services.performance import _calibration_error, _edge_band, _market_family
from app.services.settlement import evaluate_selection


async def aggregate_calibration_snapshots(
    db: AsyncSession, period_end: date, lookback_days: int = 90
) -> dict:
    period_start = period_end - timedelta(days=lookback_days - 1)
    period_start_at = datetime.combine(period_start, time.min, tzinfo=timezone.utc)
    period_end_at = datetime.combine(
        period_end + timedelta(days=1), time.min, tzinfo=timezone.utc
    )
    result = await db.execute(
        select(Prediction, Match)
        .join(Match, Prediction.match_id == Match.id)
        .where(
            Match.status == MatchStatus.FINISHED,
            Match.kickoff_at >= period_start_at,
            Match.kickoff_at < period_end_at,
            Match.home_goals.is_not(None),
            Match.away_goals.is_not(None),
        )
        .order_by(Prediction.id)
    )
    latest: dict[tuple[int, str, str], tuple[Prediction, Match, float]] = {}
    for prediction, match in result.all():
        try:
            outcome = evaluate_selection(prediction.market, match.home_goals, match.away_goals)
        except ValueError:
            continue
        if outcome == SelectionResult.VOID:
            continue
        latest[(prediction.match_id, prediction.market, prediction.model_version)] = (
            prediction,
            match,
            1.0 if outcome == SelectionResult.WON else 0.0,
        )

    model_groups: dict[tuple[str, str, str], list[tuple[float, float]]] = defaultdict(list)
    league_groups: dict[int, list[tuple[float, float]]] = defaultdict(list)
    edge_band_groups: dict[tuple[str, str, str], list[tuple[float, float]]] = defaultdict(list)
    match_outcomes: dict[tuple[int, int], dict[str, float]] = defaultdict(dict)
    for prediction, match, outcome in latest.values():
        probabilities = {
            "poisson": prediction.poisson_prob,
            "zinb": prediction.zinb_prob,
            "bayes": prediction.bayes_prob,
            "elo": prediction.elo_prob,
            "xg": prediction.xg_prob,
            "ensemble": prediction.model_probability,
        }
        for model_name, probability in probabilities.items():
            if probability is not None:
                model_groups[(prediction.model_version, model_name, prediction.market)].append((probability, outcome))
        league_groups[match.competition_id].append((prediction.model_probability, outcome))
        match_outcomes[(match.competition_id, match.id)][_market_family(prediction.market)] = outcome
        if prediction.edge is not None:
            key = (prediction.model_version, _market_family(prediction.market), _edge_band(prediction.edge))
            edge_band_groups[key].append((prediction.model_probability, outcome))

    model_rows = 0
    for (version, model_name, market), values in model_groups.items():
        row = await _get_or_create_model_row(
            db, version, model_name, market, period_start, period_end
        )
        row.sample_size = len(values)
        row.brier_score = sum((p - y) ** 2 for p, y in values) / len(values)
        row.calibration_error = _calibration_error(values)
        row.hit_rate = sum((p >= 0.5) == bool(y) for p, y in values) / len(values)
        model_rows += 1

    edge_band_rows = 0
    for (version, market_family, edge_band), values in edge_band_groups.items():
        row = await _get_or_create_edge_band_row(
            db, version, market_family, edge_band, period_start, period_end
        )
        row.sample_size = len(values)
        row.calibration_error = _calibration_error(values)
        row.hit_rate = sum((p >= 0.5) == bool(y) for p, y in values) / len(values)
        edge_band_rows += 1

    league_rows = 0
    for competition_id, values in league_groups.items():
        row = await _get_or_create_league_row(db, competition_id, period_start, period_end)
        row.sample_size = len(values)
        row.brier_score = sum((p - y) ** 2 for p, y in values) / len(values)
        row.hit_rate = sum((p >= 0.5) == bool(y) for p, y in values) / len(values)
        row.reliability_score = max(0.0, min(1.0, 1.0 - row.brier_score))
        league_rows += 1

    pair_values: dict[tuple[int, str, str], list[tuple[float, float]]] = defaultdict(list)
    for (competition_id, _), outcomes in match_outcomes.items():
        families = sorted(outcomes)
        for index, key_a in enumerate(families):
            for key_b in families[index + 1 :]:
                pair_values[(competition_id, key_a, key_b)].append((outcomes[key_a], outcomes[key_b]))
    correlation_rows = 0
    for (competition_id, key_a, key_b), values in pair_values.items():
        if len(values) < 20:
            continue
        coefficient = _pearson(values)
        row_result = await db.execute(
            select(CorrelationCoefficient).where(
                CorrelationCoefficient.scope == "market_pair",
                CorrelationCoefficient.key_a == key_a,
                CorrelationCoefficient.key_b == key_b,
                CorrelationCoefficient.competition_id == competition_id,
            )
        )
        row = row_result.scalar_one_or_none()
        if row is None:
            row = CorrelationCoefficient(
                scope="market_pair",
                key_a=key_a,
                key_b=key_b,
                competition_id=competition_id,
                coefficient=coefficient,
            )
            db.add(row)
        row.coefficient = coefficient
        row.sample_size = len(values)
        correlation_rows += 1
    await db.flush()
    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "prediction_sample_size": len(latest),
        "model_performance_rows": model_rows,
        "league_performance_rows": league_rows,
        "edge_band_calibration_rows": edge_band_rows,
        "correlation_rows": correlation_rows,
    }


async def _get_or_create_model_row(db, version, model_name, market, start, end):
    result = await db.execute(
        select(ModelPerformance).where(
            ModelPerformance.model_version == version,
            ModelPerformance.model_name == model_name,
            ModelPerformance.market == market,
            ModelPerformance.period_start == start,
            ModelPerformance.period_end == end,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = ModelPerformance(
            model_version=version,
            model_name=model_name,
            market=market,
            period_start=start,
            period_end=end,
        )
        db.add(row)
    return row


async def _get_or_create_league_row(db, competition_id, start, end):
    result = await db.execute(
        select(LeaguePerformance).where(
            LeaguePerformance.competition_id == competition_id,
            LeaguePerformance.period_start == start,
            LeaguePerformance.period_end == end,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = LeaguePerformance(
            competition_id=competition_id,
            period_start=start,
            period_end=end,
        )
        db.add(row)
    return row


async def _get_or_create_edge_band_row(db, version, market_family, edge_band, start, end):
    result = await db.execute(
        select(EdgeBandCalibration).where(
            EdgeBandCalibration.model_version == version,
            EdgeBandCalibration.market_family == market_family,
            EdgeBandCalibration.edge_band == edge_band,
            EdgeBandCalibration.period_start == start,
            EdgeBandCalibration.period_end == end,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        row = EdgeBandCalibration(
            model_version=version,
            market_family=market_family,
            edge_band=edge_band,
            period_start=start,
            period_end=end,
        )
        db.add(row)
    return row


def _pearson(values: list[tuple[float, float]]) -> float:
    xs, ys = zip(*values)
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in values)
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    return max(-1.0, min(1.0, numerator / denominator)) if denominator else 0.0
