from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import operational_monitor


@pytest.mark.asyncio
async def test_operational_snapshot_reports_read_only_signals(monkeypatch):
    monkeypatch.setattr(operational_monitor, "latest_due_pipeline_window", lambda now: None)
    db = SimpleNamespace(
        scalar=AsyncMock(side_effect=[
            SimpleNamespace(
                id=7, target_date=datetime(2026, 10, 4).date(), status=SimpleNamespace(value="completed"),
                current_stage="publication", completed_at=datetime(2026, 10, 4, tzinfo=timezone.utc),
                run_details={"run_type": "daily_pipeline"}, stages=[],
            ),
            3, 2, 1,
        ])
    )
    result = await operational_monitor.operational_snapshot(
        db, datetime(2026, 10, 4, 8, tzinfo=timezone.utc)
    )
    assert result["latest_run"]["id"] == 7
    assert result["upcoming_stale_odds_predictions"] == 3
    assert result["finished_match_refresh_lag"] == 2
    assert result["active_alerts"] == 1
