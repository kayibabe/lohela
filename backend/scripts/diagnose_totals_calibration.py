"""Diagnose why over/under 2.5 and BTTS scored worse than a coin flip in
backtest_accuracy.py (Brier ~0.259 vs 0.25 uninformative baseline).

Checks three concrete hypotheses:
  1. Systematic bias: does the fitted Poisson-DC model expect more/fewer
     goals on average than actually occurred in the test window?
  2. False ensemble confidence: are poisson/bayes/xg — the only three
     models active for totals markets, since Elo only covers 1X2 and ZINB
     is inactive here — actually independent signals, or just three
     parameterisations of the same Poisson functional form (so the
     ensemble looks more confident than it has any right to)?
  3. Calibration curve: within each predicted-probability decile for
     over_2.5, what was the real over rate? Reveals whether error is a
     directional bias (all buckets shifted one way) or just noise.

Same no-lookahead train/test split as backtest_accuracy.py — reuses none
of its state, so it re-runs the (slow) fits independently.
"""

from __future__ import annotations

import argparse
import asyncio
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import Match, MatchStatus, Competition, Team
from app.services.models import PoissonDixonColes, ZINBModel, should_use_zinb
from app.services.models.bayesian import _run_analytical, predict_market_from_posteriors

MIN_LEAGUE_HISTORICAL_MATCHES = 100


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

        total_goals_train = [m["home_goals"] + m["away_goals"] for m in historical]
        zinb_active = should_use_zinb(total_goals_train)
        print(f"ZINB active: {zinb_active}")
        print(f"Global fit: home_advantage={poisson_global.home_advantage:.3f}  rho={poisson_global.rho:.4f}")
        print(f"Per-competition fits: {len(poisson_by_comp)}")

        _, posteriors = "analytical", _run_analytical(historical)

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

        # --- Hypothesis 1: systematic goal-expectation bias ---
        implied_totals: list[float] = []
        actual_totals: list[float] = []

        # --- Hypothesis 2: are poisson/bayes/xg independent for totals? ---
        poisson_probs: list[float] = []
        bayes_probs: list[float] = []
        xg_probs: list[float] = []
        active_model_counts: dict[int, int] = defaultdict(int)

        # --- Hypothesis 3: calibration curve for over_2.5 ---
        calib_bucket: dict[int, list[int]] = defaultdict(list)  # decile -> [actual outcomes]
        calib_pred_sum: dict[int, float] = defaultdict(float)

        skipped = 0
        for match in test_rows:
            home = teams.get(match.home_team_id)
            away = teams.get(match.away_team_id)
            comp = comps.get(match.competition_id)
            if not home or not away or not comp:
                skipped += 1
                continue

            model = poisson_by_comp.get(match.competition_id) or poisson_global
            tp_home = model.team_params.get(match.home_team_id)
            tp_away = model.team_params.get(match.away_team_id)
            if tp_home and tp_away:
                lam = tp_home.attack * tp_away.defense * model.home_advantage
                mu = tp_away.attack * tp_home.defense
                implied_totals.append(lam + mu)
                actual_totals.append(match.home_goals + match.away_goals)

            p_poisson = model.predict(match.home_team_id, match.away_team_id, "over_2.5")

            home_post = posteriors.get(match.home_team_id)
            away_post = posteriors.get(match.away_team_id)
            home_adv_factor = 1.0 + (comp.home_advantage_elo / 1500.0)
            p_bayes = None
            if home_post and away_post:
                try:
                    p_bayes = predict_market_from_posteriors(
                        home_post["attack_mean"], home_post["defense_mean"],
                        away_post["attack_mean"], away_post["defense_mean"],
                        home_adv_factor, "over_2.5",
                    )
                except Exception:
                    pass

            p_xg = None
            xg_home = avg_xg_for.get(match.home_team_id)
            xg_away_against = avg_xg_against.get(match.away_team_id)
            xg_away = avg_xg_for.get(match.away_team_id)
            xg_home_against = avg_xg_against.get(match.home_team_id)
            if xg_home is not None and xg_away_against is not None and xg_away is not None and xg_home_against is not None:
                from app.services.models import xg_market_probability
                home_expected = math.sqrt(max(0.05, xg_home) * max(0.05, xg_away_against)) * 1.05
                away_expected = math.sqrt(max(0.05, xg_away) * max(0.05, xg_home_against))
                home_expected = min(4.5, max(0.2, home_expected))
                away_expected = min(4.5, max(0.2, away_expected))
                try:
                    p_xg = xg_market_probability(home_expected, away_expected, "over_2.5")
                except Exception:
                    pass

            n_active = sum(p is not None for p in (p_poisson, p_bayes, p_xg))
            active_model_counts[n_active] += 1

            if p_poisson is not None and p_bayes is not None and p_xg is not None:
                poisson_probs.append(p_poisson)
                bayes_probs.append(p_bayes)
                xg_probs.append(p_xg)

            if p_poisson is not None:
                weights_sum = 0.30 + (0.20 if p_bayes is not None else 0) + (0.20 if p_xg is not None else 0)
                ensemble_p = (
                    0.30 * p_poisson
                    + (0.20 * p_bayes if p_bayes is not None else 0)
                    + (0.20 * p_xg if p_xg is not None else 0)
                ) / weights_sum
                decile = min(9, int(ensemble_p * 10))
                actual = int((match.home_goals + match.away_goals) > 2.5)
                calib_bucket[decile].append(actual)
                calib_pred_sum[decile] += ensemble_p

        print(f"\nSkipped (missing team/competition): {skipped}")

        print("\n--- Hypothesis 1: goal-expectation bias (Poisson-DC implied total vs actual) ---")
        if implied_totals:
            mean_implied = sum(implied_totals) / len(implied_totals)
            mean_actual = sum(actual_totals) / len(actual_totals)
            print(f"  n={len(implied_totals)}")
            print(f"  Model's implied mean total goals:  {mean_implied:.3f}")
            print(f"  Actual mean total goals:            {mean_actual:.3f}")
            print(f"  Bias (implied - actual):            {mean_implied - mean_actual:+.3f}")

        print("\n--- Hypothesis 2: model independence for totals (Pearson correlation) ---")
        print(f"  Matches with all 3 models active: {len(poisson_probs)} / {len(test_rows)}")
        print(f"  Active-model-count distribution: {dict(sorted(active_model_counts.items()))}")
        if len(poisson_probs) > 10:
            import numpy as np
            pp, bp, xp = np.array(poisson_probs), np.array(bayes_probs), np.array(xg_probs)
            print(f"  corr(poisson, bayes) = {np.corrcoef(pp, bp)[0,1]:.3f}")
            print(f"  corr(poisson, xg)    = {np.corrcoef(pp, xp)[0,1]:.3f}")
            print(f"  corr(bayes, xg)      = {np.corrcoef(bp, xp)[0,1]:.3f}")
            print("  (all three route through the same Poisson/Dixon-Coles formula with")
            print("   different lambda/mu inputs — high correlation here means the ensemble's")
            print("   3 'models' are not independent evidence, just re-parameterisations.)")

        print("\n--- Hypothesis 3: over_2.5 calibration curve (predicted decile vs actual rate) ---")
        for decile in range(10):
            outcomes = calib_bucket.get(decile, [])
            if not outcomes:
                continue
            actual_rate = sum(outcomes) / len(outcomes)
            avg_pred = calib_pred_sum[decile] / len(outcomes)
            gap = actual_rate - avg_pred
            print(f"  predicted {decile*10:3d}-{decile*10+10:3d}%  n={len(outcomes):4d}  avg_pred={avg_pred:.1%}  actual={actual_rate:.1%}  gap={gap:+.1%}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-start", type=date.fromisoformat, required=True)
    parser.add_argument("--test-end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.test_start, args.test_end))
