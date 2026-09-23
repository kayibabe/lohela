"""Market ("honest") pricing: fair probabilities, gates, and tier construction."""

import asyncio
import math
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models import QGrade, TicketType
from app.services.accumulator_builder import (
    MARKET_TICKET_SPECS,
    TICKET_SPECS,
    AccumulatorBuilder,
    Leg,
    _market_rejection_reasons,
    _relax_spec,
    _research_market_rejection_reasons,
    active_ticket_specs,
    fair_market_probabilities,
    market_priced,
)

SPEC = {s.ticket_type: s for s in MARKET_TICKET_SPECS}
TARGET = datetime(2026, 9, 26).date()


def _leg(pid, *, match_id=None, market="double_chance_1x", odds=1.30, fair=0.74, model_p=0.90,
         q=40.0, models=("elo",)):
    match_id = match_id or pid
    return Leg(
        pid, match_id, match_id * 2, match_id * 2 + 1, f"H{match_id}", f"A{match_id}",
        f"L{match_id}", match_id, datetime(2026, 9, 26, 13, tzinfo=timezone.utc) + timedelta(minutes=pid),
        market, market, model_p, 0.05, odds, datetime.now(timezone.utc), q, QGrade.C,
        model_p - 1 / odds, model_p * odds - 1, list(models), 80.0, fair_probability=fair,
    )


@pytest.fixture(autouse=True)
def _market_mode(monkeypatch):
    monkeypatch.setattr(settings, "leg_probability_source", "market")


def test_fair_probabilities_remove_margin_per_market():
    implied = {"over_2.5": 1 / 1.80, "under_2.5": 1 / 2.00, "home_win": 1 / 2.0,
               "draw": 1 / 3.4, "away_win": 1 / 3.8, "btts_yes": 1 / 1.7}
    fair = fair_market_probabilities(implied)
    assert fair["over_2.5"] + fair["under_2.5"] == pytest.approx(1.0)
    assert fair["home_win"] + fair["draw"] + fair["away_win"] == pytest.approx(1.0)
    assert fair["double_chance_1x"] == pytest.approx(fair["home_win"] + fair["draw"])
    assert fair["over_2.5"] < implied["over_2.5"]  # margin removed, not added
    assert "btts_yes" not in fair  # no complement quoted -> can't be priced honestly


def test_market_priced_leg_is_repriced_and_keeps_model_for_audit():
    leg = market_priced(_leg(1, odds=1.30, fair=0.74, model_p=0.90))
    assert leg.model_probability == pytest.approx(0.74)
    assert leg.raw_model_probability == pytest.approx(0.90)
    assert leg.expected_value == pytest.approx(0.74 * 1.30 - 1)
    assert leg.edge == pytest.approx(0.74 - 1 / 1.30)


def test_market_gates_judge_price_and_data_not_the_model():
    safe = SPEC[TicketType.SAFE]
    # Low Q-score, one model, restricted market: all irrelevant in market mode.
    ok = market_priced(_leg(1, market="home_win", q=10.0, models=("elo",)))
    assert _market_rejection_reasons(ok, safe) == []
    assert _research_market_rejection_reasons(ok, safe) == []
    assert _research_market_rejection_reasons(ok, TICKET_SPECS[0]) == ["MARKET_RESTRICTED_FOR_RESEARCH"]
    assert "LEG_ODDS_OUT_OF_TIER_BAND" in _market_rejection_reasons(market_priced(_leg(2, odds=2.10, fair=0.46)), safe)
    heavy_margin = market_priced(_leg(3, odds=1.30, fair=0.70))  # EV = -9%
    assert "MARGIN_TOO_HIGH" in _market_rejection_reasons(heavy_margin, safe)
    too_good = market_priced(_leg(6, odds=1.45, fair=0.74))  # +7.3%: stale/out-of-line quote
    assert "PRICE_OUT_OF_LINE" in _market_rejection_reasons(too_good, safe)
    no_fair = _leg(4, fair=None)
    assert "MISSING_FAIR_PRICE" in _market_rejection_reasons(no_fair, safe)
    stale = market_priced(_leg(5))
    stale.source_odds_at = datetime.now(timezone.utc) - timedelta(hours=6)
    assert "STALE_ODDS" in _market_rejection_reasons(stale, safe)


