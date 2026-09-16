"""Admin endpoints — pipeline management, cache control, system stats."""

from datetime import date

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
from app.config import cat_today
from app.api.security import require_research_access
from app.database import get_db
from app.models import AutomationAlert, PipelineRun
from app.services.pipeline_tracker import infer_pipeline_run_type

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Pipeline ─────────────────────────────────────────────────────────────────

class PipelineTriggerResponse(BaseModel):
    status: str
    target_date: str
    message: str


class HistoricalSyncRequest(BaseModel):
    period_start: date
    period_end: date


@router.post("/historical/sync")
async def sync_historical_results(payload: HistoricalSyncRequest):
    """Queue historical fixture/result ingestion and paper-ticket settlement."""
    if payload.period_end < payload.period_start:
        raise HTTPException(422, "period_end must be on or after period_start")
    from app.tasks.pipeline import sync_historical_results as task
    task.delay(payload.period_start.isoformat(), payload.period_end.isoformat())
    return {
        "status": "queued",
        "period_start": payload.period_start.isoformat(),
        "period_end": payload.period_end.isoformat(),
        "message": "Historical fixture ingestion and settlement queued.",
    }


@router.get("/pipeline/status")
async def pipeline_status(limit: int = Query(default=5, ge=1, le=20), db=Depends(get_db)):
    result = await db.execute(
        select(PipelineRun).options(selectinload(PipelineRun.stages))
        .order_by(PipelineRun.id.desc()).limit(limit)
    )
    runs = []
    for run in result.scalars().all():
        run_type = infer_pipeline_run_type(run)
        runs.append({
            "id": run.id, "target_date": run.target_date.isoformat(),
            "run_type": run_type,
            "trigger_source": (run.run_details or {}).get("trigger_source"),
            "scheduled_window": (run.run_details or {}).get("scheduled_window"),
            "status": run.status.value, "current_stage": run.current_stage,
            "error_details": run.error_details,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "stages": [{"name": s.stage_name, "status": s.status.value,
                        "retry_count": s.retry_count, "input_count": s.input_count,
                        "output_count": s.output_count, "error_details": s.error_details,
                        "details": s.stage_details} for s in run.stages],
        })
    return {"runs": runs}


@router.post("/pipeline/trigger", response_model=PipelineTriggerResponse)
async def trigger_pipeline(target_date: str = Query(default=None)):
    """Manually fire the full ingest → odds → enrich → model pipeline for a date."""
    from app.tasks.pipeline import run_daily_pipeline
    td = target_date or cat_today().isoformat()
    try:
        run_daily_pipeline.delay(td, "manual", None, False)
    except Exception as exc:
        raise HTTPException(503, f"Could not enqueue pipeline: {exc}")
    return PipelineTriggerResponse(
        status="queued",
        target_date=td,
        message=f"Pipeline queued for {td} — check Celery worker logs for progress.",
    )


@router.get("/pipeline/schedule")
async def pipeline_schedule():
    """Return the configured cron schedule for the daily pipeline."""
    from app.config import settings
    from app.main import scheduler
    from app.services.automation import latest_due_pipeline_window

    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run_utc": job.next_run_time.isoformat() if job.next_run_time else None,
        })
    due = latest_due_pipeline_window()
    return {
        "schedules": [
            {"name": "early", "hour_utc": settings.pipeline_early_cron_hour, "minute_utc": settings.pipeline_early_cron_minute, "time_cat": "00:15"},
            {"name": "morning", "hour_utc": settings.pipeline_morning_cron_hour, "minute_utc": settings.pipeline_morning_cron_minute, "time_cat": "05:00"},
        ],
        "jobs": jobs,
        "automation": {
            "startup_catchup_enabled": settings.pipeline_startup_catchup_enabled,
            "watchdog_interval_minutes": settings.pipeline_watchdog_interval_minutes,
            "automatic_retry_limit": settings.pipeline_automatic_retry_limit,
            "settlement_interval_minutes": settings.settlement_interval_minutes,
            "settlement_startup_lookback_days": settings.settlement_startup_lookback_days,
            "scheduler_leader_lock_enabled": settings.scheduler_leader_lock_enabled,
            "scheduler_leader_lock_name": settings.scheduler_leader_lock_name,
            "latest_due_window": due.name if due else None,
            "latest_due_target_date": due.target_date.isoformat() if due else None,
        },
    }


@router.get("/alerts")
async def automation_alerts(
    include_resolved: bool = Query(default=False),
    db=Depends(get_db),
):
    query = select(AutomationAlert).order_by(AutomationAlert.last_seen_at.desc()).limit(100)
    if not include_resolved:
        query = query.where(AutomationAlert.resolved == False)
    rows = (await db.execute(query)).scalars().all()
    return {
        "alerts": [
            {
                "id": row.id,
                "severity": row.severity,
                "task_name": row.task_name,
                "target_date": row.target_date.isoformat() if row.target_date else None,
                "title": row.title,
                "detail": row.detail,
                "context": row.context,
                "occurrence_count": row.occurrence_count,
                "resolved": row.resolved,
                "first_seen_at": row.first_seen_at.isoformat(),
                "last_seen_at": row.last_seen_at.isoformat(),
                "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
            }
            for row in rows
        ]
    }


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: int, db=Depends(get_db)):
    from app.services.automation_alerts import resolve_automation_alert

    if not await resolve_automation_alert(db, alert_id):
        raise HTTPException(404, "Automation alert not found")
    await db.commit()
    return {"status": "resolved", "alert_id": alert_id}


# ── Cache ─────────────────────────────────────────────────────────────────────

@router.get("/cache/stats")
async def cache_stats():
    """Return count of cached keys by category."""
    from app.services import cache as _cache
    return await _cache.stats()


@router.delete("/cache/clear", dependencies=[Depends(require_research_access)])
async def cache_clear(prefix: str = Query(default=None)):
    """
    Clear cached API responses.
    Pass ?prefix=fixtures|odds|stats|injuries to clear a specific category,
    or omit to clear everything.
    """
    from app.services import cache as _cache
    if prefix:
        deleted = await _cache.delete_prefix(prefix)
    else:
        deleted = await _cache.flush_all()
    return {"deleted_keys": deleted}


# ── System stats ──────────────────────────────────────────────────────────────

@router.get("/system/stats")
async def system_stats():
    """High-level DB counts for the admin dashboard."""
    from sqlalchemy import select, func
    from app.database import AsyncSessionLocal
    from app.models import Match, Prediction, Competition, Team
    from app.models.bet import Bet

    async with AsyncSessionLocal() as db:
        counts = {}
        for model, key in [
            (Match, "matches"),
            (Prediction, "predictions"),
            (Competition, "competitions"),
            (Team, "teams"),
            (Bet, "bets"),
        ]:
            result = await db.execute(select(func.count()).select_from(model))
            counts[key] = result.scalar()

    return counts
