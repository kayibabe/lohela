"""Read-only API for immutable published paper tickets."""

from __future__ import annotations

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.config import cat_today
from app.api.security import get_optional_current_user, require_pro_access, require_research_access
from app.database import get_db
from app.models import (
    AccumulatorTicket,
    Match,
    PipelineRun,
    TicketGeneration,
    TicketSelection,
    TicketStatus,
    TicketType,
    RunStatus,
)
from app.services.ticket_publisher import get_latest_published_tickets, get_ticket_by_id
from app.services.pipeline_tracker import infer_pipeline_run_type

router = APIRouter(prefix="/tickets", tags=["tickets"])

PUBLIC_TICKET_TYPES = [TicketType.SAFE, TicketType.BALANCED, TicketType.AGGRESSIVE]


def _can_reveal(user) -> bool:
    # Direct service-level calls are trusted; HTTP requests resolve this dependency
    # to None or an actual User before reaching the handler.
    if hasattr(user, "dependency"):
        return True
    return bool(user and (getattr(user, "role", None) == "admin" or getattr(user, "plan", None) == "pro"))


async def require_internal_ticket_access(
    request: Request,
    include_internal: bool = Query(default=False),
    x_research_key: str | None = Header(default=None),
    user=Depends(get_optional_current_user),
) -> None:
    """Require the research key when an internal ticket stream is requested."""
    if include_internal and user and (user.role == "admin" or user.plan == "pro"):
        return
    if include_internal:
        await require_research_access(request, x_research_key)


class LegOut(BaseModel):
    model_config = {"protected_namespaces": ()}
    selection_id: int
    prediction_id: int
    match_id: int
    home_team: str
    away_team: str
    competition: str
    kickoff_at: str
    market: str
    selection: str
    model_probability: float | None
    model_agreement: Optional[float]
    best_odds: float | None
    q_score: float | None
    q_grade: str | None
    edge: Optional[float]
    expected_value: Optional[float]
    source_odds_at: Optional[str]
    result: str
    match_status: str
    home_goals: Optional[int]
    away_goals: Optional[int]
    live_phase: Optional[str]
    elapsed_minutes: Optional[int]
    selection_settled_at: Optional[str]
    locked_fields: list[str] = Field(default_factory=list)


class TicketOut(BaseModel):
    model_config = {"protected_namespaces": ()}
    ticket_id: int
    ticket_type: str
    name: str
    status: str
    version: int
    legs: list[LegOut]
    combined_odds: float | None
    combined_probability: float | None
    adjusted_probability: float | None
    correlation_penalty: float | None
    expected_value: float | None
    risk_score: float | None
    confidence_score: float | None
    avg_q_score: float | None
    avg_edge: Optional[float]
    model_version: str
    published_at: str
    publication_hash: str
    high_risk_label: bool
    relaxed_tier: bool
    relaxation_level: int
    horizon_days: int = 0
    # "market": legs priced at the bookmaker's de-vigged probability (no value
    # claimed); "model": the original edge-seeking research pricing.
    pricing: str = "model"
    internal_only: bool
    result: Optional[str]
    profit_loss: Optional[float]
    settled_at: Optional[str]
    settlement_source: Optional[str]
    settlement_version: Optional[int]
    locked_fields: list[str] = Field(default_factory=list)


class DailyTicketsOut(BaseModel):
    target_date: str
    qualified_pool: int
    generation_id: Optional[int] = None
    generation_status: Optional[str] = None
    generated_ticket_count: int = 0
    missing_public_ticket_types: list[str] = Field(default_factory=list)
    selection_diagnostics: dict[str, dict] = Field(default_factory=dict)
    pipeline_run_id: Optional[int] = None
    pipeline_status: Optional[str] = None
    pipeline_current_stage: Optional[str] = None
    pipeline_trigger_source: Optional[str] = None
    pipeline_started_at: Optional[str] = None
    pipeline_completed_at: Optional[str] = None
    pipeline_error: Optional[str] = None
    conservative: Optional[TicketOut]
    balanced: Optional[TicketOut]
    aggressive: Optional[TicketOut]
    best_value: Optional[TicketOut]
    superseded_versions: list[TicketHistoryOut] = Field(default_factory=list)


class TicketHistoryOut(BaseModel):
    model_config = {"protected_namespaces": ()}
    ticket_id: int
    target_date: str
    ticket_type: str
    name: str
    status: str
    version: int
    leg_count: int
    combined_odds: float | None
    adjusted_probability: float | None
    risk_score: float | None
    avg_q_score: float | None
    model_version: str
    published_at: str
    publication_hash: str
    relaxed_tier: bool
    internal_only: bool
    result: Optional[str]
    stake: Optional[float]
    return_amount: Optional[float]
    profit_loss: Optional[float]
    settled_at: Optional[str]
    locked_fields: list[str] = Field(default_factory=list)


