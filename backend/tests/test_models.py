"""
Unit tests for prediction models — spec §38.

Tests run without database or external API access.
"""

import pytest
from app.services.models.poisson_dc import (
    score_matrix,
    predict_market,
    PoissonDixonColes,
)
from app.services.models.elo import (
    expected_score,
    update_elos,
    elo_to_win_probabilities,
)
from app.services.models.ensemble import (
    compute_ensemble,
    compute_q_score,
    ModelInputs,
    QScoreInputs,
)
from app.models.prediction import QGrade
from app.services.models.ensemble import _grade
from app.services.models.zinb import should_use_zinb


# ---------------------------------------------------------------------------
# Poisson-DC tests
# ---------------------------------------------------------------------------

def test_score_matrix_sums_to_one():
    mat = score_matrix(lam=1.5, mu=1.0)
    assert abs(mat.sum() - 1.0) < 0.01


def test_over_15_plus_under_15_equals_one():
    lam, mu = 1.5, 1.0
    over = predict_market(lam, mu, "over_1.5")
    under = predict_market(lam, mu, "under_1.5")
    # Should sum to ~1 (excludes exact 1.5 which can't happen with integers)
    assert abs(over + under - 1.0) < 0.001


def test_btts_yes_plus_no_equals_one():
    over = predict_market(1.5, 1.0, "btts_yes")
    under = predict_market(1.5, 1.0, "btts_no")
    assert abs(over + under - 1.0) < 0.001


def test_1x2_sum_to_one():
    lam, mu = 1.4, 0.9
    p = predict_market(lam, mu, "home_win")
    d = predict_market(lam, mu, "draw")
    a = predict_market(lam, mu, "away_win")
    assert abs(p + d + a - 1.0) < 0.01


def test_high_lam_favours_home_win():
    # If home team is much stronger, home win probability should dominate
    p_strong = predict_market(3.0, 0.5, "home_win")
    p_weak = predict_market(0.5, 3.0, "home_win")
    assert p_strong > p_weak


def test_poisson_dc_fit_predict():
    historical = [
        {"home_team_id": 1, "away_team_id": 2, "home_goals": 2, "away_goals": 1},
        {"home_team_id": 2, "away_team_id": 1, "home_goals": 1, "away_goals": 0},
        {"home_team_id": 1, "away_team_id": 2, "home_goals": 3, "away_goals": 2},
        {"home_team_id": 2, "away_team_id": 1, "home_goals": 0, "away_goals": 1},
    ] * 10  # 40 matches
    model = PoissonDixonColes()
    model.fit(historical)
    prob = model.predict(1, 2, "over_1.5")
    assert prob is not None
    assert 0.0 <= prob <= 1.0


# ---------------------------------------------------------------------------
# Elo tests
# ---------------------------------------------------------------------------

def test_equal_teams_expected_score_close_to_half():
    # With home advantage, home team slightly > 0.5
    e = expected_score(1500, 1500, home_advantage_elo=75)
    assert 0.55 < e < 0.65


def test_stronger_team_higher_expected():
    e_strong = expected_score(1700, 1500)
    e_weak = expected_score(1500, 1700)
    assert e_strong > e_weak


def test_elo_update_win_increases_rating():
    result = update_elos(1500, 1500, home_goals=2, away_goals=0, k_factor=20)
    assert result.home_new_elo > 1500
    assert result.away_new_elo < 1500


def test_elo_update_draw_both_close_to_original():
    result = update_elos(1500, 1500, home_goals=1, away_goals=1, k_factor=20)
    # Draw: home expected ~0.59 (home adv), so home loses slightly, away gains
    assert abs(result.home_new_elo - 1500) < 15
    assert abs(result.away_new_elo - 1500) < 15


def test_1x2_sum_to_one_elo():
    probs = elo_to_win_probabilities(1600, 1500)
    total = sum(probs.values())
    assert abs(total - 1.0) < 0.001


# ---------------------------------------------------------------------------
# Ensemble + Q-Score tests
# ---------------------------------------------------------------------------

def test_ensemble_with_all_models():
    inputs = ModelInputs(
        poisson_prob=0.75,
        zinb_prob=0.70,
        bayes_prob=0.78,
        elo_prob=0.72,
        xg_prob=0.74,
    )
    result = compute_ensemble(inputs)
    assert 0.0 <= result.ensemble_probability <= 1.0
    assert result.model_agreement >= 0.0
    assert not result.confidence_downgraded  # all close together


