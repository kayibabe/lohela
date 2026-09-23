"""Backtest market ("honest") pricing against model pricing on settled days.

Usage (inside the backend container):
  python scripts/honest_pricing_backtest.py [path/to/source.json.gz]

With an archive path it reads the frozen production evidence archive
(diagnosis only — nothing is fitted or written); without, the local DB.

Reports
  1. leg-level calibration of the fair (de-vigged) probability by leg-odds
     band — the statistically meaningful part (hundreds of legs);
  2. per-day tickets rebuilt with the production builder code under each
     pricing mode, then settled — indicative only (a handful of tickets).
Odds-age gates are neutralised: they depend on wall-clock time at build.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
logging.disable(logging.CRITICAL)

from app.config import settings  # noqa: E402
from app.models import QGrade, TicketType  # noqa: E402
import app.services.accumulator_builder as ab  # noqa: E402


def outcome(market: str, h: int, a: int) -> float | None:
    t = h + a
    if market.startswith("over_"):
        return float(t > float(market[5:]))
    if market.startswith("under_"):
        return float(t < float(market[6:]))
    return {"btts_yes": float(h > 0 and a > 0), "btts_no": float(not (h > 0 and a > 0)),
            "home_win": float(h > a), "draw": float(h == a), "away_win": float(h < a),
            "double_chance_1x": float(h >= a), "double_chance_x2": float(h <= a),
            "double_chance_12": float(h != a)}.get(market)


def _dt(value):
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _grade(value) -> QGrade:
    """Stored by enum name (e.g. 'B_PLUS'); tolerate values too."""
    if isinstance(value, QGrade):
        return value
    if value in QGrade.__members__:
        return QGrade[value]
    try:
        return QGrade(value)
    except ValueError:
        return QGrade.C


def build_days(predictions, matches, runs):
    """Per CAT target date: legs from that date's latest completed run."""
    latest_run = {}
    for run in runs:
        if str(run.get("status", "")).upper().endswith("COMPLETED"):
            d = str(run["target_date"])[:10]
            if d not in latest_run or run["id"] > latest_run[d]:
                latest_run[d] = run["id"]
    by_run = defaultdict(list)
    for p in predictions:
        if not p.get("as_of_at"):
            by_run[p["model_run_id"]].append(p)
    days = {}
    for d, run_id in sorted(latest_run.items()):
        legs, results = [], {}
        for p in by_run.get(run_id, []):
            m = matches.get(p["match_id"])
            if not m or m.get("home_goals") is None or str(m.get("status", "")).upper() != "FINISHED":
                continue
            if m.get("excluded_from_models"):
                continue
            y = outcome(p["market"], m["home_goals"], m["away_goals"])
            if y is None or not p.get("source_decimal_odds") or p.get("model_probability") is None:
                continue
            leg = ab.Leg(
                p["id"], m["id"], m["home_team_id"], m["away_team_id"], "", "",
                str(m["competition_id"]), m["competition_id"], _dt(m["kickoff_at"]),
                p["market"], p["selection"], p["model_probability"], p.get("model_agreement") or 0.0,
                p["source_decimal_odds"], _dt(p.get("source_odds_at")) or datetime.now(timezone.utc),
                p.get("q_score") or 0.0, _grade(p.get("q_grade")),
                p.get("edge"), p.get("expected_value"), list(p.get("active_models") or []),
                float((p.get("data_quality_snapshot") or {}).get("score", 100.0)),
            )
            legs.append(leg)
            results[leg.prediction_id] = y
        implied = defaultdict(dict)
        for leg in legs:
            implied[leg.match_id][leg.market] = 1.0 / leg.best_odds
        fair = {mid: ab.fair_market_probabilities(v) for mid, v in implied.items()}
        for leg in legs:
            leg.fair_probability = fair[leg.match_id].get(leg.market)
        if legs:
            days[d] = (legs, results)
    return days


class _Replay(ab.AccumulatorBuilder):
    def __init__(self, legs):
        super().__init__(db=None)
        self._legs = legs

    async def _resolve_model_run(self, target_date, model_run_id):
        from types import SimpleNamespace
        return SimpleNamespace(id=1, model_version="replay", config_snapshot={})

    async def _load_all_legs(self, target_date, model_run_id):
        return [ab.replace(leg) for leg in self._legs]

    async def _load_calibration_gates(self, *args):
        return {}

    async def _load_correlation_coefficients(self):
        return {}


