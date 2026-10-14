from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_authenticated_user
from app.database import get_db
from app.models import Match, User, UserMatchFavorite


router = APIRouter(prefix="/match-favorites", tags=["match-favorites"])


class MatchFavoriteBody(BaseModel):
    match_id: int = Field(ge=1)


class MatchFavoriteIdsOut(BaseModel):
    match_ids: list[int]


async def _list_match_favorites(db: AsyncSession, user_id: int) -> MatchFavoriteIdsOut:
    match_ids = await db.scalars(
        select(UserMatchFavorite.match_id)
        .where(UserMatchFavorite.user_id == user_id)
        .order_by(UserMatchFavorite.created_at.desc(), UserMatchFavorite.match_id.desc())
    )
    return MatchFavoriteIdsOut(match_ids=list(match_ids))


@router.get("", response_model=MatchFavoriteIdsOut)
async def list_match_favorites(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    return await _list_match_favorites(db, user.id)


@router.post("", response_model=MatchFavoriteIdsOut)
async def add_match_favorite(
    payload: MatchFavoriteBody,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    user_id = user.id
    if await db.get(Match, payload.match_id) is None:
        raise HTTPException(status_code=404, detail="Match not found")

    existing = await db.scalar(
        select(UserMatchFavorite.id).where(
            UserMatchFavorite.user_id == user_id,
            UserMatchFavorite.match_id == payload.match_id,
        )
    )
    if existing is None:
        db.add(UserMatchFavorite(user_id=user_id, match_id=payload.match_id))
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()

    return await _list_match_favorites(db, user_id)


@router.delete("/{match_id}", response_model=MatchFavoriteIdsOut)
async def remove_match_favorite(
    match_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_authenticated_user),
):
    user_id = user.id
    await db.execute(
        delete(UserMatchFavorite).where(
            UserMatchFavorite.user_id == user_id,
            UserMatchFavorite.match_id == match_id,
        )
    )
    return await _list_match_favorites(db, user_id)
