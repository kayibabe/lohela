"""Research-ledger performance calculations using latest settlement versions."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import AccumulatorTicket, SelectionResult, TicketSelection, TicketStatus, TicketType, Match, Prediction
from app.services.settlement import evaluate_selection
from app.config import CURRENT_MODEL_VERSION


def _as_utc(value: datetime) -> datetime:
    """Normalize database timestamps before point-in-time comparisons."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _has_pre_kickoff_information(prediction: Prediction) -> bool:
    """Return whether the prediction itself was available before kickoff."""
    match = prediction.match
    information_at = prediction.as_of_at or prediction.created_at
    return bool(
        match is not None
        and match.kickoff_at is not None
        and information_at is not None
        and _as_utc(information_at) < _as_utc(match.kickoff_at)
    )


def _has_pre_kickoff_quote(prediction: Prediction) -> bool:
    """Return whether the entry quote was captured before kickoff."""
    match = prediction.match
    return bool(
        match is not None
        and match.kickoff_at is not None
        and prediction.source_odds_at is not None
        and _as_utc(prediction.source_odds_at) <= _as_utc(match.kickoff_at)
    )


async def performance_summary(db: AsyncSession, date_from: date | None = None, date_to: date | None = None, model_version: str | None = None) -> dict:
    result = await db.execute(
        select(AccumulatorTicket)
        .options(
            selectinload(AccumulatorTicket.results),
            selectinload(AccumulatorTicket.selections).selectinload(TicketSelection.match),
        )
        .order_by(AccumulatorTicket.target_date, AccumulatorTicket.id)
    )
    all_tickets = result.scalars().all()
    # A rerun creates a new append-only version for the same daily ticket.
    # Keep those rows available for audit/history, but count only the latest
    # version for each date and ticket type in performance reporting.
    latest_by_day_and_type: dict[tuple[date, object], AccumulatorTicket] = {}
    for ticket in all_tickets:
        if date_from and ticket.target_date < date_from or date_to and ticket.target_date > date_to:
            continue
        if model_version and ticket.model_version != model_version:
            continue
        key = (ticket.target_date, ticket.ticket_type)
        previous = latest_by_day_and_type.get(key)
        if previous is None or (ticket.version, ticket.id) > (previous.version, previous.id):
            latest_by_day_and_type[key] = ticket
    tickets = list(latest_by_day_and_type.values())
    settled: list[tuple[AccumulatorTicket, object]] = []
    for ticket in tickets:
        latest = max(ticket.results, key=lambda item: item.version, default=None)
        if latest:
            settled.append((ticket, latest))
    stakes = sum(item.stake for _, item in settled)
    returns = sum(item.return_amount for _, item in settled)
    pnl = returns - stakes
    wins = sum(item.result == SelectionResult.WON for _, item in settled)
    losses = sum(item.result == SelectionResult.LOST for _, item in settled)
    hit_rate = wins / len(settled) if settled else 0.0
    low, high = _wilson_interval(wins, len(settled))

    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for _, item in settled:
        cumulative += item.profit_loss
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)

    outcomes: list[tuple[float, float]] = []
    for ticket, _ in settled:
        for selection in ticket.selections:
            if selection.result in (SelectionResult.WON, SelectionResult.LOST):
                outcomes.append((selection.probability_snapshot, 1.0 if selection.result == SelectionResult.WON else 0.0))
    brier = sum((probability - outcome) ** 2 for probability, outcome in outcomes) / len(outcomes) if outcomes else None
    calibration_error = _calibration_error(outcomes)

    by_type: dict[str, dict] = {}
    for ticket, item in settled:
        bucket = by_type.setdefault(ticket.ticket_type.value, {"tickets": 0, "wins": 0, "stake": 0.0, "return": 0.0})
        bucket["tickets"] += 1
        bucket["wins"] += int(item.result == SelectionResult.WON)
        bucket["stake"] += item.stake
        bucket["return"] += item.return_amount
    breakdown = []
    for ticket_type, bucket in by_type.items():
        type_pnl = bucket["return"] - bucket["stake"]
        breakdown.append({
            "ticket_type": ticket_type,
            "tickets": bucket["tickets"],
            "wins": bucket["wins"],
            "stake": bucket["stake"],
            "return": bucket["return"],
            "hit_rate": bucket["wins"] / bucket["tickets"],
            "roi": type_pnl / bucket["stake"] if bucket["stake"] else 0.0,
            "profit_loss": type_pnl,
        })

    official_tickets = [ticket for ticket in tickets if ticket.ticket_type != TicketType.BEST_VALUE]
    official_settled_ids = {ticket.id for ticket, _ in settled if ticket.ticket_type != TicketType.BEST_VALUE}
    overlap = []
    tickets_by_date: dict[date, list[AccumulatorTicket]] = {}
    for ticket in official_tickets:
        tickets_by_date.setdefault(ticket.target_date, []).append(ticket)
    for target_date, day_tickets in sorted(tickets_by_date.items()):
        for index, left in enumerate(day_tickets):
            for right in day_tickets[index + 1:]:
                left_matches = {selection.match_id for selection in left.selections}
                right_matches = {selection.match_id for selection in right.selections}
                shared = len(left_matches & right_matches)
                overlap.append({
                    "target_date": target_date.isoformat(),
                    "left_ticket_type": left.ticket_type.value,
                    "right_ticket_type": right.ticket_type.value,
                    "shared_matches": shared,
                    "left_legs": len(left_matches),
                    "right_legs": len(right_matches),
                    "overlap_ratio_of_smaller": round(
                        shared / min(len(left_matches), len(right_matches)), 4
                    ) if left_matches and right_matches else 0.0,
                })
    official_selections: dict[tuple[date, int, str, str], TicketSelection] = {}
    for ticket in official_tickets:
        for selection in ticket.selections:
            official_selections.setdefault(
                (ticket.target_date, selection.match_id, selection.market, selection.selection),
                selection,
            )
    priced_selections = sum(
        selection.odds_snapshot is not None and selection.odds_snapshot > 1
        for selection in official_selections.values()
    )
    pre_kickoff_odds = sum(
        selection.source_odds_at is not None
        and selection.match is not None
        and selection.source_odds_at <= selection.match.kickoff_at
        for selection in official_selections.values()
    )

    return {
        "current_model_version": CURRENT_MODEL_VERSION,
        "published_tickets": len(tickets),
        "settled_tickets": len(settled),
        "wins": wins,
        "losses": losses,
        "hit_rate": hit_rate,
        "hit_rate_confidence_interval_95": [low, high],
        "stake": stakes,
        "return": returns,
        "profit_loss": pnl,
        "roi": pnl / stakes if stakes else 0.0,
        "yield": pnl / stakes if stakes else 0.0,
        "max_drawdown_units": max_drawdown,
        "brier_score": brier,
        "calibration_error": calibration_error,
        "selection_sample_size": len(outcomes),
        "by_ticket_type": breakdown,
        "ticket_overlap": overlap,
        "evidence_gate": {
            "official_latest_cohorts": len(official_tickets),
            "official_settled_cohorts": len(official_settled_ids),
            "published_days": len({ticket.target_date for ticket in official_tickets}),
            "settled_days": len({ticket.target_date for ticket in official_tickets if ticket.id in official_settled_ids}),
            "unique_official_selections": len(official_selections),
            "priced_selections": priced_selections,
            "pre_kickoff_odds_selections": pre_kickoff_odds,
        },
    }


