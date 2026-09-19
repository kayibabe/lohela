"""Research safety boundaries and outcome-blind portfolio accounting."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.singles_research import Candidate, Policy, evaluate, select_candidates


NOW = datetime(2026, 9, 19, 10, tzinfo=timezone.utc)


def candidate(**changes):
    return replace(Candidate("1", "10", "over_1.5", .75, 1.6, NOW, NOW,
                             NOW, NOW + timedelta(hours=2), "model-v1"), **changes)


@pytest.mark.parametrize("changes,reason", [
    ({"odds": 1.5}, "odds_below_or_equal_floor"),
    ({"probability": .699}, "probability_below_floor"),
    ({"odds": float("nan")}, "invalid_numeric_value"),
    ({"probability": float("inf")}, "invalid_numeric_value"),
    ({"probability": True}, "invalid_numeric_value"),
    ({"market": "dnb_home"}, "unsupported_market"),
    ({"market": "over_2"}, "unsupported_market"),
    ({"market": "first_half_over_1.5"}, "unsupported_market"),
    ({"historical": True}, "historical_backfill"),
    ({"created_at": NOW + timedelta(seconds=1)}, "invalid_time_provenance"),
    ({"quote_at": NOW + timedelta(seconds=1)}, "invalid_time_provenance"),
    ({"kickoff_at": NOW}, "invalid_time_provenance"),
    ({"decision_at": NOW.replace(tzinfo=None)}, "missing_or_naive_timestamp"),
    ({"quote_at": NOW - timedelta(hours=6, seconds=1)}, "stale_quote"),
    ({"source_revision": ""}, "missing_identity"),
    ({"odds": None}, "invalid_numeric_value"),
    ({"quote_at": None}, "missing_or_naive_timestamp"),
])
def test_rejections_are_explicit(changes, reason):
    selected = select_candidates([candidate(**changes)], as_of=NOW)
    assert selected.picks == ()
    assert selected.rejections == {reason: 1}


def test_earliest_eligible_snapshot_cannot_be_replaced_by_later_better_price():
    early = candidate()
    later = candidate(id="2", odds=3, decision_at=NOW + timedelta(minutes=1))
    other_market = candidate(id="3", market="btts_yes", probability=.71)
    rows = [later, other_market, early]
    selected = select_candidates(rows, as_of=NOW + timedelta(hours=1))
    assert selected.picks == (early,)
    assert selected == select_candidates(reversed(rows), as_of=NOW + timedelta(hours=1))
    assert selected.rejections == {"later_eligible_snapshot": 1, "other_market_same_match": 1}


def test_earliest_ineligible_does_not_block_first_eligible_and_duplicates_fail_closed():
    early = candidate(probability=.4)
    later = candidate(id="2", decision_at=NOW + timedelta(minutes=1))
    assert select_candidates([early, later], as_of=NOW + timedelta(hours=1)).picks == (later,)
    assert select_candidates([early, early], as_of=NOW).rejections == {"duplicate_id": 2}


def test_haircut_positive_ev_and_quote_boundary():
    row = candidate(probability=.70, odds=1.51, quote_at=NOW - timedelta(hours=6))
    assert select_candidates([row], as_of=NOW).picks == (row,)
    assert select_candidates([row], as_of=NOW, policy=Policy(price_haircut=.5)).rejections == {
        "insufficient_conservative_ev": 1}
    assert select_candidates([row], as_of=NOW - timedelta(seconds=1)).rejections == {"future_decision": 1}


def test_evaluation_losses_void_pending_days_and_calibration():
    rows = [candidate(id=str(i), match_id=str(i), odds=2,
                      kickoff_at=NOW + timedelta(days=i, hours=2)) for i in range(4)]
    selected = select_candidates(rows, as_of=NOW)
    args = dict(start_date=date(2026, 9, 19), end_date=date(2026, 9, 23), bootstrap_samples=200)
    report = evaluate(selected, {"0": "win", "1": "loss", "2": "void"}, **args)
    assert (report["wins"], report["losses"], report["voids"], report["pending"]) == (1, 1, 1, 1)
    assert report["profit_units"] == 0
    assert report["conservative_profit_units"] == pytest.approx(-.02)
    assert report["conservative_roi"] == pytest.approx(-.02 / 3)
    assert report["resolved_stakes"] == 3
    assert report["max_drawdown_units"] == 1
    assert report["no_bet_days"] == 1
    assert report["brier_score"] == pytest.approx((.25**2 + .75**2) / 2)
    assert report["hit_rate_wilson_95"][0] < .5 < report["hit_rate_wilson_95"][1]
    assert report["loss_details"][0]["id"] == "1"
    assert report["status"] == "research_only" and not report["ready_for_promotion"]
    assert report == evaluate(selected, {"0": "win", "1": "loss", "2": "void"}, **args)
    evaluate(selected, {"0": "loss", "1": "win"}, **args)
    assert selected.picks == tuple(rows)  # Outcome changes cannot change picks.


def test_empty_report_is_not_a_perfect_hit_rate():
    result = evaluate(select_candidates([], as_of=NOW), {}, start_date=NOW.date(), end_date=NOW.date(), bootstrap_samples=100)
    assert result["hit_rate"] is None and result["roi"] is None
    assert result["daily_bootstrap_roi_95"] is None
    assert result["no_bet_days"] == 1


def test_local_midnight_and_invalid_inputs():
    row = candidate(kickoff_at=NOW.replace(hour=23))
    result = evaluate(select_candidates([row], as_of=NOW), {"1": "loss"},
                      start_date=date(2026, 9, 20), end_date=date(2026, 9, 20), bootstrap_samples=100)
    assert result["losses"] == 1
    for kwargs in ({"min_odds": 1.4}, {"min_ev": -1}, {"price_haircut": float("nan")}):
        with pytest.raises(ValueError):
            Policy(**kwargs)
    with pytest.raises(ValueError):
        select_candidates([], as_of=NOW.replace(tzinfo=None))
    assert Policy().fingerprint != Policy(price_haircut=.03).fingerprint
