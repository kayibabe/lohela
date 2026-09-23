"""Daily, evidence-based review of losing paper-ticket selections."""

from datetime import date
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import cat_day_bounds_utc, cat_today
from app.models import AccumulatorTicket, Match, Prediction, SelectionResult, TicketSelection


async def review_losses(db: AsyncSession, target_date: date | None = None) -> dict:
    target = target_date or cat_today()
    start, end = cat_day_bounds_utc(target)
    result = await db.execute(
        select(TicketSelection, Match, Prediction, AccumulatorTicket.pricing)
        .join(Match, Match.id == TicketSelection.match_id)
        .join(Prediction, Prediction.id == TicketSelection.prediction_id)
        .join(AccumulatorTicket, AccumulatorTicket.id == TicketSelection.ticket_id)
        .where(
            TicketSelection.result == SelectionResult.LOST,
            Match.kickoff_at >= start,
            Match.kickoff_at < end,
        )
        .options(selectinload(TicketSelection.match).selectinload(Match.home_team),
                 selectinload(TicketSelection.match).selectinload(Match.away_team))
    )
    rows = result.all()
    analyses = []
    reason_counts: dict[str, int] = {}
    for selection, match, prediction, pricing in rows:
        reasons = []
        probability = selection.probability_snapshot
        market_priced = pricing == "market"
        if probability >= 0.70:
            reasons.append("high_confidence_miss")
        if selection.odds_snapshot >= 3.0:
            reasons.append("high_price_variance")
        # Q-score and edge are model judgements; a market-priced leg was
        # neither chosen nor priced on them, and its "edge" is just the margin.
        if not market_priced and selection.q_score_snapshot < 70:
            reasons.append("low_quality_signal")
        if not market_priced and selection.edge_snapshot is not None and selection.edge_snapshot < 0.05:
            reasons.append("thin_edge")
        if match.data_quality_score is not None and match.data_quality_score < 60:
            reasons.append("weak_match_data")
        if not reasons:
            reasons.append("normal_variance")
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
        analyses.append({
            "selection_id": selection.id,
            "match_id": match.id,
            "home_team": match.home_team.name if match.home_team else str(match.home_team_id),
            "away_team": match.away_team.name if match.away_team else str(match.away_team_id),
            "market": selection.market,
            "selection": selection.selection,
            "actual_score": f"{match.home_goals}-{match.away_goals}",
            "probability": probability,
            "pricing": pricing or "model",
            "odds": selection.odds_snapshot,
            "q_score": selection.q_score_snapshot,
            "edge": selection.edge_snapshot,
            "data_quality_score": match.data_quality_score,
            "reasons": reasons,
        })
    recommendations = _recommendations(reason_counts)
    return {"target_date": target.isoformat(), "loss_count": len(analyses), "reason_counts": reason_counts,
            "recommendations": recommendations, "losses": analyses,
            "learning_note": "Use this as diagnostic evidence; do not automatically alter model weights from one day's sample."}


def _recommendations(counts: dict[str, int]) -> list[str]:
    advice = []
    if counts.get("high_confidence_miss"):
        advice.append("Audit calibration for high-probability selections before increasing confidence thresholds.")
    if counts.get("high_price_variance"):
        advice.append("Review high-odds selections separately; their variance can dominate daily results.")
    if counts.get("low_quality_signal") or counts.get("weak_match_data"):
        advice.append("Raise data-quality requirements or exclude matches with incomplete inputs.")
    if counts.get("thin_edge"):
        advice.append("Require a wider edge buffer to avoid marginal value selections.")
    if not advice:
        advice.append("No systematic cause is established; treat the losses as a small-sample variance signal.")
    return advice
