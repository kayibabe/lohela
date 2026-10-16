"""Daily market ROI gate using published picks and point-in-time prices."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import CAT, cat_day_bounds_utc, settings
from app.models.match import Match
from app.models.prediction import Prediction
from app.models.research import AccumulatorTicket, SelectionResult, TicketSelection, TicketStatus, TicketType
from app.services.performance import _as_utc, _has_pre_kickoff_information
from app.services.settlement import evaluate_selection


ROI_THRESHOLD = 0.20


def _fresh_pre_kickoff_prediction(
    prediction: Prediction, now: datetime, *, require_current_freshness: bool = False
) -> bool:
    match = prediction.match
    provenance = prediction.source_odds_provenance or {}
    if (
        match is None
        or prediction.source_odds_id is None
        or not provenance.get("bookmaker")
        or provenance.get("is_fallback") is True
    ):
        return False
    if prediction.source_decimal_odds is None or prediction.source_decimal_odds <= 1:
        return False
    if prediction.source_odds_at is None or not _has_pre_kickoff_information(prediction):
        return False

    quote_at = _as_utc(prediction.source_odds_at)
    kickoff_at = _as_utc(match.kickoff_at)
    information_at = _as_utc(prediction.decision_at or prediction.as_of_at or prediction.created_at)
    max_age = timedelta(hours=settings.max_selection_odds_age_hours)
    quote_age_at_decision = information_at - quote_at
    valid = (
        quote_at < kickoff_at
        and information_at < kickoff_at
        and quote_at <= information_at
        and timedelta(0) <= quote_age_at_decision <= max_age
    )
    if require_current_freshness:
        valid = valid and timedelta(0) <= _as_utc(now) - quote_at <= max_age
    return valid


async def daily_market_roi(db: AsyncSession, target_date: date | None = None) -> dict:
    """Evaluate prior published picks and find today's matching candidates.

    Each historical fixture/market/selection is counted once from its earliest
    valid public market-priced ticket appearance. A flat unit stake at the
    captured decimal price is used to calculate ROI. Today's rows are a
    research shortlist only.
    """
    now = datetime.now(timezone.utc)
    target_date = target_date or now.astimezone(CAT).date()
    day_start, day_end = cat_day_bounds_utc(target_date)

    history_result = await db.execute(
        select(AccumulatorTicket, TicketSelection, Match, Prediction)
        .join(TicketSelection, TicketSelection.ticket_id == AccumulatorTicket.id)
        .join(Match, Match.id == TicketSelection.match_id)
        .join(Prediction, Prediction.id == TicketSelection.prediction_id)
        .where(
            AccumulatorTicket.target_date < target_date,
            AccumulatorTicket.pricing == "market",
            AccumulatorTicket.ticket_type.in_((TicketType.SAFE, TicketType.BALANCED, TicketType.HIGH_ODDS)),
            AccumulatorTicket.status.in_((TicketStatus.PUBLISHED, TicketStatus.SETTLED)),
            Match.kickoff_at < day_start,
        )
        .order_by(AccumulatorTicket.published_at.asc(), TicketSelection.id.asc())
    )

    # Match the published market ROI evidence table: keep the earliest valid
    # public market-priced appearance for each unique fixture/market/selection.
    unique_history: dict[tuple[int, str, str], tuple[TicketSelection, Match, SelectionResult]] = {}
    unsupported = 0
    for ticket, selection, match, prediction in history_result.all():
        if match.home_goals is None or match.away_goals is None:
            continue
        kickoff_at = _as_utc(match.kickoff_at)
        published_at = _as_utc(ticket.published_at)
        decision_at = _as_utc(selection.decision_at or ticket.published_at)
        quote_at_value = selection.source_odds_at
        quote_id = selection.source_odds_id_snapshot
        bookmaker = selection.source_bookmaker_snapshot
        information_at = prediction.as_of_at or prediction.created_at
        if (
            not quote_at_value
            or quote_id is None
            or not bookmaker
            or selection.odds_snapshot <= 1
            or not information_at
            or published_at >= kickoff_at
            or decision_at >= kickoff_at
            or decision_at > published_at
            or _as_utc(information_at) >= kickoff_at
            or _as_utc(information_at) > decision_at
        ):
            continue
        quote_at = _as_utc(quote_at_value)
        quote_age = decision_at - quote_at
        if (
            quote_at >= kickoff_at
            or quote_at > decision_at
            or quote_age < timedelta(0)
            or quote_age > timedelta(hours=settings.max_selection_odds_age_hours)
        ):
            continue
        key = (selection.match_id, selection.market, selection.selection)
        if key in unique_history:
            continue
        try:
            outcome = evaluate_selection(selection.selection, match.home_goals, match.away_goals)
        except ValueError:
            try:
                outcome = evaluate_selection(selection.market, match.home_goals, match.away_goals)
            except ValueError:
                unsupported += 1
                continue
        if outcome in (SelectionResult.WON, SelectionResult.LOST):
            unique_history[key] = (selection, match, outcome)

    cohorts: dict[str, list[tuple[TicketSelection, Match, SelectionResult]]] = defaultdict(list)
    fixture_outcomes: dict[int, list[SelectionResult]] = defaultdict(list)
    for (match_id, market, _selection), (ticket_selection, match, outcome) in unique_history.items():
        cohorts[market].append((ticket_selection, match, outcome))
        fixture_outcomes[match_id].append(outcome)

    market_rows = []
    for market, items in cohorts.items():
        wins = sum(result == SelectionResult.WON for _, _, result in items)
        losses = len(items) - wins
        pnl = sum(
            selection.odds_snapshot - 1
            if result == SelectionResult.WON
            else -1
            for selection, _match, result in items
        )
        roi = pnl / len(items)
        market_rows.append({
            "market": market,
            "sample_size": len(items),
            "wins": wins,
            "losses": losses,
            "hit_rate": wins / len(items),
            "flat_stake_pnl": pnl,
            "roi": roi,
            "average_odds": sum(selection.odds_snapshot for selection, _match, _result in items) / len(items),
            "qualifies": roi >= ROI_THRESHOLD,
            "evidence_label": "very small sample" if len(items) < 10 else "small sample" if len(items) < 30 else "larger sample",
        })
    market_rows.sort(key=lambda row: (-row["roi"], -row["sample_size"], row["market"]))
    qualifying_markets = {row["market"] for row in market_rows if row["qualifies"]}
    fixture_summary = {
        "unique_fixtures": len(fixture_outcomes),
        "all_won": sum(all(result == SelectionResult.WON for result in outcomes) for outcomes in fixture_outcomes.values()),
        "all_lost": sum(all(result == SelectionResult.LOST for result in outcomes) for outcomes in fixture_outcomes.values()),
        "mixed": sum(
            any(result == SelectionResult.WON for result in outcomes)
            and any(result == SelectionResult.LOST for result in outcomes)
            for outcomes in fixture_outcomes.values()
        ),
    }

    today_result = await db.execute(
        select(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .options(
            joinedload(Prediction.match)
            .joinedload(Match.home_team),
            joinedload(Prediction.match)
            .joinedload(Match.away_team),
            joinedload(Prediction.match)
            .joinedload(Match.competition),
        )
        .where(Match.kickoff_at >= day_start, Match.kickoff_at < day_end)
        .order_by(Prediction.created_at.desc(), Prediction.id.desc())
    )

    today_latest: dict[tuple[int, str, str], Prediction] = {}
    for prediction in today_result.scalars().all():
        match = prediction.match
        status = getattr(match.status, "value", match.status) if match else None
        if match is None or status not in {"scheduled", "live", "finished", "postponed", "cancelled"}:
            continue
        key = (prediction.match_id, prediction.market, prediction.selection)
        if key in today_latest or prediction.market not in qualifying_markets:
            continue
        if prediction.recommendation_status not in {"BET", "WATCH"}:
            continue
        # The prediction's quote was freshness-checked at its captured
        # decision time above. Do not discard the whole daily shortlist merely
        # because the saved quote has aged since that decision; expose its age
        # below so the table can distinguish an available research row from a
        # live executable price.
        if not _fresh_pre_kickoff_prediction(prediction, now):
            continue
        today_latest[key] = prediction

    roi_by_market = {row["market"]: row for row in market_rows}
    candidates = []
    for prediction in today_latest.values():
        match = prediction.match
        quote_at = _as_utc(prediction.source_odds_at)
        match_status = getattr(match.status, "value", match.status)
        result = None
        if match_status in {"postponed", "cancelled"}:
            result = SelectionResult.VOID.value
        elif match_status == "finished" and match.home_goals is not None and match.away_goals is not None:
            try:
                result = evaluate_selection(prediction.selection, match.home_goals, match.away_goals).value
            except ValueError:
                try:
                    result = evaluate_selection(prediction.market, match.home_goals, match.away_goals).value
                except ValueError:
                    result = None
        candidates.append({
            "prediction_id": prediction.id,
            "match_id": match.id,
            "home_team": match.home_team.name if match.home_team else "Home",
            "away_team": match.away_team.name if match.away_team else "Away",
            "competition": match.competition.name if match.competition else "",
            "kickoff_at": _as_utc(match.kickoff_at).isoformat(),
            "match_status": match_status,
            "home_goals": match.home_goals,
            "away_goals": match.away_goals,
            "result": result,
            "market": prediction.market,
            "selection": prediction.selection,
            "odds": prediction.source_decimal_odds,
            "model_probability": prediction.model_probability,
            "q_score": prediction.q_score,
            "q_grade": prediction.q_grade.value if hasattr(prediction.q_grade, "value") else str(prediction.q_grade),
            "recommendation_status": prediction.recommendation_status,
            "market_roi": roi_by_market[prediction.market]["roi"],
            "market_sample_size": roi_by_market[prediction.market]["sample_size"],
            "bookmaker": (prediction.source_odds_provenance or {}).get("bookmaker"),
            "quote_at": quote_at.isoformat(),
            "quote_age_minutes": max(0.0, (now - quote_at).total_seconds() / 60),
        })
    candidates.sort(key=lambda row: (row["kickoff_at"], -row["market_roi"], row["match_id"], row["market"], row["selection"]))

    return {
        "target_date": target_date.isoformat(),
        "refreshed_at": now.isoformat(),
        "roi_threshold": ROI_THRESHOLD,
        "max_quote_age_hours": settings.max_selection_odds_age_hours,
        "historical_through": (target_date - timedelta(days=1)).isoformat(),
        "historical_unique_selections": sum(row["sample_size"] for row in market_rows),
        "historical_fixture_summary": fixture_summary,
        "historical_markets": market_rows,
        "qualifying_markets": sorted(qualifying_markets),
        "candidate_count": len(candidates),
        "candidate_fixture_count": len({row["match_id"] for row in candidates}),
        "candidates": candidates,
        "unsupported_outcomes_excluded": unsupported,
        "method": "Flat one-unit ROI from earliest valid, published public market-priced ticket pick per unique fixture-market-selection, with a strictly pre-kickoff decision and quote, source quote ID, bookmaker provenance, and quote age within the configured limit. Today's candidates require the same market/selection to have ROI at least 20%, a pre-kickoff quote that was fresh at its captured decision time, and existing BET/WATCH recommendation status. Quote age at refresh is exposed separately.",
        "research_only": True,
    }
