"""Model management endpoints — spec §32."""

import asyncio
import logging
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, Field, model_validator

from app.database import get_db
from app.config import CURRENT_MODEL_VERSION
from app.services.model_runner import ModelRunner
from app.api.security import require_research_access

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/models", tags=["models"], dependencies=[Depends(require_research_access)]
)


class ModelRunResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    status: str
    result: dict


class LearningTrainRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    base_model_version: str = CURRENT_MODEL_VERSION
    challenger_version: str | None = None
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    min_train_samples: int = Field(100, ge=30, le=100000)
    min_validation_samples: int = Field(30, ge=10, le=100000)
    min_validation_bets: int = Field(10, ge=5, le=100000)
    regularization: float = Field(0.25, gt=0, le=10)
    min_brier_improvement: float = Field(0.0025, ge=0, le=0.25)
    max_calibration_regression: float = Field(0.01, ge=0, le=0.25)
    max_roi_regression: float = Field(0.02, ge=0, le=1)


class LearningPromotionRequest(BaseModel):
    learning_run_id: int
    effective_from: date
    promoted_by: str = Field(min_length=2, max_length=100)
    reason: str = Field(min_length=10, max_length=2000)


class HistoricalBackfillRequest(BaseModel):
    start: date
    end: date
    model_version: str = CURRENT_MODEL_VERSION
    min_history: int = Field(100, ge=30, le=10000)

    @model_validator(mode="after")
    def valid_period(self):
        if self.end < self.start:
            raise ValueError("end must be on or after start")
        return self


