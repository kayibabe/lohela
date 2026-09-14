"""
Zero-Inflated Negative Binomial (ZINB) model — spec §25.2.

Activated when variance/mean > 1.5 for a league's goal distribution.
Falls back to standard Poisson otherwise.
"""

import numpy as np
from scipy.stats import nbinom
from scipy.optimize import minimize


class ZINBModel:
    """
    Fits a ZINB model to goal-scoring data for a single team/league.

    Parameters
    ----------
    pi : float
        Zero-inflation probability (P of structural zero)
    r  : float
        Negative binomial dispersion parameter
    p  : float
        Negative binomial success probability
    """

    def __init__(self) -> None:
        self.pi: float = 0.1
        self.r: float = 5.0
        self.p: float = 0.5
        self._fitted = False

    def fit(self, goals: list[int]) -> "ZINBModel":
        """Fit ZINB to observed goal counts. Minimum 30 observations recommended."""
        y = np.array(goals, dtype=float)
        mean = y.mean()
        var = y.var()

        if var / mean <= 1.5:
            # Doesn't warrant ZINB — caller should use Poisson
            self._fitted = True
            return self

        def neg_log_likelihood(params):
            pi_, log_r, logit_p = params
            pi_ = 1 / (1 + np.exp(-pi_))  # sigmoid
            r_ = np.exp(log_r)
            p_ = 1 / (1 + np.exp(-logit_p))

            ll = 0.0
            for yi in y:
                if yi == 0:
                    p_zero = pi_ + (1 - pi_) * nbinom.pmf(0, r_, p_)
                    ll += np.log(p_zero + 1e-10)
                else:
                    p_pos = (1 - pi_) * nbinom.pmf(int(yi), r_, p_)
                    ll += np.log(p_pos + 1e-10)
            return -ll

        x0 = [0.0, np.log(5.0), 0.0]
        result = minimize(neg_log_likelihood, x0, method="Nelder-Mead",
                          options={"maxiter": 2000, "xatol": 1e-6})
        pi_raw, log_r, logit_p = result.x
        self.pi = 1 / (1 + np.exp(-pi_raw))
        self.r = np.exp(log_r)
        self.p = 1 / (1 + np.exp(-logit_p))
        self._fitted = True
        return self

    def pmf(self, k: int) -> float:
        """P(goals = k) under ZINB model."""
        if not self._fitted:
            raise RuntimeError("Not fitted")
        if k == 0:
            return float(self.pi + (1 - self.pi) * nbinom.pmf(0, self.r, self.p))
        return float((1 - self.pi) * nbinom.pmf(k, self.r, self.p))

    def predict_over(self, threshold: float) -> float:
        """P(goals > threshold) — e.g. threshold=1.5 → P(goals >= 2)."""
        k_min = int(threshold) + 1
        p_over = sum(self.pmf(k) for k in range(k_min, 15))
        return min(p_over, 1.0)


def should_use_zinb(goals: list[int]) -> bool:
    """Return True if league's goal variance/mean > 1.5 — spec §25.2."""
    if len(goals) < 20:
        return False
    arr = np.array(goals, dtype=float)
    mean = arr.mean()
    return bool(arr.var() / mean > 1.5) if mean > 0 else False