async def individual_selection_summary(db: AsyncSession, stake: float = 1.0, date_from: date | None = None, date_to: date | None = None, market: str | None = None, competition: str | None = None, model_version: str | None = None) -> dict:
    """Evaluate each unique published match/market pick as a flat individual bet.

    A pick repeated across ticket tiers is counted once. This is a what-if view;
    it never changes the immutable ticket results.
    """
    result = await db.execute(
        select(AccumulatorTicket)
        .where(AccumulatorTicket.status.in_([TicketStatus.PUBLISHED, TicketStatus.SETTLED, TicketStatus.VOID]))
        .options(selectinload(AccumulatorTicket.selections).selectinload(TicketSelection.match).selectinload(Match.competition))
        .order_by(AccumulatorTicket.target_date, AccumulatorTicket.id)
    )
    unique: dict[tuple, TicketSelection] = {}
    for ticket in result.scalars().all():
        if model_version and ticket.model_version != model_version:
            continue
        for selection in ticket.selections:
            key = (ticket.target_date.isoformat(), selection.match_id, selection.market, selection.selection)
            unique.setdefault(key, selection)
    if date_from or date_to or market or competition:
        unique = {key: selection for key, selection in unique.items() if
                  (not date_from or selection.match.kickoff_at.date() >= date_from) and
                  (not date_to or selection.match.kickoff_at.date() <= date_to) and
                  (not market or selection.market == market) and
                  (not competition or (selection.match.competition.name if selection.match.competition else str(selection.match.competition_id)) == competition)}

    settled = [s for s in unique.values() if s.result in (SelectionResult.WON, SelectionResult.LOST, SelectionResult.VOID)]
    def row(items: list[TicketSelection], label: str) -> dict:
        wins = sum(s.result == SelectionResult.WON for s in items)
        losses = sum(s.result == SelectionResult.LOST for s in items)
        voids = sum(s.result == SelectionResult.VOID for s in items)
        effective = [s for s in items if s.result != SelectionResult.VOID]
        staked = len(effective) * stake
        returned = sum(stake * s.odds_snapshot for s in effective if s.result == SelectionResult.WON)
        pnl = returned - staked
        return {"label": label, "selections": len(items), "settled": len(effective), "wins": wins,
                "losses": losses, "voids": voids, "staked": round(staked, 2), "returned": round(returned, 2),
                "pnl": round(pnl, 2), "roi": round(pnl / staked, 4) if staked else 0.0,
                "hit_rate": round(wins / len(effective), 4) if effective else 0.0}

    by_date = {}
    by_month = {}
    by_year = {}
    by_market = {}
    by_competition = {}
    for selection in settled:
        match_date = selection.match.kickoff_at.date().isoformat()
        month = match_date[:7]
        by_date.setdefault(match_date, []).append(selection)
        by_month.setdefault(month, []).append(selection)
        by_year.setdefault(match_date[:4], []).append(selection)
        by_market.setdefault(selection.market, []).append(selection)
        competition = selection.match.competition.name if selection.match.competition else str(selection.match.competition_id)
        by_competition.setdefault(competition, []).append(selection)
    return {"stake": stake, "unique_selections": len(unique), "settled_selections": len(settled),
            "overall": row(settled, "All dates"),
            "by_date": [row(by_date[k], k) for k in sorted(by_date, reverse=True)],
            "by_month": [row(by_month[k], k) for k in sorted(by_month, reverse=True)],
            "by_year": [row(by_year[k], k) for k in sorted(by_year, reverse=True)],
            "by_market": [row(by_market[k], k) for k in sorted(by_market)],
            "by_competition": [row(by_competition[k], k) for k in sorted(by_competition)]}

