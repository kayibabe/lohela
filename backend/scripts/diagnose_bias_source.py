"""Localise the -0.13 goals/match under-prediction found by
diagnose_totals_calibration.py to a specific cause:

  A) Thin-league shrinkage: competitions with <100 training matches fall
     back to the GLOBAL pooled Poisson-DC fit (model_runner.py's own
     threshold). If the bias is concentrated there, it's a regularisation/
     shrinkage-toward-the-pooled-mean problem for under-fit leagues.
  B) Competition mix shift: some leagues score more freely than others
     (cup ties among mismatched sides, in particular). If the test window
     (Aug-Sep, early season + early cup rounds) draws disproportionately
     from higher-scoring competitions relative to their share of the
     training window, that alone would raise the *actual* test-period
     average without the model doing anything wrong per league.

Reports bias split by (global-pool vs per-competition fit) and bias +
match-share-by-competition side by side so the two hypotheses are visually
separable.
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
from app.services.models import PoissonDixonColes

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

        # --- Training-period actual scoring level per competition ---
        train_actual_by_comp: dict[int, list[int]] = defaultdict(list)
        for row in historical:
            train_actual_by_comp[row["competition_id"]].append(row["home_goals"] + row["away_goals"])

        # --- Test-period: bias by fit-type, bias by competition, match share ---
        bias_by_fit_type: dict[str, list[float]] = {"global_pool": [], "per_competition": []}
        test_actual_by_comp: dict[int, list[int]] = defaultdict(list)
        test_implied_by_comp: dict[int, list[float]] = defaultdict(list)
        skipped = 0

        for match in test_rows:
            model = poisson_by_comp.get(match.competition_id)
            fit_type = "per_competition" if model else "global_pool"
            model = model or poisson_global

            tp_home = model.team_params.get(match.home_team_id)
            tp_away = model.team_params.get(match.away_team_id)
            if not tp_home or not tp_away:
                skipped += 1
                continue

            lam = tp_home.attack * tp_away.defense * model.home_advantage
            mu = tp_away.attack * tp_home.defense
            implied = lam + mu
            actual = match.home_goals + match.away_goals

            bias_by_fit_type[fit_type].append(implied - actual)
            test_actual_by_comp[match.competition_id].append(actual)
            test_implied_by_comp[match.competition_id].append(implied)

        print(f"Skipped (no team params): {skipped}\n")

        print("--- Bias by fit type (global-pool fallback vs per-competition fit) ---")
        for fit_type, diffs in bias_by_fit_type.items():
            if not diffs:
                continue
            n = len(diffs)
            mean_bias = sum(diffs) / n
            print(f"  {fit_type:16s}  n={n:4d}  mean(implied - actual) = {mean_bias:+.3f}")

        print("\n--- Per-competition: training scoring level vs test scoring level vs match share ---")
        print(f"{'competition':30s} {'train_n':>8s} {'train_avg':>10s} {'test_n':>7s} {'test_avg':>9s} {'implied_avg':>12s} {'bias':>7s} {'fit':>16s}")
        total_train = len(historical)
        total_test = sum(len(v) for v in test_actual_by_comp.values())
        rows_out = []
        for cid, actuals in test_actual_by_comp.items():
            comp = comps.get(cid)
            name = comp.name if comp else f"id={cid}"
            train_n = len(train_actual_by_comp.get(cid, []))
            train_avg = sum(train_actual_by_comp[cid]) / train_n if train_n else float("nan")
            test_n = len(actuals)
            test_avg = sum(actuals) / test_n
            implied_avg = sum(test_implied_by_comp[cid]) / test_n
            bias = implied_avg - test_avg
            fit = "per_competition" if train_n >= MIN_LEAGUE_HISTORICAL_MATCHES else "global_pool"
            train_share = train_n / total_train if total_train else 0
            test_share = test_n / total_test if total_test else 0
            rows_out.append((cid, name, train_n, train_avg, test_n, test_avg, implied_avg, bias, fit, train_share, test_share))

        rows_out.sort(key=lambda r: r[7])  # sort by bias ascending (most negative = most under-predicted)
        for cid, name, train_n, train_avg, test_n, test_avg, implied_avg, bias, fit, train_share, test_share in rows_out:
            print(f"{name[:30]:30s} {train_n:8d} {train_avg:10.2f} {test_n:7d} {test_avg:9.2f} {implied_avg:12.2f} {bias:+7.2f} {fit:>16s}")

        print("\n--- Match-share shift (test window vs training window) ---")
        print(f"{'competition':30s} {'train_share':>12s} {'test_share':>11s} {'shift':>8s} {'test_avg_goals':>15s}")
        for cid, name, train_n, train_avg, test_n, test_avg, implied_avg, bias, fit, train_share, test_share in sorted(rows_out, key=lambda r: r[10] - r[9], reverse=True):
            shift = test_share - train_share
            print(f"{name[:30]:30s} {train_share:12.1%} {test_share:11.1%} {shift:+8.1%} {test_avg:15.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-start", type=date.fromisoformat, required=True)
    parser.add_argument("--test-end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.test_start, args.test_end))
