from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from app.services.ticket_publisher import TicketPublisher, AccumulatorBuilder


@pytest.mark.asyncio
async def test_new_model_run_keeps_first_nonempty_daily_set(monkeypatch):
    original = SimpleNamespace(id=42)
    db = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")),
        execute=AsyncMock(side_effect=[None, SimpleNamespace(scalar_one_or_none=lambda: original)]),
    )
    builder = AsyncMock(side_effect=AssertionError("Must not rebuild published tickets"))
    monkeypatch.setattr(AccumulatorBuilder, "build", builder)
    actual = await TicketPublisher(db).generate_and_publish(date(2026, 10, 12), model_run_id=999)
    assert actual is original
    assert "pg_advisory_xact_lock" in str(db.execute.call_args_list[0].args[0])
    builder.assert_not_called()


@pytest.mark.asyncio
async def test_no_published_set_still_attempts_qualified_build(monkeypatch):
    db = SimpleNamespace(
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="sqlite")),
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None)),
    )
    builder = AsyncMock(return_value=SimpleNamespace(model_run_id=None))
    monkeypatch.setattr(AccumulatorBuilder, "build", builder)
    with pytest.raises(ValueError, match="No completed model run"):
        await TicketPublisher(db).generate_and_publish(date(2026, 10, 13))
    builder.assert_awaited_once()
