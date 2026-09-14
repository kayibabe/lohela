"""Q-score candidate selections for the daily research workspace."""

from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel

from app.database import get_db
from app.models import Prediction, Match, MatchStatus, Team, Competition, Odds, ModelRun, RunStatus, TicketType
from app.config import cat_day_bounds_utc, cat_today
from app.services.accumulator_builder import AccumulatorBuilder
from app.services.settlement import evaluate_selection

router = APIRouter(prefix="/selections", tags=["selections"])


def _finished_selection_result(match: Match, market: str) -> str | None:
    """Return an outcome only when the provider marks the match as finished."""
    if (
        match.status != MatchStatus.FINISHED
        or match.home_goals is None
        or match.away_goals is None
    ):
        return None
    return evaluate_selection(market, match.home_goals, match.away_goals).value


class SelectionOut(BaseModel):
    model_config = {"protected_namespaces": ()}
    prediction_id: int
    match_id: int
    home_team: str
    away_team: str
    competition: str
    kickoff_at: str
    market: str
    selection: str
    model_probability: float
    model_agreement: float
    edge: float | None
    expected_value: float | None
    q_score: float
    q_grade: str
    best_odds: float | None
    bookmaker_count: int | None
    avg_implied: float | None
    match_status: str
    home_goals: int | None
    away_goals: int | None
    live_phase: str | None = None
    elapsed_minutes: int | None = None
    result: str | None = None

    model_config = {"from_attributes": True}


class RejectedSelectionOut(BaseModel):
    prediction_id: int
    match_id: int
    home_team: str
    away_team: str
    competition: str
    kickoff_at: str
    market: str
    selection: str
    model_agreement: float
    q_score: float
    edge: float | None
    best_odds: float | None
    reason_codes: list[str]
    match_status: str
    home_goals: int | None
    away_goals: int | None
    result: str | None = None


class OddsQuoteOut(BaseModel):
    bookmaker: str
    decimal_odds: float
    implied_probability: float
    opening_odds: float | None
    movement: float | None
    fetched_at: str
    source_type: str
    is_fallback: bool
    is_best: bool


class TeamFormOut(BaseModel):
    team: str
    results: list[str]
    wins: int
    draws: int
    losses: int
    matches: int
    goals_for: int
    goals_against: int
    goal_difference: int
    avg_goals_for: float
    avg_goals_against: float
    points_per_game: float


class MatchContextOut(BaseModel):
    home: TeamFormOut
    away: TeamFormOut
    h2h: list[dict]


