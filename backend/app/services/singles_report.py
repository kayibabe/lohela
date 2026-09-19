"""Read-only database adapter for the singles research protocol.

Selection never receives match scores or status. Historical replay of a newly
chosen policy is diagnostic, even when the underlying predictions were live.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timezone

from sqlalchemy import select

from app.config import cat_day_bounds_utc
from app.models import Match, MatchStatus, Prediction
from app.services.settlement import evaluate_selection
from app.services.singles_research import Candidate, Policy, evaluate, select_candidates


def adapt_rows(rows):
    candidates, outcomes, rejected = [], {}, Counter()
    for prediction, match in rows:
        provenance = prediction.source_odds_provenance or {}
        if (provenance.get("source_type") != "api_football"
                or provenance.get("is_fallback") is not False
                or not provenance.get("bookmaker")
                or prediction.source_odds_id is None):
            rejected["UNVERIFIED_EXECUTABLE_QUOTE"] += 1
            continue
        if prediction.selection != prediction.market:
            rejected["SELECTION_MARKET_MISMATCH"] += 1
            continue
        candidate = Candidate(
            id=str(prediction.id), match_id=str(match.id), market=prediction.market,
            probability=prediction.model_probability, odds=prediction.source_decimal_odds,
            created_at=prediction.created_at, quote_at=prediction.source_odds_at,
            decision_at=prediction.created_at, kickoff_at=match.kickoff_at,
            source_revision=prediction.model_version, historical=prediction.as_of_at is not None,
        )
        candidates.append(candidate)
        # This map is consumed only AFTER outcome-blind selection.
        outcomes[candidate.id] = "pending"
        if (match.status == MatchStatus.FINISHED
                and isinstance(match.home_goals, int) and match.home_goals >= 0
                and isinstance(match.away_goals, int) and match.away_goals >= 0):
            try:
                result = evaluate_selection(candidate.market, match.home_goals, match.away_goals)
                outcomes[candidate.id] = {"won": "win", "lost": "loss", "void": "void"}[result.value]
            except ValueError:
                pass
        # Cancellation/postponement is not automatically a bookmaker void.
    return candidates, outcomes, dict(rejected)


async def load_rows(db, start: date, end: date, model_version: str):
    if end < start or (end - start).days > 366:
        raise ValueError("Use an ordered period of at most 367 days")
    result = await db.execute(
        select(Prediction, Match).join(Match, Prediction.match_id == Match.id)
        .where(Prediction.model_version == model_version,
               Match.kickoff_at >= cat_day_bounds_utc(start)[0],
               Match.kickoff_at < cat_day_bounds_utc(end)[1])
        .order_by(Prediction.created_at, Prediction.id)
    )
    return result.all()


async def singles_report(db, start: date, end: date, model_version: str, *, as_of=None):
    as_of = as_of or datetime.now(timezone.utc)
    rows = await load_rows(db, start, end, model_version)
    candidates, outcomes, rejected = adapt_rows(rows)
    policy = Policy()
    selection = select_candidates(candidates, as_of=as_of, policy=policy)
    report = evaluate(selection, outcomes, start_date=start, end_date=end)
    report.update({
        "evidence_type": "retrospective_policy_replay_of_prospective_predictions",
        "model_version": model_version, "generated_at": as_of.isoformat(),
        "candidate_database_rows": len(rows), "adapter_rejections": rejected,
        "policy": asdict(policy),
        "selected_picks": [
            {"prediction_id": c.id, "match_id": c.match_id, "market": c.market,
             "probability": c.probability, "odds": c.odds,
             "kickoff_at": c.kickoff_at.isoformat(), "decision_at": c.decision_at.isoformat(),
             "outcome": outcomes.get(c.id, "pending")}
            for c in selection.picks
        ],
        "limitations": [
            "This policy was chosen after these historical results existed; this is not forward strategy validation.",
            "Quote provenance does not prove bookmaker availability or bet execution.",
            "Model version is not a source-code hash; legacy feature and parameter lineage is incomplete.",
            "Profit is quoted-price flat-stake paper simulation; conservative_roi separately stresses winnings by the policy haircut.",
            "Intervals do not account for policy search or model selection; sparse samples are unreliable.",
        ],
    })
    return report, selection
