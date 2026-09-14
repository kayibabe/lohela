"""
xG-based probability model — spec §7, §8.

Applies Dixon-Coles Poisson to xG values rather than actual goals.
This smooths out randomness and serves as the 5th ensemble model.
"""

from app.services.models.poisson_dc import predict_market, Market


def xg_market_probability(home_xg: float, away_xg: float, market: Market, rho: float = -0.05) -> float:
    """
    Derive market probability from xG values using Poisson-DC.

    rho is softer here (-0.05) since xG already incorporates shot quality —
    the low-score correction from DC is less critical.
    """
    if home_xg <= 0:
        home_xg = 0.01
    if away_xg <= 0:
        away_xg = 0.01
    return predict_market(home_xg, away_xg, market, rho)
