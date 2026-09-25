"""Idempotent result ingestion and append-only paper-ticket settlement."""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import cat_day_bounds_utc
from app.models import (
    AccumulatorTicket,
    AuditEvent,
    Match,
    MatchStatus,
    SelectionResult,
    TicketResult,
    TicketSelection,
    TicketStatus,
    CustomAccumulator,
    CustomAccumulatorLeg,
    CustomAccumulatorStatus,
)
from app.models.bet import Bet, BetStatus


def evaluate_selection(market: str, home_goals: int, away_goals: int) -> SelectionResult:
    total = home_goals + away_goals
    key = market.lower().replace(".", "_")
    if key.startswith(("over_", "under_")):
        try:
            threshold = float(key.split("_", 1)[1].replace("_", "."))
        except ValueError as exc:
            raise ValueError(f"Unsupported totals market for settlement: {market}") from exc
        won = total > threshold if key.startswith("over_") else total < threshold
        return SelectionResult.WON if won else SelectionResult.LOST
    if key == "btts_yes":
        return SelectionResult.WON if home_goals > 0 and away_goals > 0 else SelectionResult.LOST
    if key == "btts_no":
        return SelectionResult.WON if home_goals == 0 or away_goals == 0 else SelectionResult.LOST
    if key == "home_win":
        return SelectionResult.WON if home_goals > away_goals else SelectionResult.LOST
    if key == "draw":
        return SelectionResult.WON if home_goals == away_goals else SelectionResult.LOST
    if key == "away_win":
        return SelectionResult.WON if away_goals > home_goals else SelectionResult.LOST
    if key == "double_chance_1x":
        return SelectionResult.WON if home_goals >= away_goals else SelectionResult.LOST
    if key == "double_chance_x2":
        return SelectionResult.WON if away_goals >= home_goals else SelectionResult.LOST
    if key == "double_chance_12":
        return SelectionResult.WON if home_goals != away_goals else SelectionResult.LOST
    if key == "dnb_home":
        if home_goals == away_goals:
            return SelectionResult.VOID
        return SelectionResult.WON if home_goals > away_goals else SelectionResult.LOST
    if key == "dnb_away":
        if home_goals == away_goals:
            return SelectionResult.VOID
        return SelectionResult.WON if away_goals > home_goals else SelectionResult.LOST
    raise ValueError(f"Unsupported market for settlement: {market}")


