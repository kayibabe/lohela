"""run_mcmc_update must report which estimation path actually produced its
result, not just whether it was called.

ADVI failure falls back to the analytical estimator silently inside
run_mcmc_update. Before this fix, every caller assumed success meant ADVI had
run — /models/run-bayesian?method=advi, /models/calibrate, and the automated
pipeline's ModelPreparationService all reported "Bayesian" results with no
way to tell a real PyMC fit from the closed-form fallback.
"""

from unittest.mock import patch

import pytest

from app.services.models.bayesian import _run_analytical, run_mcmc_update

HISTORICAL = [
    {"home_team_id": 1, "away_team_id": 2, "home_goals": 2, "away_goals": 1},
    {"home_team_id": 2, "away_team_id": 1, "home_goals": 1, "away_goals": 0},
] * 10


def test_empty_input_reports_analytical_with_no_posteriors():
    method, posteriors = run_mcmc_update([])
    assert method == "analytical"
    assert posteriors == {}


def test_advi_failure_reports_analytical_not_advi():
    """The silent fallback this test pins: a caller must be told the truth."""
    with patch(
        "app.services.models.bayesian._run_advi",
        side_effect=RuntimeError("no g++ in this image"),
    ):
        method, posteriors = run_mcmc_update(HISTORICAL)

    assert method == "analytical"
    assert posteriors == _run_analytical(HISTORICAL)


def test_advi_success_reports_advi():
    fake_posteriors = {1: {"attack_mean": 1.0, "attack_std": 0.1, "defense_mean": 1.0, "defense_std": 0.1}}
    with patch("app.services.models.bayesian._run_advi", return_value=fake_posteriors):
        method, posteriors = run_mcmc_update(HISTORICAL)

    assert method == "advi"
    assert posteriors == fake_posteriors


@pytest.mark.asyncio
async def test_model_preparation_defaults_to_analytical_and_reports_it(monkeypatch):
    """Settings.bayesian_estimation_method defaults to "analytical"; prepare() must
    say so in its result rather than leaving the caller to assume."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.model_preparation import ModelPreparationService

    match = SimpleNamespace(
        home_team_id=1, away_team_id=2, home_goals=2, away_goals=1,
    )

    class _Rows:
        def __init__(self, rows):
            self._rows = rows

        def scalars(self):
            return self

        def all(self):
            return self._rows

    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Rows([match] * 10), _Rows([])]),
        flush=AsyncMock(),
    )
    service = ModelPreparationService(db)

    with patch(
        "app.services.model_preparation.EloUpdater.update_all",
        new=AsyncMock(return_value={"matches_processed": 10}),
    ):
        from datetime import date

        result = await service.prepare(date(2026, 1, 1))

    assert result["bayesian_method"] == "analytical"


def test_prepare_reports_none_method_when_no_history():
    """The no-history early return keeps result shape consistent with the trained path."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.services.model_preparation import ModelPreparationService

    class _Rows:
        def scalars(self):
            return self

        def all(self):
            return []

    db = SimpleNamespace(execute=AsyncMock(return_value=_Rows()))
    service = ModelPreparationService(db)

    from datetime import date

    result = asyncio.run(service.prepare(date(2026, 1, 1)))
    assert result == {
        "historical_matches": 0,
        "teams_prepared": 0,
        "elo_matches": 0,
        "bayesian_method": None,
    }
