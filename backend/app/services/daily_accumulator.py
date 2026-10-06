"""Build the daily accumulator from the persisted public source tickets."""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    AccumulatorTicket,
    CustomAccumulator,
    CustomAccumulatorLeg,
    CustomAccumulatorStatus,
    SelectionResult,
    TicketType,
)
from app.services.ticket_publisher import get_latest_published_tickets


DAILY_ACCUMULATOR_SOURCE = "daily_ticket_merge"


async def daily_accumulator_merge_pending(
    db: AsyncSession, target_date: date
) -> bool:
    """Return whether both source tiers exist but their merge is missing."""
    source_tickets = await get_latest_published_tickets(
        db, target_date, include_internal=False
    )
    source_types = {ticket.ticket_type for ticket in source_tickets}
    if not {TicketType.SAFE, TicketType.BALANCED}.issubset(source_types):
        return False
    existing = await db.scalar(
        select(CustomAccumulator).where(
            CustomAccumulator.automation_key
            == f"daily_accumulator:{target_date.isoformat()}"
        )
    )
    return existing is None


def is_daily_accumulator(row: CustomAccumulator) -> bool:
    return (row.settlement_details or {}).get("source") == DAILY_ACCUMULATOR_SOURCE


def merged_source_selection_provenance(source_tickets: list[AccumulatorTicket]) -> list[tuple[object, AccumulatorTicket, bool]]:
    """Return selected legs with immutable source and conflict provenance."""
    by_type = {ticket.ticket_type: ticket for ticket in source_tickets}
    merged = {}
    # A match is a conflict when both public source tickets contain it. Keep
    # this explicit rather than inferring it later from the merged snapshot.
    match_counts: dict[int, int] = {}
    for ticket in source_tickets:
        for selection in ticket.selections:
            match_counts[selection.match_id] = match_counts.get(selection.match_id, 0) + 1
    conflicts = {match_id for match_id, count in match_counts.items() if count > 1}
    output = []
    for ticket_type in (TicketType.SAFE, TicketType.BALANCED):
        ticket = by_type.get(ticket_type)
        if ticket is None:
            continue
        for selection in sorted(ticket.selections, key=lambda item: item.position):
            if selection.match_id not in merged:
                merged[selection.match_id] = selection
                output.append((selection, ticket, selection.match_id in conflicts))
    return output


def merged_source_selections(source_tickets: list[AccumulatorTicket]) -> list:
    """Return Conservative-first unique selections, resolving conflicts by match."""
    return [selection for selection, _, _ in merged_source_selection_provenance(source_tickets)]


async def create_daily_accumulator(
    db: AsyncSession, target_date: date
) -> dict:
    """Create one idempotent system draft after both source tiers are persisted."""
    source_tickets = await get_latest_published_tickets(
        db, target_date, include_internal=False
    )
    source_by_type = {ticket.ticket_type: ticket for ticket in source_tickets}
    required = (TicketType.SAFE, TicketType.BALANCED)
    if any(ticket_type not in source_by_type for ticket_type in required):
        return {
            "status": "waiting_for_source_tickets",
            "target_date": target_date.isoformat(),
            "source_ticket_ids": {},
        }

    existing_rows = (
        await db.execute(
            select(CustomAccumulator)
            .where(
                CustomAccumulator.automation_key
                == f"daily_accumulator:{target_date.isoformat()}"
            )
            .options(selectinload(CustomAccumulator.legs))
        )
    ).scalars().all()
    existing = next((row for row in existing_rows if is_daily_accumulator(row)), None)
    if existing is not None:
        return {
            "status": "already_exists",
            "target_date": target_date.isoformat(),
            "accumulator_id": existing.id,
            "leg_count": len(existing.legs),
        }

    selections = merged_source_selection_provenance(list(source_by_type.values()))
    if not selections:
        return {
            "status": "no_source_selections",
            "target_date": target_date.isoformat(),
            "source_ticket_ids": {ticket_type.value: source_by_type[ticket_type].id for ticket_type in required},
        }

    source_ticket_ids = {
        ticket_type.value: source_by_type[ticket_type].id for ticket_type in required
    }
    row = CustomAccumulator(
        name=f"Accu-{target_date.isoformat()}",
        target_date=target_date,
        automation_key=f"daily_accumulator:{target_date.isoformat()}",
        status=CustomAccumulatorStatus.DRAFT,
        settlement_details={
            "source": DAILY_ACCUMULATOR_SOURCE,
            "source_ticket_ids": source_ticket_ids,
            "conflict_policy": "conservative_priority",
        },
    )
    db.add(row)
    await db.flush()

    combined_odds = 1.0
    for position, (selection, source_ticket, source_conflict) in enumerate(selections, start=1):
        match = selection.match
        odds = selection.odds_snapshot
        combined_odds *= odds
        db.add(
            CustomAccumulatorLeg(
                accumulator_id=row.id,
                prediction_id=selection.prediction_id,
                match_id=selection.match_id,
                position=position,
                home_team=match.home_team.name,
                away_team=match.away_team.name,
                competition=match.competition.name if match.competition else str(match.competition_id),
                kickoff_at=match.kickoff_at,
                market=selection.market,
                selection=selection.selection,
                odds_snapshot=odds,
                probability_snapshot=selection.probability_snapshot,
                q_score_snapshot=selection.q_score_snapshot,
                edge_snapshot=selection.edge_snapshot,
                source_ticket_id=source_ticket.id,
                source_ticket_type=source_ticket.ticket_type.value,
                source_ticket_version=source_ticket.version,
                source_conflict=source_conflict,
                result=SelectionResult.PENDING,
            )
        )
    row.combined_odds = round(combined_odds, 4)
    await db.flush()
    return {
        "status": "created",
        "target_date": target_date.isoformat(),
        "accumulator_id": row.id,
        "leg_count": len(selections),
        "source_ticket_ids": source_ticket_ids,
    }
