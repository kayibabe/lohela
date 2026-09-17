"""Admin endpoints — pipeline management, cache control, system stats."""

from datetime import date

from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel, Field
from app.config import cat_today
from app.api.security import require_admin_access
from app.database import get_db
from app.models import AutomationAlert, PipelineRun, User
from app.models.auth import UserSession
from app.services.auth import hash_password, normalize_email
from app.services.pipeline_tracker import infer_pipeline_run_type

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_access)],
)


# ── Pipeline ─────────────────────────────────────────────────────────────────

class PipelineTriggerResponse(BaseModel):
    status: str
    target_date: str
    message: str


class HistoricalSyncRequest(BaseModel):
    period_start: date
    period_end: date


class UserAccessUpdate(BaseModel):
    role: str | None = None
    plan: str | None = None
    account_status: str | None = None


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=6, max_length=256)
    role: str = "user"
    plan: str = "free"
    account_status: str = "active"


class AdminUserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=2, max_length=80)
    email: str | None = Field(default=None, min_length=3, max_length=320)
    password: str | None = Field(default=None, min_length=6, max_length=256)
    role: str | None = None
    plan: str | None = None
    account_status: str | None = None


def _validate_user_values(payload):
    if payload.role is not None and payload.role not in {"user", "admin"}:
        raise HTTPException(status_code=422, detail="role must be user or admin")
    if payload.plan is not None and payload.plan not in {"free", "pro"}:
        raise HTTPException(status_code=422, detail="plan must be free or pro")
    if payload.account_status is not None and payload.account_status not in {"active", "suspended", "pending"}:
        raise HTTPException(status_code=422, detail="invalid account_status")


def _admin_user_out(user: User):
    return {
        "id": user.id, "username": user.username, "email": user.email,
        "role": user.role, "plan": user.plan, "account_status": user.account_status,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.get("/users")
async def list_users(db=Depends(get_db)):
    rows = (await db.execute(select(User).order_by(User.created_at.desc(), User.id.desc()))).scalars().all()
    return {"users": [_admin_user_out(user) for user in rows]}


@router.post("/users", status_code=201)
async def create_user(payload: AdminUserCreate, db=Depends(get_db)):
    _validate_user_values(payload)
    username = payload.username.strip()
    if len(username) < 2:
        raise HTTPException(status_code=422, detail="username must contain at least 2 characters")
    email = normalize_email(payload.email)
    duplicate = await db.scalar(select(User).where(User.email == email))
    if duplicate:
        raise HTTPException(status_code=409, detail="Username or email already exists")
    user = User(username=username, email=email, password_hash=hash_password(payload.password), role=payload.role, plan=payload.plan, account_status=payload.account_status)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _admin_user_out(user)


@router.patch("/users/{user_id}")
async def update_user(user_id: int, payload: AdminUserUpdate, db=Depends(get_db)):
    _validate_user_values(payload)
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    changes = payload.model_dump(exclude_unset=True)
    if "username" in changes:
        changes["username"] = changes["username"].strip()
        if len(changes["username"]) < 2:
            raise HTTPException(status_code=422, detail="username must contain at least 2 characters")
    if "email" in changes:
        changes["email"] = normalize_email(changes["email"])
    if "password" in changes:
        changes["password_hash"] = hash_password(changes.pop("password"))
    if "email" in changes or "username" in changes:
        duplicate = await db.scalar(select(User).where(User.id != user_id).where(User.email == changes.get("email", user.email)))
        if duplicate:
            raise HTTPException(status_code=409, detail="Username or email already exists")
    for key, value in changes.items():
        setattr(user, key, value)
    if "password_hash" in changes or changes.get("account_status") in {"suspended", "pending"}:
        sessions = (await db.execute(select(UserSession).where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None)))).scalars().all()
        for session in sessions:
            session.revoked_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)
    return _admin_user_out(user)


@router.delete("/users/{user_id}", status_code=204)
async def delete_user(user_id: int, db=Depends(get_db)):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == "admin":
        admin_count = await db.scalar(select(func.count()).select_from(User).where(User.role == "admin"))
        if admin_count <= 1:
            raise HTTPException(status_code=409, detail="Cannot delete the last admin user")
    await db.delete(user)
    await db.commit()


@router.patch("/users/{user_id}/access")
async def update_user_access(user_id: int, payload: UserAccessUpdate, db=Depends(get_db)):
    return await update_user(user_id, AdminUserUpdate(**payload.model_dump()), db)


@router.post("/seed/competitions")
async def seed_competitions():
    """Seed competition rows if the table is empty. Safe to call multiple times."""
    from app.services.seed import seed_competitions_if_empty, COMPETITIONS
    from app.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        added = await seed_competitions_if_empty(db)
        if added:
            return {"status": "seeded", "added": added}
        return {"status": "already_seeded", "competition_count": len(COMPETITIONS)}


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


@router.delete("/cache/clear")
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
