"""Analytics endpoints — hit rates, ROI, P&L breakdowns."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models.bet import Bet, BetStatus

router = APIRouter(prefix="/analytics", tags=["analytics"])


async def get_db():
    async with AsyncSessionLocal() as db:
        yield db


@router.get("/summary")
async def summary(db: AsyncSession = Depends(get_db)):
    """Overall P&L, ROI, and hit rate across all settled bets."""
    rows = (await db.execute(select(Bet))).scalars().all()

    total = len(rows)
    settled = [b for b in rows if b.status != BetStatus.PENDING]
    won = [b for b in settled if b.status == BetStatus.WON]
    lost = [b for b in settled if b.status == BetStatus.LOST]
    pending = [b for b in rows if b.status == BetStatus.PENDING]

    total_staked = sum(b.stake for b in settled)
    total_returned = sum((b.actual_return or 0) for b in settled)
    pnl = round(total_returned - total_staked, 2)
    roi = round(pnl / total_staked * 100, 2) if total_staked > 0 else 0.0
    hit_rate = round(len(won) / len(settled) * 100, 1) if settled else 0.0

    return {
        "total_bets": total,
        "settled": len(settled),
        "pending": len(pending),
        "won": len(won),
        "lost": len(lost),
        "total_staked": round(total_staked, 2),
        "total_returned": round(total_returned, 2),
        "pnl": pnl,
        "roi_pct": roi,
        "hit_rate_pct": hit_rate,
        "avg_odds": round(sum(b.odds for b in settled) / len(settled), 2) if settled else None,
    }


@router.get("/by-ticket-type")
async def by_ticket_type(db: AsyncSession = Depends(get_db)):
    """P&L breakdown by ticket type (conservative, balanced, aggressive, best_value)."""
    rows = (await db.execute(select(Bet))).scalars().all()
    settled = [b for b in rows if b.status != BetStatus.PENDING]

    buckets: dict[str, dict] = {}
    for b in settled:
        key = b.ticket_type or "custom"
        if key not in buckets:
            buckets[key] = {"label": key, "bets": 0, "won": 0, "staked": 0.0, "returned": 0.0}
        buckets[key]["bets"] += 1
        if b.status == BetStatus.WON:
            buckets[key]["won"] += 1
        buckets[key]["staked"] += b.stake
        buckets[key]["returned"] += b.actual_return or 0

    result = []
    for v in buckets.values():
        staked = v["staked"]
        returned = v["returned"]
        pnl = round(returned - staked, 2)
        result.append({
            **v,
            "staked": round(staked, 2),
            "returned": round(returned, 2),
            "pnl": pnl,
            "roi_pct": round(pnl / staked * 100, 2) if staked > 0 else 0.0,
            "hit_rate_pct": round(v["won"] / v["bets"] * 100, 1) if v["bets"] > 0 else 0.0,
        })
    return sorted(result, key=lambda x: x["pnl"], reverse=True)


@router.get("/by-month")
async def by_month(db: AsyncSession = Depends(get_db)):
    """Monthly P&L summary."""
    rows = (await db.execute(select(Bet))).scalars().all()
    settled = [b for b in rows if b.status != BetStatus.PENDING and b.created_at]

    months: dict[str, dict] = {}
    for b in settled:
        key = b.created_at.strftime("%Y-%m")
        if key not in months:
            months[key] = {"month": key, "bets": 0, "won": 0, "staked": 0.0, "returned": 0.0}
        months[key]["bets"] += 1
        if b.status == BetStatus.WON:
            months[key]["won"] += 1
        months[key]["staked"] += b.stake
        months[key]["returned"] += b.actual_return or 0

    result = []
    for v in sorted(months.values(), key=lambda x: x["month"]):
        staked = v["staked"]
        returned = v["returned"]
        pnl = round(returned - staked, 2)
        result.append({
            **v,
            "staked": round(staked, 2),
            "returned": round(returned, 2),
            "pnl": pnl,
            "roi_pct": round(pnl / staked * 100, 2) if staked > 0 else 0.0,
            "hit_rate_pct": round(v["won"] / v["bets"] * 100, 1) if v["bets"] > 0 else 0.0,
        })
    return result


@router.get("/recent")
async def recent(limit: int = Query(10, le=50), db: AsyncSession = Depends(get_db)):
    """Most recent bets with their P&L for the dashboard feed."""
    from sqlalchemy import desc
    rows = (await db.execute(
        select(Bet).order_by(desc(Bet.created_at)).limit(limit)
    )).scalars().all()
    return [
        {
            "id": b.id,
            "label": b.label,
            "odds": b.odds,
            "stake": b.stake,
            "status": b.status.value if hasattr(b.status, "value") else b.status,
            "pnl": b.profit_loss,
            "ticket_date": b.ticket_date,
            "ticket_type": b.ticket_type,
        }
        for b in rows
    ]
