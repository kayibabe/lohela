from sqlalchemy import Integer, String, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_football_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    competition_id: Mapped[int] = mapped_column(Integer, ForeignKey("competitions.id"), nullable=False)

    # Elo rating — spec §25.4; updated after every result
    elo_rating: Mapped[float] = mapped_column(Float, default=1500.0)

    # Dixon-Coles / Poisson model parameters — spec §25.1; updated via rolling window
    attack_strength: Mapped[float] = mapped_column(Float, default=1.0)
    defense_strength: Mapped[float] = mapped_column(Float, default=1.0)

    # xG averages (rolling)
    avg_xg_for: Mapped[float] = mapped_column(Float, default=1.2)
    avg_xg_against: Mapped[float] = mapped_column(Float, default=1.2)

    # Bayesian posterior means — spec §25.3; updated nightly
    bayes_attack_mean: Mapped[float] = mapped_column(Float, default=1.0)
    bayes_defense_mean: Mapped[float] = mapped_column(Float, default=1.0)
    bayes_attack_std: Mapped[float] = mapped_column(Float, default=0.2)
    bayes_defense_std: Mapped[float] = mapped_column(Float, default=0.2)

    competition: Mapped["Competition"] = relationship("Competition", back_populates="teams")
    home_matches: Mapped[list["Match"]] = relationship(
        "Match", foreign_keys="Match.home_team_id", back_populates="home_team"
    )
    away_matches: Mapped[list["Match"]] = relationship(
        "Match", foreign_keys="Match.away_team_id", back_populates="away_team"
    )

    def __repr__(self) -> str:
        return f"<Team {self.name} elo={self.elo_rating:.0f}>"
