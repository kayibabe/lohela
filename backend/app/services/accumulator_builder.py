"""Formal accumulator optimiser for the internal research engine."""

from __future__ import annotations

import logging
import math
from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import cat_day_bounds_utc, cat_today, settings
from app.models import (
    Competition,
    CorrelationCoefficient,
    EdgeBandCalibration,
    Match,
    ModelRun,
    Odds,
    Prediction,
    QGrade,
    RunStatus,
    Team,
    TicketType,
)
from app.services.performance import _edge_band, _market_family

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
    # "model": legs priced by the ensemble and selected for model edge (the
    # original research tiers). "market": legs priced at the bookmaker's
    # de-vigged probability and tickets built for the most likely outcome in
    # an odds band — see MARKET_TICKET_SPECS.
    pricing: str = "model"
    min_leg_odds: float = _MIN_LEG_ODDS
    max_leg_odds: float = _MAX_LEG_ODDS


TICKET_SPECS: tuple[TicketSpec, ...] = (
    TicketSpec(TicketType.SAFE, "Conservative", 3, 6, 3.0, 5.0, 85.0, 1.0, 3, 0.0, 0.05),
    TicketSpec(TicketType.BALANCED, "Balanced", 4, 7, 5.0, 10.0, 80.0, 0.60, 1, 0.0, 0.10),
    TicketSpec(TicketType.AGGRESSIVE, "Aggressive", 5, 10, 10.0, math.inf, 75.0, 0.40, 1, 0.05, 0.15),
    TicketSpec(TicketType.BEST_VALUE, "Best Value", 3, 6, 0.0, math.inf, 85.0, 1.0, 1, 0.0, 0.05, True),
)

# Honest "likely winners" tiers (settings.leg_probability_source == "market").
# On both the frozen production archive (1,823 settled forecasts) and the local
# DB (1,300), the de-vigged market beat the ensemble on Brier and log loss, the
# best held-out model/market blend put 0-10% weight on the model, and legs the
# model rated >=3% edge won 14.5-18.5pp less often than claimed (see
# docs/HONEST_PRICING_2026-09-23.md). So legs are priced at the market's fair
# probability, Q-score/edge (model judgements) no longer gate selection, and
# each tier is the most likely ticket inside its odds band.
MARKET_TICKET_SPECS: tuple[TicketSpec, ...] = (
    TicketSpec(TicketType.SAFE, "Conservative", 3, 4, 1.8, 3.2, 0.0, 0.0, 1, 0.30, 0.05,
               pricing="market", min_leg_odds=1.20, max_leg_odds=1.65),
    TicketSpec(TicketType.BALANCED, "Balanced", 3, 5, 3.2, 6.5, 0.0, 0.0, 1, 0.14, 0.10,
               pricing="market", min_leg_odds=1.25, max_leg_odds=2.30),
    TicketSpec(TicketType.AGGRESSIVE, "Aggressive", 4, 6, 6.5, 16.0, 0.0, 0.0, 1, 0.05, 0.15,
               pricing="market", min_leg_odds=1.30, max_leg_odds=3.50),
    TicketSpec(TicketType.BEST_VALUE, "Best Value", 2, 5, 2.0, 12.0, 0.0, 0.0, 1, 0.0, 0.10, True,
               pricing="market", min_leg_odds=1.20, max_leg_odds=4.00),
)


def active_ticket_specs() -> tuple[TicketSpec, ...]:
    return MARKET_TICKET_SPECS if settings.leg_probability_source == "market" else TICKET_SPECS

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
    # De-vigged market probability for this selection (None when the
    # complementary prices needed to remove the margin are missing).
    fair_probability: Optional[float] = None
    # Set when a leg is market-priced: the ensemble's own probability, kept
    # for audit only.
    raw_model_probability: Optional[float] = None


def market_priced(leg: Leg) -> Leg:
    """Re-price a leg at its fair market probability (audit keeps the model's)."""
    fair = leg.fair_probability
    return replace(
        leg,
        model_probability=fair,
        raw_model_probability=leg.model_probability,
        edge=fair - 1.0 / leg.best_odds,
        expected_value=fair * leg.best_odds - 1.0,
    )


