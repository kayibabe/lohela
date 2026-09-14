"""Authenticated research endpoint for generating immutable paper tickets."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_research_access
from app.config import cat_today
from app.database import get_db
from app.services.ticket_publisher import TicketPublisher

router = APIRouter(prefix="/accumulators", tags=["accumulators"])


class GenerateAccumulatorRequest(BaseModel):
    model_config = {"protected_namespaces": ()}
    target_date: date | None = None
    model_run_id: int | None = None
    research_min_qscore: float | None = Field(default=None, ge=0, le=100)


class GenerateAccumulatorResponse(BaseModel):
    model_config = {"protected_namespaces": ()}
    generation_id: int
    target_date: str
    model_run_id: int
    status: str
    qualified_pool: int
    published_tickets: int


@router.post(
    "/generate",
    response_model=GenerateAccumulatorResponse,
    dependencies=[Depends(require_research_access)],
)
async def generate_accumulators(
    payload: GenerateAccumulatorRequest,
    db: AsyncSession = Depends(get_db),
):
    try:
        generation = await TicketPublisher(db).generate_and_publish(
            payload.target_date or cat_today(),
            model_run_id=payload.model_run_id,
            research_min_qscore=payload.research_min_qscore,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return GenerateAccumulatorResponse(
        generation_id=generation.id,
        target_date=generation.target_date.isoformat(),
        model_run_id=generation.model_run_id,
        status=generation.status.value,
        qualified_pool=generation.input_count,
        published_tickets=generation.output_count,
    )
