"""Daily band-mix picks: today's selections whose Lohela and market probability
bands fall in a mix that is currently among the best performers.

Each day works in two steps:

1. Scan: before listing candidates, run the whole-system probability
   calibration (the Analytics "Top combined bands by ROI" view) over settled
   evidence dated strictly before the target day, and freeze the result in
   ``band_mix_scans``. A watchlist mix is active for the day only if the scan
   still ranks it among the best-performing mixes with a positive ROI.
2. Match: band every latest prediction for the target day by model probability
   and pick-time market-implied probability, and keep those in an active mix.

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
from app.services.performance import _probability_band, probability_calibration_analysis
from app.services.settlement import evaluate_selection

# Lohela band × market band pairs the operator asked to follow (percent bands,
# matching the Analytics combined-matrix labels).
WATCHLIST_BAND_MIXES: tuple[tuple[str, str], ...] = (
    ("85-90", "75-80"),
    ("70-75", "55-60"),
    ("80-85", "50-55"),
    ("80-85", "65-70"),
    ("70-75", "60-65"),
)
# Mirrors the Analytics best-performing-mixes table: sample size >= 8, top 15 by ROI.
MIN_MIX_SAMPLE = 8
BEST_MIX_LIMIT = 15


def _mix_key(lohela_band: str, market_band: str) -> str:
    return f"{lohela_band}|{market_band}"


def evaluate_band_mixes(
    combined_matrix: list[dict],
    best_combinations: list[dict],
    watchlist: tuple[tuple[str, str], ...] = WATCHLIST_BAND_MIXES,
) -> dict:
    """Decide which mixes are active from one calibration snapshot.

    A watchlist mix is active only when it appears in the best-performing list
    (sample >= MIN_MIX_SAMPLE, top BEST_MIX_LIMIT by ROI) with ROI above zero.
    Other best-performing mixes with positive ROI are returned as "discovered"
    so they can be followed optionally, never silently.
    """
    matrix = {_mix_key(r["lohela_band"], r["market_band"]): r for r in combined_matrix}
    ranks = {
        _mix_key(r["lohela_band"], r["market_band"]): index + 1
        for index, r in enumerate(best_combinations)
    }
    stats_keys = ("sample_size", "wins", "losses", "hit_rate", "roi")

    def describe(lohela_band: str, market_band: str, source: str) -> dict:
        key = _mix_key(lohela_band, market_band)
        row = matrix.get(key)
        rank = ranks.get(key)
        if row is None:
            reason = "NO_HISTORY"
        elif row["sample_size"] < MIN_MIX_SAMPLE:
            reason = "INSUFFICIENT_SAMPLE"
        elif row["roi"] is None or row["roi"] <= 0:
            reason = "NON_POSITIVE_ROI"
        elif rank is None:
            reason = "OUTSIDE_TOP_MIXES"
        else:
            reason = None
        return {
            "lohela_band": lohela_band,
            "market_band": market_band,
            "source": source,
            "best_rank": rank,
            "active": reason is None,
            "reason": reason,
            **{k: (row[k] if row else (0 if k in ("sample_size", "wins", "losses") else None)) for k in stats_keys},
        }

    watch_keys = {_mix_key(*pair) for pair in watchlist}
    watch_rows = [describe(lohela, market, "watchlist") for lohela, market in watchlist]
    discovered = [
        describe(r["lohela_band"], r["market_band"], "discovered")
        for r in best_combinations
        if _mix_key(r["lohela_band"], r["market_band"]) not in watch_keys
    ]
    return {
        "watchlist": watch_rows,
        "discovered": [row for row in discovered if row["active"]],
    }


async def _compute_scan_payload(db: AsyncSession, target_date: date) -> tuple[date, dict]:
    evidence_through = target_date - timedelta(days=1)
    analysis = await probability_calibration_analysis(db, date_to=evidence_through)
    decision = evaluate_band_mixes(analysis["combined_matrix"], analysis["best_combinations"])
    payload = {
        "rules": {
            "min_sample": MIN_MIX_SAMPLE,
            "best_mix_limit": BEST_MIX_LIMIT,
            "requires_positive_roi": True,
            "evidence": "settled predictions with pre-kickoff odds, kickoff before target date",
        },
        "evidence_sample_size": analysis["summary"]["sample_size"],
        "best_combinations": analysis["best_combinations"],
        **decision,
    }
    return evidence_through, payload


def _scan_out(scan: BandMixScan | None, *, target_date: date, evidence_through: date, payload: dict, provisional: bool) -> dict:
    return {
        "target_date": target_date.isoformat(),
        "evidence_through": evidence_through.isoformat(),
        "captured_at": scan.captured_at.isoformat() if scan and scan.captured_at else None,
        "capture_source": scan.capture_source if scan else "provisional",
        "provisional": provisional,
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

    scan = BandMixScan(
        target_date=target_date,
        evidence_through=evidence_through,
        capture_source=capture_source,
        payload=payload,
        captured_at=datetime.now(timezone.utc),
    )
    db.add(scan)
    try:
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


async def band_mix_candidates(db: AsyncSession, target_date: date, scan: dict) -> list[dict]:
    """Latest predictions for target_date that fall in an active band mix."""
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

    rows = []
    for prediction in predictions:
        lohela_band = _probability_band(prediction.model_probability)
        market_band = _probability_band(prediction.source_implied_probability)
        mix = mixes.get(_mix_key(lohela_band, market_band))
        if mix is None:
            continue
        match = prediction.match
        pre_kickoff_quote = prediction.source_odds_at is None or prediction.source_odds_at <= match.kickoff_at
        rows.append({
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
            "pre_kickoff_quote": pre_kickoff_quote,
            "match_status": match.status.value,
            "home_goals": match.home_goals,
            "away_goals": match.away_goals,
            "live_phase": match.live_phase,
            "elapsed_minutes": match.elapsed_minutes,
            "result": _selection_result(match, prediction),
        })
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
