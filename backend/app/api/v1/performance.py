"""System-generated paper-ticket performance API."""

from datetime import date, datetime, timezone
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.api.security import require_pro_access
from app.config import CURRENT_MODEL_VERSION
from app.services.performance import performance_summary, individual_selection_summary, market_reliability_matrix, all_market_research_summary, clv_summary, totals_calibration_review, probability_calibration_analysis
from app.services.performance_export import probability_calibration_workbook
from app.services.recommendation_ledger import recommendation_pick_ledger

router = APIRouter(
    prefix="/performance",
    tags=["performance"],
    dependencies=[Depends(require_pro_access)],
)


@router.get("/summary")
async def summary(date_from: date | None = None, date_to: date | None = None, model_version: str | None = None, db: AsyncSession = Depends(get_db)):
    return await performance_summary(db, date_from, date_to, model_version)


@router.get("/individual-selections")
async def individual_selections(stake: float = Query(1.0, gt=0, le=100000), date_from: date | None = None, date_to: date | None = None, market: str | None = None, competition: str | None = None, model_version: str | None = None, db: AsyncSession = Depends(get_db)):
    return await individual_selection_summary(db, stake, date_from, date_to, market, competition, model_version)

@router.get("/market-reliability")
async def market_reliability(date_from: date | None = None, date_to: date | None = None, model_version: str | None = CURRENT_MODEL_VERSION, db: AsyncSession = Depends(get_db)):
    return await market_reliability_matrix(db, date_from, date_to, None if model_version == "all" else model_version)

@router.get("/totals-calibration")
async def totals_calibration(date_from: date | None = None, date_to: date | None = None, model_version: str | None = None, db: AsyncSession = Depends(get_db)):
    return await totals_calibration_review(db, date_from, date_to, model_version)


@router.get("/probability-calibration")
async def probability_calibration(date_from: date | None = None, date_to: date | None = None, model_version: str | None = None, db: AsyncSession = Depends(get_db)):
    """Bucket Lohela model probability vs. market-implied probability by 5pp band, whole-system."""
    return await probability_calibration_analysis(db, date_from, date_to, model_version)


@router.get("/probability-calibration/export")
async def probability_calibration_export(date_from: date | None = None, date_to: date | None = None, model_version: str | None = None, db: AsyncSession = Depends(get_db)):
    """Download the probability-calibration analytics view as an .xlsx workbook."""
    data = await probability_calibration_analysis(db, date_from, date_to, model_version)
    workbook = probability_calibration_workbook(data)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"probability-calibration-{stamp}.xlsx"
    return StreamingResponse(
        workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/all-markets")
async def all_markets(stake: float = Query(1.0, gt=0, le=100000), date_from: date | None = None, date_to: date | None = None, model_version: str | None = None, db: AsyncSession = Depends(get_db)):
    return await all_market_research_summary(db, stake, date_from, date_to, model_version)


@router.get("/clv")
async def clv(date_from: date | None = None, date_to: date | None = None, model_version: str | None = None, db: AsyncSession = Depends(get_db)):
    """Closing-line value: did our entry price beat the market's price at kickoff?"""
    return await clv_summary(db, date_from, date_to, model_version)


@router.get("/recommendation-picks")
async def recommendation_picks(
    stake: float = Query(1.0, gt=0, le=100000),
    date_from: date | None = None,
    date_to: date | None = None,
    source: str = Query("all", pattern="^(all|published|strongest|overlap)$"),
    result: str | None = Query(None, pattern="^(pending|won|lost|void)$"),
    market: str | None = None,
    competition: str | None = None,
    model_version: str | None = None,
    limit: int = Query(1000, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
):
    return await recommendation_pick_ledger(
        db,
        stake=stake,
        date_from=date_from,
        date_to=date_to,
        source=source,
        result_filter=result,
        market=market,
        competition=competition,
        model_version=model_version,
        limit=limit,
    )
