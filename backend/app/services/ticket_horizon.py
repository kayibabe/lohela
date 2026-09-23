"""Rolling-horizon support for the minimum-daily-tickets guarantee.

International breaks and quiet midweeks can leave a CAT day with almost no
modelable fixtures in the tracked leagues (e.g. 2026-09-22..10-01: 0-5 matches
a day across 24 leagues). When the target day's own slate cannot fill
`settings.min_daily_public_tickets` public tiers, the ticket stage prepares the
following days — the same ingest -> odds -> enrich -> model steps the daily
chain runs for the target day — so AccumulatorBuilder can admit legs kicking
off up to `settings.ticket_horizon_max_days` later. Each day gets its own
ordinary ModelRun, exactly as its own daily pipeline would later create.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.config import CURRENT_MODEL_VERSION, settings
from app.models import ModelRun, RunStatus

logger = logging.getLogger(__name__)


async def _recent_completed_run(db, target: date) -> ModelRun | None:
    """A completed run fresh enough that its odds still clear the stale-odds gate."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=settings.ticket_horizon_run_reuse_hours
    )
    result = await db.execute(
        select(ModelRun)
        .where(
            ModelRun.target_date == target,
            ModelRun.status == RunStatus.COMPLETED,
            ModelRun.completed_at >= cutoff,
        )
        .order_by(ModelRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def ingest_horizon_fixtures(session_factory, start: date, end: date) -> dict:
    """One fixture pull for the whole horizon (one API call per league)."""
    from app.services.fixture_ingestor import FixtureIngestor

    async with session_factory() as db:
        result = await FixtureIngestor(db).ingest(start, end)
        await db.commit()
    return result


async def prepare_horizon_date(
    session_factory, target: date, model_version: str = CURRENT_MODEL_VERSION
) -> dict:
    """Ensure `target` has a fresh completed model run. Returns a small report.

    Fixtures must already be ingested (ingest_horizon_fixtures). Each step
    commits in its own session, mirroring the separate Celery stages of the
    daily chain, so a failure part-way leaves the same state a failed daily
    stage would.
    """
    from app.services.data_enricher import DataEnricher
    from app.services.model_preparation import ModelPreparationService
    from app.services.model_runner import ModelRunner
    from app.services.odds_fetcher import LiveOddsFetcher

    async with session_factory() as db:
        existing = await _recent_completed_run(db, target)
        if existing is not None:
            return {"date": target.isoformat(), "model_run_id": existing.id, "reused": True}

    async with session_factory() as db:
        odds = await LiveOddsFetcher(db).fetch_all(target)
        await db.commit()
    async with session_factory() as db:
        await ModelPreparationService(db).prepare(target)
        enriched = await DataEnricher(db).enrich_all_scheduled(target)
        await db.commit()
    async with session_factory() as db:
        result = await ModelRunner(db, model_version=model_version).run(target)
        await db.commit()
    report = {
        "date": target.isoformat(),
        "model_run_id": result.get("model_run_id"),
        "reused": False,
        "odds_status": odds.get("status"),
        "enriched_matches": enriched,
        "predictions": result.get("predictions", 0),
    }
    logger.info("Prepared rolling-horizon date %s: %s", target, report)
    return report


async def resolve_horizon_dates(
    session_factory,
    target: date,
    model_run_id: int | None,
    *,
    prepare=prepare_horizon_date,
    ingest=ingest_horizon_fixtures,
) -> tuple[list[date], list[dict]]:
    """Extend one CAT day at a time until a preview build meets the floor.

    Returns the horizon dates to hand to the publisher (empty when the target
    day suffices, so the common path costs one extra read-only preview) and a
    per-day report for pipeline stage details. A failure preparing one day is
    recorded and the next day is still tried.
    """
    from app.services.accumulator_builder import AccumulatorBuilder

    floor = settings.min_daily_public_tickets
    max_days = settings.ticket_horizon_max_days
    if not settings.ticket_relaxation_enabled or max_days <= 0:
        return [], []

    async with session_factory() as db:
        preview = await AccumulatorBuilder(db).build(target, model_run_id=model_run_id)
    if preview.model_run_id is None or preview.public_count() >= floor:
        return [], []

    reports: list[dict] = []
    try:
        reports.append({
            "ingest": await ingest(
                session_factory, target + timedelta(days=1), target + timedelta(days=max_days)
            )
        })
    except Exception as exc:  # odds/model steps below still use whatever is stored
        logger.exception("Rolling-horizon fixture ingest failed")
        reports.append({"ingest_error": str(exc)})

    horizon: list[date] = []
    for offset in range(1, max_days + 1):
        day = target + timedelta(days=offset)
        try:
            reports.append(await prepare(session_factory, day))
        except Exception as exc:
            logger.exception("Rolling-horizon preparation failed for %s", day)
            reports.append({"date": day.isoformat(), "error": str(exc)})
            continue
        horizon.append(day)
        async with session_factory() as db:
            preview = await AccumulatorBuilder(db).build(
                target, model_run_id=model_run_id, horizon_dates=horizon
            )
        if preview.public_count() >= floor:
            break
    logger.warning(
        "Rolling horizon for %s: %d/%d public tickets using %s",
        target, preview.public_count(), floor, [d.isoformat() for d in horizon],
    )
    return horizon, reports
