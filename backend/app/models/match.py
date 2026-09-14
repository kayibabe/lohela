from sqlalchemy import Integer, String, Float, Boolean, ForeignKey, DateTime, JSON, Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.database import Base
from datetime import datetime
import enum


class MatchStatus(str, enum.Enum):
    SCHEDULED = "scheduled"
    LIVE = "live"
    FINISHED = "finished"
    POSTPONED = "postponed"
    CANCELLED = "cancelled"


class Match(Base):
    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_football_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    competition_id: Mapped[int] = mapped_column(Integer, ForeignKey("competitions.id"), nullable=False)
    home_team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    kickoff_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    status: Mapped[MatchStatus] = mapped_column(SAEnum(MatchStatus), default=MatchStatus.SCHEDULED)
    live_phase: Mapped[str | None] = mapped_column(String(20), nullable=True)
    elapsed_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    season: Mapped[str] = mapped_column(String(10), nullable=False)  # e.g. "2025"
    round: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Results — nullable until match is played
    home_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    home_goals_ht: Mapped[int | None] = mapped_column(Integer, nullable=True)
    away_goals_ht: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # xG — spec §7; nullable when unavailable (reduces data_quality_score)
    home_xg: Mapped[float | None] = mapped_column(Float, nullable=True)
    away_xg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Team news / injuries — spec §7; stored as JSON
    team_news: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    # Data quality — spec §26 Stage 2; 0–100
    data_quality_score: Mapped[float] = mapped_column(Float, default=0.0)
    excluded_from_models: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    competition: Mapped["Competition"] = relationship("Competition", back_populates="matches")
    home_team: Mapped["Team"] = relationship("Team", foreign_keys=[home_team_id], back_populates="home_matches")
    away_team: Mapped["Team"] = relationship("Team", foreign_keys=[away_team_id], back_populates="away_matches")
    team_stats: Mapped[list["TeamStats"]] = relationship("TeamStats", back_populates="match")
    odds: Mapped[list["Odds"]] = relationship("Odds", back_populates="match")
    predictions: Mapped[list["Prediction"]] = relationship("Prediction", back_populates="match")

    def __repr__(self) -> str:
        return f"<Match {self.home_team_id} vs {self.away_team_id} @ {self.kickoff_at}>"


class TeamStats(Base):
    """Per-team statistics for a given match (home or away perspective)."""
    __tablename__ = "team_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    team_id: Mapped[int] = mapped_column(Integer, ForeignKey("teams.id"), nullable=False)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)

    goals_scored: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals_conceded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    xg: Mapped[float | None] = mapped_column(Float, nullable=True)
    xga: Mapped[float | None] = mapped_column(Float, nullable=True)
    shots: Mapped[int | None] = mapped_column(Integer, nullable=True)
    shots_on_target: Mapped[int | None] = mapped_column(Integer, nullable=True)
    possession: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Pre-computed rolling form strings — e.g. "WWDLW"
    form_last_5: Mapped[str | None] = mapped_column(String(10), nullable=True)
    form_last_10: Mapped[str | None] = mapped_column(String(15), nullable=True)

    match: Mapped["Match"] = relationship("Match", back_populates="team_stats")
