"""Daily frozen minimum-odds policy research.

This is deliberately a research stream. It searches stored, pre-kickoff
predictions using only settled evidence before the target date, freezes the
chosen policy before exposing that day's matches, and never publishes a bet.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import CURRENT_MODEL_VERSION, cat_day_bounds_utc, cat_today
from app.models import MatchStatus, ParameterSweepScan
from app.services.singles_research import Policy, select_candidates
from app.services.singles_report import adapt_rows, load_rows


# The offline diagnostic used 87,360 cells. The daily worker uses a still
# broad 9,216-cell grid concentrated on the empirically useful range so it
# finishes in a bounded background task; the original script remains the tool
# for the full exploratory sweep.
ODDS_FLOORS = tuple(round(1.50 + 0.10 * i, 2) for i in range(16))
PROBABILITY_FLOORS = tuple(round(0.50 + 0.05 * i, 2) for i in range(8))
EV_FLOORS = (0.00, 0.05, 0.10, 0.20)
HAIRCUTS = (0.00, 0.02, 0.05)
QUOTE_AGES = (1.0, 2.0, 6.0)

TRAIN_DAYS = 60
VALIDATION_DAYS = 14
MIN_TRAIN_SAMPLE = 20
MIN_VALIDATION_SAMPLE = 20
MIN_VALIDATION_ROI = 0.20
POLICY_VERSION = "dynamic-parameter-sweep-v1"


def _score(candidates, outcomes, policy: Policy, start: date, end: date) -> dict:
    selection = select_candidates(
        [candidate for candidate in candidates if start <= candidate.kickoff_at.date() <= end],
        as_of=datetime.now(timezone.utc),
        policy=policy,
    )
    rows = [candidate for candidate in selection.picks if outcomes.get(candidate.id) in {"win", "loss", "void"}]
    wins = sum(outcomes[candidate.id] == "win" for candidate in rows)
    losses = sum(outcomes[candidate.id] == "loss" for candidate in rows)
    voids = sum(outcomes[candidate.id] == "void" for candidate in rows)
    profit = sum(
        candidate.odds - 1 if outcomes[candidate.id] == "win" else 0.0
        if outcomes[candidate.id] == "void" else -1.0
        for candidate in rows
    )
    resolved = len(rows)
    return {
        "selected": len(selection.picks),
        "resolved": resolved,
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "profit": round(profit, 8),
        "roi": round(profit / resolved, 8) if resolved else None,
        "hit_rate": round(wins / (wins + losses), 4) if wins + losses else None,
        "days": len({candidate.kickoff_at.date() for candidate in rows}),
    }


def _grid():
    for odds in ODDS_FLOORS:
        for probability in PROBABILITY_FLOORS:
            for ev in EV_FLOORS:
                for haircut in HAIRCUTS:
                    for age in QUOTE_AGES:
                        yield Policy(
                            version=POLICY_VERSION,
                            min_probability=probability,
                            min_odds=odds,
                            price_haircut=haircut,
                            min_ev=ev,
                            max_quote_age_hours=age,
                        )


def _choose_policy(results: list[dict]) -> dict | None:
    eligible = [
        row for row in results
        if row["train"]["resolved"] >= MIN_TRAIN_SAMPLE
        and row["validation"]["resolved"] >= MIN_VALIDATION_SAMPLE
        and (row["validation"]["roi"] or -999) >= MIN_VALIDATION_ROI
    ]
    if not eligible:
        return None
    # Rank on the older train window. Validation is a minimum gate, not the
    # optimization target, which reduces cherry-picking within the holdout.
    return max(
        eligible,
        key=lambda row: (
            row["train"]["roi"] if row["train"]["roi"] is not None else -999,
            row["validation"]["resolved"],
            row["validation"]["roi"] if row["validation"]["roi"] is not None else -999,
        ),
    )


def _policy_from_row(row: dict) -> Policy:
    return Policy(**row["policy"])


def _candidate_output(candidate, prediction, match, outcome, policy_metrics) -> dict:
    return {
        "prediction_id": int(candidate.id),
        "match_id": int(candidate.match_id),
        "home_team": match.home_team.name,
        "away_team": match.away_team.name,
        "competition": match.competition.name,
        "kickoff_at": match.kickoff_at.isoformat(),
        "market": candidate.market,
        "selection": prediction.selection,
        "model_probability": round(candidate.probability, 4),
        "odds": candidate.odds,
        "q_score": prediction.q_score,
        "q_grade": prediction.q_grade.value,
        "edge": prediction.edge,
        "source_odds_at": candidate.quote_at.isoformat(),
        "pre_kickoff_quote": True,
        "result": outcome if outcome in {"win", "loss", "void"} else None,
        "policy_train_roi": policy_metrics["train"]["roi"],
        "policy_validation_roi": policy_metrics["validation"]["roi"],
    }


async def _load_target_rows(db: AsyncSession, target_date: date):
    rows = await load_rows(db, target_date, target_date, CURRENT_MODEL_VERSION)
    return rows


async def _compute_payload(db: AsyncSession, target_date: date) -> dict:
    evidence_through = target_date - timedelta(days=1)
    validation_start = evidence_through - timedelta(days=VALIDATION_DAYS - 1)
    train_end = validation_start - timedelta(days=1)
    train_start = train_end - timedelta(days=TRAIN_DAYS - 1)
    rows = await load_rows(db, train_start, evidence_through, CURRENT_MODEL_VERSION)
    candidates, outcomes, adapter_rejections = adapt_rows(rows)
    train = [candidate for candidate in candidates if train_start <= candidate.kickoff_at.date() <= train_end]
    validation = [candidate for candidate in candidates if validation_start <= candidate.kickoff_at.date() <= evidence_through]

    results = []
    for policy in _grid():
        results.append({
            "policy": asdict(policy),
            "train": _score(train, outcomes, policy, train_start, train_end),
            "validation": _score(validation, outcomes, policy, validation_start, evidence_through),
        })
    chosen = _choose_policy(results)
    return {
        "policy_version": POLICY_VERSION,
        "status": "qualified" if chosen else "no_qualifying_policy",
        "model_version": CURRENT_MODEL_VERSION,
        "evidence_through": evidence_through.isoformat(),
        "train_window": [train_start.isoformat(), train_end.isoformat()],
        "validation_window": [validation_start.isoformat(), evidence_through.isoformat()],
        "rules": {
            "grid_size": len(results),
            "minimum_validation_roi": MIN_VALIDATION_ROI,
            "minimum_train_sample": MIN_TRAIN_SAMPLE,
            "minimum_validation_sample": MIN_VALIDATION_SAMPLE,
            "selection_rule": "validation ROI must clear the gate; rank qualifying policies by older train ROI",
            "one_selection_per_match": True,
        },
        "candidate_database_rows": len(rows),
        "adapted_candidates": len(candidates),
        "adapter_rejections": adapter_rejections,
        "chosen_policy": chosen["policy"] if chosen else None,
        "chosen_train": chosen["train"] if chosen else None,
        "chosen_validation": chosen["validation"] if chosen else None,
        "qualifying_policy_count": sum(
            row["train"]["resolved"] >= MIN_TRAIN_SAMPLE
            and row["validation"]["resolved"] >= MIN_VALIDATION_SAMPLE
            and (row["validation"]["roi"] or -999) >= MIN_VALIDATION_ROI
            for row in results
        ),
        "parameter_ranges": {
            "min_odds": [ODDS_FLOORS[0], ODDS_FLOORS[-1]],
            "min_probability": [PROBABILITY_FLOORS[0], PROBABILITY_FLOORS[-1]],
            "min_ev": [EV_FLOORS[0], EV_FLOORS[-1]],
            "price_haircut": [HAIRCUTS[0], HAIRCUTS[-1]],
            "max_quote_age_hours": [QUOTE_AGES[0], QUOTE_AGES[-1]],
        },
        "limitations": [
            "This is retrospective policy selection over stored predictions, not a guarantee of future profit.",
            "The validation gate is still subject to policy-search and model-selection bias.",
            "Quoted odds do not prove future availability, limits, or executable fills.",
            "When no policy clears every gate, no daily selections are shown.",
        ],
    }


async def _candidate_manifest(db: AsyncSession, target_date: date, payload: dict) -> list[dict]:
    if not payload.get("chosen_policy"):
        return []
    rows = await _load_target_rows(db, target_date)
    candidates, outcomes, _ = adapt_rows(rows)
    policy = _policy_from_row(payload["chosen_policy"])
    selection = select_candidates(candidates, as_of=datetime.now(timezone.utc), policy=policy)
    by_id = {str(prediction.id): (prediction, match) for prediction, match in rows}
    metrics = {"train": payload["chosen_train"], "validation": payload["chosen_validation"]}
    manifest = []
    for candidate in selection.picks:
        pair = by_id.get(str(candidate.id))
        if pair is None:
            continue
        prediction, match = pair
        manifest.append(_candidate_output(candidate, prediction, match, outcomes.get(candidate.id), metrics))
    return manifest


def _scan_out(scan: ParameterSweepScan | None, target_date: date, payload: dict, *, provisional: bool, reconstructed: bool = False) -> dict:
    return {
        "target_date": target_date.isoformat(),
        "evidence_through": payload["evidence_through"],
        "captured_at": scan.captured_at.isoformat() if scan else None,
        "capture_source": scan.capture_source if scan else "provisional",
        "provisional": provisional,
        "reconstructed": reconstructed,
        **payload,
    }


async def get_or_capture_parameter_sweep(db: AsyncSession, target_date: date, capture_source: str = "on_demand") -> dict:
    existing = (await db.execute(select(ParameterSweepScan).where(ParameterSweepScan.target_date == target_date))).scalar_one_or_none()
    if existing is not None:
        return _scan_out(existing, target_date, existing.payload, provisional=False)

    payload = await _compute_payload(db, target_date)
    payload["candidate_manifest"] = await _candidate_manifest(db, target_date, payload)
    if target_date > cat_today():
        return _scan_out(None, target_date, payload, provisional=True)
    if target_date < cat_today():
        return _scan_out(None, target_date, payload, provisional=False, reconstructed=True)

    scan = ParameterSweepScan(
        target_date=target_date,
        evidence_through=target_date - timedelta(days=1),
        capture_source=capture_source,
        payload=payload,
        captured_at=datetime.now(timezone.utc),
    )
    db.add(scan)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return await get_or_capture_parameter_sweep(db, target_date, capture_source)
    return _scan_out(scan, target_date, payload, provisional=False)


def parameter_sweep_candidates(scan: dict) -> list[dict]:
    return list(scan.get("candidate_manifest") or [])
