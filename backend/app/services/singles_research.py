"""Outcome-blind singles research. This module never authorizes publication or staking."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import math
import random
import re
from typing import Iterable, Mapping
from zoneinfo import ZoneInfo


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class Candidate:
    id: str
    match_id: str
    market: str
    probability: float
    odds: float
    created_at: datetime
    quote_at: datetime
    decision_at: datetime
    kickoff_at: datetime
    source_revision: str  # Supplied model identity; not necessarily a Git SHA.
    historical: bool = False


@dataclass(frozen=True)
class Policy:
    version: str = "singles-v1-research"
    min_probability: float = 0.70
    min_odds: float = 1.50
    price_haircut: float = 0.02
    min_ev: float = 0.0
    max_quote_age_hours: float = 6.0

    def __post_init__(self):
        values = (self.min_probability, self.min_odds, self.price_haircut,
                  self.min_ev, self.max_quote_age_hours)
        if not all(_finite(v) for v in values):
            raise ValueError("Policy parameters must be finite numbers")
        if not (0 <= self.min_probability <= 1 and self.min_odds >= 1.50
                and 0 <= self.price_haircut < 1 and self.min_ev >= 0
                and self.max_quote_age_hours > 0 and self.version):
            raise ValueError("Invalid research policy")

    def conservative_odds(self, odds: float) -> float:
        return 1 + (odds - 1) * (1 - self.price_haircut)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Selection:
    picks: tuple[Candidate, ...]
    rejections: dict[str, int]
    policy: Policy


def _aware(value) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _binary_market(market: str) -> bool:
    return isinstance(market, str) and (market in {
        "home_win", "draw", "away_win", "double_chance_1x", "double_chance_x2",
        "double_chance_12", "btts_yes", "btts_no",
    } or re.fullmatch(r"(?:over|under)_[0-9]+[._]5", market) is not None)


def select_candidates(candidates: Iterable[Candidate], *, as_of: datetime,
                      policy: Policy = Policy()) -> Selection:
    """Select earliest eligible decision per fixture, then highest conservative EV.

    Later observations cannot replace an eligible earlier decision. Outcome fields
    are deliberately absent. Input IDs must be unique; duplicates fail closed.
    """
    if not _aware(as_of):
        raise ValueError("as_of must be timezone-aware")
    candidates = tuple(candidates)
    duplicate_ids = Counter(c.id for c in candidates)
    rejected = Counter()
    eligible = defaultdict(list)
    for c in candidates:
        reason = None
        if duplicate_ids[c.id] > 1:
            reason = "duplicate_id"
        elif not c.id or not c.match_id or not c.source_revision:
            reason = "missing_identity"
        elif c.historical:
            reason = "historical_backfill"
        elif not all(_aware(t) for t in (c.created_at, c.quote_at, c.decision_at, c.kickoff_at)):
            reason = "missing_or_naive_timestamp"
        elif not (c.created_at <= c.decision_at < c.kickoff_at and c.quote_at <= c.decision_at):
            reason = "invalid_time_provenance"
        elif c.decision_at > as_of:
            reason = "future_decision"
        elif c.decision_at - c.quote_at > timedelta(hours=policy.max_quote_age_hours):
            reason = "stale_quote"
        elif not _binary_market(c.market):
            reason = "unsupported_market"
        elif not _finite(c.probability) or not _finite(c.odds) or not 0 <= c.probability <= 1:
            reason = "invalid_numeric_value"
        elif c.odds <= policy.min_odds:
            reason = "odds_below_or_equal_floor"
        elif c.probability < policy.min_probability:
            reason = "probability_below_floor"
        elif c.probability * policy.conservative_odds(c.odds) - 1 <= policy.min_ev:
            reason = "insufficient_conservative_ev"
        if reason:
            rejected[reason] += 1
        else:
            eligible[c.match_id].append(c)
    picks = []
    for rows in eligible.values():
        earliest = min(c.decision_at for c in rows)
        first = [c for c in rows if c.decision_at == earliest]
        rejected["later_eligible_snapshot"] += len(rows) - len(first)
        first.sort(key=lambda c: (-(c.probability * policy.conservative_odds(c.odds) - 1),
                                  -c.probability, c.market, str(c.id)))
        picks.append(first[0])
        rejected["other_market_same_match"] += len(first) - 1
    picks.sort(key=lambda c: (c.kickoff_at, str(c.match_id), str(c.id)))
    return Selection(tuple(picks), {k: v for k, v in sorted(rejected.items()) if v}, policy)


def _wilson(wins: int, count: int):
    if not count:
        return None
    z = 1.959963984540054
    p = wins / count
    denominator = 1 + z * z / count
    center = (p + z * z / (2 * count)) / denominator
    radius = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / denominator
    return [max(0.0, center - radius), min(1.0, center + radius)]


def evaluate(selection: Selection, outcomes: Mapping[str, str], *, start_date: date,
             end_date: date, timezone_name: str = "Africa/Blantyre",
             bootstrap_samples: int = 2000, seed: int = 1729) -> dict:
    """Join outcomes only after selection. Unit stake; pending stakes are unresolved.

    Daily ROI bootstrap resamples whole calendar dates, including no-bet dates.
    ROI denominator includes resolved void stakes (zero profit). Hit rate and
    calibration exclude voids. Profit is a paper result at quoted prices.
    """
    if end_date < start_date or not isinstance(bootstrap_samples, int) or not 100 <= bootstrap_samples <= 100_000:
        raise ValueError("Invalid date range or bootstrap sample count")
    tz = ZoneInfo(timezone_name)
    days = {}
    current = start_date
    while current <= end_date:
        days[current.isoformat()] = dict(date=current.isoformat(), bets=0, wins=0, losses=0,
                                        voids=0, pending=0, resolved_stakes=0, profit=0.0)
        current += timedelta(days=1)
    counts = Counter()
    brier = 0.0
    cumulative = peak = drawdown = conservative_profit = 0.0
    losses = []
    for c in sorted(selection.picks, key=lambda c: (c.kickoff_at, str(c.match_id), str(c.id))):
        key = c.kickoff_at.astimezone(tz).date().isoformat()
        if key not in days:
            continue
        outcome = outcomes.get(c.id, "pending")
        if outcome not in {"win", "loss", "void", "pending"}:
            raise ValueError(f"Unknown outcome for candidate {c.id}")
        d = days[key]
        d["bets"] += 1
        counts[outcome] += 1
        d[{"win": "wins", "loss": "losses", "void": "voids", "pending": "pending"}[outcome]] += 1
        if outcome == "pending":
            continue
        profit = c.odds - 1 if outcome == "win" else -1.0 if outcome == "loss" else 0.0
        d["resolved_stakes"] += 1
        d["profit"] += profit
        cumulative += profit
        conservative_profit += (selection.policy.conservative_odds(c.odds) - 1
                                if outcome == "win" else profit)
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
        if outcome in {"win", "loss"}:
            brier += (c.probability - int(outcome == "win")) ** 2
        if outcome == "loss":
            losses.append({"id": c.id, "match_id": c.match_id, "market": c.market,
                           "odds": c.odds, "probability": c.probability, "date": key})
    resolved = counts["win"] + counts["loss"] + counts["void"]
    binary = counts["win"] + counts["loss"]
    daily = list(days.values())
    rng = random.Random(seed)
    bootstrap = []
    for _ in range(bootstrap_samples):
        sample = rng.choices(daily, k=len(daily))
        stake = sum(d["resolved_stakes"] for d in sample)
        if stake:
            bootstrap.append(sum(d["profit"] for d in sample) / stake)
    bootstrap.sort()
    interval = ([bootstrap[int((len(bootstrap) - 1) * q)] for q in (0.025, 0.975)]
                if bootstrap and sum(d["resolved_stakes"] > 0 for d in daily) >= 2 else None)
    return {
        "status": "research_only", "ready_for_promotion": False,
        "evidence_class": "retrospective_policy_replay_of_prospective_predictions",
        "policy": asdict(selection.policy), "policy_fingerprint": selection.policy.fingerprint,
        "source_identity_note": "source_revision identifies the supplied model version, not necessarily source code",
        "start_date": start_date.isoformat(), "end_date": end_date.isoformat(), "timezone": timezone_name,
        "selected": sum(counts.values()), "wins": counts["win"], "losses": counts["loss"],
        "voids": counts["void"], "pending": counts["pending"], "resolved_stakes": resolved,
        "profit_units": cumulative, "roi": cumulative / resolved if resolved else None,
        "conservative_profit_units": conservative_profit,
        "conservative_roi": conservative_profit / resolved if resolved else None,
        "hit_rate": counts["win"] / binary if binary else None,
        "hit_rate_wilson_95": _wilson(counts["win"], binary),
        "brier_score": brier / binary if binary else None,
        "max_drawdown_units": drawdown, "drawdown_order": "kickoff_order_of_resolved_picks_not_settlement_time",
        "daily_bootstrap_roi_95": interval, "bootstrap_seed": seed,
        "bootstrap_samples": bootstrap_samples, "bootstrap_valid_samples": len(bootstrap),
        "no_bet_days": sum(d["bets"] == 0 for d in daily),
        "loss_free_resolved_bet_days": sum(d["resolved_stakes"] > 0 and d["losses"] == 0 and d["pending"] == 0 for d in daily),
        "daily": daily, "loss_details": losses, "rejections": selection.rejections,
        "limitations": ["Policy replay is not prospective frozen-policy evidence.",
                        "Quoted odds do not prove executable fills or profits.",
                        "Intervals do not account for policy search or model selection; sparse samples are unreliable."],
    }
