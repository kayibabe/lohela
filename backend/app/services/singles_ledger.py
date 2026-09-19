"""Exclusive-create local paper snapshots. No bookmaker or database writes."""
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.models import Match, MatchStatus
from app.services.settlement import evaluate_selection
from app.services.singles_report import adapt_rows, load_rows
from app.services.singles_research import Candidate, Policy, Selection, evaluate, select_candidates


def _canonical(payload):
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def write_snapshot(path: Path, selection: Selection, *, start: date, end: date,
                   captured_at: datetime, model_version: str) -> str:
    now = datetime.now(timezone.utc)
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("capture time must be timezone aware")
    if captured_at > now:
        raise ValueError("Cannot freeze a future capture time")
    _validate_metadata(selection, start, end, captured_at, model_version)
    for pick in selection.picks:
        if pick.kickoff_at <= now:
            raise ValueError("Only decisions captured before kickoff can be frozen")
    picks = []
    for pick in selection.picks:
        row = asdict(pick)
        for name in ("created_at", "quote_at", "decision_at", "kickoff_at"):
            row[name] = row[name].isoformat()
        picks.append(row)
    source_root = Path(__file__).resolve().parents[1]
    source_hashes = {
        str(p.relative_to(source_root)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(source_root.rglob("*.py"))
    }
    payload = {
        "schema": "singles-paper-v1", "captured_at": captured_at.isoformat(),
        "start": start.isoformat(), "end": end.isoformat(), "model_version": model_version,
        "policy": asdict(selection.policy), "policy_fingerprint": selection.policy.fingerprint,
        "picks": picks, "rejections": selection.rejections, "source_hashes": source_hashes,
        "mode": "paper_only", "promotion_ready": False,
    }
    digest = hashlib.sha256(_canonical(payload).encode()).hexdigest()
    if any(pick.kickoff_at <= datetime.now(timezone.utc) for pick in selection.picks):
        raise ValueError("Kickoff occurred before the snapshot could be written")
    # 'x' refuses overwrite, including repeat no-bet snapshots. Local hashes
    # detect accidental edits; they are not an externally trusted timestamp.
    with path.open("x", encoding="utf-8") as file:
        json.dump({"sha256": digest, "payload": payload}, file, indent=2, allow_nan=False)
        file.write("\n")
    return digest


def _validate_metadata(selection, start, end, captured, model_version):
    if end < start or (end - start).days > 366 or not model_version:
        raise ValueError("Invalid snapshot period or model identity")
    verified = select_candidates(selection.picks, as_of=captured, policy=selection.policy)
    if len(verified.picks) != len(selection.picks):
        raise ValueError("Snapshot contains ineligible or duplicate selections")
    for pick in selection.picks:
        kickoff_day = pick.kickoff_at.astimezone(ZoneInfo("Africa/Blantyre")).date()
        if (pick.decision_at != captured or pick.source_revision != model_version
                or not start <= kickoff_day <= end):
            raise ValueError("Snapshot decision, model identity, or date range mismatch")


def read_snapshot(path: Path):
    document = json.loads(path.read_text(encoding="utf-8"))
    payload = document["payload"]
    if hashlib.sha256(_canonical(payload).encode()).hexdigest() != document["sha256"]:
        raise ValueError("Snapshot integrity check failed")
    if payload["schema"] != "singles-paper-v1":
        raise ValueError("Unsupported snapshot schema")
    policy = Policy(**payload["policy"])
    if policy.fingerprint != payload["policy_fingerprint"]:
        raise ValueError("Policy fingerprint mismatch")
    candidates = []
    for original in payload["picks"]:
        row = dict(original)
        for name in ("created_at", "quote_at", "decision_at", "kickoff_at"):
            row[name] = datetime.fromisoformat(row[name])
        candidates.append(Candidate(**row))
    captured = datetime.fromisoformat(payload["captured_at"])
    if captured.tzinfo is None or captured.utcoffset() is None or captured > datetime.now(timezone.utc):
        raise ValueError("Invalid or future snapshot capture time")
    selected = select_candidates(candidates, as_of=captured, policy=policy)
    if len(selected.picks) != len(candidates) or any(
            c.kickoff_at <= captured or c.decision_at != captured for c in candidates):
        raise ValueError("Snapshot contains ineligible or duplicate selections")
    selection = Selection(selected.picks, payload["rejections"], policy)
    _validate_metadata(selection, date.fromisoformat(payload["start"]),
                       date.fromisoformat(payload["end"]), captured, payload["model_version"])
    return payload, selection


async def freeze_snapshot(db, path: Path, start: date, end: date, model_version: str):
    captured = datetime.now(timezone.utc)
    rows = await load_rows(db, start, end, model_version)
    candidates, _, adapter_rejections = adapt_rows(rows)
    # A snapshot is a NEW decision now. Earlier predictions do not establish a
    # prospective decision for this policy. Require fresh predictions as well.
    fresh = [replace(c, decision_at=captured) for c in candidates
             if c.created_at is not None and c.created_at.tzinfo is not None
             and timedelta(0) <= captured - c.created_at <= timedelta(hours=6)]
    # Use latest available prediction for each market at this decision time.
    latest = {}
    for c in fresh:
        key = (c.match_id, c.market)
        if key not in latest or (c.created_at, c.id) > (latest[key].created_at, latest[key].id):
            latest[key] = c
    selection = select_candidates(latest.values(), as_of=captured)
    selection.rejections.update(adapter_rejections)
    selection.rejections["stale_or_invalid_prediction_at_capture"] = len(candidates) - len(fresh)
    selection.rejections["superseded_prediction_at_capture"] = len(fresh) - len(latest)
    digest = write_snapshot(path, selection, start=start, end=end,
                            captured_at=captured, model_version=model_version)
    return {"path": str(path.resolve()), "sha256": digest, "picks": len(selection.picks),
            "mode": "paper_only", "rejections": selection.rejections}


async def review_snapshot(db, path: Path):
    payload, selection = read_snapshot(path)
    ids = [int(pick.match_id) for pick in selection.picks]
    matches = (await db.execute(select(Match).where(Match.id.in_(ids)))).scalars().all() if ids else []
    by_id = {str(match.id): match for match in matches}
    outcomes = {}
    for pick in selection.picks:
        match = by_id.get(pick.match_id)
        outcomes[pick.id] = "pending"
        if (match and match.status == MatchStatus.FINISHED
                and isinstance(match.home_goals, int) and match.home_goals >= 0
                and isinstance(match.away_goals, int) and match.away_goals >= 0):
            outcome = evaluate_selection(pick.market, match.home_goals, match.away_goals)
            outcomes[pick.id] = {"won": "win", "lost": "loss", "void": "void"}[outcome.value]
    report = evaluate(selection, outcomes, start_date=date.fromisoformat(payload["start"]),
                      end_date=date.fromisoformat(payload["end"]))
    report.update(evidence_type="frozen_local_paper_decisions", captured_at=payload["captured_at"],
                  evidence_class="frozen_local_paper_decisions",
                  model_version=payload["model_version"],
                  integrity_limit="Local hash is not independent proof of capture time or bookmaker execution.")
    report["limitations"][0] = "Local frozen decisions require independent prospective review before promotion."
    return report
