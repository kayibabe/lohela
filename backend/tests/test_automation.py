"""Contracts for missed-window recovery and run-type separation."""

from datetime import date, datetime, timezone
from types import SimpleNamespace

from app.models import RunStatus
from app.services.automation import (
    PipelineWindow,
    assess_pipeline_runs,
    latest_due_pipeline_window,
    pipeline_windows_for_date,
)
from app.services.pipeline_tracker import infer_pipeline_run_type


def _stage(name: str, status: RunStatus, details: dict | None = None):
    return SimpleNamespace(
        stage_name=name,
        status=status,
        stage_details=details or {},
    )


def _run(
    run_id: int,
    *,
    started_at: datetime,
    status: RunStatus,
    details: dict | None = None,
    stages: list | None = None,
):
    return SimpleNamespace(
        id=run_id,
        target_date=started_at.date(),
        started_at=started_at,
        completed_at=None,
        status=status,
        run_details=details or {},
        stages=stages or [],
    )


def test_pipeline_windows_resolve_to_expected_cat_times():
    early, morning = pipeline_windows_for_date(date(2026, 9, 1))
    assert early.scheduled_at.isoformat() == "2026-08-31T22:15:00+00:00"
    assert early.scheduled_at_cat.isoformat() == "2026-09-01T00:15:00+02:00"
    assert morning.scheduled_at.isoformat() == "2026-09-01T03:00:00+00:00"
    assert morning.scheduled_at_cat.isoformat() == "2026-09-01T05:00:00+02:00"


def test_latest_due_window_uses_morning_after_five_cat():
    window = latest_due_pipeline_window(datetime(2026, 9, 1, 6, 25, tzinfo=timezone.utc))
    assert window is not None
    assert window.name == "morning"
    assert window.target_date == date(2026, 9, 1)


def test_historical_sync_does_not_satisfy_daily_window():
    window = PipelineWindow(
        "morning",
        date(2026, 9, 1),
        datetime(2026, 9, 1, 3, tzinfo=timezone.utc),
    )
    historical = _run(
        36,
        started_at=datetime(2026, 9, 1, 6, 26, tzinfo=timezone.utc),
        status=RunStatus.PARTIAL,
        details={"run_type": "historical_sync"},
        stages=[
            _stage(
                "performance_aggregation_and_calibration",
                RunStatus.COMPLETED,
                {"historical_sync": True},
            )
        ],
    )
    decision = assess_pipeline_runs([historical], window)
    assert decision.should_queue is True
    assert infer_pipeline_run_type(historical) == "historical_sync"


def test_legacy_daily_publication_prevents_duplicate_catchup():
    window = PipelineWindow(
        "morning",
        date(2026, 9, 1),
        datetime(2026, 9, 1, 3, tzinfo=timezone.utc),
    )
    daily = _run(
        37,
        started_at=datetime(2026, 9, 1, 6, 46, tzinfo=timezone.utc),
        status=RunStatus.PARTIAL,
        stages=[
            _stage("model_execution", RunStatus.COMPLETED),
            _stage("publication", RunStatus.PARTIAL),
        ],
    )
    decision = assess_pipeline_runs([daily], window)
    assert decision.should_queue is False
    assert decision.existing_run_id == 37
    assert infer_pipeline_run_type(daily) == "daily_pipeline"


def test_automatic_retries_are_bounded_after_stale_incomplete_runs():
    window = PipelineWindow(
        "morning",
        date(2026, 9, 1),
        datetime(2026, 9, 1, 3, tzinfo=timezone.utc),
    )
    runs = [
        _run(
            run_id,
            started_at=datetime(2026, 9, 1, 3, 5 + run_id, tzinfo=timezone.utc),
            status=RunStatus.PARTIAL,
            details={
                "run_type": "daily_pipeline",
                "scheduled_window": "morning",
                "automatic": True,
            },
            stages=[_stage("publication", RunStatus.PENDING)],
        )
        for run_id in (1, 2)
    ]
    decision = assess_pipeline_runs(
        runs,
        window,
        now=datetime(2026, 9, 1, 6, tzinfo=timezone.utc),
        automatic_retry_limit=2,
    )
    assert decision.should_queue is False
    assert "retry limit" in decision.reason


def test_recent_partial_attempt_waits_for_task_level_retries():
    window = PipelineWindow(
        "morning",
        date(2026, 9, 1),
        datetime(2026, 9, 1, 3, tzinfo=timezone.utc),
    )
    retrying = _run(
        9,
        started_at=datetime(2026, 9, 1, 5, 40, tzinfo=timezone.utc),
        status=RunStatus.PARTIAL,
        details={
            "run_type": "daily_pipeline",
            "scheduled_window": "morning",
            "automatic": True,
        },
        stages=[
            _stage("model_execution", RunStatus.FAILED),
            _stage("publication", RunStatus.PENDING),
        ],
    )
    decision = assess_pipeline_runs(
        [retrying],
        window,
        now=datetime(2026, 9, 1, 6, tzinfo=timezone.utc),
        stale_after_minutes=45,
    )
    assert decision.should_queue is False
    assert "task-retry window" in decision.reason
