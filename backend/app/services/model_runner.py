"""
Model runner — Stages 3–6 of the pipeline — spec §26.

Loads all qualified matches for a date, runs all 5 models for each
target market, computes the ensemble, calculates Q-Score, and stores
predictions in the database.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import CURRENT_MODEL_VERSION, cat_day_bounds_utc, cat_today, settings
from app.models import (
    Match,
    Team,
    Competition,
    Odds,
    Prediction,
    TeamStats,
    ModelRun,
    RunStatus,
    MatchStatus,
)
from app.services.models import (
    PoissonDixonColes,
    elo_to_win_probabilities,
    xg_market_probability,
    compute_ensemble,
    compute_q_score,
    ModelInputs,
    QScoreInputs,
    ZINBModel,
    should_use_zinb,
)
from app.services.models.ensemble import DEFAULT_WEIGHTS, Q_SCORE_WEIGHTS

logger = logging.getLogger(__name__)

# Markets to score per match — spec §7, Appendix D
TARGET_MARKETS: list[tuple[str, str]] = [
    ("over_1.5", "over_1.5"),
    ("over_2.5", "over_2.5"),
    ("under_2.5", "under_2.5"),
    ("over_3.5", "over_3.5"),
    ("under_3.5", "under_3.5"),
    ("over_4.5", "over_4.5"),
    ("under_4.5", "under_4.5"),
    ("btts_yes", "btts_yes"),
    ("btts_no", "btts_no"),
    ("home_win", "home_win"),
    ("draw", "draw"),
    ("away_win", "away_win"),
    ("double_chance_1x", "double_chance_1x"),
    ("double_chance_x2", "double_chance_x2"),
    ("dnb_home", "dnb_home"),
    ("dnb_away", "dnb_away"),
]

MINIMUM_ACTIVE_MODELS = 3

# A single cross-league Poisson-DC fit pools goal environments that differ
# structurally (a Bundesliga-average team looks like a Ligue-1 top-six team),
# which biases every market it touches. Fit one model per competition once
# there is enough of its own history to support MLE; competitions below the
# threshold (cups, newly tracked leagues) keep using the pooled global fit.
MIN_LEAGUE_HISTORICAL_MATCHES = 100

# Total match count alone is not sufficient: a competition like the UEFA
# Champions League can clear MIN_LEAGUE_HISTORICAL_MATCHES while spreading
# those matches across 100+ teams from qualifying rounds, most of whom play
# only 2-4 games within the competition. Two data points can't identify two
# free MLE parameters (attack + defense) per team, so the fit for those
# teams saturates at the optimizer's bounds and produces implied match
# totals in the double digits (observed: 83.9 implied goals for one 2026-08
# fixture, traced to a team's defense parameter sitting at the fit bound
# off 2 training matches). A round-robin domestic league gives every team
# dozens of meetings per season; this threshold requires that same kind of
# repeated within-competition exposure before trusting a per-competition
# fit over the pooled global one.
MIN_MEDIAN_MATCHES_PER_TEAM = 15


def _group_by_competition(historical: list[dict]) -> dict[int, list[dict]]:
    grouped: dict[int, list[dict]] = {}
    for row in historical:
        grouped.setdefault(row["competition_id"], []).append(row)
    return grouped


def _median_matches_per_team(rows: list[dict]) -> int:
    counts: dict[int, int] = defaultdict(int)
    for row in rows:
        counts[row["home_team_id"]] += 1
        counts[row["away_team_id"]] += 1
    values = sorted(counts.values())
    return values[len(values) // 2] if values else 0


def _competitions_meeting_threshold(
    by_competition: dict[int, list[dict]],
    threshold: int = MIN_LEAGUE_HISTORICAL_MATCHES,
    min_median_matches_per_team: int = MIN_MEDIAN_MATCHES_PER_TEAM,
) -> set[int]:
    return {
        competition_id
        for competition_id, rows in by_competition.items()
        if len(rows) >= threshold
        and _median_matches_per_team(rows) >= min_median_matches_per_team
    }


class ModelRunner:
    def __init__(self, db: AsyncSession, model_version: str = CURRENT_MODEL_VERSION) -> None:
        self.db = db
        self.requested_model_version = model_version
        self.model_version = model_version
        self._poisson_model: PoissonDixonColes | None = None
        self._poisson_models: dict[int, PoissonDixonColes] = {}
        self._poisson_competitions_fitted: dict[int, int] = {}
        self._zinb_model: ZINBModel | None = None
        self._model_run: ModelRun | None = None
        self._sparse_predictions = 0
        self._learning_profiles: dict[str, object] = {}
        self._learning_config = None
        self._bayesian_posteriors: dict[int, dict[str, float]] = {}

    async def run(self, target_date: date | None = None) -> dict:
        td = target_date or cat_today()
        if td < cat_today():
            raise ValueError("Past-date scoring is not supported; use the isolated historical replay research path")
        from app.services.model_learning import resolve_active_learning_config

        self._learning_config = await resolve_active_learning_config(
            self.db, self.requested_model_version, td
        )
        if self._learning_config:
            self.model_version = self._learning_config.challenger_version
            self._learning_profiles = self._learning_config.profiles
        learning_snapshot = (
            {
                "status": "promoted",
                "promotion_id": self._learning_config.promotion_id,
                "learning_run_id": self._learning_config.learning_run_id,
                "base_model_version": self._learning_config.base_model_version,
                "challenger_version": self._learning_config.challenger_version,
                "market_profiles": {
                    market: {
                        "profile_id": profile.id,
                        "weights": profile.weights,
                        "calibrator": profile.calibrator,
                    }
                    for market, profile in self._learning_profiles.items()
                },
            }
            if self._learning_config
            else {
                "status": "default",
                "requested_model_version": self.requested_model_version,
            }
        )
        self._model_run = ModelRun(
            target_date=td,
            model_version=self.model_version,
            status=RunStatus.RUNNING,
            config_snapshot={
                "ensemble_weights": DEFAULT_WEIGHTS,
                "q_score_weights": Q_SCORE_WEIGHTS,
                "minimum_active_models": MINIMUM_ACTIVE_MODELS,
                "target_markets": [market for market, _ in TARGET_MARKETS],
                "missing_component_policy": "zero_with_reason_code",
                "learning": learning_snapshot,
            },
        )
        self.db.add(self._model_run)
        await self.db.flush()

        matches = await self._load_matches(td)
        self._model_run.input_count = len(matches)

        if not matches:
            logger.warning("No eligible matches found for %s", td)
            self._model_run.status = RunStatus.COMPLETED
            self._model_run.completed_at = datetime.now(timezone.utc)
            await self.db.flush()
            from app.services.recommendation_ledger import capture_strongest_snapshots
            strongest_count = await capture_strongest_snapshots(self.db, self._model_run.id)
            return {
                "predictions": 0,
                "matches": 0,
                "model_run_id": self._model_run.id,
                "model_version": self.model_version,
                "strongest_snapshots": strongest_count,
            }

        # Fit Poisson-DC on historical data — CPU-bound, run in thread pool.
        # The pooled global fit doubles as the fallback for thin competitions.
        historical = await self._load_historical_matches()
        # Fit the current representation from pre-run results rather than
        # consuming unversioned legacy team parameters from the database.
        from app.services.models.bayesian import _run_analytical, run_mcmc_update
        if settings.bayesian_estimation_method == "advi":
            _, self._bayesian_posteriors = await asyncio.to_thread(run_mcmc_update, historical)
        else:
            self._bayesian_posteriors = await asyncio.to_thread(_run_analytical, historical)
        if len(historical) >= 100:
            self._poisson_model = PoissonDixonColes()
            await asyncio.to_thread(self._poisson_model.fit, historical)
            logger.info("Poisson-DC (global pool) fitted on %d historical matches", len(historical))

            by_competition = _group_by_competition(historical)
            qualifying = _competitions_meeting_threshold(by_competition)
            league_models = {
                competition_id: PoissonDixonColes() for competition_id in qualifying
            }
            await asyncio.gather(*(
                asyncio.to_thread(model.fit, by_competition[competition_id])
                for competition_id, model in league_models.items()
            ))
            self._poisson_models = league_models
            self._poisson_competitions_fitted = {
                competition_id: len(by_competition[competition_id])
                for competition_id in league_models
            }
            for competition_id, match_count in self._poisson_competitions_fitted.items():
                logger.info(
                    "Poisson-DC (competition %d) fitted on %d matches",
                    competition_id, match_count,
                )
        else:
            logger.warning("Insufficient historical data (%d matches) for Poisson-DC", len(historical))

        total_goals = [int(row["home_goals"] + row["away_goals"]) for row in historical]
        if should_use_zinb(total_goals):
            self._zinb_model = ZINBModel()
            await asyncio.to_thread(self._zinb_model.fit, total_goals)
            logger.info("ZINB activated: league goal variance-to-mean criterion passed")
        else:
            logger.info("ZINB inactive: variance-to-mean criterion not met")

        total = 0
        for match in matches:
            count = await self._score_match(match)
            total += count

        logger.info("Model run complete: %d predictions for %s", total, td)
        self._model_run.output_count = total
        self._model_run.status = RunStatus.COMPLETED
        self._model_run.completed_at = datetime.now(timezone.utc)
        snapshot = dict(self._model_run.config_snapshot)
        snapshot["zinb_activated"] = self._zinb_model is not None
        snapshot["sparse_prediction_count"] = self._sparse_predictions
        snapshot["poisson_fitting"] = {
            "min_league_historical_matches": MIN_LEAGUE_HISTORICAL_MATCHES,
            "global_pool_fitted": self._poisson_model is not None,
            "competitions_fitted": self._poisson_competitions_fitted,
        }
        self._model_run.config_snapshot = snapshot
        await self.db.flush()
        from app.services.recommendation_ledger import capture_strongest_snapshots
        strongest_count = await capture_strongest_snapshots(self.db, self._model_run.id)
        return {
            "predictions": total,
            "matches": len(matches),
            "model_run_id": self._model_run.id,
            "model_version": self.model_version,
            "zinb_activated": self._zinb_model is not None,
            "sparse_predictions": self._sparse_predictions,
            "strongest_snapshots": strongest_count,
        }

    async def _load_matches(self, target_date: date) -> list[Match]:
        result = await self.db.execute(
            select(Match)
            .where(
                Match.kickoff_at >= cat_day_bounds_utc(target_date)[0],
                Match.kickoff_at < cat_day_bounds_utc(target_date)[1],
                Match.status == MatchStatus.SCHEDULED,
                Match.kickoff_at > datetime.now(timezone.utc),
                Match.excluded_from_models == False,
            )
        )
        return result.scalars().all()

    async def _load_historical_matches(self) -> list[dict]:
        """Load completed matches for Poisson fitting."""
        from app.models import MatchStatus
        result = await self.db.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.FINISHED,
                Match.kickoff_at < datetime.now(timezone.utc),
                Match.home_goals != None,
                Match.away_goals != None,
            )
            .order_by(Match.kickoff_at.desc())
            .limit(2000)
        )
        matches = result.scalars().all()
        return [
            {
                "competition_id": m.competition_id,
                "home_team_id": m.home_team_id,
                "away_team_id": m.away_team_id,
                "home_goals": m.home_goals,
                "away_goals": m.away_goals,
            }
            for m in matches
        ]

    async def _score_match(self, match: Match) -> int:
        """Generate predictions for all target markets for a single match."""
        home_team = await self.db.get(Team, match.home_team_id)
        away_team = await self.db.get(Team, match.away_team_id)
        competition = await self.db.get(Competition, match.competition_id)
        if not home_team or not away_team or not competition:
            return 0

        # Best odds per market from DB
        odds_map = await self._load_odds(match.id)
        # Pre-load form scores for both teams (reused across all markets)
        home_form = await self._team_form(match.id, match.home_team_id)
        away_form = await self._team_form(match.id, match.away_team_id)
        xg_source: str | None = None
        expected_goals: tuple[float, float] | None = None
        # Match xG describes the match's observed shots, not a pre-match
        # forecast. Never consume it as a feature for that same fixture.
        from app.services.model_preparation import expected_goals_proxy

        expected_goals = expected_goals_proxy(home_team, away_team)
        if expected_goals is not None:
            xg_source = "rolling_goal_derived_expected_goals_proxy"

        count = 0
        for market_key, market_label in TARGET_MARKETS:
            prob_inputs = ModelInputs()

            # Poisson-DC
            poisson_model = self._poisson_models.get(match.competition_id) or self._poisson_model
            if poisson_model:
                prob = poisson_model.predict(match.home_team_id, match.away_team_id, market_key)
                prob_inputs.poisson_prob = prob

            # ZINB is intentionally conditional and currently contributes only
            # to total-goals markets, which are supported by its fitted PMF.
            if self._zinb_model and market_key.startswith(("over_", "under_")):
                try:
                    threshold = float(market_key.split("_", 1)[1])
                    over_probability = self._zinb_model.predict_over(threshold)
                    prob_inputs.zinb_prob = (
                        1.0 - over_probability
                        if market_key.startswith("under_")
                        else over_probability
                    )
                except (TypeError, ValueError):
                    pass

            # Elo (1X2 markets only)
            if market_key in ("home_win", "draw", "away_win"):
                elo_probs = elo_to_win_probabilities(
                    home_team.elo_rating,
                    away_team.elo_rating,
                    competition.home_advantage_elo,
                )
                prob_inputs.elo_prob = elo_probs.get(market_key)

            # Bayesian parameters belong to this run's pre-match fit; legacy
            # mutable team estimates do not identify their representation.
            home_posterior = self._bayesian_posteriors.get(match.home_team_id)
            away_posterior = self._bayesian_posteriors.get(match.away_team_id)
            if home_posterior is not None and away_posterior is not None:
                from app.services.models.bayesian import predict_market_from_posteriors
                try:
                    # home_advantage is a multiplicative factor ~1.1–1.3 in Poisson space
                    home_adv_factor = 1.0 + (competition.home_advantage_elo / 1500.0)
                    prob_inputs.bayes_prob = predict_market_from_posteriors(
                        home_posterior["attack_mean"],
                        home_posterior["defense_mean"],
                        away_posterior["attack_mean"],
                        away_posterior["defense_mean"],
                        home_adv_factor,
                        market_key,
                    )
                except Exception:
                    pass

            # xG model
            if expected_goals is not None:
                try:
                    prob_inputs.xg_prob = xg_market_probability(
                        expected_goals[0], expected_goals[1], market_key  # type: ignore[arg-type]
                    )
                except Exception:
                    pass

            # Need at least one model probability
            active_probs = [p for p in [
                prob_inputs.poisson_prob, prob_inputs.zinb_prob,
                prob_inputs.bayes_prob, prob_inputs.elo_prob, prob_inputs.xg_prob
            ] if p is not None]
            if not active_probs:
                continue

            ensemble = compute_ensemble(prob_inputs)
            learning_profile = self._learning_profiles.get(market_key)
            if learning_profile:
                from app.services.model_learning import apply_probability_calibrator

                ensemble = compute_ensemble(prob_inputs, weights=learning_profile.weights)
                raw_ensemble_probability = ensemble.ensemble_probability
                ensemble.ensemble_probability = apply_probability_calibrator(
                    raw_ensemble_probability, learning_profile.calibrator
                )
            else:
                raw_ensemble_probability = ensemble.ensemble_probability
            minimum_models_met = len(ensemble.active_models) >= MINIMUM_ACTIVE_MODELS
            if not minimum_models_met:
                self._sparse_predictions += 1

            # Market odds bundle (best odds + consensus across bookmakers)
            bundle = odds_map.get(market_key)
            # None = no bookmaker odds for this market; 0.0 would falsely imply odds of ∞
            best_odds = bundle.best.decimal_odds if bundle else None
            implied_prob = bundle.avg_implied if bundle else None

            source_odds_at = bundle.best.fetched_at if bundle else None
            age_hours: float | None = None
            if source_odds_at:
                now = datetime.now(timezone.utc)
                fetched_at = source_odds_at
                if fetched_at.tzinfo is None:
                    fetched_at = fetched_at.replace(tzinfo=timezone.utc)
                age_hours = max(0.0, (now - fetched_at).total_seconds() / 3600.0)
            quality_factor = 1.0
            if bundle and bundle.best.is_fallback:
                quality_factor *= 0.85
            if age_hours is not None and age_hours > 2:
                quality_factor *= 0.80

            q_inputs = QScoreInputs(
                implied_probability=implied_prob,
                best_odds=best_odds,
                form_score=self._weighted_form(home_form, away_form, market_key),
                market_consensus=_compute_market_consensus(bundle),
                odds_stability=_compute_stability(bundle),
                team_news_impact=_team_news_impact(match),
                league_reliability=competition.reliability_score,
                data_quality=(match.data_quality_score / 100.0) * quality_factor,
            )
            q_result = compute_q_score(ensemble, q_inputs)

            component_status = dict(q_result.component_status)
            component_status["xg_input"] = xg_source or "missing"
            component_status["model_set"] = (
                "available"
                if minimum_models_met
                else f"insufficient_models:{len(ensemble.active_models)}/{MINIMUM_ACTIVE_MODELS}"
            )

            if source_odds_at:
                component_status["source_odds"] = "stale" if age_hours > 2 else "fresh"
            else:
                age_hours = None
                component_status["source_odds"] = "missing"

            prediction = Prediction(
                model_run_id=self._model_run.id if self._model_run else None,
                match_id=match.id,
                market=market_key,
                selection=market_label,
                model_version=self.model_version,
                poisson_prob=prob_inputs.poisson_prob,
                zinb_prob=prob_inputs.zinb_prob,
                bayes_prob=prob_inputs.bayes_prob,
                elo_prob=prob_inputs.elo_prob,
                xg_prob=prob_inputs.xg_prob,
                model_probability=ensemble.ensemble_probability,
                raw_ensemble_probability=raw_ensemble_probability,
                learning_profile_id=learning_profile.id if learning_profile else None,
                model_agreement=ensemble.model_agreement,
                edge=q_result.edge,
                expected_value=q_result.expected_value,
                q_score=q_result.q_score,
                q_grade=q_result.q_grade,
                q_model_probability=q_result.components.get("model_probability"),
                q_value_edge=q_result.components.get("value_edge"),
                q_xg_model=q_result.components.get("xg_model"),
                q_recent_form=q_result.components.get("recent_form"),
                q_market_consensus=q_result.components.get("market_consensus"),
                q_odds_stability=q_result.components.get("odds_stability"),
                q_team_news=q_result.components.get("team_news"),
                q_league_reliability=q_result.components.get("league_reliability"),
                q_data_quality=q_result.components.get("data_quality"),
                q_component_weights=q_result.component_weights,
                q_component_status=component_status,
                active_models=ensemble.active_models,
                data_quality_snapshot={
                    "score": match.data_quality_score,
                    "excluded": match.excluded_from_models,
                    "source_odds_age_hours": age_hours,
                    "source_odds_type": bundle.best.source_type if bundle else None,
                    "fallback_odds": bundle.best.is_fallback if bundle else False,
                    "xg_input": xg_source,
                },
                source_odds_id=bundle.best.id if bundle else None,
                source_odds_at=source_odds_at,
                source_decimal_odds=bundle.best.decimal_odds if bundle else None,
                source_implied_probability=implied_prob,
                source_odds_provenance={
                    "bookmaker": bundle.best.bookmaker if bundle else None,
                    "source_type": bundle.best.source_type if bundle else None,
                    "is_fallback": bundle.best.is_fallback if bundle else False,
                    "bookmaker_count": bundle.bookmaker_count if bundle else 0,
                },
            )
            self.db.add(prediction)
            count += 1

        await self.db.flush()
        return count

    async def _team_form(self, match_id: int, team_id: int) -> Optional[float]:
        """Return form score 0–1 for a team from their TeamStats row for this match."""
        _CHAR = {"W": 1.0, "D": 0.5, "L": 0.0}
        ts_result = await self.db.execute(
            select(TeamStats).where(
                TeamStats.match_id == match_id,
                TeamStats.team_id == team_id,
            )
        )
        ts = ts_result.scalar_one_or_none()
        if ts and ts.form_last_5:
            vals = [_CHAR.get(c, 0.5) for c in ts.form_last_5]
            return sum(vals) / len(vals)
        return None

    def _weighted_form(
        self, home_f: Optional[float], away_f: Optional[float], market_key: str
    ) -> Optional[float]:
        """Combine home/away form with market-appropriate weighting."""
        if home_f is None and away_f is None:
            return None
        if home_f is None:
            return away_f  # type: ignore[return-value]
        if away_f is None:
            return home_f
        # Outcome-oriented selections benefit from the selected side's form
        # and from weakness in the opposition.  Treating both teams' good form
        # as positive (the previous behaviour) incorrectly boosted a home pick
        # when the away team was also in strong form.
        if market_key in {"away_win", "double_chance_x2", "dnb_away"}:
            return 0.3 * (1.0 - home_f) + 0.7 * away_f
        if market_key in {"home_win", "double_chance_1x", "dnb_home"}:
            return 0.7 * home_f + 0.3 * (1.0 - away_f)
        return 0.5 * home_f + 0.5 * away_f

    async def _load_odds(self, match_id: int) -> dict[str, "_OddsBundle"]:
        """Load all bookmaker odds per market; return bundled stats per market."""
        result = await self.db.execute(
            select(Odds).where(Odds.match_id == match_id)
        )
        all_odds = result.scalars().all()

        # Group by market
        by_market: dict[str, list[Odds]] = {}
        for o in all_odds:
            key = o.market.lower().replace(" ", "_")
            by_market.setdefault(key, []).append(o)

        bundles: dict[str, "_OddsBundle"] = {}
        for market, entries in by_market.items():
            best = max(entries, key=lambda x: x.decimal_odds)
            all_implied = [e.implied_probability for e in entries]
            avg_implied = sum(all_implied) / len(all_implied)
            # Consensus: how tightly bookmakers agree (low std = high consensus)
            import statistics
            if len(all_implied) > 1:
                std = statistics.stdev(all_implied)
                consensus = max(0.0, 1.0 - std / avg_implied) if avg_implied > 0 else 0.5
            else:
                consensus = 0.6  # single bookmaker — moderate confidence
            bundles[market] = _OddsBundle(
                best=best,
                all_odds=entries,
                consensus=consensus,
                bookmaker_count=len(entries),
                avg_implied=avg_implied,
            )

        return bundles


class _OddsBundle:
    """Aggregated odds data for a single market across all bookmakers."""
    def __init__(self, best: Odds, all_odds: list[Odds], consensus: float,
                 bookmaker_count: int, avg_implied: float) -> None:
        self.best = best
        self.all_odds = all_odds
        self.consensus = consensus
        self.bookmaker_count = bookmaker_count
        self.avg_implied = avg_implied


def _compute_market_consensus(bundle: "_OddsBundle | None") -> Optional[float]:
    if bundle is None:
        return None
    # Scale: 1 bookmaker → 0.6, 3+ bookmakers + tight spread → up to 1.0
    base = bundle.consensus
    count_bonus = min(0.2, (bundle.bookmaker_count - 1) * 0.05)
    return min(1.0, base + count_bonus)


def _compute_stability(bundle: "_OddsBundle | None") -> Optional[float]:
    """Odds stability score — spec §27.1. Uses movement if available."""
    if bundle is None:
        return None
    odds_entry = bundle.best
    if odds_entry.movement is None:
        return None
    movement = abs(odds_entry.movement)
    return max(0.0, 1.0 - movement / odds_entry.decimal_odds) if odds_entry.decimal_odds > 0 else 0.5


def _team_news_impact(match: Match) -> Optional[float]:
    """1.0 = no news / no key absences; lower when key players missing."""
    if not match.team_news:
        return None
    unique_absences: set[tuple] = set()
    for team_name, entries in match.team_news.items():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            unique_absences.add(
                (
                    team_name,
                    entry.get("name"),
                    entry.get("reason"),
                    entry.get("type"),
                )
            )
    total_absences = len(unique_absences)
    return max(0.3, 1.0 - total_absences * 0.05)
