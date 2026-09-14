from sqlalchemy import Integer, String, Float, ForeignKey, DateTime, Enum as SAEnum, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from app.database import Base
from app.config import CURRENT_MODEL_VERSION
from datetime import datetime
import enum


class QGrade(str, enum.Enum):
    A_PLUS = "A+"
    A = "A"
    B_PLUS = "B+"
    B = "B"
    C = "C"
    REJECT = "REJECT"


class Prediction(Base):
    """Per-market model predictions and Q-Score — spec §12, §31.

    Append-only: never update existing rows, only insert new model runs.
    model_version tracks which model produced this prediction — spec §24.4.
    """
    __tablename__ = "predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model_run_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("model_runs.id"), nullable=True, index=True
    )
    match_id: Mapped[int] = mapped_column(Integer, ForeignKey("matches.id"), nullable=False, index=True)
    market: Mapped[str] = mapped_column(String(100), nullable=False)    # e.g. "OVER_UNDER"
    selection: Mapped[str] = mapped_column(String(100), nullable=False)  # e.g. "over_1.5"

    # Semver model version — required for audit trail per spec §24.4
    model_version: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CURRENT_MODEL_VERSION
    )

    # Individual model probabilities — spec §8, §25
    poisson_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    zinb_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    bayes_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    elo_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    xg_prob: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Ensemble — spec §25.5
    model_probability: Mapped[float] = mapped_column(Float, nullable=False)
    # Raw weighted probability before an optional promoted calibration layer.
    raw_ensemble_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    learning_profile_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("market_learning_profiles.id"), nullable=True, index=True
    )
    # Std deviation across models — spec §9; >0.15 triggers confidence downgrade
    model_agreement: Mapped[float] = mapped_column(Float, nullable=False)

    # Value — spec §11; edge = model_prob - implied_prob
    edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Q-Score — spec §12
    q_score: Mapped[float] = mapped_column(Float, nullable=False)
    q_grade: Mapped[QGrade] = mapped_column(SAEnum(QGrade), nullable=False)

    # Q-Score component breakdown (for transparency / audit)
    q_model_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_value_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_xg_model: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_recent_form: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_market_consensus: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_odds_stability: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_team_news: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_league_reliability: Mapped[float | None] = mapped_column(Float, nullable=True)
    q_data_quality: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Exact scoring context. Missing components are explicitly recorded with
    # reason codes and always contribute zero to the fixed-weight score.
    q_component_weights: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    q_component_status: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    active_models: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    data_quality_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    source_odds_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("odds.id", ondelete="SET NULL"), nullable=True
    )
    source_odds_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_decimal_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_implied_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_odds_provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    match: Mapped["Match"] = relationship("Match", back_populates="predictions")
    model_run: Mapped["ModelRun | None"] = relationship("ModelRun", back_populates="predictions")
    learning_profile: Mapped["MarketLearningProfile | None"] = relationship(
        "MarketLearningProfile"
    )

    def __repr__(self) -> str:
        return f"<Prediction {self.market}/{self.selection} q={self.q_score:.1f} ({self.q_grade})>"