_BINARY_PAIRS = {
    "over_0.5": "under_0.5", "over_1.5": "under_1.5", "over_2.5": "under_2.5",
    "over_3.5": "under_3.5", "over_4.5": "under_4.5", "btts_yes": "btts_no",
    "dnb_home": "dnb_away",
}
_BINARY_PAIRS.update({value: key for key, value in list(_BINARY_PAIRS.items())})
_RESULT_MARKETS = ("home_win", "draw", "away_win")


def fair_market_probabilities(implied: dict[str, float]) -> dict[str, float]:
    """Remove the bookmaker margin within each complete market for one match.

    `implied` maps market -> 1/decimal odds as quoted. Two-way markets are
    normalised pairwise, 1X2 across its three outcomes, and double chance is
    derived from the fair 1X2. Markets without their complement are omitted:
    their margin can't be removed, so they can't be priced honestly.
    """
    fair: dict[str, float] = {}
    for market, value in implied.items():
        other = _BINARY_PAIRS.get(market)
        if other and value and implied.get(other):
            fair[market] = value / (value + implied[other])
    if all(implied.get(market) for market in _RESULT_MARKETS):
        total = sum(implied[market] for market in _RESULT_MARKETS)
        result = {market: implied[market] / total for market in _RESULT_MARKETS}
        fair.update(result)
        fair["double_chance_1x"] = result["home_win"] + result["draw"]
        fair["double_chance_x2"] = result["away_win"] + result["draw"]
        fair["double_chance_12"] = result["home_win"] + result["away_win"]
    return fair


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
    # Days beyond the target date the ticket's legs may kick off (0 = target
    # day only). Set only by the rolling-horizon fallback — see build().
    horizon_days: int = 0
    pricing: str = "model"


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
    horizon_dates: list[date] = field(default_factory=list)

    def public_count(self) -> int:
        return sum(t is not None for t in (self.conservative, self.balanced, self.aggressive))


