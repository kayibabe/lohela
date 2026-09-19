"""An unsuccessful optimizer must never become a usable probability model."""
from types import SimpleNamespace

import numpy as np
import pytest

from app.services.models.poisson_dc import PoissonDixonColes, TeamParams


@pytest.mark.parametrize("success, objective, parameters", [
    (False, 10.0, np.zeros(6)),
    (True, float("nan"), np.zeros(6)),
    (True, 10.0, np.full(6, float("nan"))),
])
def test_failed_fit_invalidates_previous_model(monkeypatch, success, objective, parameters):
    monkeypatch.setattr("scipy.optimize.minimize", lambda *a, **kw: SimpleNamespace(
        success=success, fun=objective, x=parameters, message="test optimizer failure",
    ))
    model = PoissonDixonColes()
    model._fitted = True
    model.team_params = {1: TeamParams(), 2: TeamParams()}
    with pytest.raises(RuntimeError, match="Poisson-DC fit failed"):
        model.fit([{"home_team_id": 1, "away_team_id": 2, "home_goals": 1, "away_goals": 0}])
    with pytest.raises(RuntimeError, match="Model not fitted"):
        model.predict(1, 2, "home_win")
    assert model.team_params == {}


def test_empty_fit_is_not_usable():
    model = PoissonDixonColes()
    with pytest.raises(ValueError, match="requires historical matches"):
        model.fit([])
    with pytest.raises(RuntimeError, match="Model not fitted"):
        model.predict(1, 2, "home_win")
