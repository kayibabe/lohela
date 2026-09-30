from app.services.band_mix_picks import (
    MIN_MIX_SAMPLE,
    MIN_MIX_ROI,
    PROVISIONAL_MIX_SAMPLE,
    _choose_one_per_match,
    evaluate_band_mixes,
    lifecycle_transition,
    summarize_picks,
)


def _row(lohela, market, n, roi, wins=None):
    wins = wins if wins is not None else n // 2
    return {
        "lohela_band": lohela, "market_band": market, "sample_size": n,
        "wins": wins, "losses": n - wins, "hit_rate": wins / n if n else None, "roi": roi,
    }


def test_lifecycle_transitions_are_explicit_and_directional():
    assert lifecycle_transition(None, True) == "promoted"
    assert lifecycle_transition("active", False) == "demoted"
    assert lifecycle_transition("inactive", True) == "promoted"
    assert lifecycle_transition("active", True) == "unchanged"
    assert lifecycle_transition(None, False) == "initial_inactive"


def test_any_band_joins_after_the_dynamic_evidence_and_roi_thresholds():
    qualifying = _row("65-70", "<50", MIN_MIX_SAMPLE, MIN_MIX_ROI, wins=16)
    small = _row("80-85", "50-55", MIN_MIX_SAMPLE - 1, 0.80)
    below_roi = _row("70-75", "60-65", MIN_MIX_SAMPLE, MIN_MIX_ROI - 0.0001)

    result = evaluate_band_mixes([qualifying, small, below_roi])

    assert [(r["lohela_band"], r["market_band"]) for r in result["watchlist"]] == [("65-70", "<50")]
    monitored = {(r["lohela_band"], r["market_band"]): r for r in result["monitored"]}
    assert monitored[("80-85", "50-55")]["reason"] == "INSUFFICIENT_SAMPLE"
    assert monitored[("70-75", "60-65")]["reason"] == "ROI_BELOW_THRESHOLD"


def test_dynamic_group_marks_30_to_49_results_provisional_and_exposes_recent_roi():
    current = _row("70-75", "60-65", PROVISIONAL_MIX_SAMPLE - 1, 0.25, wins=30)
    recent = _row("70-75", "60-65", 12, 0.10, wins=7)

    row = evaluate_band_mixes([current], [recent])["watchlist"][0]

    assert row["source"] == "dynamic"
    assert row["provisional"] is True
    assert row["recent_sample_size"] == 12
    assert row["recent_roi"] == 0.10


def test_one_qualified_selection_is_retained_per_match_by_evidence_then_roi():
    rows = _choose_one_per_match([
        ({"match_id": 1, "prediction_id": 1}, {"provisional": True, "sample_size": 40, "roi": 0.9}),
        ({"match_id": 1, "prediction_id": 2}, {"provisional": False, "sample_size": 30, "roi": 0.2}),
        ({"match_id": 2, "prediction_id": 3}, {"provisional": False, "sample_size": 50, "roi": 0.2}),
        ({"match_id": 2, "prediction_id": 4}, {"provisional": False, "sample_size": 50, "roi": 0.3}),
    ])

    assert {row["match_id"]: row["prediction_id"] for row in rows} == {1: 2, 2: 4}


def test_summary_uses_flat_stake_at_pick_odds_and_ignores_pending():
    rows = [
        {"match_id": 1, "result": "won", "odds": 1.5},
        {"match_id": 1, "result": "lost", "odds": 1.8},
        {"match_id": 2, "result": None, "odds": 2.0},
        {"match_id": 3, "result": "void", "odds": 1.4},
    ]
    summary = summarize_picks(rows)
    assert summary["picks"] == 4
    assert summary["matches"] == 3
    assert summary["settled"] == 2
    assert (summary["wins"], summary["losses"], summary["voids"], summary["pending"]) == (1, 1, 1, 1)
    assert summary["profit_loss"] == -0.5
    assert summary["roi"] == -0.25


def test_summary_of_no_settled_picks_has_no_roi():
    assert summarize_picks([])["roi"] is None
