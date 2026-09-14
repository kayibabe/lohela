"""Authenticated result ingestion for automatic paper-ticket settlement."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_research_access
from app.database import get_db
from app.services.settlement import SettlementService

router = APIRouter(prefix="/results", tags=["results"])


class MatchResultIn(BaseModel):
    match_id: int | None = None
    api_football_id: int | None = None
    home_goals: int = Field(ge=0)
    away_goals: int = Field(ge=0)
    home_goals_ht: int | None = Field(default=None, ge=0)
    away_goals_ht: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def exactly_one_identifier(self):
        if (self.match_id is None) == (self.api_football_id is None):
            raise ValueError("Provide exactly one of match_id or api_football_id")
        return self


class ResultIngestRequest(BaseModel):
    results: list[MatchResultIn] = Field(min_length=1, max_length=500)
    source: str = Field(default="manual_research", min_length=1, max_length=100)


@router.post("/ingest", dependencies=[Depends(require_research_access)])
async def ingest_results(payload: ResultIngestRequest, db: AsyncSession = Depends(get_db)):
    try:
        return await SettlementService(db).ingest_results(
            [item.model_dump() for item in payload.results], payload.source
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
