"""Persist optimiser output as versioned, content-hashed paper tickets."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

from sqlalchemy import func, select, text
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
from app.services.accumulator_builder import (
    PUBLIC_TICKET_TYPES,
    TICKET_SPECS,
    AccumulatorBuilder,
    DailyTickets,
    Ticket,
    active_ticket_specs,
)


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
        # Lock the publication date across model runs and concurrent workers.
        # The first non-empty optimiser publication is the day's immutable set.
        if self.db.get_bind().dialect.name == "postgresql":
            await self.db.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
                {"key": f"lohela:daily-tickets:{target_date.isoformat()}"},
            )
        retained = await self.db.execute(
            select(TicketGeneration)
            .where(
                TicketGeneration.target_date == target_date,
                TicketGeneration.output_count > 0,
                TicketGeneration.status.in_([RunStatus.COMPLETED, RunStatus.PARTIAL]),
            )
            .order_by(TicketGeneration.id.asc()).limit(1)
        )
        daily_generation = retained.scalar_one_or_none()
        if daily_generation is not None:
            return daily_generation
        built = await AccumulatorBuilder(self.db).build(
            target_date,
            model_run_id=model_run_id,
            research_min_qscore=research_min_qscore,
            horizon_dates=horizon_dates,
        )
        if built.model_run_id is None:
            raise ValueError("No completed model run exists for the requested date")
        decision_at = datetime.now(timezone.utc)
        self._validate_pre_match_candidates(
            [built.conservative, built.balanced, built.high_odds],
            decision_at,
        )
        await self._validate_pipeline_context(pipeline_run_id, target_date, built.model_run_id)

        public_tickets = [built.conservative, built.balanced, built.high_odds]
        missing_public_types = [
            ticket_type.value
            for ticket_type, candidate in zip(PUBLIC_TICKET_TYPES, public_tickets)
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
                "ticket_specs": [
                    _spec_snapshot(spec)
                    for spec in (TICKET_SPECS if research_min_qscore is not None else active_ticket_specs())
                ],
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
        # Best Value is retired from the active profile. Historical rows stay
        # queryable, but no new candidate is generated or published.
        tickets = public_tickets
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

    @staticmethod
    def _validate_pre_match_candidates(candidates: list[Ticket | None], decision_at: datetime) -> None:
        """Fail closed if a build contains a leg unavailable at publication time."""
        def utc(value: datetime) -> datetime:
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)

        for candidate in candidates:
            if candidate is None:
                continue
            for leg in candidate.legs:
                kickoff = utc(leg.kickoff_at)
                if kickoff <= decision_at:
                    raise ValueError("Refusing publication: a candidate kickoff has already passed")
                if (
                    leg.source_odds_at is None
                    or utc(leg.source_odds_at) > decision_at
                    or utc(leg.source_odds_at) >= kickoff
                ):
                    raise ValueError(
                        "Refusing publication: a candidate lacks a quote available before decision and kickoff"
                    )
                if not (
                    leg.source_odds_id or leg.source_odds_provenance.get("quote_id")
                ) or not leg.source_bookmaker:
                    raise ValueError("Refusing publication: a candidate lacks quote ID or bookmaker provenance")
                if not leg.feature_snapshot:
                    raise ValueError("Refusing publication: a candidate lacks its model feature snapshot")

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
        now = datetime.now(timezone.utc)
        self._validate_pre_match_candidates([candidate], now)
        content = _canonical_ticket_content(
            generation, candidate, model_version, version, now
        )
        publication_hash = hashlib.sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
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
            relaxed_tier=candidate.relaxed,
            relaxation_level=candidate.relaxation_level,
            horizon_days=candidate.horizon_days,
            pricing=candidate.pricing,
            publication_hash=publication_hash,
            published_at=now,
        )
        self.db.add(ticket)
        await self.db.flush()
        for position, leg in enumerate(candidate.legs, start=1):
            decision_snapshot = _pick_decision_snapshot(
                generation, candidate, leg, model_version, now
            )
            decision_snapshot_hash = hashlib.sha256(
                json.dumps(decision_snapshot, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
            ).hexdigest()
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
                    source_odds_id_snapshot=(
                        leg.source_odds_id or leg.source_odds_provenance.get("quote_id")
                    ),
                    source_bookmaker_snapshot=leg.source_bookmaker,
                    decision_at=now,
                    decision_snapshot=decision_snapshot,
                    decision_snapshot_hash=decision_snapshot_hash,
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
    ticket_types = list(TicketType) if include_internal else list(PUBLIC_TICKET_TYPES)
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
    generation: TicketGeneration,
    candidate: Ticket,
    model_version: str,
    version: int,
    decision_at: datetime,
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
                "decision_snapshot": _pick_decision_snapshot(
                    generation, candidate, leg, model_version, decision_at
                ),
            }
            for position, leg in enumerate(candidate.legs, start=1)
        ],
    }


def _pick_decision_snapshot(
    generation: TicketGeneration,
    candidate: Ticket,
    leg,
    model_version: str,
    decision_at: datetime,
) -> dict:
    """Freeze the prediction, feature, market and quote lineage for one pick."""
    quote_id = leg.source_odds_id or leg.source_odds_provenance.get("quote_id")
    return {
        "schema_version": "lohela.pick-decision.v1",
        "decision_at": decision_at.isoformat(),
        "generation_id": generation.id,
        "model_run_id": leg.model_run_id or generation.model_run_id,
        "model_version": leg.model_version or model_version,
        "prediction_id": leg.prediction_id,
        "feature_snapshot": leg.feature_snapshot,
        "market": {
            "market": leg.market,
            "selection": leg.selection,
            "ticket_pricing": candidate.pricing,
            "selected_odds": leg.best_odds,
            "selected_probability": leg.model_probability,
            "raw_model_probability": leg.raw_model_probability,
            "edge": leg.edge,
            "q_score": leg.q_score,
        },
        "quote": {
            "id": quote_id,
            "bookmaker": leg.source_bookmaker,
            "decimal_odds": leg.best_odds,
            "quoted_at": leg.source_odds_at.isoformat() if leg.source_odds_at else None,
            "provenance": leg.source_odds_provenance,
        },
    }


def _spec_snapshot(spec) -> dict:
    value = dict(spec.__dict__)
    value["ticket_type"] = spec.ticket_type.value
    if value["max_combined_odds"] == float("inf"):
        value["max_combined_odds"] = None
    return value
