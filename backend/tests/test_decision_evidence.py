from datetime import datetime, timedelta, timezone

from app.services.decision_evidence import build_decision_evidence


def test_decision_evidence_is_bet_only_with_complete_positive_inputs():
    now = datetime.now(timezone.utc)
    result = build_decision_evidence(
        q_score=90,
        edge=0.08,
        model_agreement=0.03,
        data_quality_score=82,
        active_models=["poisson", "bayes"],
        source_odds_at=now,
        kickoff_at=now + timedelta(hours=2),
    )
    assert result["status"] == "BET"
    assert "positive_edge" in result["reasons"]
    assert result["policy_version"] == "bet-watch-pass-v1"


def test_decision_evidence_passes_missing_price_and_watches_weak_quality():
    now = datetime.now(timezone.utc)
    no_price = build_decision_evidence(
        q_score=90,
        edge=None,
        model_agreement=0.03,
        data_quality_score=82,
        active_models=["poisson"],
        source_odds_at=None,
        kickoff_at=now + timedelta(hours=2),
    )
    weak = build_decision_evidence(
        q_score=90,
        edge=0.08,
        model_agreement=0.03,
        data_quality_score=45,
        active_models=["poisson"],
        source_odds_at=now,
        kickoff_at=now + timedelta(hours=2),
    )
    assert no_price["status"] == "PASS"
    assert "missing_odds_timestamp" in no_price["risks"]
    assert weak["status"] == "WATCH"
    assert "data_quality_review" in weak["risks"]
