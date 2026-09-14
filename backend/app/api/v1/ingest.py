"""POST /api/v1/ingest/fixtures — Stage 1 on-demand trigger — spec §32."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update
from pydantic import BaseModel

from app.database import get_db
from app.services.fixture_ingestor import FixtureIngestor
from app.services.data_enricher import DataEnricher
from app.models import Match
from app.models.match import MatchStatus
from app.config import cat_today
from app.api.security import require_research_access

router = APIRouter(
    prefix="/ingest", tags=["ingestion"], dependencies=[Depends(require_research_access)]
)


class IngestResponse(BaseModel):
    status: str
    stats: dict


@router.post("/fixtures", response_model=IngestResponse)
async def ingest_fixtures(
    background_tasks: BackgroundTasks,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    season: Optional[int] = None,
    skip_enrichment: Optional[bool] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Trigger fixture ingestion for the specified date range.
    Defaults to today + next 48h (spec §26 Stage 1).

    Pass `season` to pull historical data — e.g. season=2024 for 2024/25.
    Free API-Football plans only cover seasons 2022–2024.
    `skip_enrichment` defaults to True when date range > 7 days (bulk pull).
    Also triggers data enrichment (form calculation) in background.
    """
    ingestor = FixtureIngestor(db)
    stats = await ingestor.ingest(from_date, to_date, season=season, skip_enrichment=skip_enrichment)

    # Enrich form in background — non-blocking
    enrich_date = from_date or cat_today()
    background_tasks.add_task(_enrich_after_ingest, enrich_date)

    return IngestResponse(status="ok", stats=stats)


async def _enrich_after_ingest(target_date: date) -> None:
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        enricher = DataEnricher(db)
        await enricher.enrich_all_scheduled(target_date)
        await db.commit()


class RescoreResponse(BaseModel):
    status: str
    updated: int


@router.post("/rescore", response_model=RescoreResponse)
async def rescore_historical(db: AsyncSession = Depends(get_db)):
    """
    Rescore all FINISHED matches that were bulk-ingested without enrichment.

    Awards the +40 result bonus so historical matches qualify for model training.
    Safe to re-run — idempotent.
    """
    from app.services.data_validator import rescore_finished_match
    from app.config import settings

    result = await db.execute(
        select(Match).where(
            Match.status == MatchStatus.FINISHED,
            Match.home_goals != None,
            Match.away_goals != None,
        )
    )
    matches = result.scalars().all()

    updated = 0
    for m in matches:
        new_score = rescore_finished_match(m)
        if new_score != m.data_quality_score:
            m.data_quality_score = new_score
            m.excluded_from_models = new_score < settings.min_data_quality_score
            updated += 1

    await db.commit()
    return RescoreResponse(status="ok", updated=updated)
