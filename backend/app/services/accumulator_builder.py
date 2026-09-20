"""Formal accumulator optimiser for the internal research engine."""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import cat_day_bounds_utc, cat_today, settings
from app.models import (
    Competition,
    CorrelationCoefficient,
    Match,
    ModelRun,
    Odds,
    Prediction,
    QGrade,
    RunStatus,
    Team,
    TicketType,
)

logger = logging.getLogger(__name__)

_MIN_LEG_ODDS = 1.20
_MAX_LEG_ODDS = 10.0
_MAX_LEGS_PER_LEAGUE = 2
_MIN_ACTIVE_MODELS = 3
_BEAM_WIDTH = 800
_CANDIDATE_LIMIT = 30


@dataclass(frozen=True)
class TicketSpec:
    ticket_type: TicketType
    display_name: str
    min_legs: int
    max_legs: int
    min_combined_odds: float
    max_combined_odds: float
    min_q_score: float
    min_high_grade_ratio: float
    min_market_types: int
    min_adjusted_probability: float
    max_pair_correlation: float
    internal_only: bool = False


TICKET_SPECS: tuple[TicketSpec, ...] = (
    TicketSpec(TicketType.SAFE, "Conservative", 3, 6, 3.0, 5.0, 85.0, 1.0, 3, 0.0, 0.05),
    TicketSpec(TicketType.BALANCED, "Balanced", 4, 7, 5.0, 10.0, 80.0, 0.60, 1, 0.0, 0.10),
    TicketSpec(TicketType.AGGRESSIVE, "Aggressive", 5, 10, 10.0, math.inf, 75.0, 0.40, 1, 0.05, 0.15),
    TicketSpec(TicketType.BEST_VALUE, "Best Value", 3, 6, 0.0, math.inf, 85.0, 1.0, 1, 0.0, 0.05, True),
)

# Bounded relaxation ladder for the minimum-daily-tickets fallback (see
# AccumulatorBuilder.build). Each step loosens the constraints that a thin
# weekday slate hits first — the high-grade-leg ratio, market-family diversity,
# and (for the conservative tier) combined-odds ceiling — while leaving
# min/max legs and correlation tolerance untouched. A relaxed ticket remains a
# coherent, auditable accumulator rather than an arbitrary leg dump. Applied
# cumulatively (step 2 includes step 1's loosening) and only ever to public
# tiers that produced no ticket at full strength.
_RELAXATION_STEPS: tuple[dict, ...] = (
    {"min_high_grade_ratio": -0.30, "min_market_types": -1, "max_combined_odds": 1.0},
    {"min_high_grade_ratio": -0.60, "min_q_score": -5.0, "max_combined_odds": 2.0},
    {"min_high_grade_ratio": -1.00, "min_q_score": -10.0, "max_combined_odds": 2.0},
)
_RELAXATION_FLOOR_Q_SCORE = 60.0
_RELAXATION_MAX_COMBINED_ODDS = 10.0


@dataclass
class Leg:
    prediction_id: int
    match_id: int
    home_team_id: int
    away_team_id: int
    home_team: str
    away_team: str
    competition: str
    competition_id: int
    kickoff_at: datetime
    market: str
    selection: str
    model_probability: float
    model_agreement: float
    best_odds: float
    source_odds_at: datetime | None
    q_score: float
    q_grade: QGrade
    edge: Optional[float]
    expected_value: Optional[float]
    active_models: list[str] = field(default_factory=list)
    data_quality_score: float = 0.0


@dataclass
class CorrelationDetail:
    left_prediction_id: int
    right_prediction_id: int
    coefficient: float
    reasons: list[str]


@dataclass
class Ticket:
    ticket_type: TicketType
    name: str
    legs: list[Leg]
    combined_odds: float
    combined_probability: float
    adjusted_probability: float
    correlation_penalty: float
    correlation_details: list[CorrelationDetail]
    expected_value: float
    risk_score: float
    confidence_score: float
    avg_q_score: float
    avg_edge: Optional[float]
    high_risk_label: bool
    internal_only: bool
    relaxed: bool = False
    relaxation_level: int = 0