async def all_market_research_summary(db: AsyncSession, stake: float = 1.0, date_from: date | None = None, date_to: date | None = None, model_version: str | None = None) -> dict:
    """Research every latest prediction with a frozen pre-kickoff price.

    This is deliberately separate from published tickets and user bets. Rows
    without a captured price or supported settlement outcome are excluded.
    """
    result = await db.execute(select(Prediction).join(Match).options(
        selectinload(Prediction.match).selectinload(Match.competition)
    ).order_by(Prediction.created_at.desc()))
    latest: dict[tuple[int, str, str], Prediction] = {}
    excluded_missing_odds = 0
    excluded_post_kickoff_information = 0
    excluded_post_kickoff_odds = 0
    for prediction in result.scalars().all():
        if model_version and prediction.model_version != model_version: continue
        match_date = prediction.match.kickoff_at.date()
        if date_from and match_date < date_from or date_to and match_date > date_to: continue
        key = (prediction.match_id, prediction.market, prediction.selection)
        if key in latest: continue
        if not _has_pre_kickoff_information(prediction):
            excluded_post_kickoff_information += 1
            continue
        if not _has_pre_kickoff_quote(prediction):
            excluded_post_kickoff_odds += 1
            continue
        if prediction.source_decimal_odds is None or prediction.source_decimal_odds <= 1:
            excluded_missing_odds += 1; continue
        latest[key] = prediction

    rows = []
    unsupported = 0
    for prediction in latest.values():
        match = prediction.match
        if match.home_goals is None or match.away_goals is None: continue
        try: outcome = evaluate_selection(prediction.selection, match.home_goals, match.away_goals)
        except ValueError:
            try: outcome = evaluate_selection(prediction.market, match.home_goals, match.away_goals)
            except ValueError: unsupported += 1; continue
        rows.append((prediction, outcome))

    def aggregate(items):
        wins = sum(outcome == SelectionResult.WON for _, outcome in items)
        losses = sum(outcome == SelectionResult.LOST for _, outcome in items)
        voids = sum(outcome == SelectionResult.VOID for _, outcome in items)
        effective = [(p, o) for p, o in items if o != SelectionResult.VOID]
        staked = len(effective) * stake
        returned = sum(stake * p.source_decimal_odds for p, o in effective if o == SelectionResult.WON)
        pnl = returned - staked
        return {"selections": len(items), "settled": len(effective), "wins": wins, "losses": losses, "voids": voids,
                "staked": round(staked, 2), "returned": round(returned, 2), "pnl": round(pnl, 2),
                "roi": round(pnl / staked, 4) if staked else 0.0, "hit_rate": round(wins / len(effective), 4) if effective else 0.0}
    groups = {"by_date": {}, "by_month": {}, "by_year": {}, "by_market": {}, "by_competition": {}}
    for prediction, outcome in rows:
        d = prediction.match.kickoff_at.date().isoformat(); comp = prediction.match.competition.name if prediction.match.competition else str(prediction.match.competition_id)
        for key, value in (("by_date", d), ("by_month", d[:7]), ("by_year", d[:4]), ("by_market", prediction.market), ("by_competition", comp)):
            groups[key].setdefault(value, []).append((prediction, outcome))
    return {"stake": stake, "eligible_predictions": len(latest), "settled_predictions": len(rows),
            "excluded_missing_odds": excluded_missing_odds,
            "excluded_post_kickoff_information": excluded_post_kickoff_information,
            "excluded_post_kickoff_odds": excluded_post_kickoff_odds,
            "unsupported_markets": unsupported,
            "overall": aggregate(rows), **{key: [{"label": label, **aggregate(items)} for label, items in sorted(value.items(), reverse=True)] for key, value in groups.items()}}


