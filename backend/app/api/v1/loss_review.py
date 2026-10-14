"""Daily loss-analysis and learning report."""

from datetime import date
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_research_access
from app.config import cat_today
from app.database import get_db
from app.services.loss_review import review_losses

router = APIRouter(prefix="/performance", tags=["performance"], dependencies=[Depends(require_research_access)])


@router.get("/loss-review")
async def get_loss_review(target_date: date | None = None, db: AsyncSession = Depends(get_db)):
    return await review_losses(db, target_date or cat_today())
