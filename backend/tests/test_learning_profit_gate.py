"""Profitability and information-time regression coverage for learning."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import RunStatus
from app.services import model_learning
from app.services.model_learning import (
    _eligible_learning_quote,
    _positive_profit_evidence,
    promote_learning_run,
)


@pytest.mark.parametrize("metrics", [
    {}, {"roi": -0.1, "roi_confidence_interval_95": [-0.2, 0.1]},
    {"roi": 0.1, "roi_confidence_interval_95": [-0.1, 0.3]},
    {"roi": 0.1, "roi_confidence_interval_95": [0, 0.3]},
    {"roi": float("nan"), "roi_confidence_interval_95": [0.1, 0.3]},
    {"roi": 0.1, "roi_confidence_interval_95": [0.2, float("inf")]},
    {"roi": 0.1, "roi_confidence_interval_95": [0.3, 0.2]},
])
def test_missing_uncertain_or_nonprofitable_evidence_fails(metrics):
    assert not _positive_profit_evidence(metrics)


def test_positive_bounded_profit_evidence_passes():
    assert _positive_profit_evidence({"roi": 0.12, "roi_confidence_interval_95": [0.01, 0.25]})


def test_improving_a_losing_incumbent_does_not_make_challenger_ready(monkeypatch):
    monkeypatch.setattr(model_learning, "_fit_regularized_weights", lambda *args: {})
    monkeypatch.setattr(model_learning, "_weighted_probability", lambda *args: 0.7)
    monkeypatch.setattr(model_learning, "_fit_calibrator", lambda *args: {"type": "identity"})
    incumbent = {"brier_score": 0.3, "calibration_error": 0.1, "eligible_bets": 50,
                 "roi": -0.3, "roi_confidence_interval_95": [-0.5, -0.1]}
    challenger = {**incumbent, "brier_score": 0.2, "roi": -0.1,
                  "roi_confidence_interval_95": [-0.3, 0.1]}
    metrics = iter([incumbent, challenger, incumbent, challenger])
    monkeypatch.setattr(model_learning, "_metric_bundle", lambda *args: next(metrics))
    rows = [SimpleNamespace(outcome=1, incumbent_probability=0.6)]
    result = model_learning.fit_market_challenger(rows, rows, minimum_edge=0.03,
        regularization=0.1, min_validation_bets=10, min_brier_improvement=0.0025,
        max_calibration_regression=0.01, max_roi_regression=0.02)
    assert result["promotion_checks"]["roi_not_worse"]
    assert not result["promotion_checks"]["positive_roi_evidence"]
    assert not result["shadow_ready"]


@pytest.mark.parametrize("quote_offset,odds,expected", [
    (-1, 1.6, True), (0, 1.6, True), (1, 1.6, False),
    (-1, float("nan"), False), (-1, float("inf"), False), (-1, 1.0, False),
])
def test_quote_must_exist_by_decision_time(quote_offset, odds, expected):
    information = datetime(2026, 9, 1, 12, tzinfo=timezone.utc)
    prediction = SimpleNamespace(as_of_at=None, created_at=information,
        source_odds_at=information + timedelta(minutes=quote_offset), source_decimal_odds=odds)
    assert _eligible_learning_quote(prediction, SimpleNamespace(kickoff_at=information + timedelta(hours=1))) is expected


@pytest.mark.asyncio
async def test_legacy_shadow_ready_profile_cannot_bypass_profit_gate():
    run = SimpleNamespace(status=RunStatus.COMPLETED, validation_end=date(2026, 9, 1),
        profiles=[SimpleNamespace(status="shadow_ready", validation_metrics={
            "challenger": {"roi": -0.1, "roi_confidence_interval_95": [-0.3, 0.1]}})])
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: run)))
    with pytest.raises(ValueError, match="positive validation ROI"):
        await promote_learning_run(db, learning_run_id=1, effective_from=date(2026, 9, 2),
                                   promoted_by="test", reason="test")
    assert db.execute.await_count == 1
