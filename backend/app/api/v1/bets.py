"""Bet tracker CRUD — POST to log a bet, PATCH to settle it, GET to list."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import AsyncSessionLocal
from app.models.bet import Bet, BetStatus
from app.models import TicketSelection, Match, MatchStatus
from app.api.security import require_authenticated_user, require_research_access

router = APIRouter(
    prefix="/bets",
    tags=["bets"],
    dependencies=[Depends(require_authenticated_user)],
)


async def get_db():
    async with AsyncSessionLocal() as db:
        yield db


# ── Schemas ──────────────────────────────────────────────────────────────────

class BetCreate(BaseModel):
    label: str = Field(..., max_length=512)
    odds: float = Field(..., gt=1.0)
    stake: float = Field(..., gt=0)
    ticket_type: str | None = None
    ticket_date: str | None = None
    notes: str | None = None

class IndividualBetCreate(BaseModel):
    selection_id: int
    stake: float = Field(..., gt=0)
    odds: float | None = Field(None, gt=1.0)
    notes: str | None = None


class BetSettle(BaseModel):
    status: Literal["won", "lost", "void", "cashout"]
    actual_return: float | None = Field(None, ge=0)


class BetPeriodOut(BaseModel):
    label: str
    bets: int
    settled: int
    wins: int
    losses: int
    staked: float
    returned: float
    profit_loss: float
    roi: float | None
    hit_rate: float | None


class BetSummaryOut(BaseModel):
    total_bets: int
    pending_bets: int
    settled_bets: int
    wins: int
    losses: int
    voids: int
    cashouts: int
    staked: float
    returned: float
    profit_loss: float
    roi: float | None
    hit_rate: float | None
    by_year: list[BetPeriodOut]
    by_month: list[BetPeriodOut]
    by_date: list[BetPeriodOut]


class BetOut(BaseModel):
    id: int
    created_at: datetime
    label: str
    odds: float
    stake: float
    potential_return: float
    ticket_type: str | None
    ticket_date: str | None
    status: str
    actual_return: float | None
    profit_loss: float | None
    roi: float | None
    notes: str | None
    source_selection_id: int | None
    match_id: int | None
    market: str | None
    selection: str | None

    model_config = {"from_attributes": True}


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("", response_model=BetOut, status_code=201, dependencies=[Depends(require_research_access)])
async def create_bet(payload: BetCreate, db: AsyncSession = Depends(get_db)):
    bet = Bet(
        label=payload.label,
        odds=payload.odds,
        stake=payload.stake,
        potential_return=round(payload.odds * payload.stake, 2),
        ticket_type=payload.ticket_type,
        ticket_date=payload.ticket_date,
        notes=payload.notes,
        status=BetStatus.PENDING,
    )
    db.add(bet)
    await db.commit()
    await db.refresh(bet)
    return _to_out(bet)

@router.post("/individual", response_model=BetOut, status_code=201, dependencies=[Depends(require_research_access)])
async def create_individual_bet(payload: IndividualBetCreate, db: AsyncSession = Depends(get_db)):
    selection = (await db.execute(select(TicketSelection).where(TicketSelection.id == payload.selection_id).options(
        selectinload(TicketSelection.match).selectinload(Match.home_team),
        selectinload(TicketSelection.match).selectinload(Match.away_team),
    ))).scalar_one_or_none()
    if not selection:
        raise HTTPException(404, "Selection not found")
    match = await db.get(Match, selection.match_id)
    if not match:
        raise HTTPException(404, "Match not found")
    if match.kickoff_at <= datetime.now(match.kickoff_at.tzinfo):
        raise HTTPException(400, "Individual bets must be confirmed before kickoff")
    odds = payload.odds or selection.odds_snapshot
    if not odds or odds <= 1:
        raise HTTPException(400, "No valid odds snapshot is available")
    bet = Bet(label=f"{match.home_team.name} vs {match.away_team.name} · {selection.market} · {selection.selection}",
              odds=odds, stake=payload.stake, potential_return=round(odds * payload.stake, 2),
              ticket_date=match.kickoff_at.date().isoformat(), notes=payload.notes,
              source_selection_id=selection.id, match_id=match.id, market=selection.market,
              selection=selection.selection, status=BetStatus.PENDING)
    db.add(bet); await db.commit(); await db.refresh(bet)
    return _to_out(bet)


@router.get("", response_model=list[BetOut])
async def list_bets(
    status: str | None = Query(None),
    ticket_date: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    q = select(Bet).order_by(desc(Bet.created_at)).limit(limit).offset(offset)
    if status:
        q = q.where(Bet.status == status)
    if ticket_date:
        q = q.where(Bet.ticket_date == ticket_date)
    rows = (await db.execute(q)).scalars().all()
    return [_to_out(b) for b in rows]


@router.get("/summary", response_model=BetSummaryOut)
async def bet_summary(db: AsyncSession = Depends(get_db)):
    """Return exact journal totals and daily/monthly/yearly performance."""
    rows = (await db.execute(select(Bet).order_by(Bet.created_at))).scalars().all()
    return _summarize_bets(rows)


@router.get("/{bet_id}", response_model=BetOut)
async def get_bet(bet_id: int, db: AsyncSession = Depends(get_db)):
    bet = await db.get(Bet, bet_id)
    if not bet:
        raise HTTPException(404, "Bet not found")
    return _to_out(bet)


@router.patch("/{bet_id}/settle", response_model=BetOut, dependencies=[Depends(require_research_access)])
async def settle_bet(bet_id: int, payload: BetSettle, db: AsyncSession = Depends(get_db)):
    bet = await db.get(Bet, bet_id)
    if not bet:
        raise HTTPException(404, "Bet not found")

    if payload.status == "cashout" and payload.actual_return is None:
        raise HTTPException(400, "Actual return is required for a cash out")

    bet.status = BetStatus(payload.status)
    if payload.actual_return is not None:
        bet.actual_return = payload.actual_return
    elif payload.status == "won":
        bet.actual_return = bet.potential_return
    elif payload.status in ("lost", "void"):
        bet.actual_return = 0.0 if payload.status == "lost" else bet.stake

    await db.commit()
    await db.refresh(bet)
    return _to_out(bet)


@router.delete("/{bet_id}", status_code=204, dependencies=[Depends(require_research_access)])
async def delete_bet(bet_id: int, db: AsyncSession = Depends(get_db)):
    bet = await db.get(Bet, bet_id)
    if not bet:
        raise HTTPException(404, "Bet not found")
    await db.delete(bet)
    await db.commit()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_out(b: Bet) -> BetOut:
    return BetOut(
        id=b.id,
        created_at=b.created_at,
        label=b.label,
        odds=b.odds,
        stake=b.stake,
        potential_return=b.potential_return,
        ticket_type=b.ticket_type,
        ticket_date=b.ticket_date,
        status=b.status.value if hasattr(b.status, "value") else b.status,
        actual_return=b.actual_return,
        profit_loss=b.profit_loss,
        roi=b.roi,
        notes=b.notes,
        source_selection_id=b.source_selection_id,
        match_id=b.match_id,
        market=b.market,
        selection=b.selection,
    )


def _period_key(bet: Bet) -> str:
    if bet.ticket_date:
        return bet.ticket_date
    created = bet.created_at
    return created.date().isoformat() if created else "unknown"


def _aggregate_period(label: str, rows: list[Bet]) -> BetPeriodOut:
    financially_settled = [row for row in rows if row.actual_return is not None]
    wins = sum(row.status == BetStatus.WON for row in rows)
    losses = sum(row.status == BetStatus.LOST for row in rows)
    staked = sum(row.stake for row in financially_settled)
    returned = sum(row.actual_return or 0 for row in financially_settled)
    profit_loss = returned - staked
    decided = wins + losses
    return BetPeriodOut(
        label=label,
        bets=len(rows),
        settled=len(financially_settled),
        wins=wins,
        losses=losses,
        staked=round(staked, 2),
        returned=round(returned, 2),
        profit_loss=round(profit_loss, 2),
        roi=round(profit_loss / staked, 4) if staked else None,
        hit_rate=round(wins / decided, 4) if decided else None,
    )


def _summarize_bets(rows: list[Bet]) -> BetSummaryOut:
    by_date: dict[str, list[Bet]] = {}
    by_month: dict[str, list[Bet]] = {}
    by_year: dict[str, list[Bet]] = {}
    for row in rows:
        date_key = _period_key(row)
        by_date.setdefault(date_key, []).append(row)
        by_month.setdefault(date_key[:7], []).append(row)
        by_year.setdefault(date_key[:4], []).append(row)

    overall = _aggregate_period("all", rows)
    return BetSummaryOut(
        total_bets=overall.bets,
        pending_bets=sum(row.status == BetStatus.PENDING for row in rows),
        settled_bets=overall.settled,
        wins=overall.wins,
        losses=overall.losses,
        voids=sum(row.status == BetStatus.VOID for row in rows),
        cashouts=sum(row.status == BetStatus.CASHOUT for row in rows),
        staked=overall.staked,
        returned=overall.returned,
        profit_loss=overall.profit_loss,
        roi=overall.roi,
        hit_rate=overall.hit_rate,
        by_year=[_aggregate_period(key, value) for key, value in sorted(by_year.items(), reverse=True)],
        by_month=[_aggregate_period(key, value) for key, value in sorted(by_month.items(), reverse=True)],
        by_date=[_aggregate_period(key, value) for key, value in sorted(by_date.items(), reverse=True)],
    )
