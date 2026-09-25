"""Pure market-settlement contracts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.models import SelectionResult
from app.models.bet import BetStatus
from app.services.settlement import SettlementService, evaluate_selection


@pytest.mark.parametrize(
    ("market", "home", "away", "expected"),
    [
        ("over_2.5", 2, 1, SelectionResult.WON),
        ("over_2.5", 1, 1, SelectionResult.LOST),
        ("under_2.5", 1, 1, SelectionResult.WON),
        ("under_2.5", 2, 1, SelectionResult.LOST),
        ("btts_yes", 2, 1, SelectionResult.WON),
        ("btts_no", 2, 0, SelectionResult.WON),
        ("home_win", 2, 1, SelectionResult.WON),
        ("draw", 1, 1, SelectionResult.WON),
        ("away_win", 0, 1, SelectionResult.WON),
        ("double_chance_1x", 1, 1, SelectionResult.WON),
        ("double_chance_x2", 1, 2, SelectionResult.WON),
        ("double_chance_12", 2, 1, SelectionResult.WON),
        ("double_chance_12", 0, 3, SelectionResult.WON),
        ("double_chance_12", 1, 1, SelectionResult.LOST),
        ("dnb_home", 1, 1, SelectionResult.VOID),
        ("dnb_away", 1, 1, SelectionResult.VOID),
        ("dnb_home", 2, 1, SelectionResult.WON),
        ("dnb_away", 1, 2, SelectionResult.WON),
    ],
)
def test_market_settlement_contract(market, home, away, expected):
    assert evaluate_selection(market, home, away) == expected


def test_unknown_market_is_rejected():
    with pytest.raises(ValueError, match="Unsupported market"):
        evaluate_selection("corner_count", 1, 0)


def test_every_modelled_market_is_settleable():
    """The model layer must never publish a market settlement cannot grade.

    A market added to `Market` without a rule in `evaluate_selection` would
    otherwise only surface as a settlement-time crash in production.
    """
    from typing import get_args

    from app.services.models.poisson_dc import Market

    unsettleable = []
    for market in get_args(Market):
        try:
            evaluate_selection(market, 2, 1)
        except ValueError:
            unsettleable.append(market)
    assert unsettleable == [], f"markets with no settlement rule: {unsettleable}"


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


@pytest.mark.asyncio
async def test_confirmed_individual_bet_settles_from_finished_match():
    bet = SimpleNamespace(
        id=7,
        match_id=11,
        source_selection_id=19,
        market="home_win",
        status=BetStatus.PENDING,
        actual_return=None,
        potential_return=24.0,
        stake=10.0,
    )
    match = SimpleNamespace(
        id=11,
        status=SimpleNamespace(value="finished"),
        home_goals=2,
        away_goals=1,
    )
    # MatchStatus is an enum in production; use the exact enum after building
    # the lightweight row so the service's safety guard is exercised.
    from app.models import MatchStatus

    match.status = MatchStatus.FINISHED
    db = SimpleNamespace(
        execute=AsyncMock(side_effect=[_Rows([bet]), _Rows([match])]),
        add=Mock(),
    )
    changed = await SettlementService(db)._settle_confirmed_individual_bets(
        {11}, "test", datetime_now_utc()
    )
    assert changed == 1
    assert bet.status == BetStatus.WON
    assert bet.actual_return == 24.0
    assert db.add.call_count == 1


class _UniqueRows(_Rows):
    def unique(self):
        return self


@pytest.mark.asyncio
async def test_unsupported_leg_does_not_abort_custom_accumulator_settlement():
    """A single ungradeable leg must not raise out of the settlement run.

    `settle_results` retries on exception, so an unsettleable market here used
    to stall every subsequent settlement until the market was removed by hand.
    """
    from app.models import CustomAccumulatorStatus, MatchStatus

    good_leg = SimpleNamespace(id=1, match_id=11, market="home_win", odds_snapshot=2.0, result=None, settled_at=None)
    bad_leg = SimpleNamespace(id=2, match_id=12, market="corner_count", odds_snapshot=1.5, result=None, settled_at=None)
    accumulator = SimpleNamespace(
        id=99,
        status=CustomAccumulatorStatus.PLACED,
        legs=[good_leg, bad_leg],
        stake=10.0,
        actual_return=None,
        settled_at=None,
        settlement_details=None,
    )
    finished = SimpleNamespace(id=11, status=MatchStatus.FINISHED, home_goals=2, away_goals=1)
    other = SimpleNamespace(id=12, status=MatchStatus.FINISHED, home_goals=1, away_goals=0)

    db = SimpleNamespace(
        execute=AsyncMock(return_value=_UniqueRows([accumulator])),
        get=AsyncMock(side_effect=[finished, other]),
        add=Mock(),
    )

    changed = await SettlementService(db)._settle_custom_accumulators(
        {11, 12}, "test", datetime_now_utc()
    )

    # The run survives, the accumulator stays unsettled, and the failure is auditable.
    assert changed == 0
    assert accumulator.status == CustomAccumulatorStatus.PLACED
    assert good_leg.result == SelectionResult.WON
    audit = [call.args[0] for call in db.add.call_args_list]
    assert any(getattr(event, "event_type", None) == "settlement_failed" for event in audit)


@pytest.mark.asyncio
async def test_custom_accumulator_settles_when_all_legs_are_gradeable():
    from app.models import CustomAccumulatorStatus, MatchStatus

    leg_a = SimpleNamespace(id=1, match_id=11, market="home_win", odds_snapshot=2.0, result=None, settled_at=None)
    leg_b = SimpleNamespace(id=2, match_id=12, market="double_chance_12", odds_snapshot=1.5, result=None, settled_at=None)
    accumulator = SimpleNamespace(
        id=99,
        status=CustomAccumulatorStatus.PLACED,
        legs=[leg_a, leg_b],
        stake=10.0,
        actual_return=None,
        settled_at=None,
        settlement_details=None,
    )
    db = SimpleNamespace(
        execute=AsyncMock(return_value=_UniqueRows([accumulator])),
        get=AsyncMock(side_effect=[
            SimpleNamespace(id=11, status=MatchStatus.FINISHED, home_goals=2, away_goals=1),
            SimpleNamespace(id=12, status=MatchStatus.FINISHED, home_goals=0, away_goals=3),
        ]),
        add=Mock(),
    )

    changed = await SettlementService(db)._settle_custom_accumulators(
        {11, 12}, "test", datetime_now_utc()
    )

    assert changed == 1
    assert accumulator.status == CustomAccumulatorStatus.WON
    assert accumulator.actual_return == 30.0


def test_settle_finished_matches_accepts_a_kickoff_window():
    """The periodic settlement path must be able to bound its scan."""
    import inspect

    signature = inspect.signature(SettlementService.settle_finished_matches)
    assert "since" in signature.parameters
    # Full-archive callers (historical sync) rely on the unbounded default.
    assert signature.parameters["since"].default is None


def test_settlement_scan_includes_voidable_match_states():
    """Automatic reconciliation must be able to close postponed fixtures as void."""
    import inspect

    source = inspect.getsource(SettlementService.settle_finished_matches)
    assert "MatchStatus.POSTPONED" in source
    assert "MatchStatus.CANCELLED" in source


def datetime_now_utc():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