def leg_calibration(days):
    bands = [(1.20, 1.40), (1.40, 1.65), (1.65, 2.30), (2.30, 3.50), (3.50, 10.0)]
    print("\n1) Leg-level calibration of the fair price (all settled legs, by quoted odds band)")
    print("   band         n    fair_p  won    model_p  |  flat ROI at quoted price")
    for lo, hi in bands:
        rows = [(leg, res[leg.prediction_id]) for legs, res in days.values() for leg in legs
                if leg.fair_probability is not None and lo <= leg.best_odds < hi]
        if not rows:
            continue
        n = len(rows)
        fp = sum(l.fair_probability for l, _ in rows) / n
        mp = sum(l.model_probability for l, _ in rows) / n
        won = sum(y for _, y in rows) / n
        roi = sum((l.best_odds - 1) if y else -1 for l, y in rows) / n
        print(f"   {lo:.2f}-{hi:<5.2f} {n:5d}   {fp:.3f}   {won:.3f}   {mp:.3f}   |  {100*roi:+.1f}%")


def ticket_replay(days):
    print("\n2) Tickets rebuilt per day with the production builder, then settled (indicative only)")
    totals = {}
    with patch.object(ab, "_odds_age_hours", lambda v: 0.0):
        for mode in ("model", "market"):
            with patch.object(settings, "leg_probability_source", mode):
                rows = []
                for d, (legs, results) in days.items():
                    built = asyncio.run(_Replay(legs).build(datetime.fromisoformat(d).date()))
                    for t in (built.conservative, built.balanced, built.aggressive):
                        if t is None:
                            continue
                        won = all(results[l.prediction_id] for l in t.legs)
                        rows.append((d, t.ticket_type.value, len(t.legs), t.combined_odds, t.adjusted_probability, won))
                totals[mode] = rows
                n = len(rows)
                if not n:
                    print(f"   {mode:6s}: no tickets"); continue
                claimed = sum(r[4] for r in rows) / n
                wins = sum(r[5] for r in rows)
                pnl = sum((r[3] - 1) if r[5] else -1 for r in rows)
                print(f"   {mode:6s}: {n} tickets over {len(days)} days | claimed hit {100*claimed:.1f}% | "
                      f"won {wins}/{n} ({100*wins/n:.1f}%) | flat P&L {pnl:+.2f} units ({100*pnl/n:+.1f}%)")
                by_tier = defaultdict(list)
                for r in rows:
                    by_tier[r[1]].append(r)
                for tier, rs in sorted(by_tier.items()):
                    print(f"       {tier:10s} n={len(rs)} claimed={100*sum(r[4] for r in rs)/len(rs):.1f}% "
                          f"won={sum(r[5] for r in rs)} avg_odds={sum(r[3] for r in rs)/len(rs):.2f}")
    return totals


def load_archive(path):
    from collections import Counter

    from app.config import CAT

    t = json.load(gzip.open(path))["tables"]
    matches = {m["id"]: m for m in t["matches"]}
    # The archive has no model_runs table: a run's target day is the CAT day
    # its (non-replay) fixtures kick off on.
    days_by_run = defaultdict(Counter)
    for p in t["predictions"]:
        m = matches.get(p["match_id"])
        if m and not p.get("as_of_at") and p.get("model_run_id"):
            days_by_run[p["model_run_id"]][_dt(m["kickoff_at"]).astimezone(CAT).date().isoformat()] += 1
    runs = [{"id": run_id, "target_date": c.most_common(1)[0][0], "status": "COMPLETED"}
            for run_id, c in days_by_run.items()]
    return t["predictions"], matches, runs


async def load_local():
    from sqlalchemy import text
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        preds = [dict(r._mapping) for r in (await db.execute(text("select * from predictions"))).all()]
        matches = {r.id: dict(r._mapping) for r in (await db.execute(text(
            "select id, home_team_id, away_team_id, competition_id, kickoff_at, status::text as status, "
            "home_goals, away_goals, excluded_from_models from matches"))).all()}
        runs = [dict(r._mapping) for r in (await db.execute(text(
            "select id, target_date, status::text as status from model_runs"))).all()]
    for p in preds:
        p["q_grade"] = getattr(p["q_grade"], "value", p["q_grade"])
    return preds, matches, runs


def main():
    if len(sys.argv) > 1:
        preds, matches, runs = load_archive(sys.argv[1])
        print(f"== PRODUCTION ARCHIVE {sys.argv[1]} (diagnosis only) ==")
    else:
        preds, matches, runs = asyncio.run(load_local())
        print("== LOCAL DATABASE ==")
    days = build_days(preds, matches, runs)
    print("days with settled legs:", list(days))
    leg_calibration(days)
    ticket_replay(days)


if __name__ == "__main__":
    main()