class AccumulatorBuilder:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def build(
        self,
        target_date: date | None = None,
        model_run_id: int | None = None,
        research_min_qscore: float | None = None,
        horizon_dates: list[date] | None = None,
    ) -> DailyTickets:
        """Build the day's portfolio.

        `horizon_dates` (later CAT days, each with its own completed model run)
        enables the rolling-horizon fallback: only when the target day's own
        slate cannot fill `settings.min_daily_public_tickets` public tiers —
        even after gate relaxation — are legs kicking off on those later days
        admitted, and only for the tiers still missing. Every leg must still
        clear the same per-leg gates (edge, model set, data quality, fresh
        odds, calibration); only *when* it kicks off is widened.
        """
        td = target_date or cat_today()
        run = await self._resolve_model_run(td, model_run_id)
        if run is None:
            return DailyTickets(td, None, None, None, None, None, 0, {})

        # Research Q-score sweeps are model-edge experiments by definition.
        specs = TICKET_SPECS if research_min_qscore is not None else active_ticket_specs()
        pricing = specs[0].pricing
        pool = await self._load_qualified_legs(td, run.id, pricing)
        learning = (run.config_snapshot or {}).get("learning", {})
        calibration = await self._load_calibration_gates(
            td,
            run.model_version,
            learning.get("base_model_version") or learning.get("requested_model_version"),
        )
        coefficients = await self._load_correlation_coefficients()
        output: dict[TicketType, Ticket | None] = {}
        diagnostics: dict[str, dict] = {"pricing": {"source": pricing}}
        for spec in specs:
            effective_spec = spec
            if research_min_qscore is not None:
                effective_spec = TicketSpec(
                    **{**spec.__dict__, "min_q_score": float(research_min_qscore)}
                )
            eligible = [
                leg
                for leg in pool
                if not _research_market_rejection_reasons(leg, effective_spec)
                and not selection_rejection_reasons(leg, effective_spec, calibration)
            ]
            rejection_counts: Counter[str] = Counter()
            for leg in pool:
                rejection_counts.update(_research_market_rejection_reasons(leg, effective_spec))
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
                max_shared_matches=(settings.max_shared_matches_between_market_tickets
                    if pricing == "market" else settings.max_shared_matches_between_tickets),
                max_match_market_exposure=settings.max_public_ticket_exposure_per_match_market,
                max_match_exposure=(settings.max_market_ticket_exposure_per_match
                    if pricing == "market" else None),
            )

        used_horizon: list[date] = []
        if research_min_qscore is None and settings.ticket_relaxation_enabled:
            self._apply_minimum_ticket_relaxation(output, pool, calibration, coefficients)
            if horizon_dates and _public_count(output) < settings.min_daily_public_tickets:
                extended_pool = list(pool)
                seen = {leg.prediction_id for leg in pool}
                for horizon_date in horizon_dates:
                    horizon_run = await self._resolve_model_run(horizon_date, None)
                    if horizon_run is None:
                        continue
                    used_horizon.append(horizon_date)
                    for leg in await self._load_qualified_legs(horizon_date, horizon_run.id, pricing):
                        if leg.prediction_id not in seen:
                            seen.add(leg.prediction_id)
                            extended_pool.append(leg)
                if len(extended_pool) > len(pool):
                    self._apply_minimum_ticket_relaxation(
                        output, extended_pool, calibration, coefficients,
                        first_level=0, target_date=td,
                    )
                diagnostics["horizon"] = {
                    "horizon_dates": [d.isoformat() for d in used_horizon],
                    "horizon_pool_count": len(extended_pool) - len(pool),
                }

        return DailyTickets(
            td,
            run.id,
            output[TicketType.SAFE],
            output[TicketType.BALANCED],
            output[TicketType.AGGRESSIVE],
            output[TicketType.BEST_VALUE],
            len(pool),
            diagnostics,
            used_horizon,
        )

    def _apply_minimum_ticket_relaxation(
        self,
        output: dict[TicketType, Ticket | None],
        pool: list[Leg],
        calibration: dict,
        coefficients: dict[tuple[str, str, str, int | None], float],
        *,
        first_level: int = 1,
        target_date: date | None = None,
    ) -> None:
        """Seek `settings.min_daily_public_tickets` public tickets when the
        qualified pool and portfolio exposure limits can support them, by loosening the tightest gates (only) for
        public tiers that produced nothing at full strength. Never touches a tier
        that already has a full-strength ticket, and never invents a combination
        the beam search can't actually build from real, edge-qualified legs.

        `first_level=0` tries the unrelaxed spec first; the rolling-horizon
        pass uses it (with `target_date`, to stamp each ticket's horizon_days)
        so a wider pool is exhausted at full strength before any gate loosens."""
        if _public_count(output) >= settings.min_daily_public_tickets:
            return
        # Loosest base spec first (AGGRESSIVE), so relaxation reaches the floor
        # with the fewest, least-invasive concessions. On a thin pool that order
        # can starve the others: AGGRESSIVE may claim up to 10 legs and the
        # public one-exposure-per-match/market rule leaves too few for
        # CONSERVATIVE. So when it falls short, also try smallest-ticket-first
        # and keep whichever fills more tiers (ties keep the original order).
        orders = (
            (TicketType.AGGRESSIVE, TicketType.BALANCED, TicketType.SAFE),
            (TicketType.SAFE, TicketType.BALANCED, TicketType.AGGRESSIVE),
        )
        best: dict[TicketType, Ticket | None] | None = None
        for order in orders:
            attempt = dict(output)
            self._fill_missing_public_tiers(
                attempt, order, pool, calibration, coefficients, first_level, target_date
            )
            if best is None or _public_count(attempt) > _public_count(best):
                best = attempt
            if _public_count(best) >= settings.min_daily_public_tickets:
                break
        output.update(best)
        for ticket_type in (TicketType.SAFE, TicketType.BALANCED, TicketType.AGGRESSIVE):
            ticket = output[ticket_type]
            if ticket is not None and (ticket.relaxed or ticket.horizon_days):
                logger.warning(
                    "Filled %s ticket (relaxation level %d, horizon +%dd) to meet minimum "
                    "daily publication (%d/%d public)",
                    ticket_type.value, ticket.relaxation_level, ticket.horizon_days,
                    _public_count(output), settings.min_daily_public_tickets,
                )

    def _fill_missing_public_tiers(
        self,
        output: dict[TicketType, Ticket | None],
        order: tuple[TicketType, ...],
        pool: list[Leg],
        calibration: dict,
        coefficients: dict[tuple[str, str, str, int | None], float],
        first_level: int,
        target_date: date | None,
    ) -> None:
        public_types = (TicketType.SAFE, TicketType.BALANCED, TicketType.AGGRESSIVE)
        published = _public_count(output)
        for ticket_type in (t for t in order if output[t] is None):
            if published >= settings.min_daily_public_tickets:
                break
            base_spec = next(item for item in active_ticket_specs() if item.ticket_type == ticket_type)
            for level in range(first_level, len(_RELAXATION_STEPS) + 1):
                relaxed_spec = _relax_spec(base_spec, level) if level else base_spec
                eligible = [
                    leg for leg in pool
                    if not _research_market_rejection_reasons(leg, relaxed_spec)
                    and not selection_rejection_reasons(leg, relaxed_spec, calibration)
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
                    max_shared_matches=(settings.max_shared_matches_between_market_tickets
                        if relaxed_spec.pricing == "market" else settings.max_shared_matches_between_tickets),
                    max_match_market_exposure=settings.max_public_ticket_exposure_per_match_market,
                    max_match_exposure=(settings.max_market_ticket_exposure_per_match
                        if relaxed_spec.pricing == "market" else None),
                )
                if ticket is not None:
                    ticket.relaxed = level > 0
                    ticket.relaxation_level = level
                    if target_date is not None:
                        ticket.horizon_days = max(
                            _cat_day_offset(leg.kickoff_at, target_date) for leg in ticket.legs
                        )
                    output[ticket_type] = ticket
                    published += 1
                    break

    async def rejected_selections(
        self, target_date: date, ticket_type: TicketType = TicketType.SAFE
    ) -> list[dict]:
        run = await self._resolve_model_run(target_date, None)
        if run is None:
            return []
        spec = next(item for item in active_ticket_specs() if item.ticket_type == ticket_type)
        rejected = []
        learning = (run.config_snapshot or {}).get("learning", {})
        calibration = await self._load_calibration_gates(
            target_date,
            run.model_version,
            learning.get("base_model_version") or learning.get("requested_model_version"),
        )
        for leg in await self._load_all_legs(target_date, run.id):
            if spec.pricing == "market" and leg.fair_probability is not None:
                leg = market_priced(leg)
            reasons = _research_market_rejection_reasons(leg, spec) + selection_rejection_reasons(leg, spec, calibration)
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

    async def _load_qualified_legs(
        self, target_date: date, model_run_id: int, pricing: str = "model"
    ) -> list[Leg]:
        legs = await self._load_all_legs(target_date, model_run_id)
        if pricing == "market":
            # Unpriceable legs stay in the pool so diagnostics count them as
            # MISSING_FAIR_PRICE; that gate keeps them out of every ticket.
            return [
                market_priced(leg) if leg.fair_probability is not None else leg
                for leg in legs
                if leg.best_odds >= _MIN_LEG_ODDS
            ]
        return [
            leg
            for leg in legs
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
        edge_version_priority = case(
            (EdgeBandCalibration.model_version == model_version, 0), else_=1
        )
        edge_band_result = await self.db.execute(
            select(EdgeBandCalibration).where(
                EdgeBandCalibration.model_version.in_(versions),
                EdgeBandCalibration.period_end < target_date,
            ).order_by(
                edge_version_priority,
                EdgeBandCalibration.period_end.desc(),
                EdgeBandCalibration.id.desc(),
            )
        )
        edge_bands = {}
        for row in edge_band_result.scalars().all():
            key = (row.market_family, row.edge_band)
            if key not in edge_bands:
                edge_bands[key] = row
        return {"leagues": leagues, "markets": markets, "edge_bands": edge_bands}

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
        consensus = await self._consensus_fair_probabilities({leg.match_id for leg in legs})
        # Fallback: the run's own best-price snapshots (older data, or a
        # fixture with no stored bookmaker book).
        implied_by_match: dict[int, dict[str, float]] = {}
        for leg in legs:
            if leg.best_odds > 1.0:
                implied_by_match.setdefault(leg.match_id, {})[leg.market] = 1.0 / leg.best_odds
        snapshot_fair = {
            match_id: fair_market_probabilities(implied)
            for match_id, implied in implied_by_match.items()
        }
        for leg in legs:
            leg.fair_probability = consensus.get(leg.match_id, {}).get(leg.market)
            if leg.fair_probability is None:
                leg.fair_probability = snapshot_fair.get(leg.match_id, {}).get(leg.market)
        return legs

    async def _consensus_fair_probabilities(self, match_ids: set[int]) -> dict[int, dict[str, float]]:
        """Consensus fair probability per match/market from stored bookmaker books.

        Each bookmaker's margin is removed within its own book (one fetch, one
        price set), then fair probabilities are averaged across bookmakers.
        Pairing best prices from different bookmakers instead can "remove" more
        than the real margin — on the frozen archive 63 legs came out at up to
        +9% EV that way — and misses complements the model doesn't predict
        (e.g. under_1.5, so over_1.5 could never be priced).
        """
        if not match_ids:
            return {}
        result = await self.db.execute(
            select(Odds.match_id, Odds.bookmaker, Odds.market, Odds.decimal_odds)
            .where(Odds.match_id.in_(match_ids), Odds.decimal_odds > 1.0)
        )
        books: dict[tuple[int, str], dict[str, float]] = {}
        for match_id, bookmaker, market, decimal_odds in result.all():
            books.setdefault((match_id, bookmaker), {})[market] = 1.0 / decimal_odds
        sums: dict[int, dict[str, list[float]]] = {}
        for (match_id, _bookmaker), implied in books.items():
            for market, value in fair_market_probabilities(implied).items():
                sums.setdefault(match_id, {}).setdefault(market, []).append(value)
        return {
            match_id: {market: sum(values) / len(values) for market, values in markets.items()}
            for match_id, markets in sums.items()
        }

    async def _load_correlation_coefficients(self) -> dict[tuple[str, str, str, int | None], float]:
        result = await self.db.execute(select(CorrelationCoefficient))
        return {
            (row.scope, row.key_a, row.key_b, row.competition_id): row.coefficient
            for row in result.scalars().all()
        }


def selection_rejection_reasons(leg: Leg, spec: TicketSpec, calibration: dict | None = None) -> list[str]:
    if spec.pricing == "market":
        return _market_rejection_reasons(leg, spec)
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
        if leg.edge is not None:
            edge_key = (_market_family(leg.market), _edge_band(leg.edge))
            edge_row = calibration.get("edge_bands", {}).get(edge_key)
            if (
                edge_row
                and edge_row.sample_size >= settings.min_edge_band_calibration_sample
                and (edge_row.calibration_error or 0.0) > settings.max_edge_band_calibration_error
            ):
                reasons.append("EDGE_BAND_CALIBRATION_UNRELIABLE")
    return reasons


def _market_rejection_reasons(leg: Leg, spec: TicketSpec) -> list[str]:
    """Gates for market-priced legs: the price and the data, not the model.

    Edge, Q-score, model-set and model-calibration gates judge the ensemble's
    opinion, which no longer prices these legs. What remains: a fair price
    must exist, sit in the tier's leg-odds band, carry a tolerable margin at
    the quoted price, and be fresh; the fixture's data must be usable.
    """
    reasons: list[str] = []
    if leg.best_odds <= 0:
        reasons.append("MISSING_ODDS")
    elif not (spec.min_leg_odds <= leg.best_odds <= spec.max_leg_odds):
        reasons.append("LEG_ODDS_OUT_OF_TIER_BAND")
    if leg.fair_probability is None:
        reasons.append("MISSING_FAIR_PRICE")
    elif leg.best_odds > 0:
        value = leg.fair_probability * leg.best_odds - 1.0
        if value < -settings.max_leg_margin:
            reasons.append("MARGIN_TOO_HIGH")
        elif value > settings.max_leg_price_advantage:
            # A quote this far above the consensus fair price is far more
            # likely stale or mis-mapped than a genuine gift.
            reasons.append("PRICE_OUT_OF_LINE")
    if leg.data_quality_score < settings.min_data_quality_score:
        reasons.append("DATA_QUALITY_BELOW_THRESHOLD")
    if leg.source_odds_at is None:
        reasons.append("MISSING_ODDS_TIMESTAMP")
    elif _odds_age_hours(leg.source_odds_at) > settings.max_selection_odds_age_hours:
        reasons.append("STALE_ODDS")
    return reasons


def _research_market_rejection_reasons(leg: Leg, spec: TicketSpec | None = None) -> list[str]:
    """Exclude restricted markets from generated paper tickets only.

    The restriction answers where the *model* lost; market-priced tiers don't
    use the model's opinion, so it doesn't apply to them."""
    if spec is not None and spec.pricing == "market":
        return []
    if leg.market in settings.research_restricted_markets:
        return ["MARKET_RESTRICTED_FOR_RESEARCH"]
    return []


def _public_count(output: dict[TicketType, Ticket | None]) -> int:
    return sum(
        output.get(t) is not None
        for t in (TicketType.SAFE, TicketType.BALANCED, TicketType.AGGRESSIVE)
    )


def _cat_day_offset(kickoff_at: datetime, target_date: date) -> int:
    """Whole CAT days between `target_date` and the CAT day `kickoff_at` falls on."""
    when = kickoff_at if kickoff_at.tzinfo else kickoff_at.replace(tzinfo=timezone.utc)
    offset = 0
    while offset < 31 and when >= cat_day_bounds_utc(
        date.fromordinal(target_date.toordinal() + offset)
    )[1]:
        offset += 1
    return offset


def _relax_spec(spec: TicketSpec, level: int) -> TicketSpec:
    """Apply relaxation steps 1..level cumulatively to `spec`, clamped to sane floors."""
    if spec.pricing == "market":
        # Market tiers have no Q/grade gates to loosen; a thin slate instead
        # widens the odds band a little per level, and from level 2 allows a
        # leg fewer (never below a 2-leg accumulator).
        return replace(
            spec,
            min_legs=max(2, spec.min_legs - (1 if level >= 2 else 0)),
            min_combined_odds=spec.min_combined_odds * (1 - 0.10 * level),
            max_combined_odds=spec.max_combined_odds * (1 + 0.15 * level),
            max_leg_odds=spec.max_leg_odds + 0.20 * level,
            min_adjusted_probability=spec.min_adjusted_probability * (1 - 0.20 * level),
        )
    ratio_delta = sum(step.get("min_high_grade_ratio", 0.0) for step in _RELAXATION_STEPS[:level])
    market_delta = sum(step.get("min_market_types", 0) for step in _RELAXATION_STEPS[:level])
    q_delta = sum(step.get("min_q_score", 0.0) for step in _RELAXATION_STEPS[:level])
    odds_delta = sum(step.get("max_combined_odds", 0.0) for step in _RELAXATION_STEPS[:level])
    relaxed_max_odds = (
        spec.max_combined_odds
        if math.isinf(spec.max_combined_odds)
        else min(_RELAXATION_MAX_COMBINED_ODDS, spec.max_combined_odds + odds_delta)
    )
    return replace(
        spec,
        max_combined_odds=relaxed_max_odds,
        min_q_score=max(_RELAXATION_FLOOR_Q_SCORE, spec.min_q_score + q_delta),
        min_high_grade_ratio=max(0.0, spec.min_high_grade_ratio + ratio_delta),
        min_market_types=max(1, spec.min_market_types + market_delta),
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
    if spec.pricing == "market":
        # The correlation heuristics still gate *which* legs may combine
        # (above), but a fair-priced ticket's chance is the product of fair
        # leg probabilities: distinct matches, each priced by the market. The
        # heuristic haircut would understate the chance shown to users.
        survival = 1.0
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
        pricing=spec.pricing,
    )


def _odds_age_hours(value: datetime | None) -> float:
    if value is None:
        return math.inf
    when = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - when).total_seconds() / 3600.0)


