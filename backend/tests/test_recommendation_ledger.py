"""Contracts for deduplicated recommendation-pick reporting."""

from datetime import date
from types import SimpleNamespace

from app.services.recommendation_ledger import (
    _canonical_pick_key,
    recommendation_metrics,
)


def test_canonical_key_deduplicates_tiers_but_keeps_opposite_selections_distinct():
    target = date(2026, 8, 30)
    home = SimpleNamespace(
        match_id=42,
        market="double_chance",
        selection="double_chance_1x",
        model_version="0.2.1",
    )
    away = SimpleNamespace(
        match_id=42,
        market="double_chance",
        selection="double_chance_x2",
        model_version="0.2.1",
    )
    newer_model = SimpleNamespace(
        match_id=42,
        market="double_chance",
        selection="double_chance_1x",
        model_version="0.3.0",
    )

    assert _canonical_pick_key(target, home) == _canonical_pick_key(target, home)
    assert _canonical_pick_key(target, home) == _canonical_pick_key(target, newer_model)
    assert _canonical_pick_key(target, home) != _canonical_pick_key(target, away)


def test_recommendation_metrics_are_flat_stake_and_exclude_missing_odds_from_roi():
    rows = [
        {"result": "won", "odds": 2.0},
        {"result": "lost", "odds": 1.8},
        {"result": "won", "odds": None},
        {"result": "void", "odds": 1.9},
        {"result": "pending", "odds": 2.1},
    ]

    metrics = recommendation_metrics(rows, stake=10)

    assert metrics["unique_picks"] == 5
    assert metrics["settled"] == 4
    assert metrics["wins"] == 2
    assert metrics["losses"] == 1
    assert metrics["voids"] == 1
    assert metrics["priced_settled"] == 2
    assert metrics["missing_odds"] == 1
    assert metrics["staked"] == 20
    assert metrics["returned"] == 20
    assert metrics["profit_loss"] == 0
    assert metrics["roi"] == 0
    assert metrics["hit_rate"] == 0.6667
