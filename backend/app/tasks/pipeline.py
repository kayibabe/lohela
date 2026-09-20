"""Celery tasks for the durable twelve-stage research pipeline."""

import asyncio
import logging
from datetime import date, timedelta

from celery import Celery
from celery.signals import task_failure

from app.config import CURRENT_MODEL_VERSION, cat_today, settings

logger = logging.getLogger(__name__)

celery_app = Celery("lohela", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
)


@task_failure.connect
def _record_final_retry_alert(sender=None, task_id=None, exception=None, args=None, kwargs=None, **_):
    """Persist one actionable alert only after a Celery task exhausts retries."""
    task_name = getattr(sender, "name", "") or ""
    if not task_name.startswith("pipeline."):
        return
    request = getattr(sender, "request", None)
    retries = getattr(request, "retries", 0) if request else 0
    max_retries = getattr(sender, "max_retries", None)
    if max_retries is None or retries < max_retries:
        return
    values = list(args or [])
    target_date = (kwargs or {}).get("target_date") or (values[0] if values and isinstance(values[0], str) else None)
    trigger_source = (kwargs or {}).get("trigger_source")
    dedupe_key = f"{task_name}:{target_date or 'global'}:{trigger_source or 'automatic'}"

    async def _save():
        from app.database import AsyncSessionLocal
        from app.services.automation_alerts import record_automation_alert

        async with AsyncSessionLocal() as db:
            await record_automation_alert(
                db,
                dedupe_key=dedupe_key,
                task_name=task_name,
                target_date=date.fromisoformat(target_date) if target_date and len(target_date) == 10 else None,
                title=f"Automation failed: {task_name.removeprefix('pipeline.')}",
                detail=f"{task_name} exhausted {max_retries} retries: {exception}",
                context={"task_id": task_id, "retry_count": retries, "trigger_source": trigger_source},
            )
            await db.commit()

    try:
        _run_async(_save())
    except Exception:
        logger.exception("Could not persist final retry alert for %s", task_name)


def _run_async(coro):
    async def _managed():
        try:
            return await coro
        finally:
            from app.services import cache
            await cache.close()

    return asyncio.run(_managed())


async def _track(
    pipeline_run_id: int | None,
    stages: list[str],
    status,
    **kwargs,
):
    if pipeline_run_id is None:
        return
    from app.database import AsyncSessionLocal
    from app.services.pipeline_tracker import update_stages

    async with AsyncSessionLocal() as db:
        await update_stages(db, pipeline_run_id, stages, status, **kwargs)
        await db.commit()


def _track_quietly(pipeline_run_id: int | None, stages: list[str], status, **kwargs):
    """Best-effort stage tracking on a failure path.

    A tracking outage must never mask the underlying task error or prevent
    the Celery retry that follows it.
    """
    try:
        _run_async(_track(pipeline_run_id, stages, status, **kwargs))
    except Exception:
        logger.exception("Stage tracking failed for %s (%s)", stages, status)


@celery_app.task(name="pipeline.ingest_fixtures", bind=True, max_retries=3)
def ingest_fixtures(
    self,
    from_date: str | None = None,
    to_date: str | None = None,
    pipeline_run_id: int | None = None,
):
    from app.database import AsyncSessionLocal
    from app.models import RunStatus
    from app.services.fixture_ingestor import FixtureIngestor

    stages = ["fixture_and_odds_ingestion"]

    async def _run():
        async with AsyncSessionLocal() as db:
            result = await FixtureIngestor(db).ingest(
                date.fromisoformat(from_date) if from_date else None,
                date.fromisoformat(to_date) if to_date else None,
            )
            await db.commit()
            return result

    try:
        _run_async(_track(pipeline_run_id, stages, RunStatus.RUNNING, retry_count=self.request.retries))
        result = _run_async(_run())
        _run_async(_track(pipeline_run_id, stages, RunStatus.COMPLETED, details=result, output_count=_count(result)))
        return result
    except Exception as exc:
        _track_quietly(pipeline_run_id, stages, RunStatus.FAILED, retry_count=self.request.retries + 1, error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="pipeline.fetch_odds", bind=True, max_retries=2)