async def totals_calibration_review(
    db: AsyncSession,
    date_from: date | None = None,
    date_to: date | None = None,
    model_version: str | None = None,
) -> dict:
    """Review settled published totals by line, competition, probability band, and type.

    Only the latest unique published match/market/selection is counted. Rows
    without a settled outcome or probability are excluded from calibration
    metrics rather than being guessed or regenerated.
    """
    result = await db.execute(
        select(AccumulatorTicket)
        .where(AccumulatorTicket.status.in_([TicketStatus.PUBLISHED, TicketStatus.SETTLED, TicketStatus.VOID]))
        .options(
            selectinload(AccumulatorTicket.selections)
            .selectinload(TicketSelection.match)
            .selectinload(Match.competition),
        )
        .order_by(AccumulatorTicket.target_date.desc(), AccumulatorTicket.version.desc(), AccumulatorTicket.id.desc())
    )
    unique: dict[tuple[date, int, str, str], TicketSelection] = {}
    for ticket in result.scalars().all():
        if model_version and ticket.model_version != model_version:
            continue
        for selection in ticket.selections:
            match = selection.match
            if not match or not selection.market.startswith(("under_", "over_")):
                continue
            match_date = match.kickoff_at.date()
            if date_from and match_date < date_from or date_to and match_date > date_to:
                continue
            unique.setdefault((ticket.target_date, selection.match_id, selection.market, selection.selection), selection)

    def competition_type(name: str) -> str:
        lowered = name.lower()
        cup_terms = ("cup", "copa", "coppa", "dfb", "fa cup", "super cup", "trophy")
        return "cup" if any(term in lowered for term in cup_terms) else "league_or_other"

    def probability_band(probability: float) -> str:
        lower = int(probability * 10) * 10
        return f"{lower / 100:.2f}-{min(1.0, (lower + 10) / 100):.2f}"

    groups: dict[tuple[str, str, str, str], list[TicketSelection]] = {}
    excluded = {"unsettled": 0, "missing_probability": 0}
    for selection in unique.values():
        if selection.result not in (SelectionResult.WON, SelectionResult.LOST):
            excluded["unsettled"] += 1
            continue
        if selection.probability_snapshot is None:
            excluded["missing_probability"] += 1
            continue
        match = selection.match
        competition = match.competition.name if match.competition else str(match.competition_id)
        line = selection.market.removeprefix("under_").removeprefix("over_")
        side = "under" if selection.market.startswith("under_") else "over"
        key = (competition, f"{side} {line}", probability_band(selection.probability_snapshot), competition_type(competition))
        groups.setdefault(key, []).append(selection)

    def aggregate(items: list[TicketSelection]) -> dict:
        wins = sum(item.result == SelectionResult.WON for item in items)
        outcomes = [(item.probability_snapshot, 1.0 if item.result == SelectionResult.WON else 0.0) for item in items]
        return {
            "selections": len(items),
            "wins": wins,
            "losses": len(items) - wins,
            "hit_rate": round(wins / len(items), 4) if items else None,
            "avg_probability": round(sum(p for p, _ in outcomes) / len(outcomes), 4) if outcomes else None,
            "brier_score": round(sum((p - y) ** 2 for p, y in outcomes) / len(outcomes), 4) if outcomes else None,
            "calibration_error": round(_calibration_error(outcomes) or 0.0, 4) if outcomes else None,
            "confidence": "strong" if len(items) >= 30 else "moderate" if len(items) >= 10 else "directional",
        }

    grouped = [{
        "competition": competition,
        "line": line,
        "probability_band": band,
        "competition_type": kind,
        **aggregate(items),
    } for (competition, line, band, kind), items in sorted(groups.items())]
    return {
        "model_version": model_version or CURRENT_MODEL_VERSION,
        "unique_totals_selections": len(unique),
        "settled_with_probability": sum(len(items) for items in groups.values()),
        "excluded": excluded,
        "rows": grouped,
        "note": "Small groups are directional evidence; no model promotion or automatic weight change is justified by this report alone.",
    }