def _builder(legs):
    builder = AccumulatorBuilder(db=None)

    async def resolve(target_date, model_run_id):
        return SimpleNamespace(id=1, model_version="t", config_snapshot={})

    async def load_all(target_date, run_id):
        return [Leg(**{**leg.__dict__}) for leg in legs]

    async def empty(*args):
        return {}

    builder._resolve_model_run = resolve
    builder._load_all_legs = load_all
    builder._load_calibration_gates = empty
    builder._load_correlation_coefficients = lambda: empty()
    return builder


def _slate():
    """A realistic mixed slate: short favourites, mid prices and longer shots."""
    legs, pid = [], 1
    for match in range(1, 25):
        for market, odds in (("double_chance_1x", 1.22 + 0.02 * (match % 5)),
                             ("over_1.5", 1.30 + 0.03 * (match % 4)),
                             ("over_2.5", 1.85 + 0.05 * (match % 5)),
                             ("away_win", 2.6 + 0.2 * (match % 4))):
            fair = (1 / odds) * 0.965  # ~3.5% margin on every quote
            legs.append(_leg(pid, match_id=match, market=market, odds=round(odds, 2), fair=fair))
            pid += 1
    return legs


def test_market_build_produces_three_tiers_inside_their_bands_with_honest_numbers():
    built = asyncio.run(_builder(_slate()).build(TARGET))
    assert built.selection_diagnostics["pricing"]["source"] == "market"
    tickets = {t.ticket_type: t for t in (built.conservative, built.balanced, built.aggressive)}
    assert all(tickets.values())
    for ticket_type, ticket in tickets.items():
        spec = SPEC[ticket_type]
        assert ticket.pricing == "market"
        assert ticket.relaxed is False  # a healthy slate needs no loosening
        assert spec.min_combined_odds <= ticket.combined_odds <= spec.max_combined_odds
        assert spec.min_legs <= len(ticket.legs) <= spec.max_legs
        # Honest: probability is the product of fair prices, EV is negative (margin).
        assert ticket.combined_probability == pytest.approx(math.prod(l.model_probability for l in ticket.legs), rel=1e-6)
        assert ticket.expected_value < 0
        assert all(l.raw_model_probability == pytest.approx(0.90) for l in ticket.legs)
    # Conservative is the most likely ticket: roughly a coin flip or better.
    assert tickets[TicketType.SAFE].adjusted_probability >= 0.40
    assert (tickets[TicketType.SAFE].adjusted_probability
            > tickets[TicketType.BALANCED].adjusted_probability
            > tickets[TicketType.AGGRESSIVE].adjusted_probability)
    match_sets = [{leg.match_id for leg in ticket.legs} for ticket in tickets.values()]
    assert all(len(left & right) <= 1 for i, left in enumerate(match_sets)
               for right in match_sets[i + 1:])
    assert not set.intersection(*match_sets)


def test_market_overlap_limit_keeps_unbuildable_tier_empty():
    from app.services.accumulator_builder import _evaluate_combo, _find_best_ticket

    # The only three distinct fixtures are already exposed twice. Different
    # markets cannot make a third public ticket sufficiently diversified.
    pool = [market_priced(_leg(i + 10 * j, match_id=i,
                              market=("double_chance_1x", "over_1.5", "home_win")[j],
                              odds=1.30, fair=0.74))
            for j in range(3) for i in (1, 2, 3)]
    prior = [_evaluate_combo(tuple(pool[j * 3:(j + 1) * 3]),
                             replace(SPEC[TicketType.SAFE], max_pair_correlation=1.0), {})
             for j in (0, 1)]
    assert all(prior)
    assert _find_best_ticket(pool, SPEC[TicketType.SAFE], {}, prior_tickets=prior,
                             max_shared_matches=1, max_match_exposure=2) is None