def fetch_odds(self, target_date: str | None = None, pipeline_run_id: int | None = None):
    from app.database import AsyncSessionLocal
    from app.models import RunStatus
    from app.services.odds_fetcher import LiveOddsFetcher

    stages = ["fixture_and_odds_ingestion"]

    async def _run():
        async with AsyncSessionLocal() as db:
            target = date.fromisoformat(target_date) if target_date else None
            result = await LiveOddsFetcher(db).fetch_all(target)
            await db.commit()
            return result

    try:
        result = _run_async(_run())
        status = RunStatus.PARTIAL if result.get("status") in {"partial", "skipped"} else RunStatus.COMPLETED
        _run_async(_track(pipeline_run_id, stages, status, details={"odds": result}, output_count=_count(result)))
        return result
    except Exception as exc:
        _track_quietly(pipeline_run_id, stages, RunStatus.PARTIAL, retry_count=self.request.retries + 1, error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="pipeline.refresh_live_status", bind=True, max_retries=2)
def refresh_live_status(self):
    """Poll API-Football's live feed and persist score/phase transitions."""
    from app.database import AsyncSessionLocal
    from app.services.fixture_ingestor import FixtureIngestor

    async def _run():
        async with AsyncSessionLocal() as db:
            refreshed = await FixtureIngestor(db).refresh_live_status()

            from app.services.clv import capture_closing_odds
            clv = await capture_closing_odds(db)

            settlement = {
                "selections_settled": 0,
                "tickets_settled": 0,
                "individual_bets_settled": 0,
                "custom_accumulators_settled": 0,
            }
            if refreshed.get("finished", 0) > 0:
                from app.config import cat_today
                from app.services.settlement import SettlementService

                # Live fixtures that just finished kicked off today, or
                # yesterday for matches running past midnight CAT.
                settlement = await SettlementService(db).settle_finished_matches(
                    source="live_status", since=cat_today() - timedelta(days=1)
                )
                await db.commit()
            return {**refreshed, "settlement": settlement, "clv": clv}

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.exception("Live status refresh failed")
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="pipeline.enrich_data", bind=True, max_retries=3)
def enrich_data(self, target_date: str | None = None, pipeline_run_id: int | None = None):
    from app.database import AsyncSessionLocal
    from app.models import RunStatus
    from app.services.data_enricher import DataEnricher
    from app.services.model_preparation import ModelPreparationService

    stages = ["data_enrichment"]

    async def _run():
        async with AsyncSessionLocal() as db:
            target = date.fromisoformat(target_date) if target_date else cat_today()
            preparation = await ModelPreparationService(db).prepare(target)
            count = await DataEnricher(db).enrich_all_scheduled(
                target
            )
            await db.commit()
            return {"enriched_matches": count, "model_preparation": preparation}

    try:
        _run_async(_track(pipeline_run_id, stages, RunStatus.RUNNING, retry_count=self.request.retries))
        result = _run_async(_run())
        _run_async(_track(pipeline_run_id, stages, RunStatus.COMPLETED, details=result, output_count=_count(result)))
        return result
    except Exception as exc:
        _track_quietly(pipeline_run_id, stages, RunStatus.FAILED, retry_count=self.request.retries + 1, error=str(exc))
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="pipeline.run_models", bind=True, max_retries=2)
def run_models(
    self,
    target_date: str | None = None,
    model_version: str = CURRENT_MODEL_VERSION,
    pipeline_run_id: int | None = None,
):
    from app.database import AsyncSessionLocal
    from app.models import RunStatus
    from app.services.model_runner import ModelRunner

    stages = ["model_execution", "ensemble", "market_comparison", "q_score"]

    async def _run():
        async with AsyncSessionLocal() as db:
            result = await ModelRunner(db, model_version=model_version).run(
                date.fromisoformat(target_date) if target_date else None
            )
            await db.commit()
            return result

    try:
        _run_async(_track(pipeline_run_id, stages, RunStatus.RUNNING, retry_count=self.request.retries))
        result = _run_async(_run())
        _run_async(_track(pipeline_run_id, stages, RunStatus.COMPLETED, details=result, output_count=result.get("predictions", 0)))
        return result
    except Exception as exc:
        _track_quietly(pipeline_run_id, stages, RunStatus.FAILED, retry_count=self.request.retries + 1, error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="pipeline.generate_tickets", bind=True, max_retries=2)