def _objective(ticket: Ticket) -> float:
    if ticket.pricing == "market":
        if ticket.internal_only:
            return ticket.expected_value
        # Most likely ticket in the tier's odds band, traded against the
        # bookmaker margin paid: +1% EV is worth +1% relative hit probability.
        return math.log(max(ticket.adjusted_probability, 1e-9)) + ticket.expected_value
    if ticket.ticket_type == TicketType.SAFE:
        return ticket.adjusted_probability + max(0.0, ticket.expected_value) * 0.05
    return ticket.expected_value / (1.0 + ticket.risk_score / 100.0)


def _partial_score(legs: tuple[Leg, ...]) -> float:
    average_q = sum(leg.q_score for leg in legs) / len(legs)
    positive_ev = sum(max(-0.2, leg.expected_value or -0.2) for leg in legs)
    return average_q + positive_ev * 20.0 + len({_market_family(leg.market) for leg in legs})


def _market_leg_target(spec: TicketSpec) -> float:
    """Typical leg price for a tier: geometric-mid combined odds of its band,
    spread over its mid leg count."""
    mid_legs = (spec.min_legs + spec.max_legs) / 2
    upper = spec.max_combined_odds if math.isfinite(spec.max_combined_odds) else spec.min_combined_odds * 4
    return math.sqrt(max(spec.min_combined_odds, 1.01) * upper) ** (1 / mid_legs)