def test_research_qscore_builds_stay_model_priced():
    built = asyncio.run(_builder(_slate()).build(TARGET, research_min_qscore=0.0))
    assert built.selection_diagnostics["pricing"]["source"] == "model"


def test_active_specs_follow_the_setting(monkeypatch):
    assert active_ticket_specs() is MARKET_TICKET_SPECS
    monkeypatch.setattr(settings, "leg_probability_source", "model")
    assert active_ticket_specs() is TICKET_SPECS


def test_market_relaxation_widens_bands_but_never_below_two_legs():
    safe = SPEC[TicketType.SAFE]
    level3 = _relax_spec(safe, 3)
    assert level3.pricing == "market"
    assert level3.max_combined_odds > safe.max_combined_odds
    assert level3.max_leg_odds > safe.max_leg_odds
    assert level3.min_legs == 2
    assert level3.min_q_score == safe.min_q_score  # no model gates reintroduced
    # Model relaxation behaviour is unchanged: Balanced 80 -> 65 at level 3,
    # combined-odds cap 10.
    assert _relax_spec(TICKET_SPECS[1], 3).max_combined_odds == 10.0
    assert _relax_spec(TICKET_SPECS[1], 3).min_q_score == 65.0


def test_ticket_api_reports_pricing_from_the_ticket():
    from app.api.v1.tickets import _ticket_pricing

    assert _ticket_pricing(SimpleNamespace(pricing="market")) == "market"
    assert _ticket_pricing(SimpleNamespace(pricing=None)) == "model"
    assert _ticket_pricing(SimpleNamespace()) == "model"


def test_consensus_devigs_each_bookmaker_within_its_own_book():
    """Pairing best prices across books can 'remove' more than the margin
    (here over 2.10 at A with under 2.05 at B sums below 1); per-book de-vig
    then averaging can't, and it prices over_1.5 from under_1.5 quotes the
    model never predicts."""
    rows = [
        (7, "A", "over_2.5", 2.10), (7, "A", "under_2.5", 1.72),
        (7, "B", "over_2.5", 1.80), (7, "B", "under_2.5", 2.05),
        (7, "A", "over_1.5", 1.30), (7, "A", "under_1.5", 3.40),
    ]

    class _DB:
        async def execute(self, statement):
            return SimpleNamespace(all=lambda: rows)

    builder = AccumulatorBuilder(db=_DB())
    fair = asyncio.run(builder._consensus_fair_probabilities({7}))[7]
    book_a = (1 / 2.10) / (1 / 2.10 + 1 / 1.72)
    book_b = (1 / 1.80) / (1 / 1.80 + 1 / 2.05)
    assert fair["over_2.5"] == pytest.approx((book_a + book_b) / 2)
    assert fair["over_2.5"] + fair["under_2.5"] == pytest.approx(1.0)
    assert fair["over_1.5"] == pytest.approx((1 / 1.30) / (1 / 1.30 + 1 / 3.40))


def test_fair_priced_ticket_chance_is_not_haircut_by_correlation_heuristics():
    from app.services.accumulator_builder import _evaluate_combo

    legs = tuple(market_priced(_leg(i, match_id=i, odds=1.30, fair=0.75)) for i in (1, 2, 3))
    for leg in legs:
        leg.competition_id = 1 if leg.match_id < 3 else 2  # same-league pair
    # Same-league pairs carry a heuristic 0.03 coefficient: allowed by the
    # pair gate here, but it must not reduce the published chance.
    spec = replace(SPEC[TicketType.SAFE], max_pair_correlation=0.10)
    ticket = _evaluate_combo(legs, spec, {})
    assert ticket is not None
    assert ticket.adjusted_probability == pytest.approx(0.75 ** 3)
    assert ticket.correlation_penalty == 0.0


def test_unpriced_legs_are_counted_not_silently_dropped():
    legs = _slate()
    legs.append(_leg(9999, match_id=99, market="dnb_home", odds=1.40, fair=None))
    built = asyncio.run(_builder(legs).build(TARGET))
    counts = built.selection_diagnostics["safe"]["rejection_counts"]
    assert counts.get("MISSING_FAIR_PRICE") == 1