def generate_tickets(
    self,
    model_result: dict,
    target_date: str,
    pipeline_run_id: int | None = None,
):
    from app.database import AsyncSessionLocal
    from app.models import RunStatus
    from app.services.automation_alerts import (
        record_automation_alert,
        resolve_automation_alert_by_dedupe_key,
    )
    from app.services.ticket_publisher import TicketPublisher

    stages = ["correlation_analysis", "accumulator_generation", "risk_validation", "publication"]

    model_run_id = model_result.get("model_run_id") if isinstance(model_result, dict) else None

    async def _run():
        async with AsyncSessionLocal() as db:
            generation = await TicketPublisher(db).generate_and_publish(
                date.fromisoformat(target_date), model_run_id=model_run_id,
                pipeline_run_id=pipeline_run_id,
            )
            publication_summary = generation.config_snapshot.get("publication_summary", {})
            missing_public_types = publication_summary.get("missing_public_ticket_types", [])
            public_published = 3 - len(missing_public_types)
            dedupe_key = f"min_daily_tickets:{target_date}"
            if public_published < settings.min_daily_public_tickets:
                await record_automation_alert(
                    db,
                    dedupe_key=dedupe_key,
                    task_name="pipeline.generate_tickets",
                    target_date=date.fromisoformat(target_date),
                    title=f"Only {public_published}/{settings.min_daily_public_tickets} public tickets published",
                    detail=(
                        f"Ticket generation for {target_date} published {public_published} of "
                        f"{settings.min_daily_public_tickets} required public tickets even after "
                        f"the relaxation fallback (qualified pool: {generation.input_count} legs). "
                        f"Missing tiers: {missing_public_types or 'all'}."
                    ),
                    context={
                        "generation_id": generation.id,
                        "qualified_pool": generation.input_count,
                        "missing_public_ticket_types": missing_public_types,
                        "relaxed_ticket_types": publication_summary.get("relaxed_ticket_types", {}),
                    },
                    severity="error" if public_published == 0 else "warning",
                )
            else:
                await resolve_automation_alert_by_dedupe_key(db, dedupe_key)
            result = {
                "generation_id": generation.id,
                "model_run_id": model_run_id,
                "model_version": model_result.get("model_version"),
                "published_tickets": generation.output_count,
                "qualified_pool": generation.input_count,
                "status": generation.status.value,
                "missing_public_ticket_types": missing_public_types,
                "public_published": public_published,
            }
            await db.commit()
            return result

    try:
        _run_async(_track(pipeline_run_id, stages, RunStatus.RUNNING, retry_count=self.request.retries))
        if model_run_id is None:
            raise ValueError(
                "Model stage did not return a model_run_id; refusing unlinked publication"
            )
        result = _run_async(_run())
        status = RunStatus.COMPLETED if result["status"] == "completed" else RunStatus.PARTIAL
        _run_async(_track(pipeline_run_id, stages, status, details=result, input_count=result["qualified_pool"], output_count=result["published_tickets"]))
        return result
    except Exception as exc:
        _track_quietly(pipeline_run_id, stages, RunStatus.FAILED, retry_count=self.request.retries + 1, error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="pipeline.settle_results", bind=True, max_retries=2)
def settle_results(
    self,
    pipeline_run_id: int | None = None,
    trigger_source: str = "periodic",
):
    from app.database import AsyncSessionLocal
    from app.models import RunStatus
    from app.services.settlement import SettlementService

    stages = ["result_ingestion_and_settlement"]

    async def _run():
        async with AsyncSessionLocal() as db:
            # Refresh the recent fixture window before settlement. API-Football
            # is the source of truth for scores; local rows can still be
            # SCHEDULED after a match has finished.
            from app.config import cat_today
            from app.services.fixture_ingestor import FixtureIngestor

            today = cat_today()
            lookback_days = (
                settings.settlement_startup_lookback_days
                if trigger_source == "startup"
                else settings.settlement_lookback_days
            )
            period_start = today - timedelta(days=lookback_days)
            refreshed = await FixtureIngestor(db).refresh_tracked_results(
                period_start, today
            )
            result = await SettlementService(db).settle_finished_matches(
                source=f"automatic_{trigger_source}", since=period_start
            )
            await db.commit()
            report = {
                **result,
                "fixtures_refreshed": refreshed,
                "period_start": period_start.isoformat(),
                "period_end": today.isoformat(),
                "trigger_source": trigger_source,
            }
            logger.info("Automatic result sync and settlement complete: %s", report)
            return report

    try:
        _run_async(_track(pipeline_run_id, stages, RunStatus.RUNNING, retry_count=self.request.retries))
        result = _run_async(_run())
        _run_async(_track(pipeline_run_id, stages, RunStatus.COMPLETED, details=result, output_count=result["tickets_settled"]))
        return result
    except Exception as exc:
        _track_quietly(pipeline_run_id, stages, RunStatus.FAILED, retry_count=self.request.retries + 1, error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="pipeline.sync_historical_results", bind=True, max_retries=2)