async def market_reliability_matrix(db: AsyncSession, date_from: date | None = None, date_to: date | None = None, model_version: str | None = CURRENT_MODEL_VERSION) -> list[dict]:
    result = await db.execute(select(AccumulatorTicket).where(AccumulatorTicket.status.in_([TicketStatus.PUBLISHED, TicketStatus.SETTLED, TicketStatus.VOID])).options(selectinload(AccumulatorTicket.selections).selectinload(TicketSelection.prediction), selectinload(AccumulatorTicket.selections).selectinload(TicketSelection.match)))
    unique: dict[tuple, TicketSelection] = {}
    for ticket in result.scalars().all():
        if model_version and ticket.model_version != model_version:
            continue
        for selection in ticket.selections:
            match_date = selection.match.kickoff_at.date() if selection.match else ticket.target_date
            if date_from and match_date < date_from or date_to and match_date > date_to: continue
            unique.setdefault((ticket.target_date, ticket.model_version, selection.match_id, selection.market, selection.selection), selection)
    buckets: dict[tuple, list[TicketSelection]] = {}
    for selection in unique.values():
        q_band = '85–100' if selection.q_score_snapshot >= 85 else '80–85' if selection.q_score_snapshot >= 80 else '75–80'
        spread = selection.prediction.model_agreement if selection.prediction else None
        spread_band = '0–5 pp' if spread is not None and spread <= .05 else '5–10 pp' if spread is not None and spread <= .10 else '10–15 pp' if spread is not None and spread <= .15 else '>15 pp' if spread is not None else 'Unknown'
        buckets.setdefault((selection.market, q_band, spread_band), []).append(selection)
    rows = []
    for (market, q_band, spread_band), items in sorted(buckets.items()):
        effective = [item for item in items if item.result in (SelectionResult.WON, SelectionResult.LOST)]
        wins = sum(item.result == SelectionResult.WON for item in effective)
        pnl = sum((item.odds_snapshot - 1) if item.result == SelectionResult.WON else -1 for item in effective)
        rows.append({'market': market, 'q_band': q_band, 'spread_band': spread_band, 'selections': len(items), 'settled': len(effective), 'wins': wins, 'losses': len(effective)-wins, 'hit_rate': round(wins / len(effective), 4) if effective else None, 'roi': round(pnl / len(effective), 4) if effective else None, 'confidence': 'Strong' if len(effective) >= 30 else 'Moderate' if len(effective) >= 10 else 'Directional'})
    return rows


