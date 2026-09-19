"""
Poisson-Dixon-Coles model — spec §25.1.

Models goal scoring as independent Poisson processes with Dixon-Coles
correction for low-scoring scorelines (0-0, 1-0, 0-1, 1-1).

Usage:
    model = PoissonDixonColes()
    model.fit(historical_matches)
    probs = model.predict_market(home_team, away_team, market="over_1.5")
"""

import math
import numpy as np
from dataclasses import dataclass
from typing import Literal


Market = Literal[
    "home_win", "draw", "away_win",
    "over_0.5", "over_1.5", "over_2.5", "over_3.5", "over_4.5",
    "under_0.5", "under_1.5", "under_2.5", "under_3.5", "under_4.5",
    "btts_yes", "btts_no",
    "double_chance_1x", "double_chance_x2", "double_chance_12",
    "dnb_home", "dnb_away",
]

MAX_GOALS = 10  # upper truncation for Poisson sum


@dataclass
class TeamParams:
    attack: float = 1.0
    defense: float = 1.0


def _tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles correction factor for low-scoring matches."""
    if x == 0 and y == 0:
        return 1.0 - lam * mu * rho
    if x == 1 and y == 0:
        return 1.0 + mu * rho
    if x == 0 and y == 1:
        return 1.0 + lam * rho
    if x == 1 and y == 1:
        return 1.0 - rho
    return 1.0


def _poisson_pmf(k: int, lam: float) -> float:
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def score_matrix(lam: float, mu: float, rho: float = -0.10) -> np.ndarray:
    """
    Returns (MAX_GOALS+1) x (MAX_GOALS+1) matrix of P(home=i, away=j).
    rho: Dixon-Coles correlation parameter, typically -0.05 to -0.15 (spec §25.1).
    """
    mat = np.zeros((MAX_GOALS + 1, MAX_GOALS + 1))
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            mat[i, j] = (
                max(0.0, _tau(i, j, lam, mu, rho))
                * _poisson_pmf(i, lam)
                * _poisson_pmf(j, mu)
            )
    # Normalise — small correction for truncation
    total = mat.sum()
    if total > 0:
        mat /= total
    return mat


def predict_market(
    lam: float,
    mu: float,
    market: Market,
    rho: float = -0.10,
) -> float:
    """Compute probability for a given market from Poisson parameters."""
    mat = score_matrix(lam, mu, rho)

    if market == "home_win":
        return float(np.tril(mat, k=-1).sum())
    if market == "draw":
        return float(np.trace(mat))
    if market == "away_win":
        return float(np.triu(mat, k=1).sum())

    # Over / Under total goals
    for threshold in (0.5, 1.5, 2.5, 3.5, 4.5):
        if market == f"over_{threshold}":
            # P(home + away > threshold)
            total_prob = 0.0
            for i in range(MAX_GOALS + 1):
                for j in range(MAX_GOALS + 1):
                    if i + j > threshold:
                        total_prob += mat[i, j]
            return total_prob
        if market == f"under_{threshold}":
            total_prob = 0.0
            for i in range(MAX_GOALS + 1):
                for j in range(MAX_GOALS + 1):
                    if i + j < threshold:
                        total_prob += mat[i, j]
            return total_prob

    if market == "btts_yes":
        return float(mat[1:, 1:].sum())
    if market == "btts_no":
        return float(1.0 - mat[1:, 1:].sum())

    if market == "double_chance_1x":
        return float(np.tril(mat, k=-1).sum() + np.trace(mat))
    if market == "double_chance_x2":
        return float(np.triu(mat, k=1).sum() + np.trace(mat))
    if market == "double_chance_12":
        return float(np.tril(mat, k=-1).sum() + np.triu(mat, k=1).sum())

    if market == "dnb_home":
        # Home win + stake returned on draw → probability of home win normalised over non-draw
        p_home = float(np.tril(mat, k=-1).sum())
        p_away = float(np.triu(mat, k=1).sum())
        return p_home / (p_home + p_away) if (p_home + p_away) > 0 else 0.5
    if market == "dnb_away":
        p_home = float(np.tril(mat, k=-1).sum())
        p_away = float(np.triu(mat, k=1).sum())
        return p_away / (p_home + p_away) if (p_home + p_away) > 0 else 0.5

    raise ValueError(f"Unknown market: {market}")


class PoissonDixonColes:
    """
    Fits attack/defense parameters per team from historical matches.
    Uses MLE via scipy.optimize.minimize.
    """

    # Above this many distinct teams, joint MLE over every attack/defense
    # pair stops converging reliably (see fit()); switch to closed-form team
    # rates plus a 2-parameter shared fit instead.
    LARGE_N_THRESHOLD = 150

    def __init__(self, rho: float = -0.10, home_advantage: float = 1.20) -> None:
        self.rho = rho
        self.home_advantage = home_advantage  # multiplicative factor on λ
        self.team_params: dict[int, TeamParams] = {}
        self._fitted = False

    def fit(self, matches: list[dict]) -> "PoissonDixonColes":
        """
        matches: list of {"home_team_id": int, "away_team_id": int,
                          "home_goals": int, "away_goals": int}
        Minimum 38 matches per team recommended (spec §8).
        """
        from scipy.optimize import minimize
        import numpy as np

        # A failed refit must not leave an older fit available for prediction.
        self._fitted = False
        self.team_params = {}
        if not matches:
            raise ValueError("Poisson-DC fitting requires historical matches")

        team_ids = list({m["home_team_id"] for m in matches} | {m["away_team_id"] for m in matches})
        idx = {t: i for i, t in enumerate(team_ids)}
        n = len(team_ids)

        def params_to_vectors(x):
            attacks = np.exp(x[:n])
            defenses = np.exp(x[n:2*n])
            home_adv = np.exp(x[2*n])
            rho_ = x[2*n + 1]
            return attacks, defenses, home_adv, rho_

        def neg_log_likelihood(x):
            attacks, defenses, home_adv, rho_ = params_to_vectors(x)
            ll = 0.0
            for m in matches:
                hi, ai = idx[m["home_team_id"]], idx[m["away_team_id"]]
                lam = attacks[hi] * defenses[ai] * home_adv
                mu = attacks[ai] * defenses[hi]
                hg, ag = m["home_goals"], m["away_goals"]
                t = _tau(hg, ag, lam, mu, rho_)
                if t <= 0 or lam <= 0 or mu <= 0:
                    return 1e10
                ll += (
                    math.log(t)
                    + math.log(_poisson_pmf(hg, lam) + 1e-10)
                    + math.log(_poisson_pmf(ag, mu) + 1e-10)
                )
            return -ll

        # A zero-vector start forces the optimizer to discover every team's
        # scoring level from scratch, which needs far more function/gradient
        # evaluations as the team count grows — at pooled multi-league scale
        # (500+ teams, 1000+ free parameters) this can exceed L-BFGS-B's
        # evaluation budget before converging at all (observed: "TOTAL NO. of
        # f AND g EVALUATIONS EXCEEDS LIMIT" on a 564-team pooled fit). Warm-
        # starting from each team's own observed scoring rate — the same
        # closed-form estimate the analytical Bayesian path uses — puts the
        # optimizer near the optimum already, so it only needs to refine for
        # the Dixon-Coles low-score correction and shared home-advantage term.
        scored: dict[int, list[int]] = {}
        conceded: dict[int, list[int]] = {}
        for m in matches:
            h, a = m["home_team_id"], m["away_team_id"]
            hg, ag = m["home_goals"], m["away_goals"]
            scored.setdefault(h, []).append(hg)
            conceded.setdefault(h, []).append(ag)
            scored.setdefault(a, []).append(ag)
            conceded.setdefault(a, []).append(hg)
        league_avg = float(np.mean([m["home_goals"] + m["away_goals"] for m in matches]) / 2)
        league_avg = league_avg if league_avg > 0 else 1.3

        log_attack = np.zeros(n)
        log_defense = np.zeros(n)
        for team_id, i in idx.items():
            attack_rate = max(0.05, float(np.mean(scored.get(team_id, [league_avg]))))
            defense_multiplier = max(0.05, float(np.mean(conceded.get(team_id, [league_avg]))) / league_avg)
            log_attack[i] = np.clip(math.log(attack_rate), -2.5, 2.5)
            log_defense[i] = np.clip(math.log(defense_multiplier), -2.5, 2.5)

        if n > self.LARGE_N_THRESHOLD:
            # A joint MLE over 2n+2 parameters stops being tractable for
            # L-BFGS-B once a pooled multi-league fit crosses a few hundred
            # teams — most of those teams have only a handful of matches each,
            # which makes the likelihood surface flat/ill-conditioned in their
            # directions regardless of warm-starting (observed: still "TOTAL
            # NO. of f AND g EVALUATIONS EXCEEDS LIMIT" at 564 teams even from
            # the closed-form warm start below). Past this size, use the
            # closed-form per-team rates directly — the same estimator the
            # analytical Bayesian path already relies on — and only fit the
            # two SHARED parameters (home advantage, Dixon-Coles rho) by MLE.
            # That fit is 2-dimensional regardless of team count, so it stays
            # fast and convergent at any pool size.
            attacks_fixed = np.exp(log_attack)
            defenses_fixed = np.exp(log_defense)

            def neg_log_likelihood_shared(x) -> float:
                home_adv = math.exp(x[0])
                rho_ = x[1]
                ll = 0.0
                for m in matches:
                    hi, ai = idx[m["home_team_id"]], idx[m["away_team_id"]]
                    lam = attacks_fixed[hi] * defenses_fixed[ai] * home_adv
                    mu = attacks_fixed[ai] * defenses_fixed[hi]
                    hg, ag = m["home_goals"], m["away_goals"]
                    t = _tau(hg, ag, lam, mu, rho_)
                    if t <= 0 or lam <= 0 or mu <= 0:
                        return 1e10
                    ll += (
                        math.log(t)
                        + math.log(_poisson_pmf(hg, lam) + 1e-10)
                        + math.log(_poisson_pmf(ag, mu) + 1e-10)
                    )
                return -ll

            shared_result = minimize(
                neg_log_likelihood_shared,
                np.array([math.log(self.home_advantage), self.rho]),
                method="L-BFGS-B",
                bounds=[(math.log(0.7), math.log(1.7)), (-0.20, 0.05)],
                options={"maxiter": 200, "ftol": 1e-8},
            )
            if (not shared_result.success or not np.isfinite(shared_result.fun)
                    or not np.all(np.isfinite(shared_result.x))):
                raise RuntimeError(f"Poisson-DC shared-parameter fit failed: {shared_result.message}")

            self.home_advantage = float(math.exp(shared_result.x[0]))
            self.rho = float(shared_result.x[1])
            for team_id, i in idx.items():
                self.team_params[team_id] = TeamParams(
                    attack=float(attacks_fixed[i]),
                    defense=float(defenses_fixed[i]),
                )
            self._fitted = True
            return self

        x0 = np.zeros(2 * n + 2)
        x0[:n] = log_attack
        x0[n:2*n] = log_defense
        x0[2*n] = math.log(self.home_advantage)
        x0[2*n + 1] = self.rho

        # Bounds and mild regularisation are essential: sparse historical
        # samples otherwise let attack/defence pairs explode while preserving
        # the same likelihood, which can produce invalid Dixon-Coles factors.
        bounds = [(-2.5, 2.5)] * (2 * n) + [(math.log(0.7), math.log(1.7)), (-0.20, 0.05)]

        def regularised_objective(x):
            return neg_log_likelihood(x) + 0.01 * float(np.square(x[: 2 * n]).sum())

        result = minimize(
            regularised_objective,
            x0,
            method="L-BFGS-B",
            bounds=bounds,
            options={"maxiter": 1000, "ftol": 1e-8},
        )

        if (not result.success or not np.isfinite(result.fun)
                or not np.all(np.isfinite(result.x))):
            raise RuntimeError(f"Poisson-DC fit failed: {result.message}")

        attacks, defenses, home_adv, rho_ = params_to_vectors(result.x)
        self.home_advantage = float(home_adv)
        self.rho = float(rho_)

        for team_id, i in idx.items():
            self.team_params[team_id] = TeamParams(
                attack=float(attacks[i]),
                defense=float(defenses[i]),
            )
        self._fitted = True
        return self

    def predict(self, home_team_id: int, away_team_id: int, market: Market) -> float | None:
        """Return market probability or None if either team has no parameters."""
        if not self._fitted:
            raise RuntimeError("Model not fitted — call fit() first")

        home_p = self.team_params.get(home_team_id)
        away_p = self.team_params.get(away_team_id)
        if home_p is None or away_p is None:
            return None

        lam = home_p.attack * away_p.defense * self.home_advantage
        mu = away_p.attack * home_p.defense
        probability = predict_market(lam, mu, market, self.rho)
        return max(0.0, min(1.0, probability))