@router.get("/qualified", response_model=list[SelectionOut])
async def get_qualified_selections(
    date: Optional[date] = Query(default=None, description="Target date (default: today)"),
    min_qscore: float = Query(default=75.0, ge=0, le=100),
    market: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """
    Return selections that pass a minimum Q-Score threshold for a date.

    These are model candidates, not fully eligible accumulator legs. Ticket
    generation applies additional odds, edge, freshness, data-quality,
    calibration, diversification, and combination constraints.
    """
    target = date or cat_today()

    run_result = await db.execute(
        select(ModelRun)
        .where(ModelRun.target_date == target, ModelRun.status == RunStatus.COMPLETED)
        .order_by(ModelRun.id.desc())
        .limit(1)
    )
    model_run = run_result.scalar_one_or_none()
    if model_run is None:
        return []

    # A live refresh can make a fixture ineligible for a new model run after
    # kickoff. Keep its latest completed pre-kickoff prediction visible rather
    # than making it disappear from All Qualifying Matches.
    latest_prediction_run = (
        select(
            Prediction.match_id,
            Prediction.market,
            func.max(Prediction.model_run_id).label("latest_run_id"),
        )
        .join(ModelRun, Prediction.model_run_id == ModelRun.id)
        .where(ModelRun.target_date == target, ModelRun.status == RunStatus.COMPLETED)
        .group_by(Prediction.match_id, Prediction.market)
        .subquery()
    )
    pred_result = await db.execute(
        select(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .join(
            latest_prediction_run,
            (latest_prediction_run.c.match_id == Prediction.match_id)
            & (latest_prediction_run.c.market == Prediction.market)
            & (latest_prediction_run.c.latest_run_id == Prediction.model_run_id),
        )
        .where(
            Prediction.q_score >= min_qscore,
            Match.kickoff_at >= cat_day_bounds_utc(target)[0],
            Match.kickoff_at < cat_day_bounds_utc(target)[1],
            Match.excluded_from_models == False,
        )
        .order_by(Prediction.q_score.desc())
        .limit(100)
    )
    predictions = pred_result.scalars().all()

    output = []
    for pred in predictions:
        match = await db.get(Match, pred.match_id)
        if not match:
            continue
        home_team = await db.get(Team, match.home_team_id)
        away_team = await db.get(Team, match.away_team_id)
        competition = await db.get(Competition, match.competition_id)
        if not home_team or not away_team or not competition:
            continue
        if market and pred.market != market:
            continue

        # Load best odds for this market
        odds_q = await db.execute(
            select(Odds)
            .where(Odds.match_id == match.id, Odds.market == pred.market)
            .order_by(Odds.decimal_odds.desc())
            .limit(20)
        )
        all_odds = odds_q.scalars().all()
        best_odds_val = max((o.decimal_odds for o in all_odds), default=None)
        bk_count = len(set(o.bookmaker for o in all_odds))
        avg_implied = (
            sum(o.implied_probability for o in all_odds) / len(all_odds)
            if all_odds else None
        )

        output.append(SelectionOut(
            prediction_id=pred.id,
            match_id=pred.match_id,
            home_team=home_team.name,
            away_team=away_team.name,
            competition=competition.name,
            kickoff_at=match.kickoff_at.isoformat(),
            market=pred.market,
            selection=pred.selection,
            model_probability=pred.model_probability,
            model_agreement=pred.model_agreement,
            edge=pred.edge,
            expected_value=pred.expected_value,
            q_score=pred.q_score,
            q_grade=pred.q_grade.value,
            best_odds=best_odds_val,
            bookmaker_count=bk_count if bk_count > 0 else None,
            avg_implied=round(avg_implied, 4) if avg_implied else None,
            match_status=match.status.value,
            home_goals=match.home_goals,
            away_goals=match.away_goals,
            live_phase=match.live_phase,
            elapsed_minutes=match.elapsed_minutes,
            result=_finished_selection_result(match, pred.market),
        ))

    return output


@router.get("/rejected", response_model=list[RejectedSelectionOut])
async def get_rejected_selections(
    date: Optional[date] = Query(default=None, description="Target date (default: today)"),
    ticket_type: TicketType = Query(default=TicketType.SAFE),
    limit: int = Query(default=100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    target = date or cat_today()
    rows = await AccumulatorBuilder(db).rejected_selections(target, ticket_type)
    output = []
    for item in rows[:limit]:
        match = await db.get(Match, item["leg"].match_id)
        if match is None:
            continue
        output.append(RejectedSelectionOut(
            prediction_id=item["leg"].prediction_id,
            match_id=item["leg"].match_id,
            home_team=item["leg"].home_team,
            away_team=item["leg"].away_team,
            competition=item["leg"].competition,
            kickoff_at=item["leg"].kickoff_at.isoformat(),
            market=item["leg"].market,
            selection=item["leg"].selection,
            model_agreement=item["leg"].model_agreement,
            q_score=item["leg"].q_score,
            edge=item["leg"].edge,
            best_odds=item["leg"].best_odds or None,
            reason_codes=item["reason_codes"],
            match_status=match.status.value,
            home_goals=match.home_goals,
            away_goals=match.away_goals,
            result=_finished_selection_result(match, item["leg"].market),
        ))
    return output


@router.get("/match-context/{match_id}", response_model=MatchContextOut)
async def get_match_context(match_id: int, db: AsyncSession = Depends(get_db)):
    match = await db.get(Match, match_id)
    if not match:
        from fastapi import HTTPException
        raise HTTPException(404, "Match not found")
    async def form_for(team_id: int, name: str) -> TeamFormOut:
        query = select(Match).where(
            Match.status == "FINISHED", Match.home_goals.is_not(None), Match.away_goals.is_not(None),
            Match.kickoff_at < match.kickoff_at,
            (Match.home_team_id == team_id) | (Match.away_team_id == team_id),
        ).order_by(Match.kickoff_at.desc()).limit(5)
        games = list((await db.execute(query)).scalars().all())
        results = []
        for game in games:
            scored = game.home_goals if game.home_team_id == team_id else game.away_goals
            conceded = game.away_goals if game.home_team_id == team_id else game.home_goals
            results.append("W" if scored > conceded else "D" if scored == conceded else "L")
        goals_for = sum((game.home_goals if game.home_team_id == team_id else game.away_goals) or 0 for game in games)
        goals_against = sum((game.away_goals if game.home_team_id == team_id else game.home_goals) or 0 for game in games)
        return TeamFormOut(team=name, results=results, wins=results.count("W"), draws=results.count("D"), losses=results.count("L"), matches=len(results), goals_for=goals_for, goals_against=goals_against, goal_difference=goals_for-goals_against, avg_goals_for=round(goals_for/len(games), 2) if games else 0, avg_goals_against=round(goals_against/len(games), 2) if games else 0, points_per_game=round((results.count("W")*3+results.count("D"))/len(games), 2) if games else 0)
    home = await db.get(Team, match.home_team_id); away = await db.get(Team, match.away_team_id)
    h2h_query = select(Match).where(Match.status == "FINISHED", Match.home_goals.is_not(None), Match.away_goals.is_not(None), Match.kickoff_at < match.kickoff_at, ((Match.home_team_id == match.home_team_id) & (Match.away_team_id == match.away_team_id)) | ((Match.home_team_id == match.away_team_id) & (Match.away_team_id == match.home_team_id))).order_by(Match.kickoff_at.desc()).limit(5)
    h2h = [{"date": game.kickoff_at.date().isoformat(), "home_team": (await db.get(Team, game.home_team_id)).name, "away_team": (await db.get(Team, game.away_team_id)).name, "score": f"{game.home_goals} - {game.away_goals}"} for game in (await db.execute(h2h_query)).scalars().all()]
    return MatchContextOut(home=await form_for(match.home_team_id, home.name if home else "Home"), away=await form_for(match.away_team_id, away.name if away else "Away"), h2h=h2h)


@router.get("/{prediction_id}/odds", response_model=list[OddsQuoteOut])
async def get_selection_odds(
    prediction_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Return the bookmaker comparison captured for one model selection."""
    prediction = await db.get(Prediction, prediction_id)
    if prediction is None:
        return []

    result = await db.execute(
        select(Odds)
        .where(
            Odds.match_id == prediction.match_id,
            Odds.market == prediction.market,
            Odds.selection == prediction.selection,
        )
        .order_by(Odds.decimal_odds.desc(), Odds.bookmaker.asc())
        .limit(20)
    )
    quotes = result.scalars().all()
    best_odds = max((quote.decimal_odds for quote in quotes), default=None)
    return [
        OddsQuoteOut(
            bookmaker=quote.bookmaker,
            decimal_odds=quote.decimal_odds,
            implied_probability=quote.implied_probability,
            opening_odds=quote.opening_odds,
            movement=quote.movement,
            fetched_at=quote.fetched_at.isoformat(),
            source_type=quote.source_type,
            is_fallback=quote.is_fallback,
            is_best=quote.decimal_odds == best_odds,
        )
        for quote in quotes
    ]
