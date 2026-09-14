"""Persistent user-built accumulators with immutable leg snapshots."""

from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.security import require_research_access
from app.config import cat_today
from app.database import get_db
from app.models import (
    Competition,
    CustomAccumulator,
    CustomAccumulatorLeg,
    CustomAccumulatorStatus,
    Match,
    MatchStatus,
    Prediction,
    Odds,
    SelectionResult,
    Team,
)

router = APIRouter(
    prefix="/custom-accumulators",
    tags=["custom-accumulators"],
    dependencies=[Depends(require_research_access)],
)


class CustomAccumulatorPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    target_date: date | None = None
    prediction_ids: list[int] = Field(default_factory=list, max_length=20)
    stake: float | None = Field(default=None, gt=0)
    status: str = "draft"


class CustomAccumulatorOut(BaseModel):
    id: int
    name: str
    target_date: str
    status: str
    stake: float | None
    combined_odds: float
    potential_return: float | None
    actual_return: float | None
    created_at: datetime
    placed_at: datetime | None
    settled_at: datetime | None
    legs: list[dict]


def _status_value(status: CustomAccumulatorStatus | str) -> str:
    return status.value if hasattr(status, "value") else str(status).lower()


def _out(row: CustomAccumulator) -> CustomAccumulatorOut:
    return CustomAccumulatorOut(
        id=row.id,
        name=row.name,
        target_date=row.target_date.isoformat(),
        status=_status_value(row.status),
        stake=row.stake,
        combined_odds=round(row.combined_odds, 4),
        potential_return=row.potential_return,
        actual_return=row.actual_return,
        created_at=row.created_at,
        placed_at=row.placed_at,
        settled_at=row.settled_at,
        legs=[
            {
                "id": leg.id,
                "position": leg.position,
                "prediction_id": leg.prediction_id,
                "match_id": leg.match_id,
                "home_team": leg.home_team,
                "away_team": leg.away_team,
                "competition": leg.competition,
                "kickoff_at": leg.kickoff_at.isoformat(),
                "market": leg.market,
                "selection": leg.selection,
                "odds_snapshot": leg.odds_snapshot,
                "probability_snapshot": leg.probability_snapshot,
                "q_score_snapshot": leg.q_score_snapshot,
                "edge_snapshot": leg.edge_snapshot,
                "result": leg.result.value if hasattr(leg.result, "value") else leg.result,
                "settled_at": leg.settled_at.isoformat() if leg.settled_at else None,
            }
            for leg in row.legs
        ],
    )


async def _load_predictions(db: AsyncSession, ids: list[int]) -> list[tuple[Prediction, Match, Team, Team, Competition]]:
    if not ids:
        return []
    output = []
    predictions = (await db.execute(select(Prediction).where(Prediction.id.in_(ids)))).scalars().all()
    if len(predictions) != len(set(ids)):
        return []
    for prediction in predictions:
        match = await db.get(Match, prediction.match_id)
        if not match:
            raise HTTPException(400, f"Prediction {prediction.id} has no match")
        home = await db.get(Team, match.home_team_id)
        away = await db.get(Team, match.away_team_id)
        competition = await db.get(Competition, match.competition_id)
        if not home or not away or not competition:
            raise HTTPException(400, f"Prediction {prediction.id} has incomplete teams")
        output.append((prediction, match, home, away, competition))
    return sorted(output, key=lambda row: ids.index(row[0].id))


