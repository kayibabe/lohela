"""
Stage 2 enrichment — spec §26 Stage 2.

Computes rolling form strings for each team from the matches already in the DB.
Called after fixture ingestion completes.
"""

import logging
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, func

from app.models import Competition, Match, MatchStatus, Odds, TeamStats
from app.config import cat_day_bounds_utc, cat_today, settings

logger = logging.getLogger(__name__)


class DataEnricher:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def enrich_form(self, match: Match) -> None:
        """Compute and store form strings for both teams in this match."""
        await self._set_form(match.home_team_id, match.id, match.kickoff_at, is_home=True)
        await self._set_form(match.away_team_id, match.id, match.kickoff_at, is_home=False)

    async def _set_form(self, team_id: int, match_id: int, before: datetime, is_home: bool) -> None:
        # Fetch last 10 completed matches for this team before this fixture's kickoff
        result = await self.db.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.FINISHED,
                Match.kickoff_at < before,
                or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
            )
            .order_by(Match.kickoff_at.desc())
            .limit(10)
        )
        past_matches = result.scalars().all()

        form_chars = []
        for m in past_matches:
            hg = m.home_goals or 0
            ag = m.away_goals or 0
            if m.home_team_id == team_id:
                form_chars.append("W" if hg > ag else "D" if hg == ag else "L")
            else:
                form_chars.append("W" if ag > hg else "D" if hg == ag else "L")

        # Most recent first → reverse for chronological
        form_str = "".join(reversed(form_chars))
        form_5 = form_str[-5:] if len(form_str) >= 5 else form_str
        form_10 = form_str[-10:] if len(form_str) >= 10 else form_str

        # Upsert TeamStats row for this match
        ts_result = await self.db.execute(
            select(TeamStats).where(
                and_(TeamStats.match_id == match_id, TeamStats.team_id == team_id)
            )
        )
        ts = ts_result.scalar_one_or_none()
        if not ts:
            ts = TeamStats(match_id=match_id, team_id=team_id, is_home=is_home)
            self.db.add(ts)
            await self.db.flush()
        ts.form_last_5 = form_5
        ts.form_last_10 = form_10
        await self.db.flush()

    async def _rescore_prematch(self, match: Match) -> dict:
        from app.services.data_validator import compute_prematch_quality_score

        history_counts: list[int] = []
        for team_id in (match.home_team_id, match.away_team_id):
            history_result = await self.db.execute(
                select(func.count(Match.id)).where(
                    Match.status == MatchStatus.FINISHED,
                    Match.kickoff_at < match.kickoff_at,
                    or_(Match.home_team_id == team_id, Match.away_team_id == team_id),
                )
            )
            history_counts.append(int(history_result.scalar_one()))

        stats_result = await self.db.execute(
            select(TeamStats).where(TeamStats.match_id == match.id)
        )
        stats = stats_result.scalars().all()
        teams_with_form = sum(1 for row in stats if row.form_last_5 and len(row.form_last_5) >= 3)

        odds_result = await self.db.execute(select(Odds).where(Odds.match_id == match.id))
        odds = odds_result.scalars().all()
        bookmakers = {row.bookmaker for row in odds}
        market_families = {_market_family(row.market) for row in odds}

        competition = await self.db.get(Competition, match.competition_id)
        reliability = (
            competition.reliability_score
            if competition and competition.active and not competition.blacklisted
            else 0.0
        )
        score, components = compute_prematch_quality_score(
            identity_complete=bool(match.home_team_id and match.away_team_id and match.kickoff_at),
            minimum_history_matches=min(history_counts, default=0),
            teams_with_form=teams_with_form,
            bookmaker_count=len(bookmakers),
            market_family_count=len(market_families),
            has_team_news=bool(match.team_news),
            league_reliability=reliability,
        )
        match.data_quality_score = score
        match.excluded_from_models = score < settings.min_data_quality_score
        logger.info(
            "Prematch quality %s: %.1f (%s)", match.id, score, components
        )
        return components

    async def enrich_all_scheduled(self, target_date: "date | None" = None) -> int:
        """Enrich form for all scheduled matches on target_date (default: today)."""
        target = target_date or cat_today()
        start_utc, end_utc = cat_day_bounds_utc(target)
        result = await self.db.execute(
            select(Match).where(
                Match.kickoff_at >= start_utc,
                Match.kickoff_at < end_utc,
            )
        )
        matches = result.scalars().all()
        for m in matches:
            await self.enrich_form(m)
            await self._rescore_prematch(m)
        logger.info("Form enrichment complete for %d matches on %s", len(matches), target)
        return len(matches)

    async def enrich_all_finished(self) -> int:
        """
        Enrich form for every finished match in chronological order.
        Run once after bulk ingestion to populate TeamStats.form_last_5/10
        for all historical fixtures so the model runner can read real form.
        """
        result = await self.db.execute(
            select(Match)
            .where(Match.status == MatchStatus.FINISHED)
            .order_by(Match.kickoff_at.asc())
        )
        matches = result.scalars().all()
        for m in matches:
            await self.enrich_form(m)
        await self.db.commit()
        logger.info("Form enrichment complete for %d finished matches", len(matches))
        return len(matches)


def _market_family(market: str) -> str:
    if market.startswith(("over_", "under_")):
        return "totals"
    if market.startswith("btts"):
        return "btts"
    if market.startswith("double_chance"):
        return "double_chance"
    if market.startswith("dnb"):
        return "draw_no_bet"
    return "match_result" if market in {"home_win", "draw", "away_win"} else market