class SettlementService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def ingest_results(self, payloads: list[dict], source: str) -> dict:
        changed_match_ids: set[int] = set()
        duplicate_count = 0
        corrected_count = 0
        for payload in payloads:
            match = await self._resolve_match(payload)
            if match is None:
                raise ValueError("Result references an unknown match")
            previous = (match.home_goals, match.away_goals, match.status.value)
            incoming = (payload["home_goals"], payload["away_goals"], MatchStatus.FINISHED.value)
            if previous == incoming:
                duplicate_count += 1
                continue
            is_correction = match.status == MatchStatus.FINISHED and match.home_goals is not None
            if is_correction:
                corrected_count += 1
            match.home_goals = payload["home_goals"]
            match.away_goals = payload["away_goals"]
            match.home_goals_ht = payload.get("home_goals_ht")
            match.away_goals_ht = payload.get("away_goals_ht")
            match.status = MatchStatus.FINISHED
            changed_match_ids.add(match.id)
            self.db.add(
                AuditEvent(
                    entity_type="match_result",
                    entity_id=str(match.id),
                    event_type="corrected" if is_correction else "ingested",
                    actor=source,
                    payload={"previous": previous, "current": incoming},
                )
            )
        await self.db.flush()
        settlement = await self.settle_for_matches(changed_match_ids, source) if changed_match_ids else {
            "selections_settled": 0,
            "tickets_settled": 0,
            "individual_bets_settled": 0,
            "custom_accumulators_settled": 0,
        }
        return {
            "matches_changed": len(changed_match_ids),
            "duplicates": duplicate_count,
            "corrections": corrected_count,
            **settlement,
        }

    async def settle_finished_matches(
        self, source: str = "pipeline", since: date | None = None
    ) -> dict:
        """Settle finished matches, optionally restricted to a kickoff window.

        The periodic task passes `since` so a run that fires every few minutes
        stays proportional to the lookback window rather than to all history.
        Callers that must sweep the full archive (historical sync, backfills)
        leave it as None.
        """
        filters = [
            Match.status.in_((MatchStatus.FINISHED, MatchStatus.POSTPONED, MatchStatus.CANCELLED)),
        ]
        if since is not None:
            start_utc, _ = cat_day_bounds_utc(since)
            filters.append(Match.kickoff_at >= start_utc)
        result = await self.db.execute(select(Match.id).where(*filters))
        return await self.settle_for_matches(set(result.scalars().all()), source)

    async def settle_for_matches(self, match_ids: set[int], source: str) -> dict:
        if not match_ids:
            return {
                "selections_settled": 0,
                "tickets_settled": 0,
                "individual_bets_settled": 0,
                "custom_accumulators_settled": 0,
            }
        result = await self.db.execute(
            select(TicketSelection)
            .where(TicketSelection.match_id.in_(match_ids))
            .options(selectinload(TicketSelection.match))
        )
        selections = result.scalars().all()
        ticket_ids: set[int] = set()
        settled = 0
        now = datetime.now(timezone.utc)
        for selection in selections:
            match = selection.match
            if match.status in (MatchStatus.POSTPONED, MatchStatus.CANCELLED):
                outcome = SelectionResult.VOID
            elif match.home_goals is not None and match.away_goals is not None:
                try:
                    outcome = evaluate_selection(selection.market, match.home_goals, match.away_goals)
                except ValueError as exc:
                    self.db.add(
                        AuditEvent(
                            entity_type="ticket_selection",
                            entity_id=str(selection.id),
                            event_type="settlement_failed",
                            actor=source,
                            reason=str(exc),
                        )
                    )
                    continue
            else:
                continue
            previous = selection.result
            if previous == outcome:
                ticket_ids.add(selection.ticket_id)
                continue
            selection.result = outcome
            selection.settled_at = now
            ticket_ids.add(selection.ticket_id)
            settled += 1
            self.db.add(
                AuditEvent(
                    entity_type="ticket_selection",
                    entity_id=str(selection.id),
                    event_type="settled" if previous == SelectionResult.PENDING else "adjusted",
                    actor=source,
                    payload={"previous": previous.value, "current": outcome.value},
                )
            )
        await self.db.flush()
        tickets_settled = 0
        for ticket_id in ticket_ids:
            if await self._settle_ticket(ticket_id, source, now):
                tickets_settled += 1
        individual_bets_settled = await self._settle_confirmed_individual_bets(
            match_ids, source, now
        )
        custom_accumulators_settled = await self._settle_custom_accumulators(
            match_ids, source, now
        )
        await self.db.flush()
        return {
            "selections_settled": settled,
            "tickets_settled": tickets_settled,
            "individual_bets_settled": individual_bets_settled,
            "custom_accumulators_settled": custom_accumulators_settled,
        }

    async def _settle_custom_accumulators(
        self,
        match_ids: set[int],
        source: str,
        now: datetime,
    ) -> int:
        """Settle placed user accumulators once every leg has an authoritative result."""
        result = await self.db.execute(
            select(CustomAccumulator)
            .join(CustomAccumulatorLeg)
            .where(
                CustomAccumulator.status == CustomAccumulatorStatus.PLACED,
                CustomAccumulatorLeg.match_id.in_(match_ids),
            )
            .options(selectinload(CustomAccumulator.legs))
        )
        rows = result.scalars().unique().all()
        changed = 0
        for accumulator in rows:
            outcomes = []
            for leg in accumulator.legs:
                match = await self.db.get(Match, leg.match_id)
                if not match:
                    outcomes.append(SelectionResult.PENDING)
                    continue
                if match.status in (MatchStatus.POSTPONED, MatchStatus.CANCELLED):
                    outcome = SelectionResult.VOID
                elif match.status == MatchStatus.FINISHED and match.home_goals is not None and match.away_goals is not None:
                    try:
                        outcome = evaluate_selection(leg.market, match.home_goals, match.away_goals)
                    except ValueError as exc:
                        self.db.add(
                            AuditEvent(
                                entity_type="custom_accumulator_leg",
                                entity_id=str(leg.id),
                                event_type="settlement_failed",
                                actor=source,
                                reason=str(exc),
                            )
                        )
                        outcomes.append(SelectionResult.PENDING)
                        continue
                else:
                    outcomes.append(SelectionResult.PENDING)
                    continue
                if leg.result != outcome:
                    leg.result = outcome
                    leg.settled_at = now
                outcomes.append(outcome)
            if not outcomes or SelectionResult.PENDING in outcomes:
                continue
            if SelectionResult.LOST in outcomes:
                status, actual_return = CustomAccumulatorStatus.LOST, 0.0
            elif all(outcome == SelectionResult.VOID for outcome in outcomes):
                status, actual_return = CustomAccumulatorStatus.VOID, accumulator.stake or 0.0
            else:
                status = CustomAccumulatorStatus.WON
                active_odds = [leg.odds_snapshot for leg, outcome in zip(accumulator.legs, outcomes) if outcome == SelectionResult.WON]
                actual_return = round((accumulator.stake or 0.0) * self._product(active_odds), 2)
            accumulator.status = status
            accumulator.actual_return = actual_return
            accumulator.settled_at = now
            accumulator.settlement_details = {
                "source": source,
                "result": status.value,
                "legs": [outcome.value for outcome in outcomes],
            }
            self.db.add(AuditEvent(
                entity_type="custom_accumulator",
                entity_id=str(accumulator.id),
                event_type="settled",
                actor=source,
                payload=accumulator.settlement_details,
            ))
            changed += 1
        return changed

    @staticmethod
    def _product(values: list[float]) -> float:
        result = 1.0
        for value in values:
            result *= value
        return result

    async def _settle_confirmed_individual_bets(
        self,
        match_ids: set[int],
        source: str,
        now: datetime,
    ) -> int:
        """Settle confirmed, match-linked bets; generic journal entries stay manual."""
        result = await self.db.execute(
            select(Bet).where(
                Bet.match_id.in_(match_ids),
                Bet.source_selection_id.is_not(None),
                Bet.market.is_not(None),
                Bet.status != BetStatus.CASHOUT,
            )
        )
        bets = result.scalars().all()
        if not bets:
            return 0

        match_result = await self.db.execute(select(Match).where(Match.id.in_(match_ids)))
        matches = {match.id: match for match in match_result.scalars().all()}
        changed = 0
        for bet in bets:
            match = matches.get(bet.match_id)
            if match is None:
                continue
            if match.status in (MatchStatus.POSTPONED, MatchStatus.CANCELLED):
                outcome = SelectionResult.VOID
            elif match.status == MatchStatus.FINISHED and match.home_goals is not None and match.away_goals is not None:
                try:
                    outcome = evaluate_selection(bet.market, match.home_goals, match.away_goals)
                except ValueError as exc:
                    self.db.add(
                        AuditEvent(
                            entity_type="bet",
                            entity_id=str(bet.id),
                            event_type="settlement_failed",
                            actor=source,
                            reason=str(exc),
                        )
                    )
                    continue
            else:
                continue

            status = {
                SelectionResult.WON: BetStatus.WON,
                SelectionResult.LOST: BetStatus.LOST,
                SelectionResult.VOID: BetStatus.VOID,
            }[outcome]
            actual_return = {
                BetStatus.WON: bet.potential_return,
                BetStatus.LOST: 0.0,
                BetStatus.VOID: bet.stake,
            }[status]
            if bet.status == status and bet.actual_return == actual_return:
                continue

            previous = {
                "status": bet.status.value,
                "actual_return": bet.actual_return,
            }
            bet.status = status
            bet.actual_return = actual_return
            changed += 1
            self.db.add(
                AuditEvent(
                    entity_type="bet",
                    entity_id=str(bet.id),
                    event_type=(
                        "settled"
                        if previous["status"] == BetStatus.PENDING.value
                        else "settlement_adjusted"
                    ),
                    actor=source,
                    payload={
                        "previous": previous,
                        "current": {
                            "status": status.value,
                            "actual_return": actual_return,
                        },
                        "match_id": match.id,
                    },
                )
            )
        return changed

    async def _settle_ticket(self, ticket_id: int, source: str, now: datetime) -> bool:
        result = await self.db.execute(
            select(AccumulatorTicket)
            .where(AccumulatorTicket.id == ticket_id)
            .options(
                selectinload(AccumulatorTicket.selections),
                selectinload(AccumulatorTicket.results),
            )
        )
        ticket = result.scalar_one()
        outcomes = [selection.result for selection in ticket.selections]
        if not outcomes or SelectionResult.PENDING in outcomes:
            return False
        if SelectionResult.LOST in outcomes:
            outcome = SelectionResult.LOST
            returned = 0.0
        elif all(item == SelectionResult.VOID for item in outcomes):
            outcome = SelectionResult.VOID
            returned = 1.0
        else:
            outcome = SelectionResult.WON
            returned = 1.0
            for selection in ticket.selections:
                if selection.result == SelectionResult.WON:
                    returned *= selection.odds_snapshot
        profit_loss = returned - 1.0
        latest = max(ticket.results, key=lambda item: item.version, default=None)
        if latest and latest.result == outcome and abs(latest.return_amount - returned) < 1e-9:
            return False
        new_result = TicketResult(
            ticket_id=ticket.id,
            version=(latest.version + 1) if latest else 1,
            supersedes_result_id=latest.id if latest else None,
            result=outcome,
            stake=1.0,
            return_amount=returned,
            profit_loss=profit_loss,
            source=source,
            details={"selection_results": [item.value for item in outcomes]},
            settled_at=now,
        )
        # Maintain the loaded relationship as well as the FK so a caller that
        # computes performance in the same transaction sees the appended
        # settlement version without requiring a new session.
        ticket.results.append(new_result)
        ticket.status = TicketStatus.VOID if outcome == SelectionResult.VOID else TicketStatus.SETTLED
        self.db.add(
            AuditEvent(
                entity_type="accumulator_ticket",
                entity_id=str(ticket.id),
                event_type="settled" if latest is None else "settlement_adjusted",
                actor=source,
                payload={
                    "result": outcome.value,
                    "return_amount": returned,
                    "profit_loss": profit_loss,
                    "result_version": new_result.version,
                },
            )
        )
        return True

    async def _resolve_match(self, payload: dict) -> Match | None:
        if payload.get("match_id") is not None:
            return await self.db.get(Match, payload["match_id"])
        result = await self.db.execute(
            select(Match).where(Match.api_football_id == payload.get("api_football_id"))
        )
        return result.scalar_one_or_none()