async def clv_summary(db: AsyncSession, date_from: date | None = None, date_to: date | None = None, model_version: str | None = None) -> dict:
    """Closing-line-value: did our entry price beat the market's price at kickoff?

    This is the standard way to judge whether a model has genuine edge,
    independent of short-run win/loss variance. Only predictions where a
    closing price was actually captured (clv_percentage is not null) are
    included — legs settled before the capture task could run, or with no
    pre-kickoff quote history, are excluded rather than guessed at.
    """
    result = await db.execute(
        select(Prediction).join(Match).options(selectinload(Prediction.match))
        .where(Prediction.clv_percentage.is_not(None))
        .order_by(Prediction.created_at.desc())
    )
    latest: dict[tuple[int, str, str], Prediction] = {}
    for prediction in result.scalars().all():
        if model_version and prediction.model_version != model_version:
            continue
        match_date = prediction.match.kickoff_at.date()
        if date_from and match_date < date_from or date_to and match_date > date_to:
            continue
        key = (prediction.match_id, prediction.market, prediction.selection)
        if key not in latest:
            latest[key] = prediction

    rows = list(latest.values())

    def aggregate(items: list[Prediction]) -> dict:
        if not items:
            return {"legs": 0, "avg_clv_pct": None, "beat_close_rate": None, "avg_edge": None}
        clvs = [p.clv_percentage for p in items]
        edges = [p.edge for p in items if p.edge is not None]
        return {
            "legs": len(items),
            "avg_clv_pct": round(sum(clvs) / len(clvs), 4),
            "beat_close_rate": round(sum(c > 0 for c in clvs) / len(clvs), 4),
            "avg_edge": round(sum(edges) / len(edges), 4) if edges else None,
        }

    by_q_grade: dict[str, list[Prediction]] = {}
    by_market: dict[str, list[Prediction]] = {}
    by_month: dict[str, list[Prediction]] = {}
    for prediction in rows:
        by_q_grade.setdefault(prediction.q_grade.value, []).append(prediction)
        by_market.setdefault(prediction.market, []).append(prediction)
        month = prediction.match.kickoff_at.date().isoformat()[:7]
        by_month.setdefault(month, []).append(prediction)

    return {
        "overall": aggregate(rows),
        "by_q_grade": [{"q_grade": k, **aggregate(v)} for k, v in sorted(by_q_grade.items())],
        "by_market": [{"market": k, **aggregate(v)} for k, v in sorted(by_market.items())],
        "by_month": [{"month": k, **aggregate(v)} for k, v in sorted(by_month.items(), reverse=True)],
    }


