"""Goal-scale and defense-direction regressions for both estimator paths."""

import numpy as np
import pytest
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

from app.services.models.bayesian import (
    _export_advi_posterior,
    _run_analytical,
    predict_market_from_posteriors,
)
from app.services.models.poisson_dc import predict_market


def test_analytical_preserves_observed_goal_environment():
    rows = [
        {"home_team_id": 1, "away_team_id": 2, "home_goals": goals, "away_goals": goals}
        for goals in (1, 2, 1, 2)
    ]
    estimates = _run_analytical(rows)
    home, away = estimates[1], estimates[2]
    probability = predict_market_from_posteriors(
        home["attack_mean"], home["defense_mean"],
        away["attack_mean"], away["defense_mean"], 1.0, "over_2.5",
    )
    assert probability == pytest.approx(predict_market(1.5, 1.5, "over_2.5", -0.08))


def test_analytical_all_scoreless_history_is_finite():
    estimates = _run_analytical([
        {"home_team_id": 1, "away_team_id": 2, "home_goals": 0, "away_goals": 0}
    ])
    assert all(np.isfinite(value) for row in estimates.values() for value in row.values())
    assert estimates[1]["attack_mean"] == 0.0
    assert _run_analytical([]) == {}


def test_advi_export_matches_fitted_goal_intensity():
    home = _export_advi_posterior(np.array([np.log(1.8)]), np.array([np.log(2.0)]))
    away = _export_advi_posterior(np.array([np.log(1.2)]), np.array([np.log(1.5)]))
    probability = predict_market_from_posteriors(
        home["attack_mean"], home["defense_mean"],
        away["attack_mean"], away["defense_mean"], 1.0, "over_2.5",
    )
    assert probability == pytest.approx(predict_market(1.8 / 1.5, 1.2 / 2.0, "over_2.5", -0.08))


def test_advi_stronger_defense_reduces_opponent_goal_rate():
    weak = _export_advi_posterior(np.array([0.0]), np.array([-0.2, 0.0, 0.2]))
    strong = _export_advi_posterior(np.array([0.0]), np.array([0.8, 1.0, 1.2]))
    assert strong["defense_mean"] < weak["defense_mean"]
    assert strong["defense_std"] == pytest.approx(np.std(np.exp(-np.array([0.8, 1.0, 1.2]))))


@pytest.mark.asyncio
async def test_live_runner_rejects_past_date_before_database_access():
    from app.config import cat_today
    from app.services.model_runner import ModelRunner

    db = AsyncMock()
    with pytest.raises(ValueError, match="Past-date scoring"):
        await ModelRunner(db).run(cat_today() - timedelta(days=1))
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_runner_history_query_excludes_future_results():
    from app.services.model_runner import ModelRunner

    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    db = AsyncMock()
    db.execute.return_value = result
    assert await ModelRunner(db)._load_historical_matches() == []
    statement = db.execute.call_args.args[0]
    assert "matches.kickoff_at <" in str(statement)