@dataclass
class DailyTickets:
    target_date: date
    model_run_id: int | None
    conservative: Optional[Ticket]
    balanced: Optional[Ticket]
    aggressive: Optional[Ticket]
    best_value: Optional[Ticket]
    qualified_pool: int
    selection_diagnostics: dict[str, dict] = field(default_factory=dict)


class AccumulatorBuilder:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build(
        self,
        target_date: date | None = None,
        model_run_id: int | None = None,
        research_min_qscore: float | None = None,
    ) -> DailyTickets:
        td = target_date or cat_today()
        run = await self._resolve_model_run(td, model_run_id)
        if run is None:
            return DailyTickets(td, None, None, None, None, None, 0, {})

        pool = await self._load_qualified_legs(td, run.id)
        learning = (run.config_snapshot or {}).get("learning", {})
        calibration = await self._load_calibration_gates(
            td,
            run.model_version,
            learning.get("base_model_version") or learning.get("requested_model_version"),
        )
        coefficients = await self._load_correlation_coefficients()
        output: dict[TicketType, Ticket | None] = {}
        diagnostics: dict[str, dict] = {}
        for spec in TICKET_SPECS:
            effective_spec = spec
            if research_min_qscore is not None:
                effective_spec = TicketSpec(
                    **{**spec.__dict__, "min_q_score": float(research_min_qscore)}
                )
            eligible = [
                leg
                for leg in pool
                if not _research_market_rejection_reasons(leg)
                and not selection_rejection_reasons(leg, effective_spec, calibration)
            ]
            rejection_counts: Counter[str] = Counter()
            for leg in pool:
                rejection_counts.update(_research_market_rejection_reasons(leg))
                rejection_counts.update(selection_rejection_reasons(leg, effective_spec, calibration))
            diagnostics[spec.ticket_type.value] = {
                "pool_count": len(pool),
                "eligible_leg_count": len(eligible),
                "distinct_eligible_match_count": len({leg.match_id for leg in eligible}),
                "rejection_counts": dict(sorted(rejection_counts.items())),
                "min_legs": effective_spec.min_legs,
                "min_combined_odds": effective_spec.min_combined_odds,
                "max_combined_odds": (
                    None
                    if math.isinf(effective_spec.max_combined_odds)
                    else effective_spec.max_combined_odds
                ),
                "min_q_score": effective_spec.min_q_score,
                "min_high_grade_ratio": effective_spec.min_high_grade_ratio,
                "min_market_types": effective_spec.min_market_types,
            }
            prior_public = [
                ticket for ticket_type, ticket in output.items()
                if ticket is not None and ticket_type in (TicketType.SAFE, TicketType.BALANCED, TicketType.AGGRESSIVE)
            ]
            output[spec.ticket_type] = _find_best_ticket(
                eligible,
                effective_spec,
                coefficients,
                prior_tickets=prior_public if not spec.internal_only else [],
                max_shared_matches=settings.max_shared_matches_between_tickets,
                max_match_market_exposure=settings.max_public_ticket_exposure_per_match_market,
            )

        if research_min_qscore is None and settings.ticket_relaxation_enabled:
            self._apply_minimum_ticket_relaxation(output, pool, calibration, coefficients)

        return DailyTickets(
            td,
            run.id,
            output[TicketType.SAFE],
            output[TicketType.BALANCED],
            output[TicketType.AGGRESSIVE],
            output[TicketType.BEST_VALUE],
            len(pool),
            diagnostics,
        )

    def _apply_minimum_ticket_relaxation(
        self,
        output: dict[TicketType, Ticket | None],
        pool: list[Leg],
        calibration: dict,
        coefficients: dict[tuple[str, str, str, int | None], float],
    ) -> None:
        """Guarantee `settings.min_daily_public_tickets` public tickets when the
        qualified pool can support it, by loosening the tightest gates (only) for
        public tiers that produced nothing at full strength. Never touches a tier
        that already has a full-strength ticket, and never invents a combination
        the beam search can't actually build from real, edge-qualified legs."""
        public_types = (TicketType.SAFE, TicketType.BALANCED, TicketType.AGGRESSIVE)
        published = sum(1 for t in public_types if output[t] is not None)
        if published >= settings.min_daily_public_tickets:
            return
        # Loosest base spec first (AGGRESSIVE), so relaxation reaches the floor
        # with the fewest, least-invasive concessions.
        relax_order = [
            t for t in (TicketType.AGGRESSIVE, TicketType.BALANCED, TicketType.SAFE)
            if output[t] is None
        ]
        for ticket_type in relax_order:
            if published >= settings.min_daily_public_tickets:
                break
            base_spec = next(item for item in TICKET_SPECS if item.ticket_type == ticket_type)
            for level in range(1, len(_RELAXATION_STEPS) + 1):
                relaxed_spec = _relax_spec(base_spec, level)
                eligible = [
                    leg for leg in pool
                    if not selection_rejection_reasons(leg, relaxed_spec, calibration)
                ]
                prior_public = [
                    existing for existing_type, existing in output.items()
                    if existing is not None
                    and existing_type in public_types
                    and existing_type != ticket_type
                ]
                ticket = _find_best_ticket(
                    eligible,
                    relaxed_spec,
                    coefficients,
                    prior_tickets=prior_public,
                    max_shared_matches=settings.max_shared_matches_between_tickets,
                    max_match_market_exposure=settings.max_public_ticket_exposure_per_match_market,
                )
                if ticket is not None:
                    ticket.relaxed = True
                    ticket.relaxation_level = level
                    output[ticket_type] = ticket
                    published += 1
                    logger.warning(
                        "Relaxed %s ticket to level %d to meet minimum daily publication (%d/%d public)",
                        ticket_type.value, level, published, settings.min_daily_public_tickets,
                    )
                    break

    async def rejected_selections(
        self, target_date: date, ticket_type: TicketType = TicketType.SAFE
    ) -> list[dict]:
        run = await self._resolve_model_run(target_date, None)
        if run is None:
            return []
        spec = next(item for item in TICKET_SPECS if item.ticket_type == ticket_type)
        rejected = []
        learning = (run.config_snapshot or {}).get("learning", {})
        calibration = await self._load_calibration_gates(
            target_date,
            run.model_version,
            learning.get("base_model_version") or learning.get("requested_model_version"),
        )
        for leg in await self._load_all_legs(target_date, run.id):
            reasons = _research_market_rejection_reasons(leg) + selection_rejection_reasons(leg, spec, calibration)
            if reasons:
                rejected.append({"leg": leg, "reason_codes": reasons})
        return rejected

    async def _resolve_model_run(self, target_date: date, model_run_id: int | None) -> ModelRun | None:
        if model_run_id is not None:
            run = await self.db.get(ModelRun, model_run_id)
            return run if run and run.target_date == target_date and run.status == RunStatus.COMPLETED else None
        result = await self.db.execute(
            select(ModelRun)
            .where(ModelRun.target_date == target_date, ModelRun.status == RunStatus.COMPLETED)
            .order_by(ModelRun.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _load_qualified_legs(self, target_date: date, model_run_id: int) -> list[Leg]:
        return [
            leg
            for leg in await self._load_all_legs(target_date, model_run_id)
            if leg.edge is not None and leg.best_odds >= _MIN_LEG_ODDS
        ]

    async def _load_calibration_gates(
        self,
        target_date: date,
        model_version: str,
        fallback_model_version: str | None = None,
    ) -> dict:
        """Load prior-period cohorts for the exact model lineage, deterministically."""
        from app.models import LeaguePerformance, ModelPerformance
        league_result = await self.db.execute(
            select(LeaguePerformance).where(LeaguePerformance.period_end < target_date)
            .order_by(LeaguePerformance.period_end.desc())
        )
        leagues = {}
        for row in league_result.scalars().all():
            if row.competition_id not in leagues:
                leagues[row.competition_id] = row
        versions = [model_version]
        if fallback_model_version and fallback_model_version not in versions:
            versions.append(fallback_model_version)
        version_priority = case(
            (ModelPerformance.model_version == model_version, 0), else_=1
        )
        market_result = await self.db.execute(
            select(ModelPerformance).where(
                ModelPerformance.model_name == "ensemble",
                ModelPerformance.model_version.in_(versions),
                ModelPerformance.period_end < target_date,
            ).order_by(
                version_priority,
                ModelPerformance.period_end.desc(),
                ModelPerformance.id.desc(),
            )
        )
        markets = {}
        for row in market_result.scalars().all():
            if row.market not in markets:
                markets[row.market] = row
        return {"leagues": leagues, "markets": markets}

    async def _load_all_legs(self, target_date: date, model_run_id: int) -> list[Leg]:
        result = await self.db.execute(
            select(Prediction, Match, Competition, Odds)
            .join(Match, Prediction.match_id == Match.id)
            .join(Competition, Match.competition_id == Competition.id)
            .outerjoin(Odds, Prediction.source_odds_id == Odds.id)
            .where(
                Prediction.model_run_id == model_run_id,
                Match.kickoff_at >= cat_day_bounds_utc(target_date)[0],
                Match.kickoff_at < cat_day_bounds_utc(target_date)[1],
                Match.excluded_from_models.is_(False),
            )
            .order_by(Prediction.q_score.desc())
        )
        legs: list[Leg] = []
        for prediction, match, competition, source_odds in result.all():
            home_team = await self.db.get(Team, match.home_team_id)
            away_team = await self.db.get(Team, match.away_team_id)
            if not home_team or not away_team:
                continue
            odds = source_odds
            decimal_odds = prediction.source_decimal_odds
            source_odds_at = prediction.source_odds_at
            if decimal_odds is None and odds is not None:
                # Compatibility for predictions created before snapshot columns
                # existed. New model runs always use the immutable values above.
                decimal_odds = odds.decimal_odds
                source_odds_at = odds.fetched_at
            legs.append(
                Leg(
                    prediction.id,
                    match.id,
                    match.home_team_id,
                    match.away_team_id,
                    home_team.name,
                    away_team.name,
                    competition.name,
                    competition.id,
                    match.kickoff_at,
                    prediction.market,
                    prediction.selection,
                    prediction.model_probability,
                    prediction.model_agreement,
                    decimal_odds or 0.0,
                    source_odds_at,
                    prediction.q_score,
                    prediction.q_grade,
                    prediction.edge,
                    prediction.expected_value,
                    list(prediction.active_models or []),
                    float((prediction.data_quality_snapshot or {}).get("score", 0.0)),
                )
            )
        return legs

    async def _load_correlation_coefficients(self) -> dict[tuple[str, str, str, int | None], float]:
        result = await self.db.execute(select(CorrelationCoefficient))
        return {
            (row.scope, row.key_a, row.key_b, row.competition_id): row.coefficient
            for row in result.scalars().all()
        }


def selection_rejection_reasons(leg: Leg, spec: TicketSpec, calibration: dict | None = None) -> list[str]:
    reasons: list[str] = []
    if leg.best_odds <= 0:
        reasons.append("MISSING_ODDS")
    elif not (_MIN_LEG_ODDS <= leg.best_odds <= _MAX_LEG_ODDS):
        reasons.append("LEG_ODDS_OUT_OF_RANGE")
    if leg.edge is None:
        reasons.append("MISSING_EDGE")
    elif leg.edge < settings.min_selection_edge:
        reasons.append("EDGE_BELOW_NO_BET_THRESHOLD")
    if leg.q_score < spec.min_q_score:
        reasons.append("Q_SCORE_BELOW_TIER")
    if len(leg.active_models) < _MIN_ACTIVE_MODELS:
        reasons.append("INSUFFICIENT_MODEL_SET")
    if leg.data_quality_score < settings.min_data_quality_score:
        reasons.append("DATA_QUALITY_BELOW_THRESHOLD")
    if leg.source_odds_at is None:
        reasons.append("MISSING_ODDS_TIMESTAMP")
    elif _odds_age_hours(leg.source_odds_at) > settings.max_selection_odds_age_hours:
        reasons.append("STALE_ODDS")
    if calibration:
        league = calibration.get("leagues", {}).get(leg.competition_id)
        if league and league.sample_size >= 20 and (league.reliability_score or 0.0) < 0.40:
            reasons.append("LEAGUE_CALIBRATION_BELOW_THRESHOLD")
        market = calibration.get("markets", {}).get(leg.market)
        if market and market.sample_size >= 20 and (market.calibration_error or 0.0) > 0.25:
            reasons.append("MARKET_CALIBRATION_UNRELIABLE")
    return reasons


def _research_market_rejection_reasons(leg: Leg) -> list[str]:
    """Exclude restricted markets from generated paper tickets only."""
    if leg.market in settings.research_restricted_markets:
        return ["MARKET_RESTRICTED_FOR_RESEARCH"]
    return []


def _relax_spec(spec: TicketSpec, level: int) -> TicketSpec:
    """Apply relaxation steps 1..level cumulatively to `spec`, clamped to sane floors."""
    ratio_delta = sum(step.get("min_high_grade_ratio", 0.0) for step in _RELAXATION_STEPS[:level])
    market_delta = sum(step.get("min_market_types", 0) for step in _RELAXATION_STEPS[:level])
    q_delta = sum(step.get("min_q_score", 0.0) for step in _RELAXATION_STEPS[:level])
    odds_delta = sum(step.get("max_combined_odds", 0.0) for step in _RELAXATION_STEPS[:level])
    relaxed_max_odds = (
        spec.max_combined_odds
        if math.isinf(spec.max_combined_odds)
        else min(_RELAXATION_MAX_COMBINED_ODDS, spec.max_combined_odds + odds_delta)
    )
    return TicketSpec(
        ticket_type=spec.ticket_type,
        display_name=spec.display_name,
        min_legs=spec.min_legs,
        max_legs=spec.max_legs,
        min_combined_odds=spec.min_combined_odds,
        max_combined_odds=relaxed_max_odds,
        min_q_score=max(_RELAXATION_FLOOR_Q_SCORE, spec.min_q_score + q_delta),
        min_high_grade_ratio=max(0.0, spec.min_high_grade_ratio + ratio_delta),
        min_market_types=max(1, spec.min_market_types + market_delta),
        min_adjusted_probability=spec.min_adjusted_probability,
        max_pair_correlation=spec.max_pair_correlation,
        internal_only=spec.internal_only,
    )


def _market_family(market: str) -> str:
    value = market.lower()
    if value.startswith(("over_", "under_")):
        return "totals"
    if value.startswith("btts"):
        return "btts"
    if value.startswith("double_chance"):
        return "double_chance"
    if value.startswith("dnb"):
        return "draw_no_bet"
    if value in {"home_win", "draw", "away_win"}:
        return "match_result"
    return value


def _pair_correlation(
    left: Leg,
    right: Leg,
    learned: dict[tuple[str, str, str, int | None], float],
) -> CorrelationDetail:
    if left.match_id == right.match_id:
        return CorrelationDetail(left.prediction_id, right.prediction_id, 1.0, ["SAME_MATCH"])
    reasons: list[str] = []
    family_a, family_b = sorted((_market_family(left.market), _market_family(right.market)))
    learned_value = learned.get(("market_pair", family_a, family_b, left.competition_id))
    if learned_value is None:
        learned_value = learned.get(("market_pair", family_a, family_b, None))
    coefficient = max(0.0, float(learned_value or 0.0))
    if learned_value is not None:
        reasons.append("LEARNED_MARKET_PAIR")
    if left.competition_id == right.competition_id:
        coefficient += 0.03
        reasons.append("SAME_LEAGUE")
        if abs((left.kickoff_at - right.kickoff_at).total_seconds()) < 300:
            coefficient += 0.02
            reasons.append("SAME_KICKOFF_SLOT")
        if family_a == family_b:
            coefficient += 0.02
            reasons.append("SAME_MARKET_FAMILY")
    if {left.home_team_id, left.away_team_id} & {right.home_team_id, right.away_team_id}:
        coefficient += 0.08
        reasons.append("SHARED_TEAM")
    return CorrelationDetail(
        left.prediction_id,
        right.prediction_id,
        min(1.0, coefficient),
        reasons or ["INDEPENDENT_DEFAULT"],
    )


def _evaluate_combo(
    legs: tuple[Leg, ...],
    spec: TicketSpec,
    learned: dict[tuple[str, str, str, int | None], float],
) -> Ticket | None:
    if len({leg.match_id for leg in legs}) != len(legs):
        return None
    league_counts: dict[int, int] = {}
    for leg in legs:
        league_counts[leg.competition_id] = league_counts.get(leg.competition_id, 0) + 1
    if max(league_counts.values(), default=0) > _MAX_LEGS_PER_LEAGUE:
        return None
    if sum(leg.q_score >= 85.0 for leg in legs) / len(legs) + 1e-9 < spec.min_high_grade_ratio:
        return None
    if len({_market_family(leg.market) for leg in legs}) < spec.min_market_types:
        return None
    combined_odds = math.prod(leg.best_odds for leg in legs)
    if not (spec.min_combined_odds <= combined_odds <= spec.max_combined_odds):
        return None

    combined_probability = math.prod(leg.model_probability for leg in legs)
    details: list[CorrelationDetail] = []
    survival = 1.0
    for index, left in enumerate(legs):
        for right in legs[index + 1 :]:
            detail = _pair_correlation(left, right, learned)
            if detail.coefficient > spec.max_pair_correlation:
                return None
            details.append(detail)
            survival *= 1.0 - detail.coefficient
    correlation_penalty = 1.0 - survival
    adjusted_probability = combined_probability * survival
    if adjusted_probability + 1e-12 < spec.min_adjusted_probability:
        return None

    expected_value = adjusted_probability * combined_odds - 1.0
    average_q = sum(leg.q_score for leg in legs) / len(legs)
    edges = [leg.edge for leg in legs if leg.edge is not None]
    average_edge = sum(edges) / len(edges) if edges else None
    league_concentration = max(league_counts.values()) / len(legs)
    stale_count = sum(_odds_age_hours(leg.source_odds_at) > 2 for leg in legs)
    model_coverage = sum(min(1.0, len(leg.active_models) / 5.0) for leg in legs) / len(legs)
    confidence = max(0.0, min(100.0, average_q * (0.7 + 0.3 * model_coverage) - stale_count * 5.0 - correlation_penalty * 25.0))
    risk = max(0.0, min(100.0, (1.0 - adjusted_probability) * 70.0 + correlation_penalty * 20.0 + league_concentration * 10.0))
    return Ticket(
        spec.ticket_type,
        spec.display_name,
        list(legs),
        round(combined_odds, 4),
        round(combined_probability, 8),
        round(adjusted_probability, 8),
        round(correlation_penalty, 8),
        details,
        round(expected_value, 6),
        round(risk, 2),
        round(confidence, 2),
        round(average_q, 2),
        round(average_edge, 6) if average_edge is not None else None,
        spec.ticket_type == TicketType.AGGRESSIVE,
        spec.internal_only,
    )


def _odds_age_hours(value: datetime | None) -> float:
    if value is None:
        return math.inf
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - when).total_seconds() / 3600.0)


