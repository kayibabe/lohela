from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_authenticated_user
from app.database import get_db
from app.models import User, UserMatchFilterPreset


router = APIRouter(prefix="/match-filters", tags=["match-filters"])


class MatchFilterCriteria(BaseModel):
    query: str = Field(default="", max_length=120)
    competition: str = Field(default="all", max_length=160)
    market: str = Field(default="all", max_length=80)
    status: Literal["all", "finished", "live", "scheduled", "postponed", "cancelled"] = "all"
    favorite_only: bool = False
    min_probability: float | None = Field(default=None, ge=0, le=1)
    max_probability: float | None = Field(default=None, ge=0, le=1)
    min_odds: float | None = Field(default=None, ge=1, le=1000)
    max_odds: float | None = Field(default=None, ge=1, le=1000)
    min_edge: float | None = Field(default=None, ge=-1, le=1)
    min_q_score: float = Field(default=75, ge=0, le=100)
    max_q_score: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_ranges(self):
        for low_name, high_name in (
            ("min_probability", "max_probability"),
            ("min_odds", "max_odds"),
            ("min_q_score", "max_q_score"),
        ):
            low = getattr(self, low_name)
            high = getattr(self, high_name)
            if low is not None and high is not None and low > high:
                raise ValueError(f"{low_name} cannot be greater than {high_name}")
        return self


class MatchFilterPresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    criteria: MatchFilterCriteria


class MatchFilterPresetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    criteria: MatchFilterCriteria


class MatchFilterPresetOut(BaseModel):
    id: int
    name: str
    criteria: MatchFilterCriteria
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[MatchFilterPresetOut])
async def list_match_filter_presets(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    rows = await db.scalars(
        select(UserMatchFilterPreset)
        .where(UserMatchFilterPreset.user_id == user.id)
        .order_by(UserMatchFilterPreset.created_at.desc(), UserMatchFilterPreset.id.desc())
    )
    return list(rows)


@router.post("", response_model=MatchFilterPresetOut, status_code=201)
async def save_match_filter_preset(
    payload: MatchFilterPresetCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="A filter name is required")
    row = UserMatchFilterPreset(
        user_id=user.id,
        name=name,
        criteria=payload.criteria.model_dump(),
    )
    db.add(row)
    await db.flush()
    await db.refresh(row)
    return row


@router.patch("/{preset_id}", response_model=MatchFilterPresetOut)
async def update_match_filter_preset(
    preset_id: int,
    payload: MatchFilterPresetUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    row = await db.scalar(
        select(UserMatchFilterPreset).where(
            UserMatchFilterPreset.id == preset_id,
            UserMatchFilterPreset.user_id == user.id,
        )
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Saved filter not found")
    if payload.name is not None:
        name = payload.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="A filter name is required")
        row.name = name
    row.criteria = payload.criteria.model_dump()
    await db.flush()
    await db.refresh(row)
    return row


@router.delete("/{preset_id}", status_code=204)
async def delete_match_filter_preset(
    preset_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    result = await db.execute(
        delete(UserMatchFilterPreset).where(
            UserMatchFilterPreset.id == preset_id,
            UserMatchFilterPreset.user_id == user.id,
        )
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Saved filter not found")