def sync_historical_results(self, period_start: str, period_end: str):
    """Ingest finished fixtures for a selected range and settle paper tickets."""
    from app.database import AsyncSessionLocal
    from app.services.fixture_ingestor import FixtureIngestor
    from app.services.settlement import SettlementService

    async def _run():
        async with AsyncSessionLocal() as db:
            start = date.fromisoformat(period_start)
            end = date.fromisoformat(period_end)
            from app.models import RunStatus
            from app.services.pipeline_tracker import create_pipeline_run, update_stages

            historical_stages = (
                "fixture_and_odds_ingestion",
                "result_ingestion_and_settlement",
                "performance_aggregation_and_calibration",
            )
            run = await create_pipeline_run(
                db,
                end,
                run_details={
                    "run_type": "historical_sync",
                    "trigger_source": "manual",
                    "period_start": period_start,
                    "period_end": period_end,
                },
                stage_names=historical_stages,
            )
            await update_stages(db, run.id, ["fixture_and_odds_ingestion"], RunStatus.RUNNING,
                                 details={"period_start": period_start, "period_end": period_end})
            ingested = await FixtureIngestor(db).ingest(start, end, skip_enrichment=True)
            await update_stages(db, run.id, ["fixture_and_odds_ingestion"], RunStatus.COMPLETED,
                                 details=ingested, output_count=ingested.get("ingested", 0) + ingested.get("updated", 0))
            await update_stages(db, run.id, ["result_ingestion_and_settlement"], RunStatus.RUNNING)
            settled = await SettlementService(db).settle_finished_matches(source="historical_sync")
            await update_stages(db, run.id, ["result_ingestion_and_settlement"], RunStatus.COMPLETED,
                                 details=settled, output_count=settled["tickets_settled"])
            # Historical sync ingests and settles but never recalibrates, so
            # this stage is reported PARTIAL rather than claiming a completed
            # calibration that did not run.
            await update_stages(db, run.id, ["performance_aggregation_and_calibration"], RunStatus.PARTIAL,
                                 details={
                                     "historical_sync": True,
                                     "calibration_skipped": "historical sync does not recalibrate models",
                                 })
            await db.commit()
            return {"pipeline_run_id": run.id, "ingested": ingested, "settled": settled}

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.exception("Historical result sync failed for %s to %s", period_start, period_end)
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="pipeline.aggregate_performance", bind=True, max_retries=2)
def aggregate_performance(self, pipeline_run_id: int | None = None):
    from app.database import AsyncSessionLocal
    from app.models import RunStatus
    from app.services.performance import performance_summary
    from app.services.calibration import aggregate_calibration_snapshots
    from app.config import cat_today

    stages = ["performance_aggregation_and_calibration"]

    async def _run():
        async with AsyncSessionLocal() as db:
            performance = await performance_summary(db)
            calibration = await aggregate_calibration_snapshots(db, cat_today())
            await db.commit()
            return {**performance, "calibration": calibration}

    try:
        _run_async(_track(pipeline_run_id, stages, RunStatus.RUNNING, retry_count=self.request.retries))
        result = _run_async(_run())
        _run_async(_track(pipeline_run_id, stages, RunStatus.COMPLETED, details=result, input_count=result["settled_tickets"], output_count=result["selection_sample_size"]))
        return result
    except Exception as exc:
        _track_quietly(pipeline_run_id, stages, RunStatus.FAILED, retry_count=self.request.retries + 1, error=str(exc))
        raise self.retry(exc=exc, countdown=120)


@celery_app.task(name="pipeline.run_daily_pipeline")
def run_daily_pipeline(
    target_date: str | None = None,
    trigger_source: str = "manual",
    scheduled_window: str | None = None,
    automatic: bool = False,
):
    from app.config import cat_today
    from app.database import AsyncSessionLocal
    from app.services.automation import (
        assess_pipeline_runs,
        find_recent_active_daily_run,
        load_pipeline_runs,
        lock_daily_pipeline_date,
        pipeline_window_for_name,
    )
    from app.services.pipeline_tracker import create_pipeline_run

    today = target_date or cat_today().isoformat()
    target = date.fromisoformat(today)

    async def _create():
        async with AsyncSessionLocal() as db:
            await lock_daily_pipeline_date(db, target)
            existing_runs = await load_pipeline_runs(db, target)
            active = find_recent_active_daily_run(existing_runs)
            if active is not None:
                return None, f"daily pipeline {active.id} is already active"

            window = pipeline_window_for_name(target, scheduled_window)
            if automatic:
                if window is None:
                    return None, "automatic pipeline request has no valid scheduled window"
                decision = assess_pipeline_runs(existing_runs, window)
                if not decision.should_queue:
                    return None, decision.reason

            details = {
                "run_type": "daily_pipeline",
                "trigger_source": trigger_source,
                "scheduled_window": scheduled_window,
                "scheduled_at": window.scheduled_at.isoformat() if window else None,
                "automatic": automatic,
                "model_version": CURRENT_MODEL_VERSION,
            }
            run = await create_pipeline_run(db, target, run_details=details)
            await db.commit()
            return run.id, None

    pipeline_run_id, skip_reason = _run_async(_create())
    if pipeline_run_id is None:
        logger.info("Daily pipeline skipped for %s: %s", today, skip_reason)
        return {
            "status": "skipped",
            "target_date": today,
            "reason": skip_reason,
        }
    _daily_pipeline_canvas(today, pipeline_run_id).apply_async()
    logger.info(
        "Twelve-stage daily pipeline %d triggered for %s (%s, window=%s)",
        pipeline_run_id,
        today,
        trigger_source,
        scheduled_window,
    )
    return {
        "status": "running",
        "pipeline_run_id": pipeline_run_id,
        "target_date": today,
        "model_version": CURRENT_MODEL_VERSION,
        "trigger_source": trigger_source,
        "scheduled_window": scheduled_window,
    }


