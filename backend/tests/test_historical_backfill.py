"""Point-in-time backfill: market coverage, Elo exclusion, odds matching."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import MatchStatus
from app.services.historical_backfill import (
    ALL_MARKETS,
    BINARY,
    TOTALS,
    HistoricalTotalsBackfill,
    _odds_for_match,
    _rolling_expected_goals,
)


def test_odds_for_match_matches_binary_markets_by_clean_key():
    kickoff = datetime(2026, 9, 20, tzinfo=timezone.utc)
    fetched = kickoff - timedelta(hours=3)
    odds = [
        SimpleNamespace(market="home_win", selection="home_win", decimal_odds=2.1,
                        fetched_at=fetched),
        SimpleNamespace(market="btts_yes", selection="btts_yes", decimal_odds=1.8,
                        fetched_at=fetched),
        SimpleNamespace(market="home_win", selection="home_win", decimal_odds=1.0,
                        fetched_at=kickoff),  # at/after kickoff: excluded
    ]
    matched = _odds_for_match(odds, kickoff, BINARY)
    assert set(matched) == {"home_win", "btts_yes"}
    assert matched["home_win"].decimal_odds == 2.1


def test_rolling_expected_goals_requires_both_teams_history():
    history = {1: [(2, 1), (1, 0)]}
    assert _rolling_expected_goals(history, 1, 2) is None
    history[2] = [(0, 1), (1, 2)]
    result = _rolling_expected_goals(history, 1, 2)
    assert result is not None and all(0.2 <= v <= 4.5 for v in result)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


def _match(id_, home, away, kickoff, competition_id=1):
    return SimpleNamespace(
        id=id_, status=MatchStatus.FINISHED, home_goals=1, away_goals=1,
        kickoff_at=kickoff, competition_id=competition_id,
        home_team_id=home, away_team_id=away, excluded_from_models=False,
        data_quality_score=80,
    )


@pytest.mark.asyncio
async def test_backfill_writes_all_markets_without_elo_and_with_bayes(monkeypatch):
    class _FakePoisson:
        def fit(self, matches):
            return self

        def predict(self, home_id, away_id, market):
            return 0.4

    class _FakeZinb:
        def fit(self, goals):
            return self

        def predict_over(self, threshold):
            return 0.35

    monkeypatch.setattr("app.services.historical_backfill.PoissonDixonColes", _FakePoisson)
    monkeypatch.setattr("app.services.historical_backfill.ZINBModel", _FakeZinb)
    monkeypatch.setattr(
        "app.services.historical_backfill._run_analytical",
        lambda rows: {
            1: {"attack_mean": 1.4, "attack_std": 0.1, "defense_mean": 1.0, "defense_std": 0.1},
            2: {"attack_mean": 1.1, "attack_std": 0.1, "defense_mean": 1.2, "defense_std": 0.1},
        },
    )

    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    history_matches = [
        _match(100 + i, 1 if i % 2 == 0 else 2, 2 if i % 2 == 0 else 1,
              base + timedelta(days=i))
        for i in range(30)
    ]
    target_kickoff = base + timedelta(days=40)
    target_match = _match(999, 1, 2, target_kickoff)
    all_matches = history_matches + [target_match]

    competition = SimpleNamespace(id=1, home_advantage_elo=90.0)

    db = AsyncMock()
    db.add = MagicMock()
    db.execute.side_effect = [
        _Result(all_matches),   # Match query
        _Result([]),            # Odds query
        _Result([competition]), # Competition query
        _Result([]),            # existing Prediction query
    ]

    backfill = HistoricalTotalsBackfill(db, model_version="test-backfill")
    result = await backfill.run(target_kickoff.date(), target_kickoff.date(), min_history=30)

    assert result["predictions_written"] == len(ALL_MARKETS)
    written = [call.args[0] for call in db.add.call_args_list]
    assert {p.market for p in written} == set(ALL_MARKETS)
    assert all(p.elo_prob is None for p in written)
    assert all(p.bayes_prob is not None for p in written)
    # adapt_rows/singles_report rejects any quote whose provenance omits
    # is_fallback (missing key reads as "not False", i.e. untrusted) —
    # regression for backfilled predictions being silently unusable there.
    assert all(p.source_odds_provenance["is_fallback"] is None for p in written)
    for prediction in written:
        if prediction.market in TOTALS:
            assert prediction.zinb_prob is not None
        else:
            assert prediction.zinb_prob is None
