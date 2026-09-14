"""
Elo rating updater — spec §25.4.

Replays all finished matches in chronological order, updating each team's
elo_rating after every result. A full replay from scratch (all teams reset
to 1500) ensures ratings are always consistent and reproducible.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from app.models import Match, MatchStatus, Team, Competition
from app.services.models.elo import update_elos

logger = logging.getLogger(__name__)

_DEFAULT_ELO = 1500.0


class EloUpdater:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def update_all(self) -> dict:
        """
        Reset all team Elo ratings to 1500 then replay every finished match
        in chronological order, updating ratings after each result.
        """
        # Step 1: reset all teams to default Elo
        await self.db.execute(update(Team).values(elo_rating=_DEFAULT_ELO))
        await self.db.flush()
        logger.info("Reset all team Elo ratings to %.0f", _DEFAULT_ELO)

        # Step 2: load all finished matches with goals in chronological order
        result = await self.db.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.FINISHED,
                Match.home_goals.is_not(None),
                Match.away_goals.is_not(None),
            )
            .order_by(Match.kickoff_at.asc())
        )
        matches = result.scalars().all()
        logger.info("Replaying Elo across %d finished matches", len(matches))

        # Cache teams and competitions to avoid N+1 queries
        teams_result = await self.db.execute(select(Team))
        teams: dict[int, Team] = {t.id: t for t in teams_result.scalars().all()}

        comps_result = await self.db.execute(select(Competition))
        comps: dict[int, Competition] = {c.id: c for c in comps_result.scalars().all()}

        processed = 0
        for match in matches:
            home = teams.get(match.home_team_id)
            away = teams.get(match.away_team_id)
            comp = comps.get(match.competition_id)
            if not home or not away:
                continue

            home_adv = comp.home_advantage_elo if comp else 75.0

            result = update_elos(
                elo_home=home.elo_rating,
                elo_away=away.elo_rating,
                home_goals=match.home_goals,
                away_goals=match.away_goals,
                k_factor=20,
                home_advantage_elo=home_adv,
            )

            home.elo_rating = result.home_new_elo
            away.elo_rating = result.away_new_elo
            processed += 1

        await self.db.commit()
        logger.info("Elo update complete — %d matches processed", processed)

        # Return summary stats
        elos = [t.elo_rating for t in teams.values()]
        return {
            "matches_processed": processed,
            "teams_updated": len([e for e in elos if e != _DEFAULT_ELO]),
            "elo_min": round(min(elos), 1),
            "elo_max": round(max(elos), 1),
            "elo_mean": round(sum(elos) / len(elos), 1) if elos else 0,
        }
