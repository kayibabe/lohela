"""Odds ingestion endpoints — spec §32."""

from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database import get_db
from app.api.security import require_research_access

router = APIRouter(
    prefix="/ingest", tags=["odds"], dependencies=[Depends(require_research_access)]
)


class OddsImportResponse(BaseModel):
    status: str
    results: list[dict]


class LiveOddsResponse(BaseModel):
    status: str
    inserted: int
    errors: list[dict]


@router.post("/odds/historical", response_model=OddsImportResponse)
async def import_historical_odds(
    league_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Import historical closing odds from Football-Data.co.uk CSV files.

    Covers Premier League, La Liga, Serie A, Bundesliga, Ligue 1 for 2024/25.
    Pass league_id to import a single league, or omit to import all 5.
    Safe to re-run — existing odds for each match are replaced.
    """
    from app.services.odds_historical import HistoricalOddsImporter, LEAGUE_CSV_MAP
    importer = HistoricalOddsImporter(db)

    if league_id:
        result = await importer.import_season(league_id)
        return OddsImportResponse(status="ok", results=[result])

    results = await importer.import_all()
    return OddsImportResponse(status="ok", results=results)


@router.post("/odds/live", response_model=LiveOddsResponse)
async def fetch_live_odds(db: AsyncSession = Depends(get_db)):
    """
    Fetch upcoming match odds from API-Football.
    """
    from app.services.odds_fetcher import LiveOddsFetcher
    fetcher = LiveOddsFetcher(db)
    result = await fetcher.fetch_all()
    return LiveOddsResponse(
        status=result.get("status", "ok"),
        inserted=result.get("inserted", 0),
        errors=result.get("errors", []),
    )
