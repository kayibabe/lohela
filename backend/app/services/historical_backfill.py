"""Point-in-time, research-only backfill for historical predictions.

This deliberately does not create ModelRun rows. Detached predictions cannot be
resolved by the public selection or ticket builders, while the learning loader
can consume them through their explicit ``as_of_at`` timestamp.

Elo is intentionally excluded from every backfilled market. ``teams.elo_rating``
is a single mutable current value with no history table, so there is no way to
recover what it was before a past fixture without leaking later results into
that fixture's "point-in-time" prediction. The live model runner uses Elo for
1X2 markets; a backfilled 1X2 prediction is therefore a poisson+bayes+xG-only
approximation of the live ensemble, not an exact replay of it.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import CURRENT_MODEL_VERSION, cat_day_bounds_utc
from app.models import Competition, Match, MatchStatus, Odds, Prediction
from app.services.models import (
    ModelInputs,
    PoissonDixonColes,
    QScoreInputs,
    ZINBModel,
    compute_ensemble,
    compute_q_score,
    xg_market_probability,
)
from app.services.models.bayesian import _run_analytical, predict_market_from_posteriors


TOTALS = ("under_2.5", "under_3.5", "under_4.5")
BINARY = ("home_win", "draw", "away_win", "btts_yes", "btts_no")
ALL_MARKETS = TOTALS + BINARY


def _normalise(value: str) -> str:
    return "".join(character for character in value.lower() if character.isalnum())


def _odds_for_match(odds: list[Odds], kickoff: datetime, markets: tuple[str, ...]) -> dict[str, Odds]:
    best: dict[str, Odds] = {}
    for quote in odds:
        fetched_at = quote.fetched_at
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        if fetched_at >= kickoff or quote.decimal_odds <= 1.0:
            continue
        text = _normalise(f"{quote.market}{quote.selection}")
        for market in markets:
            if _normalise(market) in text:
                if market not in best or quote.decimal_odds > best[market].decimal_odds:
                    best[market] = quote
    return best


def _rolling_expected_goals(
    history: dict[int, deque[tuple[int, int]]], home_id: int, away_id: int
) -> tuple[float, float] | None:
    home = history.get(home_id)
    away = history.get(away_id)
    if not home or not away:
        return None
    home_for = sum(row[0] for row in home) / len(home)
    home_against = sum(row[1] for row in home) / len(home)
    away_for = sum(row[0] for row in away) / len(away)
    away_against = sum(row[1] for row in away) / len(away)
    return (
        min(4.5, max(0.2, (max(0.05, home_for) * max(0.05, away_against)) ** 0.5 * 1.05)),
        min(4.5, max(0.2, (max(0.05, away_for) * max(0.05, home_against)) ** 0.5)),
    )


class HistoricalTotalsBackfill:
    """Replay totals and 1X2/BTTS forecasts chronologically without touching public tickets."""

    def __init__(self, db: AsyncSession, model_version: str = CURRENT_MODEL_VERSION) -> None:
        self.db = db
        self.model_version = model_version

    async def run(self, start: date, end: date, *, min_history: int = 100) -> dict:
        if end < start:
            raise ValueError("end must be on or after start")
        if min_history < 30:
            raise ValueError("min_history must be at least 30")

        result = await self.db.execute(
            select(Match)
            .where(
                Match.status == MatchStatus.FINISHED,
                Match.home_goals.is_not(None),
                Match.away_goals.is_not(None),
                Match.kickoff_at < cat_day_bounds_utc(end)[1],
                Match.excluded_from_models == False,
            )
            .order_by(Match.kickoff_at.asc(), Match.id.asc())
        )
        all_matches = list(result.scalars().all())
        target_start = cat_day_bounds_utc(start)[0]
        matches = [match for match in all_matches if match.kickoff_at >= target_start]
        if not all_matches:
            return {"status": "completed", "matches": 0, "predictions_written": 0, "skipped": {}}

        odds_result = await self.db.execute(
            select(Odds).where(Odds.match_id.in_([match.id for match in matches]))
        )
        odds_by_match: dict[int, list[Odds]] = defaultdict(list)
        for quote in odds_result.scalars().all():
            odds_by_match[quote.match_id].append(quote)

        competitions_result = await self.db.execute(
            select(Competition).where(
                Competition.id.in_({match.competition_id for match in matches})
            )
        )
        competitions = {c.id: c for c in competitions_result.scalars().all()}

        existing_result = await self.db.execute(
            select(Prediction.match_id, Prediction.market).where(
                Prediction.model_version == self.model_version,
                Prediction.match_id.in_([match.id for match in matches]),
                Prediction.source_odds_provenance["capture_source"].as_string() == "historical_backfill",
            )
        )
        existing = set(existing_result.all())

        history: list[dict] = []
        rolling: dict[int, deque[tuple[int, int]]] = defaultdict(lambda: deque(maxlen=20))
        skipped: dict[str, int] = defaultdict(int)
        written = 0
        current_day: date | None = None
        poisson: PoissonDixonColes | None = None
        zinb: ZINBModel | None = None
        bayes: dict[int, dict[str, float]] = {}
        day_as_of: datetime | None = None

        for match in all_matches:
            kickoff = match.kickoff_at
            if kickoff.tzinfo is None:
                kickoff = kickoff.replace(tzinfo=timezone.utc)
            if kickoff < target_start:
                history.append(self._history_row(match, kickoff))
                self._update_rolling(rolling, match)
                continue
            match_day = kickoff.date()
            if current_day != match_day:
                current_day = match_day
                # Mirror ModelRunner._load_historical_matches: cap the fitting
                # window at the most recent 2000 matches. Unbounded history
                # both diverges from what the live model actually saw and, at
                # full-dataset scale, exceeds the Poisson-DC optimizer's
                # function-evaluation budget (observed: "TOTAL NO. of f AND g
                # EVALUATIONS EXCEEDS LIMIT" once pooled history crossed a few
                # hundred teams).
                prior = [row for row in history if row["kickoff_at"] < kickoff][-2000:]
                if len(prior) < min_history:
                    poisson = None
                    zinb = None
                    bayes = {}
                    skipped["insufficient_prior_history"] += 1
                else:
                    poisson = await asyncio.to_thread(PoissonDixonColes().fit, prior)
                    zinb = await asyncio.to_thread(
                        self._fit_zinb,
                        [int(row["home_goals"] + row["away_goals"]) for row in prior],
                    )
                    bayes = await asyncio.to_thread(_run_analytical, prior)
                    day_as_of = max(row["kickoff_at"] for row in prior)

            if poisson is None:
                history.append(self._history_row(match, kickoff))
                self._update_rolling(rolling, match)
                continue
            expected = _rolling_expected_goals(rolling, match.home_team_id, match.away_team_id)
            if expected is None:
                skipped["missing_point_in_time_features"] += 1
                history.append(self._history_row(match, kickoff))
                self._update_rolling(rolling, match)
                continue
            odds = _odds_for_match(odds_by_match.get(match.id, []), kickoff, ALL_MARKETS)
            input_as_of = day_as_of
            if odds:
                input_as_of = max(input_as_of, max(self._aware(q.fetched_at) for q in odds.values()))
            if input_as_of >= kickoff:
                skipped["non_pre_kickoff_inputs"] += 1
                history.append(self._history_row(match, kickoff))
                self._update_rolling(rolling, match)
                continue

            competition = competitions.get(match.competition_id)
            home_posterior = bayes.get(match.home_team_id)
            away_posterior = bayes.get(match.away_team_id)

            for market in ALL_MARKETS:
                if (match.id, market) in existing:
                    skipped["already_backfilled"] += 1
                    continue
                poisson_probability = poisson.predict(match.home_team_id, match.away_team_id, market)
                if poisson_probability is None:
                    skipped["missing_poisson_parameters"] += 1
                    continue
                zinb_probability = None
                if market in TOTALS and zinb is not None:
                    threshold = float(market.rsplit("_", 1)[1])
                    zinb_probability = 1.0 - zinb.predict_over(threshold)
                bayes_probability = None
                if home_posterior is not None and away_posterior is not None and competition is not None:
                    home_adv_factor = 1.0 + (competition.home_advantage_elo / 1500.0)
                    try:
                        bayes_probability = predict_market_from_posteriors(
                            home_posterior["attack_mean"],
                            home_posterior["defense_mean"],
                            away_posterior["attack_mean"],
                            away_posterior["defense_mean"],
                            home_adv_factor,
                            market,
                        )
                    except Exception:
                        bayes_probability = None
                xg_probability = xg_market_probability(expected[0], expected[1], market)
                ensemble = compute_ensemble(ModelInputs(
                    poisson_prob=poisson_probability,
                    zinb_prob=zinb_probability,
                    bayes_prob=bayes_probability,
                    xg_prob=xg_probability,
                ))
                quote = odds.get(market)
                q_score = compute_q_score(
                    ensemble,
                    QScoreInputs(
                        implied_probability=(1.0 / quote.decimal_odds) if quote else None,
                        best_odds=quote.decimal_odds if quote else None,
                        data_quality=match.data_quality_score / 100.0,
                    ),
                )
                self.db.add(Prediction(
                    model_run_id=None,
                    match_id=match.id,
                    market=market,
                    selection=market,
                    model_version=self.model_version,
                    poisson_prob=poisson_probability,
                    zinb_prob=zinb_probability,
                    bayes_prob=bayes_probability,
                    elo_prob=None,
                    xg_prob=xg_probability,
                    model_probability=ensemble.ensemble_probability,
                    raw_ensemble_probability=ensemble.ensemble_probability,
                    model_agreement=ensemble.model_agreement,
                    edge=q_score.edge,
                    expected_value=q_score.expected_value,
                    q_score=q_score.q_score,
                    q_grade=q_score.q_grade,
                    q_model_probability=q_score.components.get("model_probability"),
                    q_value_edge=q_score.components.get("value_edge"),
                    q_xg_model=q_score.components.get("xg_model"),
                    q_data_quality=q_score.components.get("data_quality"),
                    q_component_weights=q_score.component_weights,
                    q_component_status={"capture_source": "historical_backfill", "point_in_time": "verified",
                                        "elo": "excluded_no_point_in_time_history"},
                    active_models=ensemble.active_models,
                    data_quality_snapshot={"historical_backfill": True, "as_of_at": input_as_of.isoformat()},
                    source_odds_id=quote.id if quote else None,
                    source_odds_at=self._aware(quote.fetched_at) if quote else None,
                    source_decimal_odds=quote.decimal_odds if quote else None,
                    source_implied_probability=quote.implied_probability if quote else None,
                    source_odds_provenance={
                        "capture_source": "historical_backfill",
                        "source_type": quote.source_type if quote else None,
                        "bookmaker": quote.bookmaker if quote else None,
                        "is_fallback": quote.is_fallback if quote else None,
                        "point_in_time": True,
                    },
                    as_of_at=input_as_of,
                ))
                existing.add((match.id, market))
                written += 1
            history.append(self._history_row(match, kickoff))
            self._update_rolling(rolling, match)

        await self.db.flush()
        return {
            "status": "completed",
            "matches": len(matches),
            "predictions_written": written,
            "skipped": dict(skipped),
            "model_version": self.model_version,
            "public_tickets_created": 0,
        }

    @staticmethod
    def _fit_zinb(goals: list[int]) -> ZINBModel:
        model = ZINBModel()
        model.fit(goals)
        return model

    @staticmethod
    def _aware(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _history_row(match: Match, kickoff: datetime) -> dict:
        return {
            "kickoff_at": kickoff,
            "home_team_id": match.home_team_id,
            "away_team_id": match.away_team_id,
            "home_goals": match.home_goals,
            "away_goals": match.away_goals,
        }

    @staticmethod
    def _update_rolling(rolling: dict[int, deque[tuple[int, int]]], match: Match) -> None:
        rolling[match.home_team_id].append((int(match.home_goals), int(match.away_goals)))
        rolling[match.away_team_id].append((int(match.away_goals), int(match.home_goals)))
