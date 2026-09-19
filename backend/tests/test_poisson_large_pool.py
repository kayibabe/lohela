"""A pooled multi-league fit must stay tractable as team count grows.

Regression for: joint MLE over every team's attack/defense pair stopped
converging ("TOTAL NO. of f AND g EVALUATIONS EXCEEDS LIMIT") once a real
pooled fit crossed several hundred teams, because most of them had only a
handful of matches each. Above LARGE_N_THRESHOLD, PoissonDixonColes.fit must
use closed-form per-team rates and only MLE-fit the two shared parameters.
"""
import math
import time

import numpy as np
import pytest

from app.services.models.poisson_dc import PoissonDixonColes


def _sparse_pool(n_teams: int, matches_per_team: int = 3) -> list[dict]:
    rng = np.random.default_rng(7)
    matches = []
    team_ids = list(range(n_teams))
    for _ in range(matches_per_team * n_teams // 2):
        home, away = rng.choice(team_ids, size=2, replace=False)
        matches.append({
            "home_team_id": int(home), "away_team_id": int(away),
            "home_goals": int(rng.poisson(1.4)), "away_goals": int(rng.poisson(1.1)),
        })
    return matches


def test_large_team_pool_converges_via_closed_form_fallback():
    n_teams = PoissonDixonColes.LARGE_N_THRESHOLD + 50
    matches = _sparse_pool(n_teams)
    model = PoissonDixonColes()
    start = time.time()
    model.fit(matches)
    elapsed = time.time() - start
    assert elapsed < 30, "large-n fallback should skip the intractable joint MLE"
    assert len(model.team_params) == len({m["home_team_id"] for m in matches}
                                         | {m["away_team_id"] for m in matches})
    assert math.exp(math.log(0.7)) <= model.home_advantage <= math.exp(math.log(1.7))
    assert -0.20 <= model.rho <= 0.05
    # Below the joint-MLE path, attack/defense must be the plain closed-form
    # rates (mean scored / mean conceded relative to league average), not
    # whatever the optimizer would have found.
    scored, conceded = {}, {}
    for m in matches:
        scored.setdefault(m["home_team_id"], []).append(m["home_goals"])
        conceded.setdefault(m["home_team_id"], []).append(m["away_goals"])
        scored.setdefault(m["away_team_id"], []).append(m["away_goals"])
        conceded.setdefault(m["away_team_id"], []).append(m["home_goals"])
    league_avg = np.mean([m["home_goals"] + m["away_goals"] for m in matches]) / 2
    sample_team = next(iter(model.team_params))
    expected_attack = max(0.05, float(np.mean(scored[sample_team])))
    expected_defense = max(0.05, float(np.mean(conceded[sample_team])) / league_avg)
    assert model.team_params[sample_team].attack == pytest.approx(expected_attack)
    assert model.team_params[sample_team].defense == pytest.approx(expected_defense)


def test_small_team_pool_still_uses_joint_mle(monkeypatch):
    calls = {"shared_only": 0}
    real_minimize = __import__("scipy.optimize", fromlist=["minimize"]).minimize

    def spy(fn, x0, **kwargs):
        if len(x0) == 2:
            calls["shared_only"] += 1
        return real_minimize(fn, x0, **kwargs)

    monkeypatch.setattr("scipy.optimize.minimize", spy)
    matches = _sparse_pool(10, matches_per_team=6)
    PoissonDixonColes().fit(matches)
    assert calls["shared_only"] == 0