async def _apply_payload(db: AsyncSession, row: CustomAccumulator, payload: CustomAccumulatorPayload) -> None:
    requested_status = payload.status.lower()
    if requested_status not in {"draft", "placed"}:
        raise HTTPException(400, "Only draft and placed are user-controlled statuses")
    if row.status != CustomAccumulatorStatus.DRAFT and requested_status == "draft":
        raise HTTPException(409, "Settled or placed accumulators cannot be returned to draft")
    if row.status != CustomAccumulatorStatus.DRAFT and payload.prediction_ids:
        raise HTTPException(409, "Placed accumulators cannot change legs")

    ids = list(dict.fromkeys(payload.prediction_ids))
    records = await _load_predictions(db, ids)
    if len(records) != len(ids):
        raise HTTPException(400, "One or more selections could not be found")
    if len({match.id for _, match, _, _, _ in records}) != len(records):
        raise HTTPException(400, "An accumulator may contain only one selection per match")
    target = payload.target_date or row.target_date
    for _, match, _, _, _ in records:
        if match.kickoff_at.date() != target:
            raise HTTPException(400, "All selections must belong to the accumulator date")
        if match.status != MatchStatus.SCHEDULED or match.kickoff_at <= datetime.now(match.kickoff_at.tzinfo):
            raise HTTPException(400, "Only upcoming scheduled matches can be added")
    if requested_status == "placed" and (len(records) < 2 or payload.stake is None):
        raise HTTPException(400, "A placed accumulator needs at least two selections and a stake")

    row.name = payload.name.strip()
    row.target_date = target
    row.stake = payload.stake
    row.status = CustomAccumulatorStatus.PLACED if requested_status == "placed" else CustomAccumulatorStatus.DRAFT
    row.combined_odds = 1.0
    await db.execute(delete(CustomAccumulatorLeg).where(CustomAccumulatorLeg.accumulator_id == row.id))
    for position, (prediction, match, home, away, competition) in enumerate(records, start=1):
        odds = prediction.source_decimal_odds
        if odds is None:
            odds = (
                await db.execute(
                    select(Odds.decimal_odds)
                    .where(Odds.match_id == match.id, Odds.market == prediction.market)
                    .order_by(Odds.decimal_odds.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        if odds is None or odds <= 1:
            raise HTTPException(400, f"No valid odds snapshot is available for prediction {prediction.id}")
        row.combined_odds *= odds
        db.add(CustomAccumulatorLeg(
            accumulator_id=row.id,
            prediction_id=prediction.id,
            match_id=match.id,
            position=position,
            home_team=home.name,
            away_team=away.name,
            competition=competition.name,
            kickoff_at=match.kickoff_at,
            market=prediction.market,
            selection=prediction.selection,
            odds_snapshot=odds,
            probability_snapshot=prediction.model_probability,
            q_score_snapshot=prediction.q_score,
            edge_snapshot=prediction.edge,
            result=SelectionResult.PENDING,
        ))
    row.combined_odds = round(row.combined_odds, 4)
    row.potential_return = round(row.combined_odds * row.stake, 2) if row.stake else None
    if requested_status == "placed" and row.placed_at is None:
        row.placed_at = datetime.now(timezone.utc)


@router.get("", response_model=list[CustomAccumulatorOut])
async def list_custom_accumulators(
    target_date: date | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    query = select(CustomAccumulator).options(selectinload(CustomAccumulator.legs)).order_by(CustomAccumulator.created_at.desc())
    if target_date:
        query = query.where(CustomAccumulator.target_date == target_date)
    return [_out(row) for row in (await db.execute(query)).scalars().unique().all()]


@router.post("", response_model=CustomAccumulatorOut, status_code=201)
async def create_custom_accumulator(payload: CustomAccumulatorPayload, db: AsyncSession = Depends(get_db)):
    row = CustomAccumulator(name=payload.name.strip(), target_date=payload.target_date or cat_today())
    db.add(row)
    await db.flush()
    await _apply_payload(db, row, payload)
    await db.commit()
    await db.refresh(row, attribute_names=["created_at"])
    row = (await db.execute(select(CustomAccumulator).options(selectinload(CustomAccumulator.legs)).where(CustomAccumulator.id == row.id))).scalar_one()
    return _out(row)


@router.patch("/{accumulator_id}", response_model=CustomAccumulatorOut)
async def update_custom_accumulator(accumulator_id: int, payload: CustomAccumulatorPayload, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(CustomAccumulator).options(selectinload(CustomAccumulator.legs)).where(CustomAccumulator.id == accumulator_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "Custom accumulator not found")
    await _apply_payload(db, row, payload)
    await db.commit()
    row = (await db.execute(select(CustomAccumulator).options(selectinload(CustomAccumulator.legs)).where(CustomAccumulator.id == row.id))).scalar_one()
    return _out(row)


@router.delete("/{accumulator_id}", status_code=204)
async def delete_custom_accumulator(accumulator_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(CustomAccumulator, accumulator_id)
    if not row:
        raise HTTPException(404, "Custom accumulator not found")
    if row.status != CustomAccumulatorStatus.DRAFT:
        raise HTTPException(409, "Only draft accumulators can be deleted")
    await db.delete(row)
    await db.commit()
