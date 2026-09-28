from app.services.band_mix_picks import (
    MIN_MIX_SAMPLE,
    WATCHLIST_BAND_MIXES,
    evaluate_band_mixes,
    summarize_picks,
)


def _row(lohela, market, n, roi, wins=None):
    wins = wins if wins is not None else n // 2
    return {
        "lohela_band": lohela, "market_band": market, "sample_size": n,
        "wins": wins, "losses": n - wins, "hit_rate": wins / n if n else None, "roi": roi,
    }


def test_watchlist_matches_requested_criteria():
    assert WATCHLIST_BAND_MIXES == (
        ("85-90", "75-80"),
        ("70-75", "55-60"),
        ("80-85", "50-55"),
        ("80-85", "65-70"),
        ("70-75", "60-65"),
    )


def test_watchlist_mix_is_active_only_when_it_is_a_positive_best_mix():
    top = _row("85-90", "75-80", 20, 0.12)
    negative = _row("70-75", "55-60", 30, -0.05)
    small = _row("80-85", "50-55", MIN_MIX_SAMPLE - 1, 0.40)
    matrix = [top, negative, small]
    # best_combinations is already filtered to sample >= 8 and sorted by ROI.
    result = evaluate_band_mixes(matrix, [top, negative])
    by_pair = {(r["lohela_band"], r["market_band"]): r for r in result["watchlist"]}

    assert by_pair[("85-90", "75-80")]["active"] is True
    assert by_pair[("85-90", "75-80")]["best_rank"] == 1
    assert by_pair[("70-75", "55-60")]["reason"] == "NON_POSITIVE_ROI"
    assert by_pair[("80-85", "50-55")]["reason"] == "INSUFFICIENT_SAMPLE"
    assert by_pair[("80-85", "65-70")]["reason"] == "NO_HISTORY"
    assert by_pair[("80-85", "65-70")]["sample_size"] == 0
    assert sum(r["active"] for r in result["watchlist"]) == 1


def test_positive_mix_outside_top_list_is_not_active():
    outside = _row("70-75", "60-65", 40, 0.03)
    result = evaluate_band_mixes([outside], [])
    row = next(r for r in result["watchlist"] if r["lohela_band"] == "70-75" and r["market_band"] == "60-65")
    assert row["active"] is False
    assert row["reason"] == "OUTSIDE_TOP_MIXES"


def test_discovered_mixes_exclude_watchlist_and_non_positive_roi():
    watch = _row("85-90", "75-80", 20, 0.12)
    extra = _row("60-65", "55-60", 25, 0.08)
    losing = _row("55-60", "50-55", 25, -0.02)
    result = evaluate_band_mixes([watch, extra, losing], [watch, extra, losing])
    assert [(r["lohela_band"], r["market_band"]) for r in result["discovered"]] == [("60-65", "55-60")]
    assert result["discovered"][0]["source"] == "discovered"
    assert result["discovered"][0]["best_rank"] == 2


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
