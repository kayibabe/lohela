"""Frozen-evidence, offline comparison of the pre- and post-fix candidate-pool
tie-break in accumulator_builder._find_best_ticket.

Read-only against the already-captured production snapshot
(docs/evidence/pre_retraining_2026-09-20/source.json.gz). Makes no database
writes and calls no live model run.

SCOPE — this is a reconstruction, not a full production replay. It does not
import or call the production AccumulatorBuilder (which requires a DB
session); it rebuilds an approximation of the Leg pool per model run and
runs both the old and the current _find_best_ticket candidate-selection
logic against it. Known gaps versus the real pipeline:
  - no Odds-table fallback when source_decimal_odds is null (snapshot has no
    Odds table; only the immutable per-prediction snapshot column is used);
  - no historical LeaguePerformance/ModelPerformance calibration gates
    (those tables are not in the snapshot — calibration=None throughout);
  - no research-market restriction filtering;
  - no prior-published-ticket overlap constraints (max_shared_matches /
    max_match_market_exposure);
  - no relaxation-ladder fallback;
  - STALE_ODDS and Q_SCORE_BELOW_TIER are deliberately excluded from the
    non-tier gate check before applying each tier's own min_q_score (see
    _qualified_pool), because STALE_ODDS compares against wall-clock
    datetime.now() and would reject every archived leg regardless of how
    fresh the odds were at actual selection time.

Requested by the 2026-09-20 selection-calibration review as the outstanding
verification step before trusting the prediction_id tie-break as anything
more than "a deterministic tie-break policy". This script does not change
that classification either way. Its result: across the reconstructed
candidate pools available from the frozen snapshot, the old and new
tie-breaks produced identical tickets. This does NOT certify that the
omitted production inputs or historical gates above could not contain an
exact (q_score, expected_value) tie the reconstruction misses.
"""
from __future__ import annotations

import gzip
import json
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import settings
from app.models import QGrade, TicketType
from app.services.accumulator_builder import (
    TICKET_SPECS,
    Leg,
    TicketSpec,
    _evaluate_combo,
    _partial_score,
    selection_rejection_reasons,
)

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "docs/evidence/pre_retraining_2026-09-20/source.json.gz"
_MIN_LEG_ODDS = 1.20
_MIN_ACTIVE_MODELS = 3
_CANDIDATE_LIMIT = 30
_BEAM_WIDTH = 800


def _find_best_ticket_with_key(pool, spec, tiebreak_key):
    candidates = sorted(
        pool,
        key=lambda leg: (leg.q_score, leg.expected_value or -1.0, tiebreak_key(leg)),
        reverse=True,
    )[:_CANDIDATE_LIMIT]
    if len(candidates) < spec.min_legs:
        return None
    partials = [(tuple(), tuple())]
    best = None
    best_score = -math.inf
    for size in range(1, spec.max_legs + 1):
        expanded = []
        for indices, legs in partials:
            start = indices[-1] + 1 if indices else 0
            for index in range(start, len(candidates)):
                leg = candidates[index]
                if any(existing.match_id == leg.match_id for existing in legs):
                    continue
                if sum(existing.competition_id == leg.competition_id for existing in legs) >= 2:
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
            ticket = _evaluate_combo(legs, spec, {})
            if ticket is None:
                continue
            objective = (
                ticket.adjusted_probability + max(0.0, ticket.expected_value) * 0.05
                if spec.ticket_type == TicketType.SAFE
                else ticket.expected_value / (1.0 + ticket.risk_score / 100.0)
            )
            if objective > best_score:
                best, best_score = ticket, objective
    return best


def _old_tiebreak(leg: Leg) -> float:
    return leg.model_probability


def _new_tiebreak(leg: Leg) -> int:
    return leg.prediction_id


