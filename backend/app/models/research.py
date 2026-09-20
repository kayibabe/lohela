"""Immutable research-ledger entities for model, ticket, and pipeline auditability."""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class TicketType(str, enum.Enum):
    SAFE = "safe"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    BEST_VALUE = "best_value"


class TicketStatus(str, enum.Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SETTLED = "settled"
    VOID = "void"


class SelectionResult(str, enum.Enum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    status: Mapped[RunStatus] = mapped_column(SAEnum(RunStatus), default=RunStatus.PENDING, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    input_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    predictions: Mapped[list["Prediction"]] = relationship("Prediction", back_populates="model_run")
    ticket_generations: Mapped[list["TicketGeneration"]] = relationship(
        "TicketGeneration", back_populates="model_run"
    )


class ModelLearningRun(Base):
    """Immutable definition and outcome of one offline challenger experiment."""

    __tablename__ = "model_learning_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_model_version: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    challenger_version: Mapped[str] = mapped_column(String(40), nullable=False, unique=True, index=True)
    status: Mapped[RunStatus] = mapped_column(
        SAEnum(RunStatus), default=RunStatus.PENDING, nullable=False
    )
    train_start: Mapped[date] = mapped_column(Date, nullable=False)
    train_end: Mapped[date] = mapped_column(Date, nullable=False)
    validation_start: Mapped[date] = mapped_column(Date, nullable=False)
    validation_end: Mapped[date] = mapped_column(Date, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    summary: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    profiles: Mapped[list["MarketLearningProfile"]] = relationship(
        "MarketLearningProfile", back_populates="learning_run"
    )


class MarketLearningProfile(Base):
    """Per-market weights, calibrator, and walk-forward evidence for a challenger."""

    __tablename__ = "market_learning_profiles"
    __table_args__ = (
        UniqueConstraint("learning_run_id", "market", name="uq_learning_profile_market"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    learning_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_learning_runs.id"), nullable=False, index=True
    )
    market: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="insufficient_evidence")
    train_sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    validation_sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    weights: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    calibrator: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    train_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    validation_metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    promotion_checks: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    learning_run: Mapped["ModelLearningRun"] = relationship(
        "ModelLearningRun", back_populates="profiles"
    )


class ModelLearningPromotion(Base):
    """Append-only activation record; the latest effective row wins."""

    __tablename__ = "model_learning_promotions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    learning_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_learning_runs.id"), nullable=False, unique=True, index=True
    )
    base_model_version: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    challenger_version: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    effective_from: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    promoted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    learning_run: Mapped["ModelLearningRun"] = relationship("ModelLearningRun")


class StrongestSelectionSnapshot(Base):
    """Immutable membership of a model run's displayed Strongest list."""

    __tablename__ = "strongest_selection_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "model_run_id", "prediction_id", name="uq_strongest_snapshot_prediction"
        ),
        UniqueConstraint("model_run_id", "rank", name="uq_strongest_snapshot_rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    model_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("model_runs.id"), nullable=False, index=True
    )
    prediction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("predictions.id"), nullable=False, index=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    capture_source: Mapped[str] = mapped_column(
        String(40), nullable=False, default="model_run_completion"
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    model_run: Mapped["ModelRun"] = relationship("ModelRun")
    prediction: Mapped["Prediction"] = relationship("Prediction")


class TicketGeneration(Base):
    __tablename__ = "ticket_generations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    model_run_id: Mapped[int] = mapped_column(Integer, ForeignKey("model_runs.id"), nullable=False)
    status: Mapped[RunStatus] = mapped_column(SAEnum(RunStatus), default=RunStatus.PENDING, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    input_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    model_run: Mapped["ModelRun"] = relationship("ModelRun", back_populates="ticket_generations")
    tickets: Mapped[list["AccumulatorTicket"]] = relationship(
        "AccumulatorTicket", back_populates="generation"
    )


class AccumulatorTicket(Base):
    __tablename__ = "accumulator_tickets"
    __table_args__ = (
        UniqueConstraint("target_date", "ticket_type", "version", name="uq_ticket_type_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    generation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("ticket_generations.id"), nullable=False, index=True
    )
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    ticket_type: Mapped[TicketType] = mapped_column(SAEnum(TicketType), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(TicketStatus), nullable=False, default=TicketStatus.DRAFT
    )
    combined_odds: Mapped[float] = mapped_column(Float, nullable=False)
    combined_probability: Mapped[float] = mapped_column(Float, nullable=False)
    adjusted_probability: Mapped[float] = mapped_column(Float, nullable=False)
    correlation_penalty: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expected_value: Mapped[float] = mapped_column(Float, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    average_q_score: Mapped[float] = mapped_column(Float, nullable=False)
    average_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    high_risk_label: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    relaxed_tier: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    relaxation_level: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    publication_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    generation: Mapped["TicketGeneration"] = relationship("TicketGeneration", back_populates="tickets")
    selections: Mapped[list["TicketSelection"]] = relationship(
        "TicketSelection", back_populates="ticket", order_by="TicketSelection.position"
    )
    results: Mapped[list["TicketResult"]] = relationship(
        "TicketResult", back_populates="ticket", order_by="TicketResult.version"
    )


class TicketSelection(Base):
    __tablename__ = "ticket_selections"
    __table_args__ = (
        UniqueConstraint("ticket_id", "position", name="uq_ticket_selection_position"),
        UniqueConstraint("ticket_id", "match_id", name="uq_ticket_one_selection_per_match"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accumulator_tickets.id"), nullable=False, index=True
    )
    prediction_id: Mapped[int] = mapped_column(Integer, ForeignKey("predictions.id"), nullable=False)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    market: Mapped[str] = mapped_column(String(100), nullable=False)
    selection: Mapped[str] = mapped_column(String(100), nullable=False)
    odds_snapshot: Mapped[float] = mapped_column(Float, nullable=False)
    probability_snapshot: Mapped[float] = mapped_column(Float, nullable=False)
    q_score_snapshot: Mapped[float] = mapped_column(Float, nullable=False)
    edge_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_odds_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[SelectionResult] = mapped_column(
        SAEnum(SelectionResult), default=SelectionResult.PENDING, nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    ticket: Mapped["AccumulatorTicket"] = relationship("AccumulatorTicket", back_populates="selections")
    prediction: Mapped["Prediction"] = relationship("Prediction")
    match: Mapped["Match"] = relationship("Match")


class TicketResult(Base):
    __tablename__ = "ticket_results"
    __table_args__ = (
        UniqueConstraint("ticket_id", "version", name="uq_ticket_result_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("accumulator_tickets.id"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    supersedes_result_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("ticket_results.id"), nullable=True
    )
    result: Mapped[SelectionResult] = mapped_column(SAEnum(SelectionResult), nullable=False)
    stake: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    return_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    profit_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    source: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    settled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    ticket: Mapped["AccumulatorTicket"] = relationship("AccumulatorTicket", back_populates="results")


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    actor: Mapped[str] = mapped_column(String(100), nullable=False, default="system")
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ModelPerformance(Base):
    __tablename__ = "model_performance"
    __table_args__ = (
        UniqueConstraint(
            "model_version", "model_name", "market", "period_start", "period_end",
            name="uq_model_performance_period",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    model_name: Mapped[str] = mapped_column(String(40), nullable=False)
    market: Mapped[str] = mapped_column(String(100), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    calibration_error: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi: Mapped[float | None] = mapped_column(Float, nullable=True)
    hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LeaguePerformance(Base):
    __tablename__ = "league_performance"
    __table_args__ = (
        UniqueConstraint("competition_id", "period_start", "period_end", name="uq_league_performance_period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_id: Mapped[int] = mapped_column(Integer, ForeignKey("competitions.id"), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    brier_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    roi: Mapped[float | None] = mapped_column(Float, nullable=True)
    hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    reliability_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CorrelationCoefficient(Base):
    __tablename__ = "correlation_coefficients"
    __table_args__ = (
        UniqueConstraint("scope", "key_a", "key_b", "competition_id", name="uq_correlation_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    key_a: Mapped[str] = mapped_column(String(100), nullable=False)
    key_b: Mapped[str] = mapped_column(String(100), nullable=False)
    competition_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("competitions.id"), nullable=True)
    coefficient: Mapped[float] = mapped_column(Float, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[RunStatus] = mapped_column(SAEnum(RunStatus), default=RunStatus.PENDING, nullable=False)
    current_stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    run_details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    stages: Mapped[list["PipelineStageRun"]] = relationship(
        "PipelineStageRun", back_populates="pipeline_run", order_by="PipelineStageRun.stage_order"
    )


class PipelineStageRun(Base):
    __tablename__ = "pipeline_stage_runs"
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "stage_name", name="uq_pipeline_stage"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pipeline_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pipeline_runs.id"), nullable=False, index=True
    )
    stage_name: Mapped[str] = mapped_column(String(80), nullable=False)
    stage_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RunStatus] = mapped_column(SAEnum(RunStatus), default=RunStatus.PENDING, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    stage_details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pipeline_run: Mapped["PipelineRun"] = relationship("PipelineRun", back_populates="stages")


class SinglesLedgerSnapshot(Base):
    """Immutable, insert-only capture of one real prospective singles decision.

    Application code must never UPDATE or DELETE rows here — a captured
    decision is permanent evidence, exactly like the local-file snapshots in
    app.services.singles_ledger (whose "'x' mode refuses overwrite" comment
    describes the same guarantee this table provides via insert-only Postgres
    storage instead of the container's ephemeral filesystem, which does not
    survive a redeploy or restart). sha256 detects accidental payload
    corruption; it is not an externally trusted timestamp.
    """

    __tablename__ = "singles_ledger_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, unique=True, index=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    picks_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[RunStatus] = mapped_column(SAEnum(RunStatus), default=RunStatus.PENDING, nullable=False)
    config_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    leakage_checks_passed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    error_details: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
