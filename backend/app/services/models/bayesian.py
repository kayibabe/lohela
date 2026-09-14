"""
Bayesian inference layer — spec §25.3.

Two estimation paths update team attack/defense posteriors, never at
per-match prediction time:

- "analytical": a closed-form MAP estimate from goal tallies relative to
  league average. Deterministic and sub-second, which matters for the
  backtester's no-lookahead reproducibility guarantee.
- "advi": real PyMC variational inference over a partial-pooling model.
  Much slower and, being a stochastic fit, not bit-for-bit reproducible
  run to run. Falls back to "analytical" if PyMC raises (e.g. no g++).

Settings.bayesian_estimation_method selects which one the automated pipeline
uses (app/services/model_preparation.py); POST /models/run-bayesian and
POST /models/calibrate can invoke either on demand regardless of that
default. Whichever path actually ran is reported back by run_mcmc_update
rather than assumed, since ADVI failure is a silent fallback.

Posterior means are stored in teams.bayes_attack_mean / bayes_defense_mean.
At prediction time, stored posterior means are used directly (fast path).
"""

import logging
import numpy as np

logger = logging.getLogger(__name__)


def predict_market_from_posteriors(
    home_attack_mean: float,
    home_defense_mean: float,
    away_attack_mean: float,
    away_defense_mean: float,
    home_advantage: float,
    market: str,
    rho: float = -0.08,
) -> float:
    """
    Fast prediction path using stored posterior means.
    Delegates to Poisson-DC with Bayesian-derived parameters.
    """
    from app.services.models.poisson_dc import predict_market, Market
    lam = home_attack_mean * away_defense_mean * home_advantage
    mu = away_attack_mean * home_defense_mean
    return predict_market(lam, mu, market, rho)  # type: ignore[arg-type]


def run_mcmc_update(
    historical_matches: list[dict],
) -> tuple[str, dict[int, dict[str, float]]]:
    """
    Bayesian team rating update — spec §25.3.

    Tries PyMC ADVI first (fast variational inference, single-process so it
    works inside Docker without a C compiler). Falls back to analytical
    Dixon-Coles-style MAP estimation if PyMC is unavailable or fails.

    historical_matches: list of {
        "home_team_id": int, "away_team_id": int,
        "home_goals": int, "away_goals": int
    }

    Returns (method, posteriors): `method` is "advi" or "analytical",
    naming whichever path actually produced the result — the ADVI fallback
    is silent otherwise, which is how "Bayesian" model runs have reported
    success while quietly never running PyMC.
    posteriors: {team_id: {"attack_mean": float, "attack_std": float,
                            "defense_mean": float, "defense_std": float}}
    """
    if not historical_matches:
        return "analytical", {}

    try:
        return "advi", _run_advi(historical_matches)
    except Exception as exc:
        logger.warning("PyMC ADVI failed (%s) — falling back to analytical estimation", exc)
        return "analytical", _run_analytical(historical_matches)


def _run_advi(historical_matches: list[dict]) -> dict[int, dict[str, float]]:
    """ADVI variational inference via PyMC — much faster than MCMC, Docker-friendly."""
    import pymc as pm  # type: ignore
    import pytensor  # type: ignore
    import numpy as np

    # Suppress g++ warning — we accept Python-mode performance
    pytensor.config.cxx = ""

    team_ids = list(
        {m["home_team_id"] for m in historical_matches}
        | {m["away_team_id"] for m in historical_matches}
    )
    idx = {t: i for i, t in enumerate(team_ids)}
    n = len(team_ids)

    home_idx = np.array([idx[m["home_team_id"]] for m in historical_matches])
    away_idx = np.array([idx[m["away_team_id"]] for m in historical_matches])
    home_goals = np.array([m["home_goals"] for m in historical_matches])
    away_goals = np.array([m["away_goals"] for m in historical_matches])

    with pm.Model():
        mu_attack = pm.Normal("mu_attack", mu=0, sigma=1)
        mu_defense = pm.Normal("mu_defense", mu=0, sigma=1)
        sigma_attack = pm.HalfNormal("sigma_attack", sigma=0.5)
        sigma_defense = pm.HalfNormal("sigma_defense", sigma=0.5)

        attack = pm.Normal("attack", mu=mu_attack, sigma=sigma_attack, shape=n)
        defense = pm.Normal("defense", mu=mu_defense, sigma=sigma_defense, shape=n)
        home_adv = pm.Normal("home_adv", mu=0.2, sigma=0.1)

        lam = pm.math.exp(attack[home_idx] - defense[away_idx] + home_adv)
        mu_goals = pm.math.exp(attack[away_idx] - defense[home_idx])

        pm.Poisson("home_goals_obs", mu=lam, observed=home_goals)
        pm.Poisson("away_goals_obs", mu=mu_goals, observed=away_goals)

        # ADVI: fast variational inference, no multiprocessing needed
        approx = pm.fit(n=10000, method="advi", progressbar=False)
        samples = approx.sample(500)

    results: dict[int, dict[str, float]] = {}
    for team_id, i in idx.items():
        attack_s = samples.posterior["attack"].values[:, :, i].flatten()
        defense_s = samples.posterior["defense"].values[:, :, i].flatten()
        results[team_id] = {
            "attack_mean": float(np.mean(np.exp(attack_s))),
            "attack_std": float(np.std(np.exp(attack_s))),
            "defense_mean": float(np.mean(np.exp(defense_s))),
            "defense_std": float(np.std(np.exp(defense_s))),
        }

    logger.info("ADVI update complete for %d teams", len(results))
    return results


def _run_analytical(historical_matches: list[dict]) -> dict[int, dict[str, float]]:
    """
    Analytical fallback: compute attack/defense indices from goal tallies.

    attack  = goals_scored / league_avg_goals   (relative to average)
    defense = goals_conceded / league_avg_goals  (lower = better defence)
    std is approximated as 0.15 (tight) for teams with many matches.
    """
    import numpy as np

    scored: dict[int, list[int]] = {}
    conceded: dict[int, list[int]] = {}

    for m in historical_matches:
        h, a = m["home_team_id"], m["away_team_id"]
        hg, ag = m["home_goals"], m["away_goals"]
        scored.setdefault(h, []).append(hg)
        conceded.setdefault(h, []).append(ag)
        scored.setdefault(a, []).append(ag)
        conceded.setdefault(a, []).append(hg)

    all_goals = [m["home_goals"] + m["away_goals"] for m in historical_matches]
    league_avg = np.mean(all_goals) / 2 if all_goals else 1.3

    results: dict[int, dict[str, float]] = {}
    for team_id in scored:
        n_matches = len(scored[team_id])
        atk_mean = np.mean(scored[team_id]) / league_avg
        def_mean = np.mean(conceded[team_id]) / league_avg
        # More matches → tighter std
        std = max(0.05, 0.15 - 0.002 * n_matches)
        results[team_id] = {
            "attack_mean": float(atk_mean),
            "attack_std": float(std),
            "defense_mean": float(def_mean),
            "defense_std": float(std),
        }

    logger.info("Analytical estimation complete for %d teams", len(results))
    return results
