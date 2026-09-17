"""Pinpoint the UEFA Champions League implied-goals anomaly (avg 6.25 total
goals across 28 test matches, vs ~2.7-3.3 everywhere else).

Prints every UCL test match's implied total goals (sorted descending) plus
each team's fitted attack/defense parameters, to show whether this is one
or two saturated-parameter outlier teams dragging the average, or a
systemic issue with the competition's fit.
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
UCL_API_FOOTBALL_ID = 2


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
        ucl_comp = next((c for c in comps.values() if c.api_football_id == UCL_API_FOOTBALL_ID), None)
        if not ucl_comp:
            print("UEFA Champions League competition row not found")
            return

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
        by_comp: dict[int, list[dict]] = defaultdict(list)
        for row in historical:
            by_comp[row["competition_id"]].append(row)

        ucl_rows = by_comp.get(ucl_comp.id, [])
        print(f"UCL training matches: {len(ucl_rows)}")
        ucl_team_match_count: dict[int, int] = defaultdict(int)
        for row in ucl_rows:
            ucl_team_match_count[row["home_team_id"]] += 1
            ucl_team_match_count[row["away_team_id"]] += 1
        print(f"UCL distinct teams in training: {len(ucl_team_match_count)}")
        counts = sorted(ucl_team_match_count.values())
        print(f"Matches-per-team in UCL fit: min={counts[0]} median={counts[len(counts)//2]} max={counts[-1]}")

        model = PoissonDixonColes()
        model.fit(ucl_rows)
        print(f"UCL fit: home_advantage={model.home_advantage:.3f}  rho={model.rho:.4f}")

        # Flag teams whose fitted attack/defense sit near the optimizer bounds
        # (bounds were exp(-2.5)=0.082 to exp(2.5)=12.18 in poisson_dc.py).
        extreme = []
        for tid, params in model.team_params.items():
            if params.attack > 5.0 or params.defense > 5.0 or params.attack < 0.2 or params.defense < 0.2:
                name = teams[tid].name if tid in teams else f"id={tid}"
                extreme.append((name, tid, params.attack, params.defense, ucl_team_match_count.get(tid, 0)))
        extreme.sort(key=lambda r: max(r[2], r[3]), reverse=True)
        print(f"\nTeams with extreme fitted attack/defense (>5.0 or <0.2, bounds are 0.082-12.18):")
        for name, tid, attack, defense, n_matches in extreme[:20]:
            print(f"  {name[:35]:35s}  attack={attack:7.2f}  defense={defense:7.2f}  ucl_training_matches={n_matches}")

        print(f"\n--- Every UCL test match, sorted by implied total goals (descending) ---")
        results = []
        for match in test_rows:
            if match.competition_id != ucl_comp.id:
                continue
            tp_home = model.team_params.get(match.home_team_id)
            tp_away = model.team_params.get(match.away_team_id)
            home_name = teams[match.home_team_id].name if match.home_team_id in teams else "?"
            away_name = teams[match.away_team_id].name if match.away_team_id in teams else "?"
            if not tp_home or not tp_away:
                results.append((f"{home_name} vs {away_name}", None, match.home_goals, match.away_goals, "NO PARAMS", None, None))
                continue
            lam = tp_home.attack * tp_away.defense * model.home_advantage
            mu = tp_away.attack * tp_home.defense
            results.append((
                f"{home_name} vs {away_name}", lam + mu, match.home_goals, match.away_goals,
                f"atk={tp_home.attack:.2f}/def={tp_home.defense:.2f}",
                f"atk={tp_away.attack:.2f}/def={tp_away.defense:.2f}",
                ucl_team_match_count.get(match.home_team_id, 0),
            ))

        results.sort(key=lambda r: (r[1] if r[1] is not None else -1), reverse=True)
        for name, implied, hg, ag, home_params, away_params, home_n in results:
            if implied is None:
                print(f"  {name[:45]:45s}  actual={hg}-{ag}  {home_params}")
            else:
                print(f"  {name[:45]:45s}  implied_total={implied:6.2f}  actual={hg}-{ag}  home[{home_params}]  away[{away_params}]  home_ucl_train_n={home_n}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--test-start", type=date.fromisoformat, required=True)
    parser.add_argument("--test-end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.test_start, args.test_end))
