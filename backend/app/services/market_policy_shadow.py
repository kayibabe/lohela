"""Frozen, non-publishing market-policy counterfactuals."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import MarketPolicyShadowSnapshot, TicketGeneration
from app.services.accumulator_builder import AccumulatorBuilder, DailyTickets, PUBLIC_TICKET_TYPES, Ticket


POLICY_VERSION = "market-policy-exclusion-v1"


def _ticket_out(ticket: Ticket | None) -> dict | None:
    if ticket is None:
        return None
    return {
        "ticket_type": ticket.ticket_type.value,
        "legs": [
            {
                "prediction_id": leg.prediction_id,
                "match_id": leg.match_id,
                "market": leg.market,
                "selection": leg.selection,
                "odds": leg.best_odds,
                "source_odds_at": leg.source_odds_at.isoformat() if leg.source_odds_at else None,
            }
            for leg in ticket.legs
        ],
        "combined_odds": ticket.combined_odds,
        "combined_probability": ticket.combined_probability,
        "expected_value": ticket.expected_value,
        "relaxed": ticket.relaxed,
        "relaxation_level": ticket.relaxation_level,
        "horizon_days": ticket.horizon_days,
    }


def snapshot_payload(built: DailyTickets, excluded_markets: frozenset[str]) -> dict:
    tickets = [built.conservative, built.balanced, built.high_odds]
    return {
        "mode": "shadow_only",
        "control_unchanged": True,
        "excluded_markets": sorted(excluded_markets),
        "qualified_pool": built.qualified_pool,
        "public_ticket_count": sum(ticket is not None for ticket in tickets),
        "missing_public_ticket_types": [
            ticket_type.value
            for ticket_type, ticket in zip(PUBLIC_TICKET_TYPES, tickets)
            if ticket is None
        ],
        "horizon_dates": [value.isoformat() for value in built.horizon_dates],
        "selection_diagnostics": built.selection_diagnostics,
        "tickets": [_ticket_out(ticket) for ticket in tickets],
    }


async def capture_market_policy_shadow(
    db: AsyncSession,
    target_date: date,
    model_run_id: int,
    *,
    excluded_markets: frozenset[str] | None = None,
) -> MarketPolicyShadowSnapshot:
    """Persist one immutable shadow portfolio; never call the publisher."""
    excluded = excluded_markets or frozenset(settings.market_policy_shadow_excluded_markets)
    existing = await db.scalar(
        select(MarketPolicyShadowSnapshot).where(
            MarketPolicyShadowSnapshot.target_date == target_date,
            MarketPolicyShadowSnapshot.model_run_id == model_run_id,
            MarketPolicyShadowSnapshot.policy_version == POLICY_VERSION,
        )
    )
    if existing is not None:
        return existing

    generation = await db.scalar(
        select(TicketGeneration)
        .where(
            TicketGeneration.target_date == target_date,
            TicketGeneration.model_run_id == model_run_id,
        )
        .order_by(TicketGeneration.id.desc())
        .limit(1)
    )
    values = ((generation.config_snapshot or {}).get("publication_summary", {}).get("horizon_dates", [])) if generation else []
    horizon_dates = [date.fromisoformat(value) for value in values]
    built = await AccumulatorBuilder(db).build(
        target_date,
        model_run_id=model_run_id,
        horizon_dates=horizon_dates,
        market_policy_excluded_markets=excluded,
    )
    row = MarketPolicyShadowSnapshot(
        target_date=target_date,
        model_run_id=model_run_id,
        policy_version=POLICY_VERSION,
        excluded_markets=sorted(excluded),
        payload=snapshot_payload(built, excluded),
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        row = await db.scalar(
            select(MarketPolicyShadowSnapshot).where(
                MarketPolicyShadowSnapshot.target_date == target_date,
                MarketPolicyShadowSnapshot.model_run_id == model_run_id,
                MarketPolicyShadowSnapshot.policy_version == POLICY_VERSION,
            )
        )
        if row is None:
            raise
    return row
