"""Regression coverage for persisted daily-ticket generation context."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1 import tickets as tickets_api
from app.models import RunStatus


class _Result:
    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows or []

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows


@pytest.mark.asyncio
async def test_daily_tickets_keeps_candidate_count_when_generation_publishes_nothing(
    monkeypatch,
):
    generation = SimpleNamespace(
        id=19,
        input_count=97,
        output_count=0,
        status=RunStatus.PARTIAL,
        config_snapshot={
            "publication_summary": {
                "published_ticket_types": [],
                "missing_public_ticket_types": ["safe", "balanced"],
            }
        },
    )
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(scalar=generation),
                _Result(rows=[]),
                _Result(rows=[]),
            ]
        )
    )
    monkeypatch.setattr(
        tickets_api,
        "get_latest_published_tickets",
        AsyncMock(return_value=[]),
    )

    response = await tickets_api.get_daily_tickets(date(2026, 8, 31), db)

    assert response.qualified_pool == 97
    assert response.generation_id == 19
    assert response.generation_status == "partial"
    assert response.generated_ticket_count == 0
    assert response.missing_public_ticket_types == ["safe", "balanced"]
    assert response.pipeline_run_id is None
    assert response.pipeline_status is None
    assert response.conservative is None
    assert response.balanced is None
    assert "aggressive" not in type(response).model_fields


@pytest.mark.asyncio
async def test_daily_tickets_exposes_valid_partial_generation_rows(monkeypatch):
    generation = SimpleNamespace(
        id=20,
        input_count=143,
        output_count=2,
        status=RunStatus.PARTIAL,
        config_snapshot={
            "publication_summary": {
                "published_ticket_types": ["safe", "best_value"],
                "missing_public_ticket_types": ["balanced"],
            }
        },
    )
    safe_ticket = SimpleNamespace(id=1, ticket_type="safe", generation=SimpleNamespace(input_count=143))
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(scalar=generation),
                _Result(rows=[]),
                _Result(rows=[safe_ticket]),
            ]
        )
    )
    monkeypatch.setattr(
        tickets_api,
        "get_latest_published_tickets",
        AsyncMock(return_value=[safe_ticket]),
    )
    monkeypatch.setattr(
        tickets_api,
        "_ticket",
        lambda ticket, reveal: SimpleNamespace(ticket_id=ticket.id),
    )
    monkeypatch.setattr(
        tickets_api,
        "DailyTicketsOut",
        lambda **kwargs: SimpleNamespace(**kwargs),
    )

    response = await tickets_api.get_daily_tickets(date(2026, 9, 17), db)

    assert response.generated_ticket_count == 2
    assert response.conservative.ticket_id == 1
    assert response.balanced is None
    assert not hasattr(response, "aggressive")


@pytest.mark.asyncio
async def test_daily_tickets_exposes_fallback_pool_context(monkeypatch):
    generation = SimpleNamespace(
        id=21,
        input_count=0,
        output_count=2,
        status=RunStatus.PARTIAL,
        config_snapshot={
            "publication_summary": {
                "published_ticket_types": ["safe", "balanced"],
                "missing_public_ticket_types": ["high_odds"],
                "relaxed_ticket_types": {"safe": 2},
                "horizon_ticket_types": {"safe": 1},
            },
            "selection_diagnostics": {
                "pricing": {"source": "market"},
                "horizon": {
                    "horizon_dates": ["2026-10-07"],
                    "horizon_pool_count": 51,
                },
            },
        },
    )
    safe_ticket = SimpleNamespace(id=2, ticket_type="safe", generation=SimpleNamespace(input_count=0))
    db = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(scalar=generation),
                _Result(rows=[]),
                _Result(rows=[]),
            ]
        )
    )
    monkeypatch.setattr(tickets_api, "get_latest_published_tickets", AsyncMock(return_value=[safe_ticket]))
    monkeypatch.setattr(tickets_api, "_ticket", lambda ticket, reveal: SimpleNamespace(ticket_id=ticket.id))
    monkeypatch.setattr(tickets_api, "DailyTicketsOut", lambda **kwargs: SimpleNamespace(**kwargs))

    response = await tickets_api.get_daily_tickets(date(2026, 10, 6), db)

    assert response.qualified_pool == 0
    assert response.horizon_pool == 51
    assert response.effective_candidate_pool == 51
    assert response.horizon_dates == ["2026-10-07"]
    assert response.relaxed_ticket_types == {"safe": 2}
    assert response.horizon_ticket_types == {"safe": 1}