def _probability_band(probability: float) -> str:
    """Bucket a 0-1 probability into a 5-point band from 50% up (e.g. '55-60')."""
    pct_value = probability * 100
    if pct_value < 50:
        return "<50"
    lower = min(95, int(pct_value // 5) * 5)
    return f"{lower}-{lower + 5}"


def _edge_band(edge: float) -> str:
    """Bucket a model-vs-market edge (model_prob - implied_prob) into pp ranges."""
    pct_edge = edge * 100
    if pct_edge < -10:
        return "< -10pp"
    if pct_edge < -5:
        return "-10 to -5pp"
    if pct_edge < 0:
        return "-5 to 0pp"
    if pct_edge < 5:
        return "0 to 5pp"
    if pct_edge < 10:
        return "5 to 10pp"
    if pct_edge < 15:
        return "10 to 15pp"
    return ">= 15pp"


_PROBABILITY_BAND_ORDER = ["<50"] + [f"{lower}-{lower + 5}" for lower in range(50, 100, 5)]
_EDGE_BAND_ORDER = ["< -10pp", "-10 to -5pp", "-5 to 0pp", "0 to 5pp", "5 to 10pp", "10 to 15pp", ">= 15pp"]


def _market_family(market: str) -> str:
    """Group granular market strings (e.g. 'over_1.5') into families for reporting."""
    lowered = market.lower()
    if lowered.startswith(("over_", "under_")):
        return "totals"
    if lowered.startswith("btts"):
        return "btts"
    if lowered.startswith("double_chance"):
        return "double_chance"
    if lowered.startswith("dnb"):
        return "draw_no_bet"
    if lowered in {"home_win", "draw", "away_win"}:
        return "match_result"
    return lowered


async def probability_calibration_analysis(
    db: AsyncSession,
    date_from: date | None = None,
    date_to: date | None = None,
    model_version: str | None = None,
) -> dict:
    """Compare Lohela model probability vs. market-implied probability across the whole system.

    Uses the same whole-system population as all_market_research_summary: every
    latest prediction (accepted into a ticket or rejected) with a pre-kickoff
    information timestamp, a pre-kickoff price, and a settled outcome. This
    answers where each probability source is well-calibrated (bucketed by 5pp
    bands) and which model/market combinations perform best together.
    """
    result = await db.execute(select(Prediction).join(Match).options(
        selectinload(Prediction.match)
    ).order_by(Prediction.created_at.desc()))
    latest: dict[tuple[int, str, str], Prediction] = {}
    for prediction in result.scalars().all():
        if model_version and prediction.model_version != model_version:
            continue
        match_date = prediction.match.kickoff_at.date()
        if date_from and match_date < date_from or date_to and match_date > date_to:
            continue
        key = (prediction.match_id, prediction.market, prediction.selection)
        if key in latest:
            continue
        if not _has_pre_kickoff_information(prediction):
            continue
        if not _has_pre_kickoff_quote(prediction):
            continue
        if prediction.source_decimal_odds is None or prediction.source_decimal_odds <= 1:
            continue
        if prediction.source_implied_probability is None:
            continue
        latest[key] = prediction

    rows: list[tuple[Prediction, SelectionResult]] = []
    unsupported = 0
    for prediction in latest.values():
        match = prediction.match
        if match.home_goals is None or match.away_goals is None:
            continue
        try:
            outcome = evaluate_selection(prediction.selection, match.home_goals, match.away_goals)
        except ValueError:
            try:
                outcome = evaluate_selection(prediction.market, match.home_goals, match.away_goals)
            except ValueError:
                unsupported += 1
                continue
        rows.append((prediction, outcome))

    effective = [(p, o) for p, o in rows if o != SelectionResult.VOID]

    def outcomes_aggregate(items: list[tuple[Prediction, SelectionResult]]) -> dict:
        wins = sum(o == SelectionResult.WON for _, o in items)
        losses = len(items) - wins
        staked = len(items)
        returned = sum(p.source_decimal_odds for p, o in items if o == SelectionResult.WON)
        pnl = returned - staked
        return {
            "sample_size": len(items),
            "wins": wins,
            "losses": losses,
            "hit_rate": round(wins / len(items), 4) if items else None,
            "roi": round(pnl / staked, 4) if staked else None,
        }

    def bucket_breakdown(prob_of) -> list[dict]:
        groups: dict[str, list[tuple[Prediction, SelectionResult]]] = {}
        for p, o in effective:
            groups.setdefault(_probability_band(prob_of(p)), []).append((p, o))
        rows_out = []
        for band in _PROBABILITY_BAND_ORDER:
            items = groups.get(band)
            if not items:
                continue
            avg_probability = sum(prob_of(p) for p, _ in items) / len(items)
            agg = outcomes_aggregate(items)
            rows_out.append({
                "band": band,
                **agg,
                "avg_probability": round(avg_probability, 4),
                "calibration_gap": round((agg["hit_rate"] or 0.0) - avg_probability, 4),
            })
        return rows_out

    lohela_buckets = bucket_breakdown(lambda p: p.model_probability)
    market_buckets = bucket_breakdown(lambda p: p.source_implied_probability)

    combined_groups: dict[tuple[str, str], list[tuple[Prediction, SelectionResult]]] = {}
    for p, o in effective:
        key = (_probability_band(p.model_probability), _probability_band(p.source_implied_probability))
        combined_groups.setdefault(key, []).append((p, o))
    combined_matrix = []
    for (lohela_band, market_band), items in combined_groups.items():
        avg_lohela = sum(p.model_probability for p, _ in items) / len(items)
        avg_market = sum(p.source_implied_probability for p, _ in items) / len(items)
        combined_matrix.append({
            "lohela_band": lohela_band,
            "market_band": market_band,
            **outcomes_aggregate(items),
            "avg_lohela_probability": round(avg_lohela, 4),
            "avg_market_probability": round(avg_market, 4),
        })
    combined_matrix.sort(key=lambda row: (
        _PROBABILITY_BAND_ORDER.index(row["lohela_band"]),
        _PROBABILITY_BAND_ORDER.index(row["market_band"]),
    ))
    best_combinations = sorted(
        (row for row in combined_matrix if row["sample_size"] >= 8 and row["roi"] is not None),
        key=lambda row: row["roi"],
        reverse=True,
    )[:15]

    edge_groups: dict[str, list[tuple[Prediction, SelectionResult]]] = {}
    for p, o in effective:
        edge_value = p.edge if p.edge is not None else p.model_probability - p.source_implied_probability
        edge_groups.setdefault(_edge_band(edge_value), []).append((p, o))
    edge_buckets = []
    for band in _EDGE_BAND_ORDER:
        items = edge_groups.get(band)
        if not items:
            continue
        edge_buckets.append({"edge_band": band, **outcomes_aggregate(items)})

    def brier(outcomes: list[tuple[float, float]]) -> float | None:
        return round(sum((p - y) ** 2 for p, y in outcomes) / len(outcomes), 4) if outcomes else None

    def calibration_summary(items: list[tuple[Prediction, SelectionResult]]) -> dict:
        lohela_o = [(p.model_probability, 1.0 if o == SelectionResult.WON else 0.0) for p, o in items]
        market_o = [(p.source_implied_probability, 1.0 if o == SelectionResult.WON else 0.0) for p, o in items]
        return {
            **outcomes_aggregate(items),
            "lohela_brier_score": brier(lohela_o),
            "market_brier_score": brier(market_o),
            "lohela_calibration_error": round(_calibration_error(lohela_o) or 0.0, 4) if lohela_o else None,
            "market_calibration_error": round(_calibration_error(market_o) or 0.0, 4) if market_o else None,
        }

    def edge_bucket_breakdown(items: list[tuple[Prediction, SelectionResult]]) -> list[dict]:
        groups: dict[str, list[tuple[Prediction, SelectionResult]]] = {}
        for p, o in items:
            edge_value = p.edge if p.edge is not None else p.model_probability - p.source_implied_probability
            groups.setdefault(_edge_band(edge_value), []).append((p, o))
        return [{"edge_band": band, **outcomes_aggregate(groups[band])} for band in _EDGE_BAND_ORDER if band in groups]

    by_market_family: dict[str, list[tuple[Prediction, SelectionResult]]] = {}
    for p, o in effective:
        by_market_family.setdefault(_market_family(p.market), []).append((p, o))
    market_breakdown = [
        {
            "market_family": family,
            **calibration_summary(items),
            "edge_buckets": edge_bucket_breakdown(items),
        }
        for family, items in sorted(by_market_family.items(), key=lambda kv: len(kv[1]), reverse=True)
    ]

    lohela_outcomes = [(p.model_probability, 1.0 if o == SelectionResult.WON else 0.0) for p, o in effective]
    market_outcomes = [(p.source_implied_probability, 1.0 if o == SelectionResult.WON else 0.0) for p, o in effective]

    return {
        "model_version": model_version or "all",
        "summary": {
            "sample_size": len(effective),
            "voids_excluded": len(rows) - len(effective),
            "unsupported_markets_excluded": unsupported,
            "lohela_brier_score": brier(lohela_outcomes),
            "market_brier_score": brier(market_outcomes),
            "lohela_calibration_error": round(_calibration_error(lohela_outcomes) or 0.0, 4) if lohela_outcomes else None,
            "market_calibration_error": round(_calibration_error(market_outcomes) or 0.0, 4) if market_outcomes else None,
        },
        "lohela_buckets": lohela_buckets,
        "market_buckets": market_buckets,
        "combined_matrix": combined_matrix,
        "best_combinations": best_combinations,
        "edge_buckets": edge_buckets,
        "by_market": market_breakdown,
        "note": "Whole-system evidence: every latest prediction (accepted or rejected) with a pre-kickoff price and settled outcome. Bands with sample_size < 10 are directional, not conclusive.",
    }


def _wilson_interval(wins: int, total: int) -> tuple[float, float]:
    if total == 0:
        return 0.0, 0.0
    z = 1.959963984540054
    p = wins / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _calibration_error(outcomes: list[tuple[float, float]], bins: int = 10) -> float | None:
    if not outcomes:
        return None
    total_error = 0.0
    for index in range(bins):
        lower, upper = index / bins, (index + 1) / bins
        bucket = [(p, y) for p, y in outcomes if lower <= p < upper or (index == bins - 1 and p == 1)]
        if not bucket:
            continue
        confidence = sum(p for p, _ in bucket) / len(bucket)
        accuracy = sum(y for _, y in bucket) / len(bucket)
        total_error += len(bucket) / len(outcomes) * abs(confidence - accuracy)
    return total_error
