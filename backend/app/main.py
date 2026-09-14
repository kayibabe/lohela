"""
Lohela — Football Intelligence & Accumulator Analytics Platform
FastAPI application entry point — spec §32, §35.
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import CURRENT_MODEL_VERSION, settings
from app.database import engine, Base
from app.database import AsyncSessionLocal
from app.api.v1 import router as api_v1_router

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Create tables on startup (development convenience — production uses Alembic)
    if settings.app_env == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables created/verified")

    # Schedule daily pipeline — spec §34
    scheduler.add_job(
        _trigger_daily_pipeline,
        CronTrigger(hour=settings.pipeline_early_cron_hour, minute=settings.pipeline_early_cron_minute),
        args=["early"],
        id="daily_pipeline_early",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _trigger_daily_pipeline,
        CronTrigger(hour=settings.pipeline_morning_cron_hour, minute=settings.pipeline_morning_cron_minute),
        args=["morning"],
        id="daily_pipeline_morning",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    scheduler.add_job(
        _trigger_settlement,
        IntervalTrigger(minutes=settings.settlement_interval_minutes),
        id="paper_ticket_settlement",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _trigger_live_status,
        IntervalTrigger(minutes=1),
        id="live_match_status_refresh",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    scheduler.add_job(
        _trigger_automation_watchdog,
        IntervalTrigger(minutes=settings.pipeline_watchdog_interval_minutes),
        id="pipeline_automation_watchdog",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
    )
    if settings.learning_shadow_schedule_enabled:
        scheduler.add_job(
            _trigger_shadow_learning,
            CronTrigger(
                day_of_week=settings.learning_shadow_cron_day,
                hour=settings.learning_shadow_cron_hour,
                minute=settings.learning_shadow_cron_minute,
            ),
            id="weekly_shadow_model_learning",
            replace_existing=True,
        )
    scheduler.start()
    logger.info(
        "Pipeline scheduled at %02d:%02d UTC and %02d:%02d UTC (00:15 and 05:00 CAT)",
        settings.pipeline_early_cron_hour, settings.pipeline_early_cron_minute,
        settings.pipeline_morning_cron_hour, settings.pipeline_morning_cron_minute,
    )
    await _run_startup_automation()

    yield

    scheduler.shutdown(wait=False)


async def _trigger_daily_pipeline(scheduled_window: str):
    """Trigger the Celery pipeline chain from APScheduler."""
    async with _scheduler_leadership(f"daily:{scheduled_window}") as leader:
        if not leader:
            return
        await _queue_daily_pipeline(scheduled_window)


async def _queue_daily_pipeline(scheduled_window: str):
    """Queue daily work after scheduler leadership has been acquired."""
    from app.tasks.pipeline import run_daily_pipeline
    from app.config import cat_today

    today = cat_today().isoformat()
    run_daily_pipeline.delay(today, "scheduled", scheduled_window, True)
    logger.info(
        "Daily pipeline task queued for %s (%s window)", today, scheduled_window
    )


async def _trigger_settlement(trigger_source: str = "periodic"):
    """Re-evaluate finished matches and settle paper tickets idempotently."""
    async with _scheduler_leadership("settlement") as leader:
        if not leader:
            return
        await _queue_settlement(trigger_source)


async def _queue_settlement(trigger_source: str):
    """Queue settlement after scheduler leadership has been acquired."""
    from app.tasks.pipeline import settle_results

    settle_results.delay(None, trigger_source)
    logger.info("%s result reconciliation task queued", trigger_source.capitalize())


async def _trigger_live_status():
    """Keep active match status and scorelines current independently of the daily pipeline."""
    async with _scheduler_leadership("live_status") as leader:
        if not leader:
            return
        await _queue_live_status()


async def _queue_live_status():
    """Queue live refresh after scheduler leadership has been acquired."""
    from app.tasks.pipeline import refresh_live_status
    refresh_live_status.delay()
    logger.info("Live match status refresh task queued")


async def _trigger_automation_watchdog(trigger_source: str = "watchdog"):
    """Queue one bounded catch-up when the latest due CAT window was missed."""
    if not settings.pipeline_startup_catchup_enabled:
        return

    async with _scheduler_leadership("watchdog") as leader:
        if not leader:
            return
        await _run_automation_watchdog(trigger_source)


async def _run_automation_watchdog(trigger_source: str):
    """Assess and queue catch-up work after scheduler leadership is acquired."""

    from app.database import AsyncSessionLocal
    from app.services.automation import assess_due_pipeline, latest_due_pipeline_window
    from app.tasks.pipeline import run_daily_pipeline

    window = latest_due_pipeline_window()
    if window is None:
        logger.info("Pipeline watchdog: no daily window is due yet")
        return
    async with AsyncSessionLocal() as db:
        decision = await assess_due_pipeline(db, window)
    if not decision.should_queue:
        logger.info(
            "Pipeline watchdog: no catch-up needed for %s/%s (%s, run=%s)",
            window.target_date,
            window.name,
            decision.reason,
            decision.existing_run_id,
        )
        return
    run_daily_pipeline.delay(
        window.target_date.isoformat(), trigger_source, window.name, True
    )
    logger.warning(
        "Pipeline watchdog queued %s catch-up for %s: %s",
        window.name,
        window.target_date,
        decision.reason,
    )


@asynccontextmanager
async def _scheduler_leadership(scope: str):
    """Use a short PostgreSQL advisory lock so only one API replica schedules work."""
    if not settings.scheduler_leader_lock_enabled:
        yield True
        return
    from sqlalchemy import text

    async with AsyncSessionLocal() as db:
        key = f"{settings.scheduler_leader_lock_name}:{scope}"
        acquired = (
            await db.execute(text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"), {"key": key})
        ).scalar()
        if not acquired:
            logger.debug("Scheduler leadership busy for %s", scope)
            yield False
            return
        try:
            yield True
        finally:
            await db.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"), {"key": key})
            await db.commit()


async def _run_startup_automation():
    """Recover missed data work immediately whenever the app comes back online."""
    await _trigger_automation_watchdog("startup_catchup")
    await _trigger_settlement("startup")


async def _trigger_shadow_learning():
    """Queue a no-lookahead challenger; this task can never promote it."""
    async with _scheduler_leadership("shadow_learning") as leader:
        if not leader:
            return
        await _queue_shadow_learning()


async def _queue_shadow_learning():
    """Queue shadow learning after scheduler leadership has been acquired."""
    from app.tasks.pipeline import train_shadow_challenger

    train_shadow_challenger.delay()
    logger.info("Weekly shadow model-learning task queued")


app = FastAPI(
    title="Lohela Intelligence API",
    description="Football Intelligence & Accumulator Analytics Platform — Internal API",
    version="0.2.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.include_router(api_v1_router)


@app.get("/health")
async def health():
    """Liveness: the process is up and serving. Deliberately dependency-free.

    Use /health/ready for container and load-balancer checks; a liveness probe
    that fails on a transient database blip causes restart loops.
    """
    return {"status": "ok", "version": CURRENT_MODEL_VERSION, "env": settings.app_env}


@app.get("/health/ready")
async def readiness(response: Response):
    """Readiness: verifies the app can actually reach Postgres and Redis.

    Returns 503 when a dependency is unreachable so orchestrators stop routing
    traffic instead of seeing a green check on a functionally dead app.
    """
    from sqlalchemy import text

    from app.services import cache

    checks: dict[str, str] = {}

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.warning("Readiness: database unreachable: %s", exc)
        checks["database"] = f"error: {type(exc).__name__}"

    try:
        client = await cache._get_client()
        await client.ping()
        checks["redis"] = "ok"
    except Exception as exc:
        logger.warning("Readiness: redis unreachable: %s", exc)
        checks["redis"] = f"error: {type(exc).__name__}"

    ready = all(value == "ok" for value in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "degraded",
        "version": CURRENT_MODEL_VERSION,
        "env": settings.app_env,
        "checks": checks,
    }


# The React client calls the API with same-origin relative paths (/api/v1/...),
# so in production the built SPA is served from this app rather than a separate
# host. That keeps one Railway service and needs no CORS. In development Vite
# serves the SPA and proxies /api here, so this mount is simply absent.
FRONTEND_DIR = (Path(__file__).resolve().parent.parent / "static").resolve()

# Paths the SPA fallback must never answer for, so a mistyped API route returns
# a JSON 404 instead of silently returning the HTML shell with status 200.
_RESERVED_PREFIXES = ("api", "docs", "redoc", "openapi.json", "health")


def _mount_frontend(application: FastAPI) -> None:
    index_file = FRONTEND_DIR / "index.html"
    if not index_file.is_file():
        logger.info("No frontend build at %s — running API-only", FRONTEND_DIR)
        return

    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.is_dir():
        application.mount(
            "/assets", StaticFiles(directory=assets_dir), name="frontend-assets"
        )

    @application.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa(full_path: str):
        if full_path.split("/", 1)[0] in _RESERVED_PREFIXES:
            raise HTTPException(status_code=404, detail="Not Found")

        if full_path:
            candidate = (FRONTEND_DIR / full_path).resolve()
            # Containment check keeps ../ traversal out of the response.
            if candidate.is_file() and FRONTEND_DIR in candidate.parents:
                return FileResponse(candidate)

        # Unknown path: hand back the shell so client-side routing can resolve it.
        return FileResponse(index_file)

    logger.info("Serving frontend build from %s", FRONTEND_DIR)


# Registered last so the API router, /docs and /health always match first.
_mount_frontend(app)