@router.post("/run", response_model=ModelRunResponse)
async def run_models(
    target_date: Optional[date] = None,
    model_version: str = CURRENT_MODEL_VERSION,
    db: AsyncSession = Depends(get_db),
):
    """Run all prediction models for a given date — spec §26 Stages 3–6."""
    runner = ModelRunner(db, model_version=model_version)
    try:
        result = await runner.run(target_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ModelRunResponse(status="ok", result=result)


@router.post("/learning/train", response_model=ModelRunResponse)
async def train_learning_model(
    payload: LearningTrainRequest,
    db: AsyncSession = Depends(get_db),
):
    """Train and walk-forward validate an immutable shadow challenger."""
    from app.services.model_learning import train_learning_challenger

    try:
        run = await train_learning_challenger(db, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ModelRunResponse(
        status="ok",
        result={
            "learning_run_id": run.id,
            "base_model_version": run.base_model_version,
            "challenger_version": run.challenger_version,
            "status": run.status.value,
            "summary": run.summary,
            "promotion_required": True,
        },
    )


@router.post("/learning/backfill-totals", response_model=ModelRunResponse)
async def backfill_historical_totals(
    payload: HistoricalBackfillRequest,
    db: AsyncSession = Depends(get_db),
):
    """Create detached, point-in-time totals research predictions only."""
    from app.services.historical_backfill import HistoricalTotalsBackfill

    result = await HistoricalTotalsBackfill(db, payload.model_version).run(
        payload.start, payload.end, min_history=payload.min_history
    )
    return ModelRunResponse(status="ok", result=result)


@router.post("/learning/promote", response_model=ModelRunResponse)
async def promote_learning_model(
    payload: LearningPromotionRequest,
    db: AsyncSession = Depends(get_db),
):
    """Explicitly activate only the market profiles that passed every check."""
    from app.services.model_learning import promote_learning_run

    try:
        promotion = await promote_learning_run(db, **payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ModelRunResponse(
        status="ok",
        result={
            "promotion_id": promotion.id,
            "learning_run_id": promotion.learning_run_id,
            "challenger_version": promotion.challenger_version,
            "effective_from": promotion.effective_from.isoformat(),
        },
    )


@router.get("/learning/status")
async def model_learning_status(
    limit: int = 10,
    db: AsyncSession = Depends(get_db),
):
    from app.services.model_learning import learning_status

    return await learning_status(db, max(1, min(limit, 50)))


@router.post("/update-elo", response_model=ModelRunResponse)
async def update_elo(db: AsyncSession = Depends(get_db)):
    """
    Replay all finished matches in chronological order to update Elo ratings.
    Resets all teams to 1500 first for reproducibility. Takes ~5s for 2000 matches.
    """
    from app.services.elo_updater import EloUpdater
    updater = EloUpdater(db)
    result = await updater.update_all()
    return ModelRunResponse(status="ok", result=result)


@router.post("/enrich-form", response_model=ModelRunResponse)
async def enrich_form(db: AsyncSession = Depends(get_db)):
    """
    Compute rolling form strings (form_last_5, form_last_10) for all finished
    matches in chronological order. Needed before model runs use real form scores.
    """
    from app.services.data_enricher import DataEnricher
    enricher = DataEnricher(db)
    count = await enricher.enrich_all_finished()
    return ModelRunResponse(status="ok", result={"matches_enriched": count})


@router.post("/run-bayesian", response_model=ModelRunResponse)
async def run_bayesian(
    method: str = "analytical",
    db: AsyncSession = Depends(get_db),
):
    """
    Update team attack/defense posteriors.

    method=analytical (default, fast): computes attack/defense from historical
      goals scored/conceded relative to league average. Completes in <1s.
    method=advi: PyMC variational inference. Much slower; requires patience.
      Falls back to analytical if PyMC fails (e.g., no g++ in Docker).
    """
    from sqlalchemy import select, update
    from app.models import Match, MatchStatus, Team
    from app.services.models.bayesian import _run_analytical, run_mcmc_update

    result = await db.execute(
        select(Match)
        .where(
            Match.status == MatchStatus.FINISHED,
            Match.home_goals.is_not(None),
            Match.away_goals.is_not(None),
        )
        .order_by(Match.kickoff_at.desc())
        .limit(2000)
    )
    matches = result.scalars().all()
    historical = [
        {
            "home_team_id": m.home_team_id,
            "away_team_id": m.away_team_id,
            "home_goals": m.home_goals,
            "away_goals": m.away_goals,
        }
        for m in matches
    ]

    if method == "analytical":
        method_used, posteriors = "analytical", _run_analytical(historical)
    else:
        method_used, posteriors = await asyncio.to_thread(run_mcmc_update, historical)

    if not posteriors:
        return ModelRunResponse(status="skipped", result={"reason": "No data or method failed"})

    for team_id, p in posteriors.items():
        await db.execute(
            update(Team)
            .where(Team.id == team_id)
            .values(
                bayes_attack_mean=p["attack_mean"],
                bayes_attack_std=p["attack_std"],
                bayes_defense_mean=p["defense_mean"],
                bayes_defense_std=p["defense_std"],
            )
        )
    await db.commit()

    return ModelRunResponse(
        status="ok",
        result={
            "teams_updated": len(posteriors),
            "method_requested": method,
            "method_used": method_used,
        },
    )


@router.post("/run-batch", response_model=ModelRunResponse)
async def run_models_batch(
    from_date: date,
    to_date: date,
    min_matches: int = 10,
    model_version: str = CURRENT_MODEL_VERSION,
    db: AsyncSession = Depends(get_db),
):
    """
    Run models for every date in [from_date, to_date] that has >= min_matches.
    Skips dates already having predictions for this model_version.
    Returns a summary of dates processed and total predictions written.
    """
    from sqlalchemy import select, func, cast, Date as SADate
    from app.models import Match, Prediction

    # Find distinct dates with enough matches
    result = await db.execute(
        select(
            cast(Match.kickoff_at, SADate).label("d"),
            func.count().label("cnt"),
        )
        .where(
            Match.kickoff_at >= datetime.combine(from_date, datetime.min.time()),
            Match.kickoff_at <= datetime.combine(to_date, datetime.max.time()),
            Match.excluded_from_models == False,
        )
        .group_by("d")
        .having(func.count() >= min_matches)
        .order_by("d")
    )
    dates = result.all()

    processed, skipped, total_preds = 0, 0, 0
    summary: list[dict] = []

    for row in dates:
        td = row.d

        # Skip if already has predictions for this version on this date
        existing = await db.execute(
            select(func.count(Prediction.id))
            .join(Match, Prediction.match_id == Match.id)
            .where(
                Match.kickoff_at >= datetime.combine(td, datetime.min.time()),
                Match.kickoff_at < datetime.combine(td, datetime.max.time()),
                Prediction.model_version == model_version,
            )
        )
        if existing.scalar() > 0:
            skipped += 1
            continue

        runner = ModelRunner(db, model_version=model_version)
        run_result = await runner.run(td)
        preds = run_result.get("predictions", 0)
        total_preds += preds
        processed += 1
        summary.append({"date": str(td), "predictions": preds})
        logger.info("Batch run: %s → %d predictions", td, preds)

    return ModelRunResponse(
        status="ok",
        result={
            "dates_processed": processed,
            "dates_skipped": skipped,
            "total_predictions": total_preds,
            "summary": summary,
        },
    )


@router.post("/calibrate", response_model=ModelRunResponse)
async def calibrate(db: AsyncSession = Depends(get_db)):
    """
    Full calibration pipeline: Elo update → form enrichment → Bayesian MCMC.
    Run once after bulk ingestion to prime all model inputs.
    Bayesian MCMC is skipped if PyMC is unavailable.
    """
    from app.services.elo_updater import EloUpdater
    from app.services.data_enricher import DataEnricher

    elo_updater = EloUpdater(db)
    elo_result = await elo_updater.update_all()
    logger.info("Elo update complete: %s", elo_result)

    enricher = DataEnricher(db)
    form_count = await enricher.enrich_all_finished()
    logger.info("Form enrichment complete: %d matches", form_count)

    # Bayesian — best-effort, non-fatal if PyMC unavailable
    bayes_result: dict = {"skipped": True}
    try:
        from sqlalchemy import select
        from app.models import Match, MatchStatus
        from app.services.models.bayesian import run_mcmc_update
        from sqlalchemy import update as sql_update
        from app.models import Team

        result = await db.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.FINISHED,
                Match.home_goals.is_not(None),
                Match.away_goals.is_not(None),
            )
            .order_by(Match.kickoff_at.desc())
            .limit(2000)
        )
        matches = result.scalars().all()
        historical = [
            {
                "home_team_id": m.home_team_id,
                "away_team_id": m.away_team_id,
                "home_goals": m.home_goals,
                "away_goals": m.away_goals,
            }
            for m in matches
        ]
        method_used, posteriors = await asyncio.to_thread(run_mcmc_update, historical)
        if posteriors:
            for team_id, p in posteriors.items():
                await db.execute(
                    sql_update(Team)
                    .where(Team.id == team_id)
                    .values(
                        bayes_attack_mean=p["attack_mean"],
                        bayes_attack_std=p["attack_std"],
                        bayes_defense_mean=p["defense_mean"],
                        bayes_defense_std=p["defense_std"],
                    )
                )
            await db.commit()
            bayes_result = {"teams_updated": len(posteriors), "method_used": method_used}
    except Exception as exc:
        logger.warning("Bayesian MCMC failed: %s", exc)
        bayes_result = {"error": str(exc)}

    return ModelRunResponse(
        status="ok",
        result={
            "elo": elo_result,
            "form_matches_enriched": form_count,
            "bayesian": bayes_result,
        },
    )