class MatchHistoryOut(BaseModel):
    match_id: int
    target_date: str
    kickoff_at: str
    home_team: str
    away_team: str
    competition: str
    status: str
    live_phase: Optional[str]
    elapsed_minutes: Optional[int]
    home_goals: Optional[int]
    away_goals: Optional[int]
    outcome: Optional[str]
    ticket_types: list[str]
    selections: list[str]
    selection_evidence: list[dict]


@router.get("/daily", response_model=DailyTicketsOut)
async def get_daily_tickets(
    date: Optional[date] = Query(default=None, description="Target date (default: today)"),
    db: AsyncSession = Depends(get_db),
    include_internal: bool = Query(default=False),
    _: None = Depends(require_internal_ticket_access),
    user=Depends(get_optional_current_user),
):
    """Return latest persisted versions. This endpoint never regenerates tickets."""
    target = date or cat_today()
    rows = await get_latest_published_tickets(db, target, include_internal=include_internal)
    reveal = _can_reveal(user)
    mapped = {ticket.ticket_type: _ticket(ticket, reveal=reveal) for ticket in rows}
    generation_result = await db.execute(
        select(TicketGeneration)
        .where(TicketGeneration.target_date == target)
        .order_by(TicketGeneration.id.desc())
        .limit(1)
    )
    latest_generation = generation_result.scalar_one_or_none()
    # A generation can legitimately publish no ticket. Read its persisted input
    # count directly instead of deriving it from child ticket rows that do not
    # exist in that case.
    qualified_pool = (
        latest_generation.input_count
        if latest_generation is not None
        else max((ticket.generation.input_count for ticket in rows), default=0)
    )
    publication_summary = (
        (latest_generation.config_snapshot or {}).get("publication_summary", {})
        if latest_generation is not None
        else {}
    )
    pipeline_result = await db.execute(
        select(PipelineRun)
        .where(PipelineRun.target_date == target)
        .options(selectinload(PipelineRun.stages))
        .order_by(PipelineRun.id.desc())
    )
    latest_daily_pipeline = next(
        (
            run
            for run in pipeline_result.scalars().all()
            if infer_pipeline_run_type(run) == "daily_pipeline"
        ),
        None,
    )
    published_types = set(publication_summary.get("published_ticket_types", []))
    public_published = len(published_types.intersection(
        {ticket_type.value for ticket_type in PUBLIC_TICKET_TYPES}
    ))
    metadata_inconsistent = bool(
        latest_generation
        and (
            latest_generation.status == RunStatus.FAILED
            or public_published == 0
        )
    )
    pipeline_blocked = bool(
        latest_daily_pipeline
        and latest_daily_pipeline.status == RunStatus.FAILED
    )
    publication_blocked = metadata_inconsistent or pipeline_blocked
    if publication_blocked:
        # Never fall back to an older ticket when the current daily attempt is
        # below the configured publication minimum or failed. Historical rows
        # remain available through the history endpoint for audit.
        mapped = {}
    history_result = await db.execute(
        select(AccumulatorTicket)
        .where(
            AccumulatorTicket.target_date == target,
            AccumulatorTicket.status.in_([TicketStatus.PUBLISHED, TicketStatus.SETTLED, TicketStatus.VOID]),
        )
        .options(selectinload(AccumulatorTicket.selections), selectinload(AccumulatorTicket.results))
        .order_by(AccumulatorTicket.ticket_type, AccumulatorTicket.version.desc())
    )
    latest_ids = {ticket.ticket_id for ticket in mapped.values() if ticket}
    superseded = [] if publication_blocked else [
        _history_ticket(ticket, reveal=reveal)
        for ticket in history_result.scalars().all()
        if ticket.id not in latest_ids
    ]
    return DailyTicketsOut(
        target_date=target.isoformat(),
        qualified_pool=qualified_pool,
        generation_id=latest_generation.id if latest_generation else None,
        generation_status=(
            latest_generation.status.value if latest_generation else None
        ),
        generated_ticket_count=(0 if publication_blocked else latest_generation.output_count)
        if latest_generation
        else (0 if publication_blocked else len(rows)),
        missing_public_ticket_types=list(
            publication_summary.get("missing_public_ticket_types", [])
        ),
        selection_diagnostics=(
            (latest_generation.config_snapshot or {}).get("selection_diagnostics", {})
            if latest_generation
            else {}
        ),
        pipeline_run_id=latest_daily_pipeline.id if latest_daily_pipeline else None,
        pipeline_status=(
            latest_daily_pipeline.status.value if latest_daily_pipeline else None
        ),
        pipeline_current_stage=(
            latest_daily_pipeline.current_stage if latest_daily_pipeline else None
        ),
        pipeline_trigger_source=(
            (latest_daily_pipeline.run_details or {}).get("trigger_source")
            if latest_daily_pipeline
            else None
        ),
        pipeline_started_at=(
            latest_daily_pipeline.started_at.isoformat()
            if latest_daily_pipeline and latest_daily_pipeline.started_at
            else None
        ),
        pipeline_completed_at=(
            latest_daily_pipeline.completed_at.isoformat()
            if latest_daily_pipeline and latest_daily_pipeline.completed_at
            else None
        ),
        pipeline_error=(
            latest_daily_pipeline.error_details if latest_daily_pipeline else None
        ),
        conservative=mapped.get(TicketType.SAFE),
        balanced=mapped.get(TicketType.BALANCED),
        aggressive=mapped.get(TicketType.AGGRESSIVE),
        best_value=mapped.get(TicketType.BEST_VALUE),
        superseded_versions=superseded,
    )