def _market_leg_score(leg: Leg, target: float) -> float:
    """Low margin first, nudged toward the tier's typical leg price.

    Ranking (or beam-pruning) on probability alone keeps only 1.2x favourites,
    whose combinations never reach the Balanced/Aggressive odds bands; the
    final choice among valid tickets is still the most likely one
    (_objective)."""
    value = -1.0 if leg.expected_value is None else leg.expected_value
    return value - 0.05 * abs(math.log(leg.best_odds / target))


def _market_candidates(pool: list[Leg], spec: TicketSpec) -> list[Leg]:
    """Best-scoring legs for the tier; at most two markets per match so the
    window spans enough fixtures for three tickets."""
    target = _market_leg_target(spec)
    ranked = sorted(
        pool, key=lambda leg: (_market_leg_score(leg, target), leg.prediction_id), reverse=True
    )
    per_match: Counter[int] = Counter()
    chosen: list[Leg] = []
    for leg in ranked:
        if per_match[leg.match_id] < 2:
            per_match[leg.match_id] += 1
            chosen.append(leg)
    return chosen


def _find_best_ticket(
    pool: list[Leg],
    spec: TicketSpec,
    learned: dict[tuple[str, str, str, int | None], float],
    *,
    prior_tickets: list[Ticket] | None = None,
    max_shared_matches: int | None = None,
    max_match_market_exposure: int | None = None,
    max_match_exposure: int | None = None,
) -> Optional[Ticket]:
    # Deterministic tie-break on prediction_id rather than model_probability,
    # for exact (q_score, expected_value) ties only. Not a calibration fix:
    # selection may concentrate observed forecast errors, but a causal
    # amplification mechanism and its magnitude remain unproven, and both
    # primary sort keys (q_score, expected_value) already embed
    # model_probability regardless of this tie-break. See
    # docs/SELECTION_CALIBRATION_REVIEW_2026-09-20.md.
    if max_match_market_exposure is not None and prior_tickets:
        # A leg whose match/market an earlier public tier already exposes up
        # to the limit can never be part of a valid ticket. Drop it before the
        # beam: otherwise the top-_BEAM_WIDTH partials can consist entirely of
        # such legs and a buildable later tier is reported as impossible.
        exposed = Counter(
            (leg.match_id, leg.market) for prior in prior_tickets for leg in prior.legs
        )
        pool = [
            leg for leg in pool
            if exposed[(leg.match_id, leg.market)] < max_match_market_exposure
        ]
    if max_match_exposure is not None and prior_tickets:
        exposed_matches = Counter(leg.match_id for prior in prior_tickets for leg in prior.legs)
        pool = [leg for leg in pool if exposed_matches[leg.match_id] < max_match_exposure]
    if spec.pricing == "market":
        candidates = _market_candidates(pool, spec)[:_CANDIDATE_LIMIT]
        target = _market_leg_target(spec)

        def partial_score(legs: tuple[Leg, ...]) -> float:
            return sum(_market_leg_score(leg, target) for leg in legs)
    else:
        candidates = sorted(
            pool,
            key=lambda leg: (leg.q_score, leg.expected_value or -1.0, leg.prediction_id),
            reverse=True,
        )[:_CANDIDATE_LIMIT]
        partial_score = _partial_score
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
        expanded.sort(key=lambda item: partial_score(item[1]), reverse=True)
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
                max_match_exposure,
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
    max_match_exposure: int | None = None,
) -> bool:
    if limit is not None and not all(shared_match_count(ticket, prior) <= limit for prior in prior_tickets):
        return False
    if max_match_exposure is not None:
        prior_matches = Counter(leg.match_id for prior in prior_tickets for leg in prior.legs)
        if any(prior_matches[leg.match_id] >= max_match_exposure for leg in ticket.legs):
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
