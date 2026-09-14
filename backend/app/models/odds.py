from sqlalchemy import Integer, String, Float, Boolean, ForeignKey, DateTime, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.database import Base
from datetime import datetime


class Odds(Base):
    """Bookmaker odds per match / market / selection — spec §31."""
    __tablename__ = "odds"
    __table_args__ = (
        UniqueConstraint("match_id", "bookmaker", "market", "selection", name="uq_odds_entry"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    bookmaker: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[str] = mapped_column(String(100), nullable=False)   # e.g. "OVER_UNDER_2_5"
    selection: Mapped[str] = mapped_column(String(100), nullable=False) # e.g. "Over 2.5"

    decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)
    # implied_probability = 1 / decimal_odds — spec §10
    implied_probability: Mapped[float] = mapped_column(Float, nullable=False)

    opening_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Positive = odds drifted (less likely), negative = shortened (more likely)
    movement: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Quote provenance is retained in every downstream prediction snapshot.
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="live")
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    match: Mapped["Match"] = relationship("Match", back_populates="odds")


class OddsSnapshot(Base):
    """Immutable local capture of each API-Football quote refresh."""
    __tablename__ = "odds_snapshots"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    bookmaker: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[str] = mapped_column(String(100), nullable=False)
    selection: Mapped[str] = mapped_column(String(100), nullable=False)
    decimal_odds: Mapped[float] = mapped_column(Float, nullable=False)
    implied_probability: Mapped[float] = mapped_column(Float, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
