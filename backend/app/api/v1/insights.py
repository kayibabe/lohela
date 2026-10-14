"""Read-only league and team trends backed by completed match results."""

from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.security import require_pro_access
from app.database import get_db
from app.models import Competition, Match, MatchStatus, Team, User


router = APIRouter(prefix="/insights", tags=["insights"])


class CompetitionOption(BaseModel):
    id: int
    name: str
    country: str
    seasons: list[str]
    completed_matches: int


class TeamTrend(BaseModel):
    team_id: int
    team: str
    played: int
    won: int
    drawn: int
    lost: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    avg_xg: float | None
    recent_form: str


class LeagueSummary(BaseModel):
    played: int
    average_goals: float | None
    home_win_pct: float | None
    draw_pct: float | None
    away_win_pct: float | None
    btts_pct: float | None
    over_2_5_pct: float | None
    xg_coverage_pct: float | None


class InsightsOut(BaseModel):
    competitions: list[CompetitionOption]
    competition_id: int | None
    season: str | None
    summary: LeagueSummary | None
    teams: list[TeamTrend]


@router.get("", response_model=InsightsOut)
async def get_league_insights(
    competition_id: int | None = None,
    season: str | None = None,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_pro_access),
):
    competition_rows = (await db.execute(
        select(
            Competition.id,
            Competition.name,
            Competition.country,
            Match.season,
            func.count(Match.id),
        )
        .join(Match, Match.competition_id == Competition.id)
        .where(
            Match.status == MatchStatus.FINISHED,
            Match.home_goals.is_not(None),
            Match.away_goals.is_not(None),
        )
        .group_by(Competition.id, Competition.name, Competition.country, Match.season)
        .order_by(Competition.name, Match.season.desc())
    )).all()

    grouped: dict[int, dict] = {}
    for comp_id, name, country, comp_season, count in competition_rows:
        option = grouped.setdefault(comp_id, {
            "id": comp_id, "name": name, "country": country, "seasons": [], "completed_matches": 0,
        })
        option["seasons"].append(comp_season)
        option["completed_matches"] += count
    competitions = [CompetitionOption(**item) for item in grouped.values()]
    if competition_id is None:
        return InsightsOut(competitions=competitions, competition_id=None, season=None, summary=None, teams=[])

    option = next((item for item in competitions if item.id == competition_id), None)
    if option is None:
        raise HTTPException(status_code=404, detail="Competition has no completed match data")
    selected_season = season or option.seasons[0]
    if selected_season not in option.seasons:
        raise HTTPException(status_code=404, detail="Season has no completed match data")

    match_rows = (await db.execute(
        select(
            Match.home_team_id,
            Team.name,
            Match.away_team_id,
            Match.home_goals,
            Match.away_goals,
            Match.home_xg,
            Match.away_xg,
        )
        .join(Team, Team.id == Match.home_team_id)
        .where(
            Match.competition_id == competition_id,
            Match.season == selected_season,
            Match.status == MatchStatus.FINISHED,
            Match.home_goals.is_not(None),
            Match.away_goals.is_not(None),
        )
        .order_by(Match.kickoff_at.desc())
    )).all()
    if not match_rows:
        raise HTTPException(status_code=404, detail="Season has no completed match data")

    team_rows: dict[int, dict] = defaultdict(lambda: {
        "played": 0, "won": 0, "drawn": 0, "lost": 0, "goals_for": 0,
        "goals_against": 0, "points": 0, "xg_total": 0.0, "xg_matches": 0, "recent_form": [],
    })
    home_wins = draws = away_wins = btts = over_2_5 = 0
    xg_available = 0
    total_goals = 0
    for home_id, home_name, away_id, home_goals, away_goals, home_xg, away_xg in match_rows:
        total_goals += home_goals + away_goals
        home_wins += int(home_goals > away_goals)
        away_wins += int(home_goals < away_goals)
        draws += int(home_goals == away_goals)
        btts += int(home_goals > 0 and away_goals > 0)
        over_2_5 += int(home_goals + away_goals > 2)
        for team_id, name, scored, conceded, xg in (
            (home_id, home_name, home_goals, away_goals, home_xg),
            (away_id, str(away_id), away_goals, home_goals, away_xg),
        ):
            team = team_rows[team_id]
            team["team"] = name
            team["played"] += 1
            team["goals_for"] += scored
            team["goals_against"] += conceded
            if scored > conceded:
                team["won"] += 1
                team["points"] += 3
            elif scored == conceded:
                team["drawn"] += 1
                team["points"] += 1
            else:
                team["lost"] += 1
            if xg is not None:
                team["xg_total"] += xg
                team["xg_matches"] += 1
                xg_available += 1
            if len(team["recent_form"]) < 5:
                team["recent_form"].append("W" if scored > conceded else "D" if scored == conceded else "L")

    # Resolve away-team labels with one small lookup, avoiding assumptions about relationship loading.
    team_ids = list(team_rows)
    team_names = dict((await db.execute(select(Team.id, Team.name).where(Team.id.in_(team_ids)))).all())
    teams = [TeamTrend(
        team_id=team_id,
        team=team_names.get(team_id, str(team_id)),
        played=stats["played"], won=stats["won"], drawn=stats["drawn"], lost=stats["lost"],
        goals_for=stats["goals_for"], goals_against=stats["goals_against"],
        goal_difference=stats["goals_for"] - stats["goals_against"], points=stats["points"],
        avg_xg=(stats["xg_total"] / stats["xg_matches"] if stats["xg_matches"] else None),
        recent_form="".join(stats["recent_form"]),
    ) for team_id, stats in team_rows.items()]
    teams.sort(key=lambda item: (-item.points, -item.goal_difference, -item.goals_for, item.team.casefold()))
    total = len(match_rows)
    summary = LeagueSummary(
        played=total,
        average_goals=total_goals / total,
        home_win_pct=home_wins * 100 / total,
        draw_pct=draws * 100 / total,
        away_win_pct=away_wins * 100 / total,
        btts_pct=btts * 100 / total,
        over_2_5_pct=over_2_5 * 100 / total,
        xg_coverage_pct=xg_available * 100 / (total * 2),
    )
    return InsightsOut(
        competitions=competitions,
        competition_id=competition_id,
        season=selected_season,
        summary=summary,
        teams=teams,
    )
