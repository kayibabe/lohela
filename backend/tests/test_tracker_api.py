"""Regression coverage for Tracker evidence and journal accounting."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1 import bets as bets_api
from app.api.v1 import tickets as tickets_api
from app.models.bet import Bet, BetStatus


def _bet(*, status: BetStatus, actual_return: float | None, ticket_date: str) -> Bet:
    return Bet(
        id=1,
        created_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        label="Tracked bet",
        odds=2.0,
        stake=10.0,
        potential_return=20.0,
        ticket_date=ticket_date,
        status=status,
        actual_return=actual_return,
    )


def test_journal_summary_uses_actual_returns_and_exact_periods():
    rows = [
        _bet(status=BetStatus.WON, actual_return=20.0, ticket_date="2026-08-30"),
        _bet(status=BetStatus.LOST, actual_return=0.0, ticket_date="2026-08-29"),
        _bet(status=BetStatus.PENDING, actual_return=None, ticket_date="2026-08-29"),
    ]
    summary = bets_api._summarize_bets(rows)
    assert summary.total_bets == 3
    assert summary.settled_bets == 2
    assert summary.staked == 20
    assert summary.returned == 20
    assert summary.profit_loss == 0
    assert summary.hit_rate == .5
    assert summary.roi == 0
    assert summary.by_year[0].label == "2026"
    assert len(summary.by_date) == 2


@pytest.mark.asyncio
async def test_cashout_requires_an_actual_return():
    db = SimpleNamespace(get=AsyncMock(return_value=_bet(status=BetStatus.PENDING, actual_return=None, ticket_date="2026-08-30")))
    with pytest.raises(HTTPException, match="Actual return is required"):
        await bets_api.settle_bet(1, bets_api.BetSettle(status="cashout"), db)


class _Result:
    def __init__(self, rows): self.rows = rows
    def scalars(self): return self
    def all(self): return self.rows


@pytest.mark.asyncio
async def test_ticket_history_can_return_superseded_versions(monkeypatch):
    versions = [SimpleNamespace(id=2), SimpleNamespace(id=1)]
    db = SimpleNamespace(execute=AsyncMock(return_value=_Result(versions)))
    monkeypatch.setattr(tickets_api, "_history_ticket", lambda ticket: ticket.id)
    result = await tickets_api.get_ticket_history(limit=200, include_internal=True, include_superseded=True, db=db)
    assert result == [2, 1]
