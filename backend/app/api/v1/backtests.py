"""Authenticated historical validation runs."""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_research_access
from app.config import CURRENT_MODEL_VERSION
from app.database import get_db
from app.services.backtesting import Backtester

router = APIRouter(prefix="/backtests", tags=["backtests"])


class BacktestRequest(BaseModel):
    period_start: date
    period_end: date
    model_version: str = CURRENT_MODEL_VERSION
    simulations: int = Field(default=10_000, ge=1_000, le=50_000)

    @model_validator(mode="after")
    def valid_period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        return self


@router.post("/run", dependencies=[Depends(require_research_access)])
async def run_backtest(payload: BacktestRequest, db: AsyncSession = Depends(get_db)):
    run = await Backtester(db).run(
        payload.period_start,
        payload.period_end,
        payload.model_version,
        payload.simulations,
    )
    return {
        "backtest_run_id": run.id,
        "status": run.status.value,
        "leakage_checks_passed": run.leakage_checks_passed,
        "error_details": run.error_details,
        "metrics": run.metrics,
    }