@router.get("/history", response_model=list[TicketHistoryOut])
async def get_ticket_history(
    limit: int = Query(default=100, ge=1, le=200),
    include_internal: bool = Query(default=False),
    include_superseded: bool = Query(
        default=False,
        description="Include every immutable published version instead of latest cohorts only",
    ),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_internal_ticket_access),
    user=Depends(get_optional_current_user),
):
    """Return immutable ticket history, latest cohorts by default."""
    ticket_types = list(TicketType) if include_internal else PUBLIC_TICKET_TYPES
    if include_superseded:
        result = await db.execute(
            select(AccumulatorTicket)
            .where(
                AccumulatorTicket.ticket_type.in_(ticket_types),
                AccumulatorTicket.status.in_([
                    TicketStatus.PUBLISHED,
                    TicketStatus.SETTLED,
                    TicketStatus.VOID,
                ]),
            )
            .options(
                selectinload(AccumulatorTicket.selections),
                selectinload(AccumulatorTicket.results),
            )
            .order_by(
                AccumulatorTicket.target_date.desc(),
                AccumulatorTicket.ticket_type,
                AccumulatorTicket.version.desc(),
            )
            .limit(limit)
        )
        reveal = _can_reveal(user)
        return [
            _history_ticket(ticket) if reveal else _history_ticket(ticket, reveal=False)
            for ticket in result.scalars().all()
        ]

    latest_versions = (
        select(
            AccumulatorTicket.target_date.label("target_date"),
            AccumulatorTicket.ticket_type.label("ticket_type"),
            func.max(AccumulatorTicket.version).label("version"),
        )
        .where(
            AccumulatorTicket.ticket_type.in_(ticket_types),
            AccumulatorTicket.status.in_([TicketStatus.PUBLISHED, TicketStatus.SETTLED, TicketStatus.VOID]),
        )
        .group_by(AccumulatorTicket.target_date, AccumulatorTicket.ticket_type)
        .subquery()
    )
    result = await db.execute(
        select(AccumulatorTicket)
        .join(
            latest_versions,
            (AccumulatorTicket.target_date == latest_versions.c.target_date)
            & (AccumulatorTicket.ticket_type == latest_versions.c.ticket_type)
            & (AccumulatorTicket.version == latest_versions.c.version),
        )
        .options(
            selectinload(AccumulatorTicket.selections),
            selectinload(AccumulatorTicket.results),
        )
        .order_by(AccumulatorTicket.target_date.desc(), AccumulatorTicket.ticket_type)
        .limit(limit)
    )
    reveal = _can_reveal(user)
    return [
        _history_ticket(ticket) if reveal else _history_ticket(ticket, reveal=False)
        for ticket in result.scalars().all()
    ]


