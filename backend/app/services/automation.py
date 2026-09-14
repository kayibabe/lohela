"""Persistent checks that make Lohela's in-process schedules self-healing."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import CAT, settings
from app.models import PipelineRun, RunStatus
from app.services.pipeline_tracker import infer_pipeline_run_type


@dataclass(frozen=True)
class PipelineWindow:
    name: str
    target_date: date
    scheduled_at: datetime

    @property
    def scheduled_at_cat(self) -> datetime:
        return self.scheduled_at.astimezone(CAT)


@dataclass(frozen=True)
class PipelineAutomationDecision:
    should_queue: bool
    reason: str
    existing_run_id: int | None = None


def pipeline_windows_for_date(target_date: date) -> tuple[PipelineWindow, ...]:
    specs = (
        ("early", settings.pipeline_early_cron_hour, settings.pipeline_early_cron_minute),
        ("morning", settings.pipeline_morning_cron_hour, settings.pipeline_morning_cron_minute),
    )
    windows: list[PipelineWindow] = []
    for name, hour, minute in specs:
        candidates = []
        for offset in (-1, 0, 1):
            utc_day = target_date + timedelta(days=offset)
            candidate = datetime.combine(utc_day, time(hour, minute), tzinfo=timezone.utc)
            if candidate.astimezone(CAT).date() == target_date:
                candidates.append(candidate)
        if len(candidates) != 1:
            raise ValueError(f"Could not resolve {name} pipeline window for {target_date}")
        windows.append(PipelineWindow(name, target_date, candidates[0]))
    return tuple(sorted(windows, key=lambda item: item.scheduled_at))


def latest_due_pipeline_window(now: datetime | None = None) -> PipelineWindow | None:
    now_utc = _as_utc(now or datetime.now(timezone.utc))
    target_date = now_utc.astimezone(CAT).date()
    due = [
        window
        for window in pipeline_windows_for_date(target_date)
        if window.scheduled_at <= now_utc
    ]
    return due[-1] if due else None


def pipeline_window_for_name(target_date: date, name: str | None) -> PipelineWindow | None:
    if name is None:
        return None
    return next((item for item in pipeline_windows_for_date(target_date) if item.name == name), None)


async def load_pipeline_runs(db: AsyncSession, target_date: date) -> list[PipelineRun]:
    result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.target_date == target_date)
        .options(selectinload(PipelineRun.stages))
        .order_by(PipelineRun.id.desc())
    )
    return list(result.scalars().all())


def assess_pipeline_runs(
    runs: list[PipelineRun],
    window: PipelineWindow,
    *,
    now: datetime | None = None,
    stale_after_minutes: int | None = None,
    automatic_retry_limit: int | None = None,
) -> PipelineAutomationDecision:
    """Decide whether the latest due window needs one bounded automatic run."""
    now_utc = _as_utc(now or datetime.now(timezone.utc))
    stale_after = timedelta(
        minutes=stale_after_minutes or settings.pipeline_stale_after_minutes
    )
    retry_limit = automatic_retry_limit or settings.pipeline_automatic_retry_limit
    matching: list[PipelineRun] = []
    automatic_attempts = 0

    for run in runs:
        if infer_pipeline_run_type(run) != "daily_pipeline":
            continue
        details = run.run_details or {}
        run_window = details.get("scheduled_window")
        started_at = _as_utc(run.started_at)
        if run_window and run_window != window.name:
            continue
        if not run_window and started_at < window.scheduled_at:
            continue
        matching.append(run)
        if details.get("automatic") and run_window in (None, window.name):
            automatic_attempts += 1

    for run in matching:
        publication = next(
            (stage for stage in run.stages if stage.stage_name == "publication"),
            None,
        )
        if publication and publication.status in (RunStatus.COMPLETED, RunStatus.PARTIAL):
            return PipelineAutomationDecision(
                False, "publication already reached a terminal outcome", run.id
            )
        if run.status == RunStatus.COMPLETED:
            return PipelineAutomationDecision(False, "daily pipeline already completed", run.id)
        age = now_utc - _as_utc(run.started_at)
        if run.completed_at is None and age < stale_after:
            return PipelineAutomationDecision(
                False, "daily pipeline is active or within its task-retry window", run.id
            )

    if automatic_attempts >= retry_limit:
        latest_id = matching[0].id if matching else None
        return PipelineAutomationDecision(
            False,
            f"automatic retry limit ({retry_limit}) reached",
            latest_id,
        )
    if matching:
        return PipelineAutomationDecision(True, "previous daily attempt is stale or incomplete")
    return PipelineAutomationDecision(True, "latest scheduled window has no full pipeline attempt")


async def assess_due_pipeline(
    db: AsyncSession,
    window: PipelineWindow,
    *,
    now: datetime | None = None,
) -> PipelineAutomationDecision:
    return assess_pipeline_runs(
        await load_pipeline_runs(db, window.target_date),
        window,
        now=now,
    )


def find_recent_active_daily_run(
    runs: list[PipelineRun],
    *,
    now: datetime | None = None,
    stale_after_minutes: int | None = None,
) -> PipelineRun | None:
    now_utc = _as_utc(now or datetime.now(timezone.utc))
    stale_after = timedelta(
        minutes=stale_after_minutes or settings.pipeline_stale_after_minutes
    )
    for run in runs:
        if infer_pipeline_run_type(run) != "daily_pipeline":
            continue
        publication = next(
            (stage for stage in run.stages if stage.stage_name == "publication"),
            None,
        )
        publication_terminal = publication and publication.status in (
            RunStatus.COMPLETED,
            RunStatus.PARTIAL,
        )
        if (
            not publication_terminal
            and run.completed_at is None
            and now_utc - _as_utc(run.started_at) < stale_after
        ):
            return run
    return None


async def lock_daily_pipeline_date(db: AsyncSession, target_date: date) -> None:
    """Serialize full-run admission on PostgreSQL; harmlessly no-op in unit DBs."""
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))"),
        {"lock_key": f"lohela:daily-pipeline:{target_date.isoformat()}"},
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
