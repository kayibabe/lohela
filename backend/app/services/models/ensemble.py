"""
Ensemble aggregation and Q-Score calculation — spec §25.5, §12, §27.

Ensemble weights (initial, to be calibrated via backtesting):
  Poisson-DC:  0.30
  ZINB:        0.15
  Bayesian:    0.20
  Elo:         0.15
  xG model:    0.20

Q-Score component weights (spec §12):
  Model Probability  25%
  Value / Edge       20%
  xG Model           15%
  Recent Form        10%
  Market Consensus   10%
  Odds Stability      5%
  Team News           5%
  League Reliability  5%
  Data Quality        5%
"""

import statistics
from dataclasses import dataclass, field
from typing import Optional

from app.models import QGrade

# Default ensemble weights — calibrated via backtesting (spec §20)
DEFAULT_WEIGHTS = {
    "poisson": 0.30,
    "zinb": 0.15,
    "bayes": 0.20,
    "elo": 0.15,
    "xg": 0.20,
}

Q_SCORE_WEIGHTS = {
    "model_probability": 25.0,
    "value_edge": 20.0,
    "xg_model": 15.0,
    "recent_form": 10.0,
    "market_consensus": 10.0,
    "odds_stability": 5.0,
    "team_news": 5.0,
    "league_reliability": 5.0,
    "data_quality": 5.0,
}

# Model agreement threshold — spec §9
MODEL_AGREEMENT_DOWNGRADE_THRESHOLD = 0.15  # 15 percentage points std dev


@dataclass
class ModelInputs:
    """All model probabilities for a single market/selection."""
    poisson_prob: Optional[float] = None
    zinb_prob: Optional[float] = None
    bayes_prob: Optional[float] = None
    elo_prob: Optional[float] = None
    xg_prob: Optional[float] = None


@dataclass
class QScoreInputs:
    """Non-model inputs required for Q-Score calculation."""
    implied_probability: Optional[float] = None  # None = no odds available; 1/best_odds otherwise
    best_odds: Optional[float] = None
    form_score: Optional[float] = None
    market_consensus: Optional[float] = None
    odds_stability: Optional[float] = None
    team_news_impact: Optional[float] = None
    league_reliability: Optional[float] = None
    data_quality: Optional[float] = None


@dataclass
class EnsembleResult:
    ensemble_probability: float
    model_agreement: float              # std dev across available model probs
    confidence_downgraded: bool         # True if agreement > 15pp threshold
    active_models: list[str] = field(default_factory=list)
    model_probabilities: dict[str, float] = field(default_factory=dict)


@dataclass
class QScoreResult:
    q_score: float                      # 0–100
    q_grade: QGrade
    edge: Optional[float]               # None when no market odds exist
    expected_value: Optional[float]     # None when no market odds exist
    components: dict[str, float] = field(default_factory=dict)
    component_status: dict[str, str] = field(default_factory=dict)
    component_weights: dict[str, float] = field(default_factory=dict)


def compute_ensemble(inputs: ModelInputs, weights: dict[str, float] | None = None) -> EnsembleResult:
    """Weighted ensemble of available model probabilities — spec §25.5."""
    w = weights or DEFAULT_WEIGHTS
    model_map = {
        "poisson": inputs.poisson_prob,
        "zinb": inputs.zinb_prob,
        "bayes": inputs.bayes_prob,
        "elo": inputs.elo_prob,
        "xg": inputs.xg_prob,
    }

    available = {k: v for k, v in model_map.items() if v is not None}
    if not available:
        raise ValueError("No model probabilities provided")

    # Renormalise weights for available models
    total_w = sum(w[k] for k in available)
    weighted_prob = sum(w[k] * v for k, v in available.items()) / total_w

    # Model agreement = std dev across available probabilities — spec §9
    probs = list(available.values())
    agreement_std = statistics.stdev(probs) if len(probs) > 1 else 0.0
    confidence_downgraded = agreement_std > MODEL_AGREEMENT_DOWNGRADE_THRESHOLD

    return EnsembleResult(
        ensemble_probability=max(0.0, min(1.0, weighted_prob)),
        model_agreement=agreement_std,
        confidence_downgraded=confidence_downgraded,
        active_models=list(available.keys()),
        model_probabilities=available,
    )