def _objective(ticket: Ticket) -> float:
    if ticket.ticket_type == TicketType.SAFE:
        return ticket.adjusted_probability + max(0.0, ticket.expected_value) * 0.05
    return ticket.expected_value / (1.0 + ticket.risk_score / 100.0)


def _partial_score(legs: tuple[Leg, ...]) -> float:
    average_q = sum(leg.q_score for leg in legs) / len(legs)
    positive_ev = sum(max(-0.2, leg.expected_value or -0.2) for leg in legs)
    return average_q + positive_ev * 20.0 + len({_market_family(leg.market) for leg in legs})


def _find_best_ticket(
    pool: list[Leg],
    spec: TicketSpec,
    learned: dict[tuple[str, str, str, int | None], float],
    *,
    prior_tickets: list[Ticket] | None = None,
    max_shared_matches: int | None = None,
    max_match_market_exposure: int | None = None,
) -> Optional[Ticket]:
    # Deterministic tie-break on prediction_id rather than model_probability,
    # for exact (q_score, expected_value) ties only. Not a calibration fix:
    # selection may concentrate observed forecast errors, but a causal
    # amplification mechanism and its magnitude remain unproven, and both
    # primary sort keys (q_score, expected_value) already embed
    # model_probability regardless of this tie-break. See
    # docs/SELECTION_CALIBRATION_REVIEW_2026-09-20.md.
    candidates = sorted(
        pool,
        key=lambda leg: (leg.q_score, leg.expected_value or -1.0, leg.prediction_id),
        reverse=True,
    )[:_CANDIDATE_LIMIT]
    if len(candidates) < spec.min_legs:
        return None
    partials: list[tuple[tuple[int, ...], tuple[Leg, ...]]] = [(tuple(), tuple())]
    best: Ticket | None = None
    best_score = -math.inf
    for size in range(1, spec.max_legs + 1):
        expanded: list[tuple[tuple[int, ...], tuple[Leg, ...]]] = []
        for indices, legs in partials:
            start = indices[-1] + 1 if indices else 0
            for index in range(start, len(candidates)):
                leg = candidates[index]
                if any(existing.match_id == leg.match_id for existing in legs):
                    continue
                if sum(existing.competition_id == leg.competition_id for existing in legs) >= _MAX_LEGS_PER_LEAGUE:
                    continue
                next_legs = legs + (leg,)
                if math.prod(item.best_odds for item in next_legs) > spec.max_combined_odds:
                    continue
                expanded.append((indices + (index,), next_legs))
        expanded.sort(key=lambda item: _partial_score(item[1]), reverse=True)
        partials = expanded[:_BEAM_WIDTH]
        if size < spec.min_legs:
            continue
        for _, legs in partials:
            ticket = _evaluate_combo(legs, spec, learned)
            if ticket is not None and _within_ticket_overlap_limit(
                ticket,
                prior_tickets or [],
                max_shared_matches,
                max_match_market_exposure,
            ) and _objective(ticket) > best_score:
                best, best_score = ticket, _objective(ticket)
    return best


def shared_match_count(left: Ticket, right: Ticket) -> int:
    """Return the number of matches exposed by both ticket portfolios."""
    return len({leg.match_id for leg in left.legs} & {leg.match_id for leg in right.legs})


def _within_ticket_overlap_limit(
    ticket: Ticket,
    prior_tickets: list[Ticket],
    limit: int | None,
    max_match_market_exposure: int | None = None,
) -> bool:
    if limit is not None and not all(shared_match_count(ticket, prior) <= limit for prior in prior_tickets):
        return False
    if max_match_market_exposure is None:
        return True
    prior_exposure: dict[tuple[int, str], int] = {}
    for prior in prior_tickets:
        for leg in prior.legs:
            key = (leg.match_id, leg.market)
            prior_exposure[key] = prior_exposure.get(key, 0) + 1
    for leg in ticket.legs:
        if prior_exposure.get((leg.match_id, leg.market), 0) >= max_match_market_exposure:
            return False
    return True
