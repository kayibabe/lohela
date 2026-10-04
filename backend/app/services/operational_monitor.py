"""Read-only production health signals and deduplicated operational alerts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AutomationAlert, Match, MatchStatus, PipelineRun, Prediction, RunStatus
from app.services.automation import assess_due_pipeline, latest_due_pipeline_window
from app.services.automation_alerts import (
    record_automation_alert,
    resolve_automation_alert_by_dedupe_key,
)
from app.services.pipeline_tracker import infer_pipeline_run_type


async def operational_snapshot(db: AsyncSession, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    latest_run = await db.scalar(
        select(PipelineRun).order_by(PipelineRun.id.desc()).limit(1)
    )
    due = latest_due_pipeline_window(now)
    due_decision = await assess_due_pipeline(db, due) if due else None
    stale_before = now - timedelta(hours=settings.max_selection_odds_age_hours)
    upcoming_stale_odds = await db.scalar(
        select(func.count())
        .select_from(Prediction)
        .join(Match)
        .where(
            Match.status == MatchStatus.SCHEDULED,
            Match.kickoff_at > now,
            Match.kickoff_at <= now + timedelta(days=1),
            (Prediction.source_odds_at.is_(None)) | (Prediction.source_odds_at < stale_before),
        )
    )
    finished_stale = await db.scalar(
        select(func.count()).select_from(Match).where(
            Match.status == MatchStatus.FINISHED,
            Match.kickoff_at >= now - timedelta(days=1),
            Match.updated_at < now - timedelta(minutes=settings.settlement_interval_minutes * 3),
        )
    )
    active_alerts = await db.scalar(
        select(func.count()).select_from(AutomationAlert).where(AutomationAlert.resolved.is_(False))
    )
    return {
        "observed_at": now.isoformat(),
        "latest_run": None if latest_run is None else {
            "id": latest_run.id,
            "target_date": latest_run.target_date.isoformat(),
            "run_type": infer_pipeline_run_type(latest_run),
            "status": latest_run.status.value,
            "current_stage": latest_run.current_stage,
            "completed_at": latest_run.completed_at.isoformat() if latest_run.completed_at else None,
        },
        "latest_due_window": None if due is None else {
            "name": due.name,
            "target_date": due.target_date.isoformat(),
            "needs_catchup": due_decision.should_queue,
            "reason": due_decision.reason,
        },
        "upcoming_stale_odds_predictions": int(upcoming_stale_odds or 0),
        "finished_match_refresh_lag": int(finished_stale or 0),
        "active_alerts": int(active_alerts or 0),
    }


async def monitor_operational_health(db: AsyncSession, now: datetime | None = None) -> dict:
    """Record only actionable drift; no model, odds, or ticket rows are changed."""
    snapshot = await operational_snapshot(db, now)
    checks = {
        "pipeline_catchup": bool((snapshot.get("latest_due_window") or {}).get("needs_catchup")),
        "stale_odds": snapshot["upcoming_stale_odds_predictions"] > 0,
        "settlement_lag": snapshot["finished_match_refresh_lag"] > 0,
    }
    descriptions = {
        "pipeline_catchup": ("Scheduled daily pipeline requires catch-up", snapshot.get("latest_due_window")),
        "stale_odds": ("Upcoming predictions have stale or missing odds", {"count": snapshot["upcoming_stale_odds_predictions"]}),
        "settlement_lag": ("Finished matches have not been refreshed recently", {"count": snapshot["finished_match_refresh_lag"]}),
    }
    for name, active in checks.items():
        key = f"operational:{name}"
        if active:
            title, context = descriptions[name]
            await record_automation_alert(
                db, dedupe_key=key, task_name="operational_monitor", title=title,
                detail=f"{title}. Inspect the operational status and pipeline before manual intervention.",
                context=context or {}, severity="warning",
            )
        else:
            await resolve_automation_alert_by_dedupe_key(db, key)
    return snapshot
