from datetime import datetime, timezone

from app.services.parameter_sweep import _choose_policy, _score
from app.services.singles_research import Candidate, Policy


def _candidate(identifier, match_id, day, odds=2.0):
    timestamp = datetime.fromisoformat(f"2026-09-{day:02d}T10:00:00+00:00")
    return Candidate(
        id=str(identifier),
        match_id=str(match_id),
        market="over_1.5",
        probability=0.70,
        odds=odds,
        created_at=timestamp,
        quote_at=timestamp,
        decision_at=timestamp,
        kickoff_at=timestamp.replace(hour=12),
        source_revision="0.3.0",
    )


def test_policy_gate_requires_validation_sample_and_roi():
    base = {
        "policy": {"version": "dynamic-parameter-sweep-v1", "min_probability": 0.65,
                   "min_odds": 1.7, "price_haircut": 0.08, "min_ev": 0.0,
                   "max_quote_age_hours": 0.5},
        "train": {"resolved": 20, "roi": 0.30},
        "validation": {"resolved": 20, "roi": 0.20},
    }
    assert _choose_policy([base]) == base
    assert _choose_policy([{**base, "validation": {"resolved": 19, "roi": 0.50}}]) is None
    assert _choose_policy([{**base, "validation": {"resolved": 20, "roi": 0.19}}]) is None


def test_score_uses_flat_stake_and_excludes_pending():
    candidates = [_candidate(1, 1, 20, 2.0), _candidate(2, 2, 21, 3.0), _candidate(3, 3, 22, 2.5)]
    outcomes = {"1": "win", "2": "loss", "3": "pending"}
    metrics = _score(candidates, outcomes, Policy(min_probability=0.60, min_odds=1.50, min_ev=0),
                     datetime(2026, 9, 20, tzinfo=timezone.utc).date(),
                     datetime(2026, 9, 22, tzinfo=timezone.utc).date())
    assert metrics["resolved"] == 2
    assert metrics["wins"] == 1
    assert metrics["losses"] == 1
    assert metrics["profit"] == 0.0
    assert metrics["roi"] == 0.0
