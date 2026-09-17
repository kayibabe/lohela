"""No-lookahead model-accuracy backtest (single train/test split).

Historical bookmaker odds are not available for this window (API-Football
only serves live/near-term odds; see chat record 2026-09-14), so this
evaluates raw model calibration and pick accuracy against real final
scores instead of ticket EV/ROI, which requires real market odds.

Fits every ensemble component (Poisson-DC, ZINB, Elo, Bayesian analytical,
xG-proxy) on matches strictly before --test-start, then scores every
finished match in [--test-start, --test-end) with that frozen fit — the
test window never influences the fit, so this is a legitimate walk-forward
holdout, just a single split rather than a fresh daily refit (which the
live pipeline does, but which would take ~44x longer here for a fixed
model version and is not needed to answer "is the model calibrated").

Usage:
  python scripts/backtest_accuracy.py --test-start 2026-08-01 --test-end 2026-09-14
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Match, MatchStatus, Competition, Team
from app.services.models import (
    PoissonDixonColes,
    ZINBModel,
    should_use_zinb,
    elo_to_win_probabilities,
    xg_market_probability,
    compute_ensemble,
    ModelInputs,
)
from app.services.models.bayesian import _run_analytical, predict_market_from_posteriors
from app.services.models.elo import update_elos

MARKETS = ["home_win", "draw", "away_win", "over_2.5", "under_2.5", "btts_yes", "btts_no"]
MIN_LEAGUE_HISTORICAL_MATCHES = 100


def _outcome(market: str, hg: int, ag: int) -> bool:
    if market == "home_win":
        return hg > ag
    if market == "draw":
        return hg == ag
    if market == "away_win":
        return ag > hg
    if market == "over_2.5":
        return (hg + ag) > 2.5
    if market == "under_2.5":
        return (hg + ag) < 2.5
    if market == "btts_yes":
        return hg > 0 and ag > 0
    if market == "btts_no":
        return hg == 0 or ag == 0
    raise ValueError(market)


async def main(test_start: date, test_end: date) -> None:
    cutoff = datetime.combine(test_start, datetime.min.time(), tzinfo=timezone.utc)
    end_cutoff = datetime.combine(test_end, datetime.min.time(), tzinfo=timezone.utc)

    async with AsyncSessionLocal() as db:
        train_rows = (await db.execute(
            select(Match).where(
                Match.status == MatchStatus.FINISHED,
                Match.kickoff_at < cutoff,
                Match.home_goals.is_not(None),
                Match.away_goals.is_not(None),
            ).order_by(Match.kickoff_at.asc())
        )).scalars().all()

        test_rows = (await db.execute(
            select(Match).where(
                Match.status == MatchStatus.FINISHED,
                Match.kickoff_at >= cutoff,
                Match.kickoff_at < end_cutoff,
                Match.home_goals.is_not(None),
                Match.away_goals.is_not(None),
            ).order_by(Match.kickoff_at.asc())
        )).scalars().all()

        teams = {t.id: t for t in (await db.execute(select(Team))).scalars().all()}
        comps = {c.id: c for c in (await db.execute(select(Competition))).scalars().all()}

        print(f"Training matches (strictly before {test_start}): {len(train_rows)}")
        print(f"Test matches [{test_start}, {test_end}): {len(test_rows)}")

        historical = [
            {
                "competition_id": m.competition_id,
                "home_team_id": m.home_team_id,
                "away_team_id": m.away_team_id,
                "home_goals": m.home_goals,
                "away_goals": m.away_goals,
            }
            for m in train_rows
        ]

        # --- Poisson-DC: global pool + per-competition (mirrors model_runner.py) ---
        poisson_global = PoissonDixonColes()
        poisson_global.fit(historical)
        by_comp: dict[int, list[dict]] = defaultdict(list)
        for row in historical:
            by_comp[row["competition_id"]].append(row)
        poisson_by_comp: dict[int, PoissonDixonColes] = {}
        for cid, rows in by_comp.items():
            if len(rows) >= MIN_LEAGUE_HISTORICAL_MATCHES:
                model = PoissonDixonColes()
                model.fit(rows)
                poisson_by_comp[cid] = model
        print(f"Poisson-DC: global pool + {len(poisson_by_comp)} per-competition fits")

        # --- ZINB (total-goals markets only) ---
        total_goals = [m["home_goals"] + m["away_goals"] for m in historical]
        zinb = None
        if should_use_zinb(total_goals):
            zinb = ZINBModel()
            zinb.fit(total_goals)
            print("ZINB: activated")
        else:
            print("ZINB: inactive (variance-to-mean criterion not met)")

        # --- Elo: replay training matches chronologically from 1500 ---
        elo: dict[int, float] = defaultdict(lambda: 1500.0)
        for m in train_rows:
            comp = comps.get(m.competition_id)
            home_adv = comp.home_advantage_elo if comp else 75.0
            result = update_elos(
                elo_home=elo[m.home_team_id], elo_away=elo[m.away_team_id],
                home_goals=m.home_goals, away_goals=m.away_goals,
                k_factor=20, home_advantage_elo=home_adv,
            )
            elo[m.home_team_id] = result.home_new_elo
            elo[m.away_team_id] = result.away_new_elo
        print(f"Elo: replayed {len(train_rows)} matches for {len(elo)} teams")

        # --- Bayesian analytical posteriors (same function model_preparation.py uses) ---
        _, posteriors = "analytical", _run_analytical(historical)
        print(f"Bayesian (analytical): posteriors for {len(posteriors)} teams")

        # --- Rolling goal-derived xG proxy per team (last 20 matches, training-only) ---
        scored: dict[int, list[int]] = defaultdict(list)
        conceded: dict[int, list[int]] = defaultdict(list)
        for row in reversed(historical):
            h, a = row["home_team_id"], row["away_team_id"]
            if len(scored[h]) < 20:
                scored[h].append(row["home_goals"]); conceded[h].append(row["away_goals"])
            if len(scored[a]) < 20:
                scored[a].append(row["away_goals"]); conceded[a].append(row["home_goals"])
        avg_xg_for = {tid: max(0.2, sum(v) / len(v)) for tid, v in scored.items() if v}
        avg_xg_against = {tid: max(0.2, sum(v) / len(v)) for tid, v in conceded.items() if v}

        # --- Score every test match across MARKETS ---
        per_market = {m: {"n": 0, "hits": 0, "brier_sum": 0.0} for m in MARKETS}
        top_pick_1x2 = {"n": 0, "hits": 0}
        top_pick_ou = {"n": 0, "hits": 0}
        high_conf = {"n": 0, "hits": 0}  # ensemble prob >= 0.60 on the picked side
        skipped_no_teams = 0

        for match in test_rows:
            home = teams.get(match.home_team_id)
            away = teams.get(match.away_team_id)
            comp = comps.get(match.competition_id)
            if not home or not away or not comp:
                skipped_no_teams += 1
                continue

            poisson_model = poisson_by_comp.get(match.competition_id) or poisson_global
            home_adv_factor = 1.0 + (comp.home_advantage_elo / 1500.0)
            home_post = posteriors.get(match.home_team_id)
            away_post = posteriors.get(match.away_team_id)
            xg_home = avg_xg_for.get(match.home_team_id)
            xg_away_against = avg_xg_against.get(match.away_team_id)
            xg_away = avg_xg_for.get(match.away_team_id)
            xg_home_against = avg_xg_against.get(match.home_team_id)
            expected_goals = None
            if xg_home is not None and xg_away_against is not None and xg_away is not None and xg_home_against is not None:
                import math
                home_expected = math.sqrt(max(0.05, xg_home) * max(0.05, xg_away_against)) * 1.05
                away_expected = math.sqrt(max(0.05, xg_away) * max(0.05, xg_home_against))
                expected_goals = (min(4.5, max(0.2, home_expected)), min(4.5, max(0.2, away_expected)))

            match_probs: dict[str, float] = {}
            for market in MARKETS:
                inputs = ModelInputs()
                prob = poisson_model.predict(match.home_team_id, match.away_team_id, market)
                inputs.poisson_prob = prob
                if zinb and market.startswith(("over_", "under_")):
                    threshold = float(market.split("_", 1)[1])
                    over_p = zinb.predict_over(threshold)
                    inputs.zinb_prob = over_p if market.startswith("over_") else 1.0 - over_p
                if market in ("home_win", "draw", "away_win"):
                    inputs.elo_prob = elo_to_win_probabilities(
                        elo[match.home_team_id], elo[match.away_team_id], comp.home_advantage_elo
                    ).get(market)
                if home_post and away_post:
                    try:
                        inputs.bayes_prob = predict_market_from_posteriors(
                            home_post["attack_mean"], home_post["defense_mean"],
                            away_post["attack_mean"], away_post["defense_mean"],
                            home_adv_factor, market,
                        )
                    except Exception:
                        pass
                if expected_goals:
                    try:
                        inputs.xg_prob = xg_market_probability(expected_goals[0], expected_goals[1], market)
                    except Exception:
                        pass
                active = [p for p in [inputs.poisson_prob, inputs.zinb_prob, inputs.bayes_prob, inputs.elo_prob, inputs.xg_prob] if p is not None]
                if not active:
                    continue
                ensemble = compute_ensemble(inputs)
                match_probs[market] = ensemble.ensemble_probability

                actual = _outcome(market, match.home_goals, match.away_goals)
                per_market[market]["n"] += 1
                per_market[market]["hits"] += int(actual)
                per_market[market]["brier_sum"] += (ensemble.ensemble_probability - float(actual)) ** 2

            if all(k in match_probs for k in ("home_win", "draw", "away_win")):
                pick = max(("home_win", "draw", "away_win"), key=lambda k: match_probs[k])
                actual_pick = _outcome(pick, match.home_goals, match.away_goals)
                top_pick_1x2["n"] += 1
                top_pick_1x2["hits"] += int(actual_pick)
                if match_probs[pick] >= 0.60:
                    high_conf["n"] += 1
                    high_conf["hits"] += int(actual_pick)

            if "over_2.5" in match_probs and "under_2.5" in match_probs:
                pick = "over_2.5" if match_probs["over_2.5"] >= match_probs["under_2.5"] else "under_2.5"
                actual_pick = _outcome(pick, match.home_goals, match.away_goals)
                top_pick_ou["n"] += 1
                top_pick_ou["hits"] += int(actual_pick)

        print(f"\nSkipped (missing team/competition row): {skipped_no_teams}")
        print("\n--- Per-market calibration (no odds; raw model probability vs actual outcome) ---")
        for market in MARKETS:
            row = per_market[market]
            if row["n"] == 0:
                print(f"  {market:12s}  n=0")
                continue
            hit_rate = row["hits"] / row["n"]
            brier = row["brier_sum"] / row["n"]
            print(f"  {market:12s}  n={row['n']:5d}  actual_rate={hit_rate:6.1%}  brier={brier:.4f}  (0=perfect, 0.25=coin-flip-uninformative)")

        print("\n--- Top-pick accuracy ---")
        if top_pick_1x2["n"]:
            print(f"  1X2 (highest-probability side):   {top_pick_1x2['hits']}/{top_pick_1x2['n']} = {top_pick_1x2['hits']/top_pick_1x2['n']:.1%}")
        if top_pick_ou["n"]:
            print(f"  Over/Under 2.5 (higher side):      {top_pick_ou['hits']}/{top_pick_ou['n']} = {top_pick_ou['hits']/top_pick_ou['n']:.1%}")
        if high_conf["n"]:
            print(f"  1X2 picks with prob >= 60%:        {high_conf['hits']}/{high_conf['n']} = {high_conf['hits']/high_conf['n']:.1%}  (baseline if well-calibrated: ~60%+)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-start", type=date.fromisoformat, required=True)
    parser.add_argument("--test-end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.test_start, args.test_end))