@router.get("/matches/history", response_model=list[MatchHistoryOut])
async def get_match_history(
    limit: int = Query(default=500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
    _: object = Depends(require_pro_access),
):
    """Return one match record per published match, with its latest outcomes."""
    latest_versions = (
        select(AccumulatorTicket.target_date, AccumulatorTicket.ticket_type, func.max(AccumulatorTicket.version).label("version"))
        .where(
            AccumulatorTicket.ticket_type.in_(PUBLIC_TICKET_TYPES),
            AccumulatorTicket.status.in_([TicketStatus.PUBLISHED, TicketStatus.SETTLED, TicketStatus.VOID]),
        )
        .group_by(AccumulatorTicket.target_date, AccumulatorTicket.ticket_type).subquery()
    )
    result = await db.execute(
        select(AccumulatorTicket).join(latest_versions,
            (AccumulatorTicket.target_date == latest_versions.c.target_date) &
            (AccumulatorTicket.ticket_type == latest_versions.c.ticket_type) &
            (AccumulatorTicket.version == latest_versions.c.version))
        .options(selectinload(AccumulatorTicket.selections).selectinload(TicketSelection.match).selectinload(Match.home_team),
                 selectinload(AccumulatorTicket.selections).selectinload(TicketSelection.match).selectinload(Match.away_team),
                 selectinload(AccumulatorTicket.selections).selectinload(TicketSelection.match).selectinload(Match.competition),
                 selectinload(AccumulatorTicket.selections).selectinload(TicketSelection.prediction))
        .order_by(AccumulatorTicket.target_date.desc()).limit(limit)
    )
    grouped: dict[int, dict] = {}
    for ticket in result.scalars().all():
        for selection in ticket.selections:
            match = selection.match
            item = grouped.setdefault(match.id, {"match_id": match.id, "target_date": match.kickoff_at.date().isoformat(), "kickoff_at": match.kickoff_at.isoformat(), "home_team": match.home_team.name, "away_team": match.away_team.name, "competition": match.competition.name, "status": match.status.value, "live_phase": match.live_phase, "elapsed_minutes": match.elapsed_minutes, "home_goals": match.home_goals, "away_goals": match.away_goals, "outcome": None, "ticket_types": [], "selections": [], "selection_evidence": []})
            if ticket.ticket_type.value not in item["ticket_types"]: item["ticket_types"].append(ticket.ticket_type.value)
            if selection.selection not in item["selections"]: item["selections"].append(selection.selection)
            if not any(row["market"] == selection.market and row["selection"] == selection.selection for row in item["selection_evidence"]):
                item["selection_evidence"].append({"market": selection.market, "selection": selection.selection, "model_probability": selection.probability_snapshot, "model_spread": selection.prediction.model_agreement if selection.prediction else None, "odds": selection.odds_snapshot, "q_score": selection.q_score_snapshot, "edge": selection.edge_snapshot, "odds_captured_at": selection.source_odds_at.isoformat() if selection.source_odds_at else None, "result": selection.result.value})
            if selection.result is not None:
                if item["outcome"] is None:
                    item["outcome"] = selection.result.value
                elif item["outcome"] != selection.result.value:
                    item["outcome"] = "mixed"
    return sorted(grouped.values(), key=lambda item: (item["target_date"], item["kickoff_at"]), reverse=True)


@router.get("/{ticket_id}", response_model=TicketOut)
async def get_ticket(
    ticket_id: int,
    request: Request,
    x_research_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
    user=Depends(get_optional_current_user),
):
    ticket = await get_ticket_by_id(db, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail="Ticket not found")
    if ticket.ticket_type == TicketType.BEST_VALUE:
        await require_research_access(request, x_research_key)
    reveal = bool(user and (getattr(user, "role", None) == "admin" or getattr(user, "plan", None) == "pro"))
    return _ticket(ticket, reveal=reveal)


def _ticket(ticket: AccumulatorTicket, *, reveal: bool = True) -> TicketOut:
    latest_result = max(ticket.results, key=lambda item: item.version, default=None)
    locked = [] if reveal else ["probability", "odds", "q_score", "edge", "expected_value", "risk_score"]
    return TicketOut(
        ticket_id=ticket.id,
        ticket_type=ticket.ticket_type.value,
        name={
            TicketType.SAFE: "Conservative",
            TicketType.BALANCED: "Balanced",
            TicketType.AGGRESSIVE: "Aggressive",
            TicketType.BEST_VALUE: "Best Value",
        }[ticket.ticket_type],
        status=ticket.status.value,
        version=ticket.version,
        legs=[_leg(selection, reveal=reveal) for selection in ticket.selections],
        combined_odds=ticket.combined_odds if reveal else None,
        combined_probability=ticket.combined_probability if reveal else None,
        adjusted_probability=ticket.adjusted_probability if reveal else None,
        correlation_penalty=ticket.correlation_penalty if reveal else None,
        expected_value=ticket.expected_value if reveal else None,
        risk_score=ticket.risk_score if reveal else None,
        confidence_score=ticket.confidence_score if reveal else None,
        avg_q_score=ticket.average_q_score if reveal else None,
        avg_edge=ticket.average_edge if reveal else None,
        model_version=ticket.model_version,
        published_at=ticket.published_at.isoformat(),
        publication_hash=ticket.publication_hash,
        high_risk_label=ticket.high_risk_label,
        relaxed_tier=ticket.relaxed_tier,
        relaxation_level=ticket.relaxation_level,
        horizon_days=ticket.horizon_days or 0,
        pricing=_ticket_pricing(ticket),
        internal_only=ticket.ticket_type == TicketType.BEST_VALUE,
        result=latest_result.result.value if latest_result else None,
        profit_loss=latest_result.profit_loss if latest_result else None,
        settled_at=latest_result.settled_at.isoformat() if latest_result else None,
        settlement_source=latest_result.source if latest_result else None,
        settlement_version=latest_result.version if latest_result else None,
        locked_fields=locked,
    )


def _ticket_pricing(ticket: AccumulatorTicket) -> str:
    """Pricing recorded on the ticket's generation; older generations are model-priced."""
    generation = ticket.__dict__.get("generation")  # never lazy-load in async context
    return ((generation.config_snapshot or {}).get("pricing") if generation else None) or "model"


def _history_ticket(ticket: AccumulatorTicket, *, reveal: bool = True) -> TicketHistoryOut:
    latest_result = max(ticket.results, key=lambda item: item.version, default=None)
    names = {
        TicketType.SAFE: "Conservative",
        TicketType.BALANCED: "Balanced",
        TicketType.AGGRESSIVE: "Aggressive",
        TicketType.BEST_VALUE: "Best Value",
    }
    return TicketHistoryOut(
        ticket_id=ticket.id,
        target_date=ticket.target_date.isoformat(),
        ticket_type=ticket.ticket_type.value,
        name=names[ticket.ticket_type],
        status=ticket.status.value,
        version=ticket.version,
        leg_count=len(ticket.selections),
        combined_odds=ticket.combined_odds if reveal else None,
        adjusted_probability=ticket.adjusted_probability if reveal else None,
        risk_score=ticket.risk_score if reveal else None,
        avg_q_score=ticket.average_q_score if reveal else None,
        model_version=ticket.model_version,
        published_at=ticket.published_at.isoformat(),
        publication_hash=ticket.publication_hash,
        relaxed_tier=ticket.relaxed_tier,
        internal_only=ticket.ticket_type == TicketType.BEST_VALUE,
        result=latest_result.result.value if latest_result else None,
        stake=latest_result.stake if latest_result else None,
        return_amount=latest_result.return_amount if latest_result else None,
        profit_loss=latest_result.profit_loss if latest_result else None,
        settled_at=latest_result.settled_at.isoformat() if latest_result else None,
        locked_fields=[] if reveal else ["odds", "probability", "q_score", "risk_score"],
    )


def _leg(selection: TicketSelection, *, reveal: bool = True) -> LegOut:
    match = selection.match
    return LegOut(
        selection_id=selection.id,
        prediction_id=selection.prediction_id,
        match_id=selection.match_id,
        home_team=match.home_team.name,
        away_team=match.away_team.name,
        competition=match.competition.name if match.competition else str(match.competition_id),
        kickoff_at=match.kickoff_at.isoformat(),
        market=selection.market,
        selection=selection.selection,
        model_probability=selection.probability_snapshot if reveal else None,
        model_agreement=selection.prediction.model_agreement if selection.prediction else None,
        best_odds=selection.odds_snapshot if reveal else None,
        q_score=selection.q_score_snapshot if reveal else None,
        q_grade=_grade_label(selection.q_score_snapshot) if reveal else None,
        edge=selection.edge_snapshot if reveal else None,
        expected_value=selection.probability_snapshot * selection.odds_snapshot - 1.0 if reveal else None,
        source_odds_at=selection.source_odds_at.isoformat() if selection.source_odds_at else None,
        result=selection.result.value,
        match_status=match.status.value,
        home_goals=match.home_goals,
        away_goals=match.away_goals,
        live_phase=match.live_phase,
        elapsed_minutes=match.elapsed_minutes,
        selection_settled_at=selection.settled_at.isoformat() if selection.settled_at else None,
        locked_fields=[] if reveal else ["probability", "odds", "q_score", "edge", "expected_value"],
    )


def _grade_label(score: float) -> str:
    if score >= 90:
        return "A+"
    if score >= 85:
        return "A"
    if score >= 80:
        return "B+"
    if score >= 75:
        return "B"
    if score >= 70:
        return "C"
    return "REJECT"
