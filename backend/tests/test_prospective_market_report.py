"""The prospective report scores each published prediction once and rejects late data."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models import SelectionResult
from scripts.prospective_market_report import summarize


def test_same_selection_across_tickets_is_scored_once_with_pre_kickoff_close():
    kickoff = datetime(2026, 9, 26, 15, tzinfo=timezone.utc)
    published = kickoff - timedelta(hours=4)
    ticket = SimpleNamespace(published_at=published)
    selection = SimpleNamespace(prediction_id=7, market="over_1.5", source_odds_at=published - timedelta(minutes=2),
                                probability_snapshot=0.7, result=SelectionResult.WON,
                                odds_snapshot=1.35)
    prediction = SimpleNamespace(created_at=published - timedelta(minutes=3),
                                 model_probability=0.9, closing_decimal_odds=1.30,
                                 closing_odds_at=kickoff - timedelta(minutes=5))
    match = SimpleNamespace(kickoff_at=kickoff)
    report = summarize([(ticket, selection, prediction, match)] * 2)
    assert report["coverage"]["duplicate_prediction_excluded"] == 1
    assert report["market"]["count"] == report["model_on_same_selections"]["count"] == 1
    assert report["market"]["brier"] == 0.09
    assert report["model_on_same_selections"]["brier"] == 0.01
    assert report["closing_line"]["count"] == 1


def test_late_publication_is_excluded_and_post_kickoff_close_is_missing():
    kickoff = datetime(2026, 9, 26, 15, tzinfo=timezone.utc)
    ticket = SimpleNamespace(published_at=kickoff - timedelta(hours=2))
    selection = SimpleNamespace(prediction_id=8, market="over_1.5", source_odds_at=kickoff - timedelta(hours=3),
                                probability_snapshot=0.7, result=SelectionResult.LOST,
                                odds_snapshot=1.35)
    prediction = SimpleNamespace(created_at=kickoff - timedelta(hours=4),
                                 model_probability=0.9, closing_decimal_odds=1.30,
                                 closing_odds_at=kickoff + timedelta(minutes=1))
    match = SimpleNamespace(kickoff_at=kickoff)
    report = summarize([(ticket, selection, prediction, match)])
    assert report["market"]["count"] == 1
    assert report["closing_line"]["count"] == 0
    assert report["coverage"]["missing_later_pre_kickoff_close"] == 1
    ticket.published_at = kickoff + timedelta(minutes=1)
    report = summarize([(ticket, selection, prediction, match)])
    assert report["market"]["count"] == 0
    assert report["coverage"]["invalid_time_order_excluded"] == 1