@celery_app.task(name="pipeline.train_shadow_challenger", bind=True, max_retries=1)
def train_shadow_challenger(self):
    """Train a weekly challenger on older data and leave it unpromoted."""
    from datetime import timedelta

    from app.config import cat_today
    from app.database import AsyncSessionLocal
    from app.services.model_learning import (
        resolve_active_learning_config,
        train_learning_challenger,
    )

    validation_end = cat_today() - timedelta(days=1)
    validation_start = validation_end - timedelta(days=settings.learning_validation_days - 1)
    train_end = validation_start - timedelta(days=1)
    train_start = train_end - timedelta(days=settings.learning_train_days - 1)

    async def _run():
        async with AsyncSessionLocal() as db:
            try:
                active = await resolve_active_learning_config(
                    db, CURRENT_MODEL_VERSION, validation_end
                )
                training_version = (
                    active.challenger_version if active else CURRENT_MODEL_VERSION
                )
                run = await train_learning_challenger(
                    db,
                    base_model_version=training_version,
                    train_start=train_start,
                    train_end=train_end,
                    validation_start=validation_start,
                    validation_end=validation_end,
                )
                await db.commit()
                return {
                    "learning_run_id": run.id,
                    "challenger_version": run.challenger_version,
                    "summary": run.summary,
                    "promotion_required": True,
                }
            except ValueError as exc:
                await db.rollback()
                if "already exists" in str(exc):
                    return {"status": "skipped", "reason": str(exc)}
                raise

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.exception("Weekly shadow learning failed")
        raise self.retry(exc=exc, countdown=300)


@celery_app.task(name="pipeline.capture_singles_ledger", bind=True, max_retries=1)
def capture_singles_ledger(self):
    """Freeze today's real prospective singles decision into the DB ledger.

    Outcome-blind by construction (see app.services.singles_ledger): this
    only selects from predictions already on the board, applies the fixed
    odds>1.50/probability>=0.70 research policy, and writes an insert-only
    row. A day with zero eligible picks still records a row — a no-bet day
    is valid evidence, so it is never treated as a failure or skipped.
    """
    from app.config import cat_today
    from app.database import AsyncSessionLocal
    from app.services.singles_ledger import freeze_snapshot_db

    today = cat_today()

    async def _run():
        async with AsyncSessionLocal() as db:
            result = await freeze_snapshot_db(db, today, today, CURRENT_MODEL_VERSION)
            await db.commit()
            return result

    try:
        result = _run_async(_run())
        logger.info("Singles ledger snapshot captured: %s", result)
        return result
    except Exception as exc:
        logger.exception("Singles ledger capture failed")
        raise self.retry(exc=exc, countdown=300)


def _daily_pipeline_canvas(today: str, pipeline_run_id: int):
    """Build the deterministic task graph separately so its wiring is testable."""
    from celery import chain

    return chain(
        ingest_fixtures.si(today, today, pipeline_run_id),
        fetch_odds.si(today, pipeline_run_id),
        enrich_data.si(today, pipeline_run_id),
        run_models.si(today, CURRENT_MODEL_VERSION, pipeline_run_id),
        # This mutable signature receives the exact ModelRunner result from the
        # previous stage, preserving model-run lineage into publication.
        generate_tickets.s(today, pipeline_run_id),
        settle_results.si(pipeline_run_id, "daily_pipeline"),
        aggregate_performance.si(pipeline_run_id),
    )


def _count(result) -> int:
    if isinstance(result, int):
        return result
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        numeric = [value for value in result.values() if isinstance(value, int)]
        return max(numeric, default=0)
    return 0
