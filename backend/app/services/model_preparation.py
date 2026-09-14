"""Reproducible pre-match model preparation from immutable finished results."""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Match, MatchStatus, Team
from app.services.elo_updater import EloUpdater
from app.services.models.bayesian import _run_analytical, run_mcmc_update


class ModelPreparationService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def prepare(self, target_date: date) -> dict:
        cutoff = datetime.combine(target_date, datetime.min.time(), tzinfo=timezone.utc)
        result = await self.db.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.FINISHED,
                Match.kickoff_at < cutoff,
                Match.home_goals.is_not(None),
                Match.away_goals.is_not(None),
            )
            .order_by(Match.kickoff_at.asc())
        )
        matches = result.scalars().all()
        historical = [
            {
                "home_team_id": match.home_team_id,
                "away_team_id": match.away_team_id,
                "home_goals": match.home_goals,
                "away_goals": match.away_goals,
            }
            for match in matches
        ]
        if not historical:
            return {
                "historical_matches": 0,
                "teams_prepared": 0,
                "elo_matches": 0,
                "bayesian_method": None,
            }

        elo = await EloUpdater(self.db).update_all()
        if settings.bayesian_estimation_method == "advi":
            # CPU-bound variational inference — keep it off the event loop.
            bayes_method, posteriors = await asyncio.to_thread(run_mcmc_update, historical)
        else:
            bayes_method, posteriors = "analytical", _run_analytical(historical)

        scored: dict[int, list[int]] = defaultdict(list)
        conceded: dict[int, list[int]] = defaultdict(list)
        for row in reversed(historical):
            home_id, away_id = row["home_team_id"], row["away_team_id"]
            if len(scored[home_id]) < 20:
                scored[home_id].append(row["home_goals"])
                conceded[home_id].append(row["away_goals"])
            if len(scored[away_id]) < 20:
                scored[away_id].append(row["away_goals"])
                conceded[away_id].append(row["home_goals"])

        teams_result = await self.db.execute(select(Team))
        teams = {team.id: team for team in teams_result.scalars().all()}
        updated = 0
        for team_id, posterior in posteriors.items():
            team = teams.get(team_id)
            if team is None:
                continue
            team.bayes_attack_mean = posterior["attack_mean"]
            team.bayes_attack_std = posterior["attack_std"]
            team.bayes_defense_mean = posterior["defense_mean"]
            team.bayes_defense_std = posterior["defense_std"]
            if scored[team_id]:
                team.avg_xg_for = max(0.2, sum(scored[team_id]) / len(scored[team_id]))
                team.avg_xg_against = max(0.2, sum(conceded[team_id]) / len(conceded[team_id]))
            updated += 1
        await self.db.flush()
        return {
            "historical_matches": len(historical),
            "teams_prepared": updated,
            "elo_matches": elo["matches_processed"],
            "bayesian_method": bayes_method,
        }


def expected_goals_proxy(home: Team, away: Team) -> tuple[float, float] | None:
    """Rolling, goal-derived pre-match intensities with explicit proxy semantics."""
    if home.bayes_attack_std == 0.2 or away.bayes_attack_std == 0.2:
        return None
    home_expected = math.sqrt(max(0.05, home.avg_xg_for) * max(0.05, away.avg_xg_against)) * 1.05
    away_expected = math.sqrt(max(0.05, away.avg_xg_for) * max(0.05, home.avg_xg_against))
    return min(4.5, max(0.2, home_expected)), min(4.5, max(0.2, away_expected))
