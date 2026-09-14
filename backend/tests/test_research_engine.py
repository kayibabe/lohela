"""Pure acceptance tests for optimiser, settlement, and Kelly constraints."""

from datetime import datetime, timezone

import pytest

from app.models import Match, QGrade, SelectionResult, Team, TicketType
from app.services.accumulator_builder import (
    TICKET_SPECS,
    Leg,
    TicketSpec,
    _evaluate_combo,
    _find_best_ticket,
    _pair_correlation,
    _relax_spec,
    selection_rejection_reasons,
)
from app.services.backtesting import _fractional_kelly
from app.services.calibration import _pearson
from app.services.data_validator import compute_prematch_quality_score
from app.services.fixture_ingestor import _build_team_news
from app.services.model_preparation import expected_goals_proxy
from app.services.model_runner import ModelRunner, _team_news_impact
from app.services.odds_fetcher import _map_outcome
from app.services.settlement import evaluate_selection


def _leg(
    prediction_id: int,
    match_id: int,
    market: str,
    *,
    competition_id: int | None = None,
    q_score: float = 88.0,
    probability: float = 0.75,
    odds: float = 1.55,
) -> Leg:
    return Leg(
        prediction_id=prediction_id,
        match_id=match_id,
        home_team_id=match_id * 2,
        away_team_id=match_id * 2 + 1,
        home_team=f"Home {match_id}",
        away_team=f"Away {match_id}",
        competition=f"League {competition_id or match_id}",
        competition_id=competition_id or match_id,
        kickoff_at=datetime(2026, 8, 28, 12 + match_id % 8, tzinfo=timezone.utc),
        market=market,
        selection=market,
        model_probability=probability,
        model_agreement=0.05,
        best_odds=odds,
        source_odds_at=datetime(2026, 8, 28, 9, tzinfo=timezone.utc),
        q_score=q_score,
        q_grade=QGrade.A,
        edge=0.05,
        expected_value=probability * odds - 1.0,
        active_models=["poisson", "bayes", "xg"],
        data_quality_score=90.0,
    )


def test_safe_ticket_requires_three_market_families():
    spec = TicketSpec(TicketType.SAFE, "Conservative", 3, 6, 3.0, 5.0, 85, 1.0, 3, 0, 0.05)
    valid = (_leg(1, 1, "home_win"), _leg(2, 2, "over_1.5"), _leg(3, 3, "btts_yes"))
    invalid = (_leg(1, 1, "home_win"), _leg(2, 2, "away_win"), _leg(3, 3, "draw"))
    assert _evaluate_combo(valid, spec, {}) is not None
    assert _evaluate_combo(invalid, spec, {}) is None


def test_optimizer_rejects_two_selections_from_same_match():
    spec = TicketSpec(TicketType.BALANCED, "Balanced", 2, 7, 1, 20, 80, 0, 1, 0, 0.10)
    combo = (_leg(1, 10, "home_win"), _leg(2, 10, "over_1.5"))
    assert _evaluate_combo(combo, spec, {}) is None


def test_pairwise_correlation_is_explicit_and_bounded():
    left = _leg(1, 1, "home_win", competition_id=50)
    right = _leg(2, 2, "over_1.5", competition_id=50)
    detail = _pair_correlation(left, right, {})
    assert detail.coefficient == 0.03
    assert "SAME_LEAGUE" in detail.reasons


def test_calibration_pearson_handles_signal_and_constant_samples():
    assert _pearson([(0.0, 0.0), (0.5, 0.5), (1.0, 1.0)]) == 1.0
    assert _pearson([(0.5, 0.0), (0.5, 1.0)]) == 0.0


def test_prematch_quality_uses_history_form_and_market_coverage():
    score, components = compute_prematch_quality_score(
        identity_complete=True,
        minimum_history_matches=10,
        teams_with_form=2,
        bookmaker_count=8,
        market_family_count=4,
        has_team_news=False,
        league_reliability=0.95,
    )
    assert score == 89.5
    assert components["historical_coverage"] == 25.0
    assert components["odds_coverage"] == 20.0


def test_expected_goals_proxy_requires_prepared_team_history():
    home = Team(api_football_id=1, name="Home", competition_id=1)
    away = Team(api_football_id=2, name="Away", competition_id=1)
    home.bayes_attack_std = away.bayes_attack_std = 0.2
    assert expected_goals_proxy(home, away) is None
    home.bayes_attack_std = away.bayes_attack_std = 0.1
    home.avg_xg_for, home.avg_xg_against = 1.8, 0.9
    away.avg_xg_for, away.avg_xg_against = 1.2, 1.5
    assert expected_goals_proxy(home, away) is not None


