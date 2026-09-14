"""No-lookahead challenger training, validation, and explicit promotion."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timezone

import numpy as np
from scipy.optimize import minimize
from sklearn.linear_model import LogisticRegression
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import cat_day_bounds_utc, settings
from app.models import (
    AuditEvent,
    MarketLearningProfile,
    Match,
    MatchStatus,
    ModelLearningPromotion,
    ModelLearningRun,
    Prediction,
    RunStatus,
    SelectionResult,
)
from app.services.models.ensemble import DEFAULT_WEIGHTS
from app.services.settlement import evaluate_selection


MODEL_NAMES = tuple(DEFAULT_WEIGHTS)


@dataclass(frozen=True)
class LearningExample:
    match_id: int
    market: str
    kickoff_at: datetime
    outcome: float
    incumbent_probability: float
    model_probabilities: dict[str, float]
    decimal_odds: float | None


@dataclass(frozen=True)
class ActiveLearningConfig:
    promotion_id: int
    learning_run_id: int
    base_model_version: str
    challenger_version: str
    profiles: dict[str, MarketLearningProfile]


def apply_probability_calibrator(probability: float, calibrator: dict | None) -> float:
    """Apply a stored Platt/logit calibrator without mutating source evidence."""
    p = min(1.0 - 1e-6, max(1e-6, float(probability)))
    if not calibrator or calibrator.get("type") != "platt_logit":
        return p
    slope = float(calibrator.get("slope", 1.0))
    intercept = float(calibrator.get("intercept", 0.0))
    score = max(-35.0, min(35.0, slope * math.log(p / (1.0 - p)) + intercept))
    return 1.0 / (1.0 + math.exp(-score))


def _weighted_probability(example: LearningExample, weights: dict[str, float]) -> float:
    available = {
        name: probability
        for name, probability in example.model_probabilities.items()
        if name in weights and probability is not None
    }
    denominator = sum(weights[name] for name in available)
    if denominator <= 0:
        return example.incumbent_probability
    return min(
        1.0,
        max(0.0, sum(weights[name] * probability for name, probability in available.items()) / denominator),
    )


def _fit_regularized_weights(
    examples: list[LearningExample], regularization: float
) -> dict[str, float]:
    defaults = np.array([DEFAULT_WEIGHTS[name] for name in MODEL_NAMES], dtype=float)

    def objective(vector: np.ndarray) -> float:
        weights = dict(zip(MODEL_NAMES, vector.tolist()))
        probabilities = np.clip(
            [_weighted_probability(example, weights) for example in examples], 1e-6, 1 - 1e-6
        )
        outcomes = np.array([example.outcome for example in examples], dtype=float)
        log_loss = -np.mean(
            outcomes * np.log(probabilities) + (1.0 - outcomes) * np.log(1.0 - probabilities)
        )
        return float(log_loss + regularization * np.sum((vector - defaults) ** 2))

    result = minimize(
        objective,
        defaults,
        method="SLSQP",
        bounds=[(0.01, 0.80)] * len(MODEL_NAMES),
        constraints=[{"type": "eq", "fun": lambda vector: float(np.sum(vector) - 1.0)}],
        options={"maxiter": 300, "ftol": 1e-10},
    )
    vector = result.x if result.success else defaults
    vector = np.maximum(vector, 0.0)
    vector = vector / vector.sum()
    return {name: round(float(value), 8) for name, value in zip(MODEL_NAMES, vector)}


def _fit_calibrator(probabilities: list[float], outcomes: list[float]) -> dict:
    if len(set(outcomes)) < 2:
        return {"type": "identity", "reason": "single_outcome_class"}
    clipped = np.clip(probabilities, 1e-6, 1 - 1e-6)
    logits = np.log(clipped / (1.0 - clipped)).reshape(-1, 1)
    model = LogisticRegression(C=2.0, solver="lbfgs", random_state=17)
    model.fit(logits, np.asarray(outcomes, dtype=int))
    candidate = {
        "type": "platt_logit",
        "slope": round(float(model.coef_[0][0]), 8),
        "intercept": round(float(model.intercept_[0]), 8),
    }
    raw_brier = float(np.mean((clipped - np.asarray(outcomes)) ** 2))
    calibrated = np.asarray([apply_probability_calibrator(p, candidate) for p in clipped])
    calibrated_brier = float(np.mean((calibrated - np.asarray(outcomes)) ** 2))
    return candidate if calibrated_brier <= raw_brier else {"type": "identity", "reason": "no_train_brier_gain"}


def _expected_calibration_error(probabilities: list[float], outcomes: list[float], bins: int = 10) -> float:
    total = len(probabilities)
    error = 0.0
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        members = [
            (p, y)
            for p, y in zip(probabilities, outcomes)
            if low <= p < high or (index == bins - 1 and p == 1.0)
        ]
        if members:
            error += len(members) / total * abs(
                sum(p for p, _ in members) / len(members)
                - sum(y for _, y in members) / len(members)
            )
    return error


def _roi_confidence_interval(profits: list[float], samples: int = 1000) -> list[float] | None:
    if len(profits) < 2:
        return None
    generator = np.random.default_rng(17)
    values = np.asarray(profits, dtype=float)
    bootstrapped = generator.choice(values, size=(samples, len(values)), replace=True).mean(axis=1)
    low, high = np.quantile(bootstrapped, [0.025, 0.975])
    return [round(float(low), 6), round(float(high), 6)]


def _metric_bundle(
    examples: list[LearningExample], probabilities: list[float], minimum_edge: float
) -> dict:
    outcomes = [example.outcome for example in examples]
    clipped = [min(1.0 - 1e-6, max(1e-6, probability)) for probability in probabilities]
    brier = sum((p - y) ** 2 for p, y in zip(clipped, outcomes)) / len(examples)
    log_loss = -sum(
        y * math.log(p) + (1.0 - y) * math.log(1.0 - p)
        for p, y in zip(clipped, outcomes)
    ) / len(examples)
    profits: list[float] = []
    edges: list[float] = []
    for example, probability in zip(examples, clipped):
        if not example.decimal_odds or example.decimal_odds <= 1.0:
            continue
        edge = probability - 1.0 / example.decimal_odds
        if edge < minimum_edge:
            continue
        edges.append(edge)
        profits.append(example.decimal_odds - 1.0 if example.outcome == 1.0 else -1.0)
    return {
        "sample_size": len(examples),
        "wins": int(sum(outcomes)),
        "losses": int(len(outcomes) - sum(outcomes)),
        "brier_score": round(float(brier), 6),
        "log_loss": round(float(log_loss), 6),
        "calibration_error": round(float(_expected_calibration_error(clipped, outcomes)), 6),
        "eligible_bets": len(profits),
        "roi": round(float(sum(profits) / len(profits)), 6) if profits else None,
        "roi_confidence_interval_95": _roi_confidence_interval(profits),
        "average_edge": round(float(sum(edges) / len(edges)), 6) if edges else None,
    }


def fit_market_challenger(
    train: list[LearningExample],
    validation: list[LearningExample],
    *,
    minimum_edge: float,
    regularization: float,
    min_validation_bets: int,
    min_brier_improvement: float,
    max_calibration_regression: float,
    max_roi_regression: float,
) -> dict:
    """Fit on the earlier period and evaluate once on the untouched later period."""
    weights = _fit_regularized_weights(train, regularization)
    raw_train = [_weighted_probability(example, weights) for example in train]
    calibrator = _fit_calibrator(raw_train, [example.outcome for example in train])
    candidate_train = [apply_probability_calibrator(p, calibrator) for p in raw_train]
    candidate_validation = [
        apply_probability_calibrator(_weighted_probability(example, weights), calibrator)
        for example in validation
    ]
    incumbent_train = [example.incumbent_probability for example in train]
    incumbent_validation = [example.incumbent_probability for example in validation]

    train_metrics = {
        "incumbent": _metric_bundle(train, incumbent_train, minimum_edge),
        "challenger": _metric_bundle(train, candidate_train, minimum_edge),
    }
    validation_metrics = {
        "incumbent": _metric_bundle(validation, incumbent_validation, minimum_edge),
        "challenger": _metric_bundle(validation, candidate_validation, minimum_edge),
    }
    incumbent = validation_metrics["incumbent"]
    challenger = validation_metrics["challenger"]
    checks = {
        "brier_improved": challenger["brier_score"] <= incumbent["brier_score"] - min_brier_improvement,
        "calibration_not_worse": challenger["calibration_error"] <= incumbent["calibration_error"] + max_calibration_regression,
        "enough_priced_bets": challenger["eligible_bets"] >= min_validation_bets,
        "roi_not_worse": (
            challenger["roi"] is not None
            and incumbent["roi"] is not None
            and challenger["roi"] >= incumbent["roi"] - max_roi_regression
        ),
    }
    return {
        "weights": weights,
        "calibrator": calibrator,
        "train_metrics": train_metrics,
        "validation_metrics": validation_metrics,
        "promotion_checks": checks,
        "shadow_ready": all(checks.values()),
    }


async def _load_learning_examples(
    db: AsyncSession, start: date, end: date, model_version: str
) -> list[LearningExample]:
    start_at = cat_day_bounds_utc(start)[0]
    end_at = cat_day_bounds_utc(end)[1]
    result = await db.execute(
        select(Prediction, Match)
        .join(Match, Prediction.match_id == Match.id)
        .where(
            Prediction.model_version == model_version,
            Prediction.created_at < Match.kickoff_at,
            Match.status == MatchStatus.FINISHED,
            Match.kickoff_at >= start_at,
            Match.kickoff_at < end_at,
            Match.home_goals.is_not(None),
            Match.away_goals.is_not(None),
        )
        .order_by(Prediction.created_at.desc(), Prediction.id.desc())
    )
    latest: dict[tuple[int, str, str], LearningExample] = {}
    for prediction, match in result.all():
        key = (prediction.match_id, prediction.market, prediction.selection)
        if key in latest:
            continue
        try:
            outcome = evaluate_selection(prediction.market, match.home_goals, match.away_goals)
        except ValueError:
            continue
        if outcome == SelectionResult.VOID:
            continue
        odds = prediction.source_decimal_odds
        if (
            prediction.source_odds_at is None
            or prediction.source_odds_at >= match.kickoff_at
            or odds is None
            or odds <= 1.0
        ):
            odds = None
        probabilities = {
            "poisson": prediction.poisson_prob,
            "zinb": prediction.zinb_prob,
            "bayes": prediction.bayes_prob,
            "elo": prediction.elo_prob,
            "xg": prediction.xg_prob,
        }
        model_probabilities = {
            name: float(probability)
            for name, probability in probabilities.items()
            if probability is not None
        }
        if len(model_probabilities) < 3:
            continue
        latest[key] = LearningExample(
            match_id=prediction.match_id,
            market=prediction.market,
            kickoff_at=match.kickoff_at,
            outcome=1.0 if outcome == SelectionResult.WON else 0.0,
            incumbent_probability=float(prediction.model_probability),
            model_probabilities=model_probabilities,
            decimal_odds=float(odds) if odds is not None else None,
        )
    return list(latest.values())


async def train_learning_challenger(
    db: AsyncSession,
    *,
    base_model_version: str,
    train_start: date,
    train_end: date,
    validation_start: date,
    validation_end: date,
    challenger_version: str | None = None,
    min_train_samples: int = 100,
    min_validation_samples: int = 30,
    min_validation_bets: int = 10,
    regularization: float = 0.25,
    min_brier_improvement: float = 0.0025,
    max_calibration_regression: float = 0.01,
    max_roi_regression: float = 0.02,
) -> ModelLearningRun:
    if not (train_start <= train_end < validation_start <= validation_end):
        raise ValueError("Learning windows must be ordered and non-overlapping")
    lineage_root = base_model_version.split("-l", 1)[0]
    challenger_version = challenger_version or f"{lineage_root}-l{validation_end:%y%m%d}"
    existing = await db.execute(
        select(ModelLearningRun).where(ModelLearningRun.challenger_version == challenger_version)
    )
    if existing.scalar_one_or_none():
        raise ValueError(f"Challenger version already exists: {challenger_version}")

    config = {
        "strict_no_lookahead": True,
        "minimum_active_models": 3,
        "min_train_samples": min_train_samples,
        "min_validation_samples": min_validation_samples,
        "min_validation_bets": min_validation_bets,
        "minimum_edge": settings.min_selection_edge,
        "regularization": regularization,
        "min_brier_improvement": min_brier_improvement,
        "max_calibration_regression": max_calibration_regression,
        "max_roi_regression": max_roi_regression,
        "default_weights": DEFAULT_WEIGHTS,
    }
    learning_run = ModelLearningRun(
        base_model_version=base_model_version,
        challenger_version=challenger_version,
        status=RunStatus.RUNNING,
        train_start=train_start,
        train_end=train_end,
        validation_start=validation_start,
        validation_end=validation_end,
        config_snapshot=config,
    )
    db.add(learning_run)
    await db.flush()

    try:
        train_examples = await _load_learning_examples(db, train_start, train_end, base_model_version)
        validation_examples = await _load_learning_examples(
            db, validation_start, validation_end, base_model_version
        )
        train_by_market: dict[str, list[LearningExample]] = {}
        validation_by_market: dict[str, list[LearningExample]] = {}
        for example in train_examples:
            train_by_market.setdefault(example.market, []).append(example)
        for example in validation_examples:
            validation_by_market.setdefault(example.market, []).append(example)

        ready = 0
        markets = sorted(set(train_by_market) | set(validation_by_market))
        for market in markets:
            train_rows = train_by_market.get(market, [])
            validation_rows = validation_by_market.get(market, [])
            profile = MarketLearningProfile(
                learning_run_id=learning_run.id,
                market=market,
                train_sample_size=len(train_rows),
                validation_sample_size=len(validation_rows),
                weights=dict(DEFAULT_WEIGHTS),
                calibrator={"type": "identity"},
            )
            if len(train_rows) < min_train_samples or len(validation_rows) < min_validation_samples:
                profile.status = "insufficient_evidence"
                profile.rejection_reason = (
                    f"requires {min_train_samples}/{min_validation_samples} train/validation rows; "
                    f"found {len(train_rows)}/{len(validation_rows)}"
                )
            elif len({example.outcome for example in train_rows}) < 2:
                profile.status = "insufficient_outcome_variation"
                profile.rejection_reason = "training period contains only one outcome class"
            else:
                fitted = fit_market_challenger(
                    train_rows,
                    validation_rows,
                    minimum_edge=settings.min_selection_edge,
                    regularization=regularization,
                    min_validation_bets=min_validation_bets,
                    min_brier_improvement=min_brier_improvement,
                    max_calibration_regression=max_calibration_regression,
                    max_roi_regression=max_roi_regression,
                )
                profile.weights = fitted["weights"]
                profile.calibrator = fitted["calibrator"]
                profile.train_metrics = fitted["train_metrics"]
                profile.validation_metrics = fitted["validation_metrics"]
                profile.promotion_checks = fitted["promotion_checks"]
                profile.status = "shadow_ready" if fitted["shadow_ready"] else "validation_rejected"
                if fitted["shadow_ready"]:
                    ready += 1
                else:
                    failed = [name for name, passed in fitted["promotion_checks"].items() if not passed]
                    profile.rejection_reason = "failed checks: " + ", ".join(failed)
            db.add(profile)

        learning_run.status = RunStatus.COMPLETED
        learning_run.summary = {
            "markets_evaluated": len(markets),
            "shadow_ready_markets": ready,
            "rejected_markets": len(markets) - ready,
            "train_rows": len(train_examples),
            "validation_rows": len(validation_examples),
            "promotion_requires_explicit_action": True,
        }
        learning_run.completed_at = datetime.now(timezone.utc)
        await db.flush()
        return learning_run
    except Exception as exc:
        learning_run.status = RunStatus.FAILED
        learning_run.error_details = str(exc)
        learning_run.completed_at = datetime.now(timezone.utc)
        await db.flush()
        raise


async def promote_learning_run(
    db: AsyncSession,
    *,
    learning_run_id: int,
    effective_from: date,
    promoted_by: str,
    reason: str,
) -> ModelLearningPromotion:
    result = await db.execute(
        select(ModelLearningRun)
        .where(ModelLearningRun.id == learning_run_id)
        .options(selectinload(ModelLearningRun.profiles))
    )
    learning_run = result.scalar_one_or_none()
    if learning_run is None:
        raise ValueError("Learning run not found")
    if learning_run.status != RunStatus.COMPLETED:
        raise ValueError("Only completed learning runs can be promoted")
    if effective_from <= learning_run.validation_end:
        raise ValueError("Promotion must become effective after the validation period")
    ready = [profile for profile in learning_run.profiles if profile.status == "shadow_ready"]
    if not ready:
        raise ValueError("No market profile passed all promotion checks")
    existing = await db.execute(
        select(ModelLearningPromotion).where(
            ModelLearningPromotion.learning_run_id == learning_run.id
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("Learning run has already been promoted")
    promotion = ModelLearningPromotion(
        learning_run_id=learning_run.id,
        base_model_version=learning_run.base_model_version,
        challenger_version=learning_run.challenger_version,
        effective_from=effective_from,
        promoted_by=promoted_by,
        reason=reason,
    )
    db.add(promotion)
    await db.flush()
    db.add(
        AuditEvent(
            entity_type="model_learning_promotion",
            entity_id=str(promotion.id),
            event_type="promoted",
            actor=promoted_by,
            reason=reason,
            payload={
                "base_model_version": learning_run.base_model_version,
                "challenger_version": learning_run.challenger_version,
                "effective_from": effective_from.isoformat(),
                "promoted_markets": sorted(profile.market for profile in ready),
            },
        )
    )
    return promotion


async def resolve_active_learning_config(
    db: AsyncSession, requested_model_version: str, target_date: date
) -> ActiveLearningConfig | None:
    # Follow the latest effective promotion at each lineage hop. This lets the
    # stable scheduled version resolve 0.2.1 -> learned-1 -> learned-2 without
    # changing deployment configuration or silently skipping a generation.
    current_version = requested_model_version
    promotion = None
    visited = {current_version}
    for _ in range(10):
        result = await db.execute(
            select(ModelLearningPromotion)
            .where(
                ModelLearningPromotion.effective_from <= target_date,
                ModelLearningPromotion.base_model_version == current_version,
            )
            .order_by(
                ModelLearningPromotion.effective_from.desc(),
                ModelLearningPromotion.id.desc(),
            )
            .limit(1)
        )
        next_promotion = result.scalar_one_or_none()
        if next_promotion is None or next_promotion.challenger_version in visited:
            break
        promotion = next_promotion
        current_version = promotion.challenger_version
        visited.add(current_version)
    if promotion is None:
        return None
    profile_result = await db.execute(
        select(MarketLearningProfile).where(
            MarketLearningProfile.learning_run_id == promotion.learning_run_id,
            MarketLearningProfile.status == "shadow_ready",
        )
    )
    profiles = {profile.market: profile for profile in profile_result.scalars().all()}
    return ActiveLearningConfig(
        promotion_id=promotion.id,
        learning_run_id=promotion.learning_run_id,
        base_model_version=promotion.base_model_version,
        challenger_version=promotion.challenger_version,
        profiles=profiles,
    )


async def learning_status(db: AsyncSession, limit: int = 10) -> dict:
    run_result = await db.execute(
        select(ModelLearningRun)
        .options(selectinload(ModelLearningRun.profiles))
        .order_by(ModelLearningRun.id.desc())
        .limit(limit)
    )
    promotion_result = await db.execute(
        select(ModelLearningPromotion)
        .order_by(ModelLearningPromotion.effective_from.desc(), ModelLearningPromotion.id.desc())
    )
    runs = []
    for run in run_result.scalars().all():
        runs.append(
            {
                "id": run.id,
                "base_model_version": run.base_model_version,
                "challenger_version": run.challenger_version,
                "status": run.status.value,
                "train_period": [run.train_start.isoformat(), run.train_end.isoformat()],
                "validation_period": [run.validation_start.isoformat(), run.validation_end.isoformat()],
                "summary": run.summary,
                "profiles": [
                    {
                        "id": profile.id,
                        "market": profile.market,
                        "status": profile.status,
                        "train_sample_size": profile.train_sample_size,
                        "validation_sample_size": profile.validation_sample_size,
                        "weights": profile.weights,
                        "calibrator": profile.calibrator,
                        "validation_metrics": profile.validation_metrics,
                        "promotion_checks": profile.promotion_checks,
                        "rejection_reason": profile.rejection_reason,
                    }
                    for profile in sorted(run.profiles, key=lambda item: item.market)
                ],
            }
        )
    promotions = [
        {
            "id": promotion.id,
            "learning_run_id": promotion.learning_run_id,
            "base_model_version": promotion.base_model_version,
            "challenger_version": promotion.challenger_version,
            "effective_from": promotion.effective_from.isoformat(),
            "promoted_by": promotion.promoted_by,
            "reason": promotion.reason,
        }
        for promotion in promotion_result.scalars().all()
    ]
    return {"runs": runs, "promotions": promotions}
