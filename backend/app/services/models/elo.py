"""
Elo rating system with home advantage — spec §25.4.

K-factors:
  20 — standard league match
  30 — promotion/relegation matches
  40 — cup finals

Home advantage: +65 to +100 Elo points per league (calibrated, stored in
competitions.home_advantage_elo). Default: +75.
"""

import math
from dataclasses import dataclass


@dataclass
class EloResult:
    """Match outcome and updated Elo ratings."""
    home_new_elo: float
    away_new_elo: float
    expected_home: float  # E[S] for home team
    expected_away: float


def expected_score(elo_home: float, elo_away: float, home_advantage_elo: float = 75.0) -> float:
    """E[S] for home team — spec §25.4."""
    return 1.0 / (1.0 + 10.0 ** (-( (elo_home + home_advantage_elo) - elo_away) / 400.0))


def update_elos(
    elo_home: float,
    elo_away: float,
    home_goals: int,
    away_goals: int,
    k_factor: int = 20,
    home_advantage_elo: float = 75.0,
) -> EloResult:
    """Compute updated Elo ratings after a completed match."""
    e_home = expected_score(elo_home, elo_away, home_advantage_elo)
    e_away = 1.0 - e_home

    # Actual scores: 1 = win, 0.5 = draw, 0 = loss
    if home_goals > away_goals:
        s_home, s_away = 1.0, 0.0
    elif home_goals == away_goals:
        s_home, s_away = 0.5, 0.5
    else:
        s_home, s_away = 0.0, 1.0

    new_home = elo_home + k_factor * (s_home - e_home)
    new_away = elo_away + k_factor * (s_away - e_away)

    return EloResult(
        home_new_elo=new_home,
        away_new_elo=new_away,
        expected_home=e_home,
        expected_away=e_away,
    )


def elo_to_win_probabilities(
    elo_home: float,
    elo_away: float,
    home_advantage_elo: float = 75.0,
    draw_factor: float = 0.25,
) -> dict[str, float]:
    """
    Convert Elo ratings into 1X2 market probabilities.
    draw_factor: fraction of probability mass allocated to draw (approximate).
    """
    p_home_no_draw = expected_score(elo_home, elo_away, home_advantage_elo)
    # Allocate draw probability proportionally
    p_draw = draw_factor
    p_home = p_home_no_draw * (1.0 - draw_factor)
    p_away = (1.0 - p_home_no_draw) * (1.0 - draw_factor)
    # Renormalise
    total = p_home + p_draw + p_away
    return {
        "home_win": p_home / total,
        "draw": p_draw / total,
        "away_win": p_away / total,
    }