def test_additional_soccer_market_mapping():
    event = {"home_team": "Crystal Palace", "away_team": "Manchester City"}
    assert _map_outcome("btts", {"name": "Yes"}, event) == ("btts_yes", "btts_yes")
    assert _map_outcome("double_chance", {"name": "Crystal Palace or Draw"}, event) == (
        "double_chance_1x", "double_chance_1x"
    )
    assert _map_outcome("double_chance", {"name": "Manchester City or Draw"}, event) == (
        "double_chance_x2", "double_chance_x2"
    )
    assert _map_outcome("draw_no_bet", {"name": "Manchester City"}, event) == (
        "dnb_away", "dnb_away"
    )


def test_team_news_deduplicates_provider_rows():
    injury = {
        "team": {"id": 1, "name": "Home"},
        "player": {"id": 9, "name": "Player", "reason": "Injury", "type": "Missing Fixture"},
    }
    assert len(_build_team_news([injury, injury])["Home"]) == 1


def test_team_news_score_does_not_double_count_duplicate_rows():
    match = Match()
    absence = {"name": "Player", "reason": "Injury", "type": "Missing Fixture"}
    match.team_news = {"Home": [absence, absence]}
    assert _team_news_impact(match) == 0.95


def test_outcome_market_form_is_directional_against_opposition():
    runner = ModelRunner(None)  # type: ignore[arg-type]
    assert runner._weighted_form(0.8, 0.2, "dnb_home") == pytest.approx(0.8)
    assert runner._weighted_form(0.8, 0.2, "double_chance_x2") == pytest.approx(0.2)


def test_fractional_kelly_is_non_negative_and_capped_at_two_percent():
    assert _fractional_kelly(0.40, 2.0) == 0.0
    assert 0 < _fractional_kelly(0.70, 2.0) <= 0.02


def test_relax_spec_loosens_cumulatively_and_floors():
    base = next(spec for spec in TICKET_SPECS if spec.ticket_type == TicketType.BALANCED)
    level1 = _relax_spec(base, 1)
    level3 = _relax_spec(base, 3)
    assert level1.min_high_grade_ratio == pytest.approx(base.min_high_grade_ratio - 0.30)
    assert level1.min_legs == base.min_legs  # relaxation never touches leg counts
    assert level1.max_pair_correlation == base.max_pair_correlation  # or correlation tolerance
    # Cumulative through all three steps still respects the floors.
    assert level3.min_high_grade_ratio == 0.0
    assert level3.min_q_score >= 60.0
    assert level3.min_market_types >= 1


def test_thin_slate_relaxation_recovers_a_balanced_ticket():
    """Reproduces the 2026-08-31-style outage: a handful of matches where only
    one leg clears the high-grade bar, so the full-strength spec can't build
    anything — but the relaxation ladder can, without inventing extra legs."""
    base = next(spec for spec in TICKET_SPECS if spec.ticket_type == TicketType.BALANCED)
    pool = [
        _leg(1, 1, "home_win", competition_id=1, q_score=90.0, odds=1.7),
        _leg(2, 2, "over_1.5", competition_id=2, q_score=78.0, odds=1.7),
        _leg(3, 3, "btts_yes", competition_id=3, q_score=76.0, odds=1.7),
        _leg(4, 4, "away_win", competition_id=4, q_score=74.0, odds=1.7),
    ]
    for leg in pool:
        leg.source_odds_at = datetime.now(timezone.utc)  # selection_rejection_reasons checks odds age

    eligible_at_full_strength = [leg for leg in pool if not selection_rejection_reasons(leg, base, None)]
    assert _find_best_ticket(eligible_at_full_strength, base, {}) is None

    relaxed = _relax_spec(base, 3)
    eligible_relaxed = [leg for leg in pool if not selection_rejection_reasons(leg, relaxed, None)]
    ticket = _find_best_ticket(eligible_relaxed, relaxed, {})
    assert ticket is not None
    assert len(ticket.legs) == 4


def test_market_settlement_rules():
    assert evaluate_selection("over_2.5", 2, 1) == SelectionResult.WON
    assert evaluate_selection("under_2.5", 1, 1) == SelectionResult.WON
    assert evaluate_selection("under_3.5", 2, 2) == SelectionResult.LOST
    assert evaluate_selection("over_4.5", 3, 2) == SelectionResult.WON
    assert evaluate_selection("btts_yes", 1, 0) == SelectionResult.LOST
    assert evaluate_selection("double_chance_1x", 1, 1) == SelectionResult.WON
    assert evaluate_selection("dnb_home", 0, 0) == SelectionResult.VOID
