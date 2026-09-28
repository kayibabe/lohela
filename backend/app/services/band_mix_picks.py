"""Daily dynamic Lohela-band × market-band monitoring and candidate picks.

Each day works in two steps:

1. Scan: before listing candidates, calculate every Lohela × market pair from
   settled evidence dated strictly before the target day and freeze the active
   pairs in ``band_mix_scans``. A pair qualifies only at the configured
   minimum evidence and ROI threshold.
2. Match: band every latest prediction for the target day by model probability
   and pick-time market-implied probability. Keep only active-mix selections,
   then retain one deterministic selection per fixture.

The scan is captured once per day (first writer wins) so the criteria cannot
drift as that day's own results arrive.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import cat_day_bounds_utc, cat_today
from app.models import BandMixScan, Match, MatchStatus, ModelRun, Prediction, RunStatus
from app.services.performance import (
    _has_pre_kickoff_information,
    _has_pre_kickoff_quote,
    _probability_band,
    probability_calibration_analysis,
)
from app.services.settlement import evaluate_selection

# Dynamic research-monitoring criteria. An active pair is never a guarantee of
# future profit; the daily evidence cutoff is frozen before candidates appear.
MIN_MIX_SAMPLE = 30
PROVISIONAL_MIX_SAMPLE = 50
MIN_MIX_ROI = 0.20
RECENT_WINDOW_DAYS = 60
POLICY_VERSION = "dynamic-roi-v1"


def _mix_key(lohela_band: str, market_band: str) -> str:
    return f"{lohela_band}|{market_band}"


def evaluate_band_mixes(
    combined_matrix: list[dict],
    recent_matrix: list[dict] | None = None,
) -> dict:
    """Return every currently qualifying pair from one frozen evidence set."""
    recent = {_mix_key(r["lohela_band"], r["market_band"]): r for r in (recent_matrix or [])}
    ranked = sorted(combined_matrix, key=lambda row: (row["roi"] is not None, row["roi"] or float("-inf")), reverse=True)
    ranks = {_mix_key(r["lohela_band"], r["market_band"]): index + 1 for index, r in enumerate(ranked)}
    stats_keys = ("sample_size", "wins", "losses", "hit_rate", "roi")

    def describe(row: dict) -> dict:
        lohela_band, market_band = row["lohela_band"], row["market_band"]
        key = _mix_key(lohela_band, market_band)
        recent_row = recent.get(key)
        rank = ranks.get(key)
        if row["sample_size"] < MIN_MIX_SAMPLE:
            reason = "INSUFFICIENT_SAMPLE"
        elif row["roi"] is None or row["roi"] < MIN_MIX_ROI:
            reason = "ROI_BELOW_THRESHOLD"
        else:
            reason = None
        return {
            "lohela_band": lohela_band,
            "market_band": market_band,
            "source": "dynamic",
            "best_rank": rank,
            "active": reason is None,
            "reason": reason,
            "research_status": "research_qualified" if reason is None else "not_qualified",
            "provisional": reason is None and row["sample_size"] < PROVISIONAL_MIX_SAMPLE,
            "recent_sample_size": recent_row["sample_size"] if recent_row else 0,
            "recent_hit_rate": recent_row["hit_rate"] if recent_row else None,
            "recent_roi": recent_row["roi"] if recent_row else None,
            **{k: row[k] for k in stats_keys},
        }

    monitored = [describe(row) for row in ranked]
    return {
        "watchlist": [row for row in monitored if row["active"]],
        "discovered": [],
        "monitored": monitored,
    }


async def _compute_scan_payload(db: AsyncSession, target_date: date) -> tuple[date, dict]:
    evidence_through = target_date - timedelta(days=1)
    analysis = await probability_calibration_analysis(db, date_to=evidence_through)
    recent_start = evidence_through - timedelta(days=RECENT_WINDOW_DAYS - 1)
    recent = await probability_calibration_analysis(db, date_from=recent_start, date_to=evidence_through)
    decision = evaluate_band_mixes(analysis["combined_matrix"], recent["combined_matrix"])
    payload = {
        "rules": {
            "min_sample": MIN_MIX_SAMPLE,
            "min_roi": MIN_MIX_ROI,
            "provisional_below_sample": PROVISIONAL_MIX_SAMPLE,
            "recent_window_days": RECENT_WINDOW_DAYS,
            "one_selection_per_match": True,
            "evidence": "settled predictions with pre-kickoff odds, kickoff before target date",
        },
        "policy_version": POLICY_VERSION,
        "evidence_sample_size": analysis["summary"]["sample_size"],
        "best_combinations": analysis["best_combinations"],
        **decision,
    }
    return evidence_through, payload


def _scan_out(
    scan: BandMixScan | None, *, target_date: date, evidence_through: date,
    payload: dict, provisional: bool, reconstructed: bool = False,
) -> dict:
    return {
        "target_date": target_date.isoformat(),
        "evidence_through": evidence_through.isoformat(),
        "captured_at": scan.captured_at.isoformat() if scan and scan.captured_at else None,
        "capture_source": scan.capture_source if scan else "provisional",
        "provisional": provisional,
        "reconstructed": reconstructed,
        **payload,
    }


async def get_or_capture_scan(db: AsyncSession, target_date: date, capture_source: str = "on_demand") -> dict:
    """Return the frozen scan for target_date, capturing it on first request.

    Future dates get a provisional (unsaved) scan: their evidence window is not
    closed yet, so freezing it early would be misleading.
    """
    existing = (await db.execute(
        select(BandMixScan).where(BandMixScan.target_date == target_date)
    )).scalar_one_or_none()
    if existing is not None:
        return _scan_out(existing, target_date=target_date, evidence_through=existing.evidence_through, payload=existing.payload, provisional=False)

    evidence_through, payload = await _compute_scan_payload(db, target_date)
    if target_date > cat_today():
        return _scan_out(None, target_date=target_date, evidence_through=evidence_through, payload=payload, provisional=True)
    if target_date < cat_today():
        # Historical views remain read-only reconstructions. Persisting a new
        # old-date scan would make a post-hoc candidate set look prospective.
        return _scan_out(
            None, target_date=target_date, evidence_through=evidence_through,
            payload=payload, provisional=False, reconstructed=True,
        )

    scan = BandMixScan(
        target_date=target_date,
        evidence_through=evidence_through,
        capture_source=capture_source,
        payload=payload,
        captured_at=datetime.now(timezone.utc),
    )
    db.add(scan)
    try:
        # Persist the exact candidate set before results exist. Never backfill
        # old scans: that would turn a forward audit into a reconstruction.
        rows = await band_mix_candidates(db, target_date, payload)
        payload["candidate_manifest_version"] = 1
        payload["candidate_manifest"] = [{
            key: row[key] for key in (
                "prediction_id", "match_id", "home_team", "away_team", "competition",
                "kickoff_at", "market", "selection", "model_probability",
                "market_probability", "odds", "edge", "q_score", "q_grade",
                "lohela_band", "market_band", "mix_source", "mix_rank",
                "mix_sample_size", "mix_hit_rate", "mix_roi", "pre_kickoff_quote",
            )
        } for row in rows]
        await db.commit()
    except IntegrityError:
        # A concurrent request captured the day first; theirs is authoritative.
        await db.rollback()
        return await get_or_capture_scan(db, target_date, capture_source)
    return _scan_out(scan, target_date=target_date, evidence_through=evidence_through, payload=payload, provisional=False)


def _selection_result(match: Match, prediction: Prediction) -> str | None:
    if match.status != MatchStatus.FINISHED or match.home_goals is None or match.away_goals is None:
        return None
    for value in (prediction.selection, prediction.market):
        try:
            return evaluate_selection(value, match.home_goals, match.away_goals).value
        except ValueError:
            continue
    return None


def _choose_one_per_match(candidates: list[tuple[dict, dict]]) -> list[dict]:
    """Pick one qualified selection per match using a stable evidence-first order."""
    def choice_key(candidate: tuple[dict, dict]) -> tuple[bool, int, float, int]:
        row, mix = candidate
        # Prefer an established qualifying band, then the larger evidence base,
        # then ROI. Prediction ID is only the deterministic final tie-break.
        return (
            not bool(mix.get("provisional", False)),
            int(mix["sample_size"]),
            float(mix["roi"] or float("-inf")),
            int(row["prediction_id"]),
        )

    best_by_match: dict[int, tuple[dict, dict]] = {}
    for candidate in candidates:
        match_id = candidate[0]["match_id"]
        existing = best_by_match.get(match_id)
        if existing is None or choice_key(candidate) > choice_key(existing):
            best_by_match[match_id] = candidate
    return [candidate[0] for candidate in best_by_match.values()]


async def band_mix_candidates(db: AsyncSession, target_date: date, scan: dict) -> list[dict]:
    """One qualifying, pre-kickoff selection per fixture for the target date."""
    mixes = {
        _mix_key(row["lohela_band"], row["market_band"]): row
        for row in [*scan["watchlist"], *scan["discovered"]]
        if row["active"]
    }
    if not mixes:
        return []

    day_start, day_end = cat_day_bounds_utc(target_date)
    latest_run = (
        select(
            Prediction.match_id,
            Prediction.market,
            Prediction.selection,
            func.max(Prediction.model_run_id).label("latest_run_id"),
        )
        .join(ModelRun, Prediction.model_run_id == ModelRun.id)
        .where(ModelRun.target_date == target_date, ModelRun.status == RunStatus.COMPLETED)
        .group_by(Prediction.match_id, Prediction.market, Prediction.selection)
        .subquery()
    )
    predictions = (await db.execute(
        select(Prediction)
        .join(Match, Prediction.match_id == Match.id)
        .join(
            latest_run,
            (latest_run.c.match_id == Prediction.match_id)
            & (latest_run.c.market == Prediction.market)
            & (latest_run.c.selection == Prediction.selection)
            & (latest_run.c.latest_run_id == Prediction.model_run_id),
        )
        .where(
            Match.kickoff_at >= day_start,
            Match.kickoff_at < day_end,
            Match.excluded_from_models == False,  # noqa: E712
            Prediction.source_implied_probability.is_not(None),
            Prediction.source_decimal_odds > 1,
        )
        .options(
            selectinload(Prediction.match).selectinload(Match.home_team),
            selectinload(Prediction.match).selectinload(Match.away_team),
            selectinload(Prediction.match).selectinload(Match.competition),
        )
    )).scalars().all()

    candidates: list[tuple[dict, dict]] = []
    for prediction in predictions:
        lohela_band = _probability_band(prediction.model_probability)
        market_band = _probability_band(prediction.source_implied_probability)
        mix = mixes.get(_mix_key(lohela_band, market_band))
        if mix is None or not _has_pre_kickoff_information(prediction) or not _has_pre_kickoff_quote(prediction):
            continue
        match = prediction.match
        candidates.append(({
            "prediction_id": prediction.id,
            "match_id": match.id,
            "home_team": match.home_team.name,
            "away_team": match.away_team.name,
            "competition": match.competition.name,
            "kickoff_at": match.kickoff_at.isoformat(),
            "market": prediction.market,
            "selection": prediction.selection,
            "model_probability": round(prediction.model_probability, 4),
            "market_probability": round(prediction.source_implied_probability, 4),
            "odds": prediction.source_decimal_odds,
            "edge": prediction.edge,
            "q_score": prediction.q_score,
            "q_grade": prediction.q_grade.value,
            "lohela_band": lohela_band,
            "market_band": market_band,
            "mix_source": mix["source"],
            "mix_rank": mix["best_rank"],
            "mix_sample_size": mix["sample_size"],
            "mix_hit_rate": mix["hit_rate"],
            "mix_roi": mix["roi"],
            "pre_kickoff_quote": True,
            "match_status": match.status.value,
            "home_goals": match.home_goals,
            "away_goals": match.away_goals,
            "live_phase": match.live_phase,
            "elapsed_minutes": match.elapsed_minutes,
            "result": _selection_result(match, prediction),
        }, mix))

    rows = _choose_one_per_match(candidates)
    rows.sort(key=lambda r: (r["kickoff_at"], r["home_team"], r["market"]))
    return rows


def summarize_picks(rows: list[dict]) -> dict:
    """Flat one-unit-stake tally of the day's picks at their pick-time odds."""
    settled = [r for r in rows if r["result"] in ("won", "lost")]
    wins = sum(r["result"] == "won" for r in settled)
    returned = sum(r["odds"] for r in settled if r["result"] == "won")
    return {
        "picks": len(rows),
        "matches": len({r["match_id"] for r in rows}),
        "settled": len(settled),
        "wins": wins,
        "losses": len(settled) - wins,
        "voids": sum(r["result"] == "void" for r in rows),
        "pending": sum(r["result"] is None for r in rows),
        "profit_loss": round(returned - len(settled), 4),
        "roi": round((returned - len(settled)) / len(settled), 4) if settled else None,
    }