def _build_legs(tables: dict) -> dict[int, list[Leg]]:
    matches = {m["id"]: m for m in tables["matches"]}
    teams = {t["id"]: t["name"] for t in tables["teams"]}
    by_run: dict[int, list[Leg]] = {}
    for p in tables["predictions"]:
        if p.get("model_run_id") is None:
            continue
        match = matches.get(p["match_id"])
        if match is None or match["home_team_id"] not in teams or match["away_team_id"] not in teams:
            continue
        if match["excluded_from_models"]:
            continue
        decimal_odds = p.get("source_decimal_odds") or 0.0
        leg = Leg(
            prediction_id=p["id"],
            match_id=p["match_id"],
            home_team_id=match["home_team_id"],
            away_team_id=match["away_team_id"],
            home_team=teams[match["home_team_id"]],
            away_team=teams[match["away_team_id"]],
            competition="",
            competition_id=match["competition_id"],
            kickoff_at=datetime.fromisoformat(match["kickoff_at"]),
            market=p["market"],
            selection=p["selection"],
            model_probability=p["model_probability"],
            model_agreement=p["model_agreement"] or 0.0,
            best_odds=decimal_odds,
            source_odds_at=(
                datetime.fromisoformat(p["source_odds_at"]) if p.get("source_odds_at") else None
            ),
            q_score=p["q_score"] or 0.0,
            q_grade=QGrade[p["q_grade"]] if p.get("q_grade") else QGrade.REJECT,
            edge=p["edge"],
            expected_value=p["expected_value"],
            active_models=list(p.get("active_models") or []),
            data_quality_score=float((p.get("data_quality_snapshot") or {}).get("score", 0.0)),
        )
        by_run.setdefault(p["model_run_id"], []).append(leg)
    return by_run


def _qualified_pool(legs: list[Leg]) -> list[Leg]:
    pool = [leg for leg in legs if leg.edge is not None and leg.best_odds >= _MIN_LEG_ODDS]
    eligible = []
    for leg in pool:
        reasons = selection_rejection_reasons(leg, TICKET_SPECS[0], None)
        # Tier-specific min_q_score is applied per spec below, matching
        # AccumulatorBuilder's per-spec eligibility filtering, not here.
        # STALE_ODDS is excluded: selection_rejection_reasons compares
        # source_odds_at against wall-clock datetime.now(), which is
        # meaningless when replaying a frozen historical snapshot long after
        # capture — every leg would be "stale" regardless of how fresh the
        # odds were at actual selection time.
        reasons = [r for r in reasons if r not in ("Q_SCORE_BELOW_TIER", "STALE_ODDS")]
        if not reasons:
            eligible.append(leg)
    return eligible


def main() -> None:
    raw = SNAPSHOT.read_bytes()
    tables = json.loads(gzip.decompress(raw))["tables"]
    by_run = _build_legs(tables)

    diffs = []
    identical = 0
    both_none = 0
    total_compared = 0
    tie_group_sizes: Counter[int] = Counter()

    for model_run_id, legs in sorted(by_run.items()):
        eligible = _qualified_pool(legs)
        for spec in TICKET_SPECS:
            tier_pool = [leg for leg in eligible if leg.q_score >= spec.min_q_score]
            # Measure exact (q_score, expected_value) tie-group sizes in the
            # actual candidate pool before any truncation.
            key_counts = Counter((leg.q_score, leg.expected_value) for leg in tier_pool)
            for size in key_counts.values():
                if size > 1:
                    tie_group_sizes[size] += 1

            old_ticket = _find_best_ticket_with_key(tier_pool, spec, _old_tiebreak)
            new_ticket = _find_best_ticket_with_key(tier_pool, spec, _new_tiebreak)
            total_compared += 1
            if old_ticket is None and new_ticket is None:
                both_none += 1
                continue
            old_ids = sorted(leg.prediction_id for leg in old_ticket.legs) if old_ticket else None
            new_ids = sorted(leg.prediction_id for leg in new_ticket.legs) if new_ticket else None
            if old_ids == new_ids:
                identical += 1
            else:
                diffs.append(
                    {
                        "model_run_id": model_run_id,
                        "ticket_type": spec.ticket_type.value,
                        "pool_size": len(tier_pool),
                        "old_legs": old_ids,
                        "new_legs": new_ids,
                    }
                )

    print(f"Model runs in snapshot: {len(by_run)}")
    print(f"Tier/run combinations compared: {total_compared}")
    print(f"  both produced no ticket: {both_none}")
    print(f"  identical ticket (by prediction_id set): {identical}")
    print(f"  different ticket: {len(diffs)}")
    print(f"Exact (q_score, expected_value) tie-group sizes observed in candidate pools: {dict(sorted(tie_group_sizes.items()))}")
    if diffs:
        print("\nDiffs:")
        for d in diffs:
            print(f"  run={d['model_run_id']} tier={d['ticket_type']} pool={d['pool_size']}")
            print(f"    old: {d['old_legs']}")
            print(f"    new: {d['new_legs']}")
    else:
        print(
            "\nAcross the reconstructed candidate pools available from the frozen "
            "snapshot, the old and new tie-breaks produced identical tickets. This "
            "does not certify that omitted production inputs or historical gates "
            "(see module docstring SCOPE section) could not contain an exact tie."
        )


if __name__ == "__main__":
    main()
