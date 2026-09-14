"""Persisted user-built accumulators and immutable selection snapshots."""

from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum as SAEnum, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.research import SelectionResult


class CustomAccumulatorStatus(str, enum.Enum):
    DRAFT = "draft"
    PLACED = "placed"
    WON = "won"
    LOST = "lost"
    VOID = "void"
    CASHOUT = "cashout"


class CustomAccumulator(Base):
    __tablename__ = "custom_accumulators"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    target_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    status: Mapped[CustomAccumulatorStatus] = mapped_column(
        SAEnum(CustomAccumulatorStatus), default=CustomAccumulatorStatus.DRAFT, nullable=False, index=True
    )
    stake: Mapped[float | None] = mapped_column(Float, nullable=True)
    combined_odds: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    potential_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_return: Mapped[float | None] = mapped_column(Float, nullable=True)
    settlement_details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    placed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    legs: Mapped[list["CustomAccumulatorLeg"]] = relationship(
        "CustomAccumulatorLeg",
        back_populates="accumulator",
        order_by="CustomAccumulatorLeg.position",
        cascade="all, delete-orphan",
    )


class CustomAccumulatorLeg(Base):
    __tablename__ = "custom_accumulator_legs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    accumulator_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("custom_accumulators.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prediction_id: Mapped[int] = mapped_column(Integer, ForeignKey("predictions.id"), nullable=False)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    home_team: Mapped[str] = mapped_column(String(160), nullable=False)
    away_team: Mapped[str] = mapped_column(String(160), nullable=False)
    competition: Mapped[str] = mapped_column(String(160), nullable=False)
    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    market: Mapped[str] = mapped_column(String(100), nullable=False)
    selection: Mapped[str] = mapped_column(String(100), nullable=False)
    odds_snapshot: Mapped[float] = mapped_column(Float, nullable=False)
    probability_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_score_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    edge_snapshot: Mapped[float | None] = mapped_column(Float, nullable=True)
    result: Mapped[SelectionResult] = mapped_column(
        SAEnum(SelectionResult), default=SelectionResult.PENDING, nullable=False
    )
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    accumulator: Mapped[CustomAccumulator] = relationship("CustomAccumulator", back_populates="legs")