def test_ensemble_disagree_triggers_downgrade():
    inputs = ModelInputs(
        poisson_prob=0.85,
        zinb_prob=0.50,  # large divergence
        bayes_prob=0.82,
    )
    result = compute_ensemble(inputs)
    assert result.confidence_downgraded


def test_ensemble_single_model():
    inputs = ModelInputs(poisson_prob=0.80)
    result = compute_ensemble(inputs)
    assert abs(result.ensemble_probability - 0.80) < 0.01
    assert result.model_agreement == 0.0


def test_q_score_high_for_strong_selection():
    inputs = ModelInputs(
        poisson_prob=0.82,
        zinb_prob=0.80,
        bayes_prob=0.84,
        elo_prob=0.78,
        xg_prob=0.81,
    )
    ensemble = compute_ensemble(inputs)
    q_inputs = QScoreInputs(
        implied_probability=0.65,  # positive edge
        best_odds=1.54,
        form_score=0.8,
        market_consensus=0.8,
        odds_stability=0.9,
        team_news_impact=1.0,
        league_reliability=0.95,
        data_quality=0.9,
    )
    result = compute_q_score(ensemble, q_inputs)
    assert result.q_score >= 60.0  # should be high given positive edge + high prob
    assert result.edge > 0.0
    assert result.q_grade != QGrade.REJECT


def test_q_score_probability_component_uses_raw_ensemble_probability():
    ensemble = compute_ensemble(
        ModelInputs(poisson_prob=0.75, bayes_prob=0.75, xg_prob=0.75)
    )
    result = compute_q_score(ensemble, QScoreInputs())
    assert result.components["model_probability"] == pytest.approx(18.75)


def test_q_score_reject_for_weak_selection():
    inputs = ModelInputs(poisson_prob=0.40)
    ensemble = compute_ensemble(inputs)
    q_inputs = QScoreInputs(
        implied_probability=0.55,  # negative edge
        best_odds=1.82,
        form_score=0.3,
        market_consensus=0.3,
        odds_stability=0.4,
        team_news_impact=0.5,
        league_reliability=0.5,
        data_quality=0.4,
    )
    result = compute_q_score(ensemble, q_inputs)
    assert result.q_grade == QGrade.REJECT


@pytest.mark.parametrize(
    ("score", "grade"),
    [
        (90.0, QGrade.A_PLUS),
        (89.999, QGrade.A),
        (85.0, QGrade.A),
        (80.0, QGrade.B_PLUS),
        (75.0, QGrade.B),
        (70.0, QGrade.C),
        (69.999, QGrade.REJECT),
    ],
)
def test_q_score_grade_boundaries(score, grade):
    assert _grade(score) == grade


def test_missing_q_score_components_receive_zero_credit_and_reason_codes():
    ensemble = compute_ensemble(ModelInputs(poisson_prob=0.8, bayes_prob=0.78))
    result = compute_q_score(
        ensemble,
        QScoreInputs(implied_probability=None, best_odds=None),
    )
    assert result.components["value_edge"] == 0
    assert result.components["xg_model"] == 0
    assert result.components["recent_form"] == 0
    assert result.component_status["value_edge"] == "missing_odds"
    assert result.component_status["xg_model"] == "missing_xg"
    assert result.component_status["recent_form"] == "missing_form"


def test_xg_alignment_uses_xg_probability_not_general_disagreement():
    aligned = compute_q_score(
        compute_ensemble(ModelInputs(poisson_prob=0.75, bayes_prob=0.75, xg_prob=0.75)),
        QScoreInputs(),
    )
    misaligned = compute_q_score(
        compute_ensemble(ModelInputs(poisson_prob=0.75, bayes_prob=0.75, xg_prob=0.25)),
        QScoreInputs(),
    )
    assert aligned.components["xg_model"] > misaligned.components["xg_model"]


def test_zinb_activation_requires_overdispersion_and_minimum_sample():
    assert not should_use_zinb([2] * 100)
    assert not should_use_zinb([0, 5] * 5)
    assert should_use_zinb([0] * 80 + [8] * 20)
