from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.performance import _has_pre_kickoff_information, _has_pre_kickoff_quote
from app.services.accumulator_builder import _research_market_rejection_reasons


def _prediction(*, kickoff, created_at, as_of_at=None, source_odds_at=None):
    return SimpleNamespace(
        match=SimpleNamespace(kickoff_at=kickoff),
        created_at=created_at,
        as_of_at=as_of_at,
        source_odds_at=source_odds_at,
    )


def test_research_requires_prediction_information_before_kickoff():
    kickoff = datetime(2026, 9, 19, 15, tzinfo=timezone.utc)
    before = kickoff - timedelta(hours=2)
    after = kickoff + timedelta(minutes=1)

    assert _has_pre_kickoff_information(_prediction(kickoff=kickoff, created_at=before))
    assert not _has_pre_kickoff_information(_prediction(kickoff=kickoff, created_at=after))


def test_historical_as_of_timestamp_is_used_for_replayed_predictions():
    kickoff = datetime(2026, 9, 19, 15, tzinfo=timezone.utc)
    assert _has_pre_kickoff_information(_prediction(
        kickoff=kickoff,
        created_at=kickoff + timedelta(days=2),
        as_of_at=kickoff - timedelta(hours=3),
    ))


def test_research_requires_quote_at_or_before_kickoff():
    kickoff = datetime(2026, 9, 19, 15, tzinfo=timezone.utc)
    assert _has_pre_kickoff_quote(_prediction(
        kickoff=kickoff,
        created_at=kickoff - timedelta(hours=2),
        source_odds_at=kickoff,
    ))
    assert not _has_pre_kickoff_quote(_prediction(
        kickoff=kickoff,
        created_at=kickoff - timedelta(hours=2),
        source_odds_at=kickoff + timedelta(seconds=1),
    ))


def test_restricted_markets_stay_in_research_but_leave_generated_tickets():
    assert _research_market_rejection_reasons(SimpleNamespace(market="under_2.5")) == [
        "MARKET_RESTRICTED_FOR_RESEARCH"
    ]
    assert _research_market_rejection_reasons(SimpleNamespace(market="over_1.5")) == []
