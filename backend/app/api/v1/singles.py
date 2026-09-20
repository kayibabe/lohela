"""Authenticated, read-only singles diagnostics; never publishes bets."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_research_access
from app.config import CURRENT_MODEL_VERSION
from app.database import get_db
from app.services.singles_ledger import review_all_snapshots_db
from app.services.singles_report import singles_report

router = APIRouter(prefix="/singles", tags=["singles"],
                   dependencies=[Depends(require_research_access)])


@router.get("/research")
async def research_singles(start: date, end: date,
                           model_version: str = Query(CURRENT_MODEL_VERSION, min_length=1, max_length=100),
                           db: AsyncSession = Depends(get_db)):
    try:
        report, _ = await singles_report(db, start, end, model_version)
        return report
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/ledger")
async def singles_ledger(db: AsyncSession = Depends(get_db)):
    """The real frozen prospective ledger: every daily-captured decision combined.

    Distinct from /research, which replays a policy over already-published
    predictions after the fact. This reads only outcome-blind snapshots
    captured before each pick's kickoff and frozen as insert-only DB rows.
    """
    try:
        return await review_all_snapshots_db(db)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