def compute_q_score(
    ensemble: EnsembleResult,
    inputs: QScoreInputs,
) -> QScoreResult:
    """
    Composite Q-Score on 0–100 scale — spec §12, §27.

    Each component is normalised to 0–1 then multiplied by its weight.
    """
    p = ensemble.ensemble_probability
    has_odds = inputs.implied_probability is not None

    # Component 1: Model Probability (25%).  The formal specification defines
    # this component as the ensemble probability for the specific market, so
    # the probability itself is the normalised 0–1 input.  Subtracting a 50%
    # baseline would turn the documented 75% Safe target into only 12.5/25 and
    # is not part of the Q-Score contract.
    c_prob = max(0.0, min(1.0, p))

    # Downgrade if models disagree significantly
    if ensemble.confidence_downgraded:
        c_prob *= 0.75

    # Component 2: Value / Edge (20%)
    # Zero when no odds exist — implied_prob=None means market has no bookmaker data
    if has_odds:
        edge: Optional[float] = p - inputs.implied_probability  # type: ignore[operator]
        c_edge = max(0.0, min(1.0, edge / 0.20)) if edge > 0 else 0.0  # type: ignore[operator]
    else:
        edge = None
        c_edge = 0.0

    status: dict[str, str] = {
        "model_probability": "available",
        "value_edge": "available" if has_odds else "missing_odds",
    }

    # Component 3: alignment between the xG probability and the ensemble made
    # from every available non-xG model. General model dispersion is a separate
    # confidence signal and must not stand in for xG alignment.
    xg_probability = ensemble.model_probabilities.get("xg")
    non_xg = {
        name: probability
        for name, probability in ensemble.model_probabilities.items()
        if name != "xg"
    }
    if xg_probability is not None and non_xg:
        non_xg_weight = sum(DEFAULT_WEIGHTS[name] for name in non_xg)
        non_xg_probability = sum(
            DEFAULT_WEIGHTS[name] * probability for name, probability in non_xg.items()
        ) / non_xg_weight
        c_xg = max(0.0, min(1.0, 1.0 - abs(xg_probability - non_xg_probability)))
        status["xg_model"] = "available"
    else:
        c_xg = 0.0
        status["xg_model"] = "missing_xg" if xg_probability is None else "missing_non_xg_model"

    def _component(value: Optional[float], name: str, reason: str) -> float:
        if value is None:
            status[name] = reason
            return 0.0
        status[name] = "available"
        return max(0.0, min(1.0, value))

    c_form = _component(inputs.form_score, "recent_form", "missing_form")
    c_consensus = _component(inputs.market_consensus, "market_consensus", "missing_market_odds")
    c_stability = _component(inputs.odds_stability, "odds_stability", "missing_odds_history")
    c_news = _component(inputs.team_news_impact, "team_news", "missing_team_news")
    c_league = _component(inputs.league_reliability, "league_reliability", "missing_league_reliability")
    c_data = _component(inputs.data_quality, "data_quality", "missing_data_quality")

    # Weighted sum → 0–100
    q = (
        c_prob * Q_SCORE_WEIGHTS["model_probability"] +
        c_edge * Q_SCORE_WEIGHTS["value_edge"] +
        c_xg * Q_SCORE_WEIGHTS["xg_model"] +
        c_form * Q_SCORE_WEIGHTS["recent_form"] +
        c_consensus * Q_SCORE_WEIGHTS["market_consensus"] +
        c_stability * Q_SCORE_WEIGHTS["odds_stability"] +
        c_news * Q_SCORE_WEIGHTS["team_news"] +
        c_league * Q_SCORE_WEIGHTS["league_reliability"] +
        c_data * Q_SCORE_WEIGHTS["data_quality"]
    )
    q = max(0.0, min(100.0, q))

    # Expected value — spec §11: EV = (P × Odds) - 1; None when no odds
    ev: Optional[float] = (p * inputs.best_odds) - 1.0 if inputs.best_odds else None

    return QScoreResult(
        q_score=q,
        q_grade=_grade(q),
        edge=edge,
        expected_value=ev,
        components={
            "model_probability": c_prob * Q_SCORE_WEIGHTS["model_probability"],
            "value_edge": c_edge * Q_SCORE_WEIGHTS["value_edge"],
            "xg_model": c_xg * Q_SCORE_WEIGHTS["xg_model"],
            "recent_form": c_form * Q_SCORE_WEIGHTS["recent_form"],
            "market_consensus": c_consensus * Q_SCORE_WEIGHTS["market_consensus"],
            "odds_stability": c_stability * Q_SCORE_WEIGHTS["odds_stability"],
            "team_news": c_news * Q_SCORE_WEIGHTS["team_news"],
            "league_reliability": c_league * Q_SCORE_WEIGHTS["league_reliability"],
            "data_quality": c_data * Q_SCORE_WEIGHTS["data_quality"],
        },
        component_status=status,
        component_weights=Q_SCORE_WEIGHTS.copy(),
    )


def _grade(q: float) -> QGrade:
    """Map Q-Score to grade — spec §12."""
    if q >= 90:
        return QGrade.A_PLUS
    if q >= 85:
        return QGrade.A
    if q >= 80:
        return QGrade.B_PLUS
    if q >= 75:
        return QGrade.B
    if q >= 70:
        return QGrade.C
    return QGrade.REJECT
