"""Durable stage-level observability for the daily research pipeline."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import PipelineRun, PipelineStageRun, RunStatus


PIPELINE_STAGES = (
    "fixture_and_odds_ingestion",
    "data_enrichment",
    "model_execution",
    "ensemble",
    "market_comparison",
    "q_score",
    "correlation_analysis",
    "accumulator_generation",
    "risk_validation",
    "publication",
    "result_ingestion_and_settlement",
    "performance_aggregation_and_calibration",
)


async def create_pipeline_run(
    db: AsyncSession,
    target_date: date,
    *,
    run_details: dict | None = None,
    stage_names: Iterable[str] = PIPELINE_STAGES,
) -> PipelineRun:
    run = PipelineRun(
        target_date=target_date,
        status=RunStatus.RUNNING,
        run_details=dict(run_details or {}),
    )
    db.add(run)
    await db.flush()
    for order, name in enumerate(stage_names, start=1):
        db.add(PipelineStageRun(pipeline_run_id=run.id, stage_name=name, stage_order=order))
    await db.flush()
    return run


async def update_stages(
    db: AsyncSession,
    pipeline_run_id: int,
    stage_names: list[str],
    status: RunStatus,
    *,
    retry_count: int = 0,
    input_count: int | None = None,
    output_count: int | None = None,
    details: dict | None = None,
    error: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(PipelineStageRun).where(
            PipelineStageRun.pipeline_run_id == pipeline_run_id,
            PipelineStageRun.stage_name.in_(stage_names),
        )
    )
    stages = result.scalars().all()
    for stage in stages:
        stage.status = status
        stage.retry_count = retry_count
        if status == RunStatus.RUNNING and stage.started_at is None:
            stage.started_at = now
        if status in (RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED):
            stage.completed_at = now
        if input_count is not None:
            stage.input_count = input_count
        if output_count is not None:
            stage.output_count = output_count
        if details is not None:
            stage.stage_details = details
        stage.error_details = error
    run = await db.get(PipelineRun, pipeline_run_id)
    if run:
        run.current_stage = stage_names[-1]
        if status == RunStatus.FAILED:
            run.status = RunStatus.PARTIAL
            run.error_details = error
            run.completed_at = now  # mark done so retries aren't blocked for 45 min
        if stage_names[-1] == PIPELINE_STAGES[-1] and status in (
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
            RunStatus.FAILED,
        ):
            all_result = await db.execute(
                select(PipelineStageRun).where(PipelineStageRun.pipeline_run_id == pipeline_run_id)
            )
            all_statuses = {stage.status for stage in all_result.scalars().all()}
            incomplete = {RunStatus.PENDING, RunStatus.RUNNING} & all_statuses
            run.status = (
                RunStatus.PARTIAL
                if incomplete or RunStatus.PARTIAL in all_statuses or RunStatus.FAILED in all_statuses
                else RunStatus.COMPLETED
            )
            if incomplete:
                run.error_details = (
                    "Pipeline reached its final task while stages remained "
                    f"incomplete: {', '.join(sorted(status.value for status in incomplete))}"
                )
            run.completed_at = now
    await db.flush()


async def get_pipeline_run(db: AsyncSession, pipeline_run_id: int) -> PipelineRun | None:
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.id == pipeline_run_id)
        .options(selectinload(PipelineRun.stages))
    )
    return result.scalar_one_or_none()


def infer_pipeline_run_type(run: PipelineRun) -> str:
    """Classify legacy and tagged runs without confusing result syncs for daily runs."""
    details = run.run_details or {}
    explicit = details.get("run_type")
    if explicit:
        return str(explicit)

    stages = {stage.stage_name: stage for stage in getattr(run, "stages", [])}
    if any((stage.stage_details or {}).get("historical_sync") for stage in stages.values()):
        return "historical_sync"
    if any(
        stages.get(name) is not None
        and stages[name].status != RunStatus.PENDING
        for name in (
            "data_enrichment",
            "model_execution",
            "correlation_analysis",
            "accumulator_generation",
            "publication",
        )
    ):
        return "daily_pipeline"
    return "unknown"
