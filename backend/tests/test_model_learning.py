"""Deterministic tests for the offline learning and promotion evidence logic."""

from datetime import datetime, timezone

import pytest

from app.services.model_learning import (
    LearningExample,
    apply_probability_calibrator,
    fit_market_challenger,
)


def _examples(size: int) -> list[LearningExample]:
    rows = []
    for index in range(size):
        won = index % 2 == 0
        rows.append(
            LearningExample(
                match_id=index + 1,
                market="over_2.5",
                kickoff_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                outcome=1.0 if won else 0.0,
                incumbent_probability=0.61 if won else 0.39,
                model_probabilities={
                    "poisson": 0.88 if won else 0.12,
                    "zinb": 0.55 if won else 0.45,
                    "bayes": 0.54 if won else 0.46,
                    "elo": 0.50,
                    "xg": 0.56 if won else 0.44,
                },
                decimal_odds=2.0,
            )
        )
    return rows


def test_challenger_uses_untouched_validation_metrics_and_can_pass_all_checks():
    result = fit_market_challenger(
        _examples(160),
        _examples(60),
        minimum_edge=0.03,
        regularization=0.10,
        min_validation_bets=10,
        min_brier_improvement=0.0025,
        max_calibration_regression=0.01,
        max_roi_regression=0.02,
    )

    assert sum(result["weights"].values()) == pytest.approx(1.0)
    assert result["weights"]["poisson"] > 0.30
    assert result["validation_metrics"]["challenger"]["brier_score"] < result["validation_metrics"]["incumbent"]["brier_score"]
    assert result["shadow_ready"] is True
    assert all(result["promotion_checks"].values())


def test_challenger_is_rejected_when_priced_validation_evidence_is_too_small():
    validation = [
        LearningExample(**{**row.__dict__, "decimal_odds": None})
        for row in _examples(60)
    ]
    result = fit_market_challenger(
        _examples(160),
        validation,
        minimum_edge=0.03,
        regularization=0.10,
        min_validation_bets=10,
        min_brier_improvement=0.0025,
        max_calibration_regression=0.01,
        max_roi_regression=0.02,
    )

    assert result["promotion_checks"]["enough_priced_bets"] is False
    assert result["shadow_ready"] is False


def test_platt_calibrator_is_bounded_and_identity_is_stable():
    assert apply_probability_calibrator(0.75, {"type": "identity"}) == pytest.approx(0.75)
    calibrated = apply_probability_calibrator(
        0.75, {"type": "platt_logit", "slope": 1.2, "intercept": -0.1}
    )
    assert 0.0 < calibrated < 1.0
