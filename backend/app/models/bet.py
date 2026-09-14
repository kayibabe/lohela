"""Bet tracking model — records placed accumulators for P&L / ROI analysis."""

import enum
from datetime import datetime

from sqlalchemy import Column, Integer, String, Float, DateTime, Enum, Text, Boolean, ForeignKey
from sqlalchemy.sql import func

from app.database import Base


class BetStatus(str, enum.Enum):
    PENDING = "pending"
    WON = "won"
    LOST = "lost"
    VOID = "void"
    CASHOUT = "cashout"


class Bet(Base):
    __tablename__ = "bets"

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Which system ticket this was placed from (optional)
    ticket_type = Column(String(32))   # conservative | balanced | aggressive | best_value | custom
    ticket_date = Column(String(10))   # YYYY-MM-DD of the fixtures

    # Human-readable label, e.g. "Man City / Arsenal / Barcelona - 3-leg acca"
    label = Column(String(512), nullable=False)

    # Bet financials
    odds = Column(Float, nullable=False)
    stake = Column(Float, nullable=False)
    potential_return = Column(Float, nullable=False)

    # Settlement
    status = Column(Enum(BetStatus), default=BetStatus.PENDING, nullable=False)
    actual_return = Column(Float)   # populated on settlement

    # Optional free-text notes
    notes = Column(Text)

    # Optional immutable provenance for a confirmed individual selection.
    source_selection_id = Column(Integer, ForeignKey("ticket_selections.id"), index=True)
    match_id = Column(Integer, ForeignKey("matches.id"), index=True)
    market = Column(String(64), index=True)
    selection = Column(String(128))

    @property
    def profit_loss(self) -> float | None:
        if self.actual_return is not None:
            return round(self.actual_return - self.stake, 2)
        return None

    @property
    def roi(self) -> float | None:
        if self.actual_return is not None and self.stake > 0:
            return round((self.actual_return - self.stake) / self.stake * 100, 2)
        return None
