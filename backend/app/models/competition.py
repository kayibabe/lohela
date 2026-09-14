from sqlalchemy import Integer, String, Float, Boolean, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base
import enum


class LeagueTier(str, enum.Enum):
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"
    TIER4 = "tier4"


class Competition(Base):
    __tablename__ = "competitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_football_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    tier: Mapped[LeagueTier] = mapped_column(SAEnum(LeagueTier), default=LeagueTier.TIER1)
    # Spec §17: League Reliability Score (0.0–1.0)
    reliability_score: Mapped[float] = mapped_column(Float, default=1.0)
    # Home advantage in Elo points — spec §25.4 (65–100 range, calibrated per league)
    home_advantage_elo: Mapped[float] = mapped_column(Float, default=75.0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Dynamic blacklist — spec §17
    blacklisted: Mapped[bool] = mapped_column(Boolean, default=False)
    blacklist_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    teams: Mapped[list["Team"]] = relationship("Team", back_populates="competition")
    matches: Mapped[list["Match"]] = relationship("Match", back_populates="competition")

    def __repr__(self) -> str:
        return f"<Competition {self.name} ({self.country})>"
