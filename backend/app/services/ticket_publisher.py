"""Persist optimiser output as versioned, content-hashed paper tickets."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    AccumulatorTicket,
    AuditEvent,
    ModelRun,
    RunStatus,
    TicketGeneration,
    TicketSelection,
    TicketStatus,
    TicketType,
    Match,
    PipelineRun,
    PipelineStageRun,
)
from app.services.accumulator_builder import AccumulatorBuilder, DailyTickets, Ticket, active_ticket_specs


class TicketPublisher:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def generate_and_publish(
        self,
        target_date: date,
        model_run_id: int | None = None,
        research_min_qscore: float | None = None,
        pipeline_run_id: int | None = None,
        horizon_dates: list[date] | None = None,
    ) -> TicketGeneration:
        built = await AccumulatorBuilder(self.db).build(
            target_date,
            model_run_id=model_run_id,
            research_min_qscore=research_min_qscore,
            horizon_dates=horizon_dates,
        )
        if built.model_run_id is None:
            raise ValueError("No completed model run exists for the requested date")
        await self._validate_pipeline_context(pipeline_run_id, target_date, built.model_run_id)

        public_tickets = [built.conservative, built.balanced, built.aggressive]
        missing_public_types = [
            ticket_type.value
            for ticket_type, candidate in zip(
                (TicketType.SAFE, TicketType.BALANCED, TicketType.AGGRESSIVE),
                public_tickets,
            )
            if candidate is None
        ]
        existing_result = await self.db.execute(
            select(TicketGeneration)
            .where(
                TicketGeneration.target_date == target_date,
                TicketGeneration.model_run_id == built.model_run_id,
            )
            .order_by(TicketGeneration.id.desc())
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if existing is not None and existing.status in {RunStatus.COMPLETED, RunStatus.PARTIAL}:
            return existing

        generation = TicketGeneration(
            target_date=target_date,
            model_run_id=built.model_run_id,
            status=RunStatus.RUNNING,
            config_snapshot={
                "ticket_specs": [_spec_snapshot(spec) for spec in active_ticket_specs()],
                "pricing": (built.selection_diagnostics.get("pricing") or {}).get("source", "model"),
                "research_min_qscore": research_min_qscore,
                "publication_mode": "immutable_paper_trading",
                "selection_diagnostics": built.selection_diagnostics,
            },
            input_count=built.qualified_pool,
        )
        self.db.add(generation)
        await self.db.flush()

        model_run = await self.db.get(ModelRun, built.model_run_id)
        tickets = [*public_tickets, built.best_value]
        published = 0
        published_types = []
        for candidate in tickets:
            if candidate is None:
                continue
            await self._publish_candidate(generation, candidate, model_run.model_version if model_run else "unknown")
            published += 1
            published_types.append(candidate.ticket_type.value)

        generation.output_count = published
        public_published = sum(candidate is not None for candidate in public_tickets)
        generation.status = (
            RunStatus.COMPLETED
            if public_published == len(public_tickets)
            else RunStatus.PARTIAL
        )
        generation.completed_at = datetime.now(timezone.utc)
        relaxed_ticket_types = {
            candidate.ticket_type.value: candidate.relaxation_level
            for candidate in tickets
            if candidate is not None and candidate.relaxed
        }
        horizon_ticket_types = {
            candidate.ticket_type.value: candidate.horizon_days
            for candidate in tickets
            if candidate is not None and candidate.horizon_days
        }
        generation.config_snapshot = {
            **generation.config_snapshot,
            "publication_summary": {
                "published_ticket_types": published_types,
                "missing_public_ticket_types": missing_public_types,
                "relaxed_ticket_types": relaxed_ticket_types,
                "horizon_ticket_types": horizon_ticket_types,
                "horizon_dates": [d.isoformat() for d in built.horizon_dates],
            },
        }
        self.db.add(
            AuditEvent(
                entity_type="ticket_generation",
                entity_id=str(generation.id),
                event_type="published",
                actor="system",
                payload={
                    "target_date": target_date.isoformat(),
                    "model_run_id": built.model_run_id,
                    "qualified_pool": built.qualified_pool,
                    "published_ticket_count": published,
                    "published_ticket_types": published_types,
                    "missing_public_ticket_types": missing_public_types,
                    "relaxed_ticket_types": relaxed_ticket_types,
                    "horizon_ticket_types": horizon_ticket_types,
                },
            )
        )
        await self.db.flush()
        return generation

    async def _validate_pipeline_context(
        self, pipeline_run_id: int | None, target_date: date, model_run_id: int
    ) -> None:
        """Refuse publication when the upstream run is partial or mis-linked."""
        if pipeline_run_id is None:
            return
        run = await self.db.get(PipelineRun, pipeline_run_id)
        if run is None or run.target_date != target_date:
            raise ValueError("Refusing publication: pipeline run is missing or targets another date")
        if run.status in {RunStatus.PARTIAL, RunStatus.FAILED}:
            raise ValueError(f"Refusing publication: pipeline run is {run.status.value}")
        stage_result = await self.db.execute(
            select(PipelineStageRun).where(PipelineStageRun.pipeline_run_id == pipeline_run_id)
        )
        stages = {stage.stage_name: stage for stage in stage_result.scalars().all()}
        required = (
            "fixture_and_odds_ingestion",
            "data_enrichment",
            "model_execution",
            "ensemble",
            "market_comparison",
            "q_score",
        )
        incomplete = [name for name in required if stages.get(name) is None or stages[name].status != RunStatus.COMPLETED]
        if incomplete:
            raise ValueError(f"Refusing publication: incomplete upstream stages={incomplete}")
        if stages["model_execution"].stage_details.get("model_run_id") not in (None, model_run_id):
            raise ValueError("Refusing publication: model run does not match pipeline metadata")

    async def _publish_candidate(
        self, generation: TicketGeneration, candidate: Ticket, model_version: str
    ) -> AccumulatorTicket:
        version_result = await self.db.execute(
            select(func.max(AccumulatorTicket.version)).where(
                AccumulatorTicket.target_date == generation.target_date,
                AccumulatorTicket.ticket_type == candidate.ticket_type,
            )
        )
        version = int(version_result.scalar() or 0) + 1
        content = _canonical_ticket_content(generation, candidate, model_version, version)
        publication_hash = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        now = datetime.now(timezone.utc)
        ticket = AccumulatorTicket(
            generation_id=generation.id,
            target_date=generation.target_date,
            ticket_type=candidate.ticket_type,
            version=version,
            status=TicketStatus.PUBLISHED,
            combined_odds=candidate.combined_odds,
            combined_probability=candidate.combined_probability,
            adjusted_probability=candidate.adjusted_probability,
            correlation_penalty=candidate.correlation_penalty,
            expected_value=candidate.expected_value,
            risk_score=candidate.risk_score,
            confidence_score=candidate.confidence_score,
            average_q_score=candidate.avg_q_score,
            average_edge=candidate.avg_edge,
            model_version=model_version,
            high_risk_label=candidate.high_risk_label,
            relaxed_tier=candidate.relaxed,
            relaxation_level=candidate.relaxation_level,
            horizon_days=candidate.horizon_days,
            publication_hash=publication_hash,
            published_at=now,
        )
        self.db.add(ticket)
        await self.db.flush()
        for position, leg in enumerate(candidate.legs, start=1):
            self.db.add(
                TicketSelection(
                    ticket_id=ticket.id,
                    prediction_id=leg.prediction_id,
                    match_id=leg.match_id,
                    position=position,
                    market=leg.market,
                    selection=leg.selection,
                    odds_snapshot=leg.best_odds,
                    probability_snapshot=leg.model_probability,
                    q_score_snapshot=leg.q_score,
                    edge_snapshot=leg.edge,
                    source_odds_at=leg.source_odds_at,
                )
            )
        self.db.add(
            AuditEvent(
                entity_type="accumulator_ticket",
                entity_id=str(ticket.id),
                event_type="published",
                actor="system",
                payload={"version": version, "publication_hash": publication_hash, **content},
            )
        )
        await self.db.flush()
        return ticket


async def get_latest_published_tickets(
    db: AsyncSession, target_date: date, include_internal: bool = True
) -> list[AccumulatorTicket]:
    ticket_types = list(TicketType) if include_internal else [
        TicketType.SAFE,
        TicketType.BALANCED,
        TicketType.AGGRESSIVE,
    ]
    latest_versions = (
        select(
            AccumulatorTicket.ticket_type.label("ticket_type"),
            func.max(AccumulatorTicket.version).label("version"),
        )
        .where(
            AccumulatorTicket.target_date == target_date,
            AccumulatorTicket.ticket_type.in_(ticket_types),
            AccumulatorTicket.status.in_([TicketStatus.PUBLISHED, TicketStatus.SETTLED, TicketStatus.VOID]),
        )
        .group_by(AccumulatorTicket.ticket_type)
        .subquery()
    )
    result = await db.execute(
        select(AccumulatorTicket)
        .join(
            latest_versions,
            (AccumulatorTicket.ticket_type == latest_versions.c.ticket_type)
            & (AccumulatorTicket.version == latest_versions.c.version),
        )
        .where(AccumulatorTicket.target_date == target_date)
        .options(
            selectinload(AccumulatorTicket.selections)
            .selectinload(TicketSelection.match)
            .selectinload(Match.home_team),
            selectinload(AccumulatorTicket.selections)
            .selectinload(TicketSelection.match)
            .selectinload(Match.away_team),
            selectinload(AccumulatorTicket.selections)
            .selectinload(TicketSelection.match)
            .selectinload(Match.competition),
            selectinload(AccumulatorTicket.selections)
            .selectinload(TicketSelection.prediction),
            selectinload(AccumulatorTicket.results),
            selectinload(AccumulatorTicket.generation),
        )
    )
    return result.scalars().all()


async def get_ticket_by_id(db: AsyncSession, ticket_id: int) -> AccumulatorTicket | None:
    result = await db.execute(
        select(AccumulatorTicket)
        .where(AccumulatorTicket.id == ticket_id)
        .options(
            selectinload(AccumulatorTicket.selections)
            .selectinload(TicketSelection.match)
            .selectinload(Match.home_team),
            selectinload(AccumulatorTicket.selections)
            .selectinload(TicketSelection.match)
            .selectinload(Match.away_team),
            selectinload(AccumulatorTicket.selections)
            .selectinload(TicketSelection.match)
            .selectinload(Match.competition),
            selectinload(AccumulatorTicket.selections)
            .selectinload(TicketSelection.prediction),
            selectinload(AccumulatorTicket.results),
            selectinload(AccumulatorTicket.generation),
        )
    )
    return result.scalar_one_or_none()


def _canonical_ticket_content(
    generation: TicketGeneration, candidate: Ticket, model_version: str, version: int
) -> dict:
    return {
        "target_date": generation.target_date.isoformat(),
        "generation_id": generation.id,
        "model_run_id": generation.model_run_id,
        "model_version": model_version,
        "ticket_type": candidate.ticket_type.value,
        "version": version,
        "combined_odds": candidate.combined_odds,
        "combined_probability": candidate.combined_probability,
        "adjusted_probability": candidate.adjusted_probability,
        "correlation_penalty": candidate.correlation_penalty,
        "expected_value": candidate.expected_value,
        "risk_score": candidate.risk_score,
        "confidence_score": candidate.confidence_score,
        "relaxed_tier": candidate.relaxed,
        "relaxation_level": candidate.relaxation_level,
        "horizon_days": candidate.horizon_days,
        "pricing": candidate.pricing,
        "legs": [
            {
                "position": position,
                "prediction_id": leg.prediction_id,
                "match_id": leg.match_id,
                "market": leg.market,
                "selection": leg.selection,
                "odds": leg.best_odds,
                "probability": leg.model_probability,
                "raw_model_probability": leg.raw_model_probability,
                "q_score": leg.q_score,
                "edge": leg.edge,
                "source_odds_at": leg.source_odds_at.isoformat() if leg.source_odds_at else None,
            }
            for position, leg in enumerate(candidate.legs, start=1)
        ],
    }


def _spec_snapshot(spec) -> dict:
    value = dict(spec.__dict__)
    value["ticket_type"] = spec.ticket_type.value
    if value["max_combined_odds"] == float("inf"):
        value["max_combined_odds"] = None
    return value
