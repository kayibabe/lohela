"""Minimum-daily-tickets guarantee: rolling horizon, relaxation, league supply.

Regression suite for the 2026-09-22..10-01 zero-ticket week (Sept FIFA
window: 0-5 tracked fixtures a day).
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models import QGrade, TicketType
from app.services import ticket_horizon
from app.services.accumulator_builder import (
    AccumulatorBuilder,
    DailyTickets,
    Leg,
    _cat_day_offset,
)
from app.services.fixture_ingestor import _season_segments

TARGET = date(2026, 9, 24)


@pytest.fixture(autouse=True)
def _model_pricing(monkeypatch):
    """These tests cover the horizon/relaxation machinery with model-priced
    legs; market pricing has its own suite (test_honest_pricing.py)."""
    monkeypatch.setattr(settings, "leg_probability_source", "model")
_SAFE_MARKETS = ("over_1.5", "btts_yes", "away_win", "over_2.5", "dnb_away")


def _leg(prediction_id: int, *, day_offset: int = 0, market: str | None = None, q: float = 90.0) -> Leg:
    match_id = prediction_id
    return Leg(
        prediction_id=prediction_id,
        match_id=match_id,
        home_team_id=match_id * 2,
        away_team_id=match_id * 2 + 1,
        home_team=f"Home {match_id}",
        away_team=f"Away {match_id}",
        competition=f"League {match_id}",
        competition_id=match_id,  # distinct leagues -> independent legs
        # 13:00 UTC = 15:00 CAT: comfortably inside the CAT day.
        kickoff_at=datetime(2026, 9, 24, 13, tzinfo=timezone.utc) + timedelta(days=day_offset),
        market=market or _SAFE_MARKETS[prediction_id % len(_SAFE_MARKETS)],
        selection="x",
        model_probability=0.72,
        model_agreement=0.05,
        best_odds=1.6,
        source_odds_at=datetime.now(timezone.utc),
        q_score=q,
        q_grade=QGrade.A,
        edge=0.06,
        expected_value=0.72 * 1.6 - 1.0,
        active_models=["poisson", "bayes", "xg"],
        data_quality_score=90.0,
    )


def _empty_output() -> dict:
    return {t: None for t in TicketType}


def _fake_builder(pools: dict[date, list[Leg]]) -> AccumulatorBuilder:
    """AccumulatorBuilder with its four DB reads replaced by in-memory pools."""
    builder = AccumulatorBuilder(db=None)
    resolved: list[date] = []

    async def resolve(target_date, model_run_id):
        resolved.append(target_date)
        if target_date not in pools:
            return None
        return SimpleNamespace(id=1000 + target_date.day, model_version="t", config_snapshot={})

    async def load(target_date, run_id, pricing="model"):
        return list(pools[target_date])

    async def calibration(*args):
        return {}

    async def coefficients():
        return {}

    builder._resolve_model_run = resolve
    builder._load_qualified_legs = load
    builder._load_calibration_gates = calibration
    builder._load_correlation_coefficients = coefficients
    builder.resolved_dates = resolved
    return builder


# ── CAT day arithmetic ──────────────────────────────────────────────────────

def test_cat_day_offset_uses_cat_not_utc_midnight():
    assert _cat_day_offset(datetime(2026, 9, 24, 13, tzinfo=timezone.utc), TARGET) == 0
    # 21:59 UTC is still 23:59 CAT on the target day...
    assert _cat_day_offset(datetime(2026, 9, 24, 21, 59, tzinfo=timezone.utc), TARGET) == 0
    # ...but 22:30 UTC is 00:30 CAT the next day.
    assert _cat_day_offset(datetime(2026, 9, 24, 22, 30, tzinfo=timezone.utc), TARGET) == 1
    assert _cat_day_offset(datetime(2026, 9, 26, 13, tzinfo=timezone.utc), TARGET) == 2


# ── Relaxation must respect the research market restriction ─────────────────

def test_relaxation_never_uses_research_restricted_markets():
    """Before this fix the relaxation ladder skipped the restricted-market
    filter the full-strength pass applies, so a thin day could publish e.g.
    home_win/draw legs that the research policy excludes."""
    restricted = settings.research_restricted_markets
    pool = [_leg(i, market=restricted[i % len(restricted)]) for i in range(1, 16)]
    output = _empty_output()
    AccumulatorBuilder(db=None)._apply_minimum_ticket_relaxation(output, pool, {}, {})
    assert all(ticket is None for ticket in output.values())


def test_later_tier_is_found_when_prior_tiers_hold_the_top_legs():
    """Beam starvation: the best-ranked legs are already exposed by earlier
    public tiers, so every top-800 partial used to contain a blocked leg and
    a perfectly buildable tier came back None."""
    from app.services.accumulator_builder import TICKET_SPECS, _evaluate_combo, _find_best_ticket

    spec = {s.ticket_type: s for s in TICKET_SPECS}
    top = [_leg(i, q=95.0) for i in range(1, 12)]  # outrank everything below
    prior = _evaluate_combo(tuple(top[:4]), spec[TicketType.BALANCED], {})
    prior_2 = _evaluate_combo(tuple(top[4:11]), spec[TicketType.BALANCED], {}) or _evaluate_combo(
        tuple(top[4:9]), spec[TicketType.AGGRESSIVE], {}
    )
    assert prior is not None and prior_2 is not None
    rest = [_leg(i, q=88.0) for i in range(20, 25)]
    ticket = _find_best_ticket(
        top + rest,
        spec[TicketType.AGGRESSIVE],
        {},
        prior_tickets=[prior, prior_2],
        max_shared_matches=settings.max_shared_matches_between_tickets,
        max_match_market_exposure=settings.max_public_ticket_exposure_per_match_market,
    )
    assert ticket is not None
    claimed = {(leg.match_id, leg.market) for t in (prior, prior_2) for leg in t.legs}
    assert not claimed & {(leg.match_id, leg.market) for leg in ticket.legs}


# ── Rolling horizon inside build() ──────────────────────────────────────────

def test_build_uses_horizon_only_when_target_day_is_short():
    today_only = {TARGET: [_leg(i) for i in range(1, 16)]}
    builder = _fake_builder({**today_only, TARGET + timedelta(days=1): [_leg(100 + i, day_offset=1) for i in range(15)]})
    built = asyncio.run(builder.build(TARGET, horizon_dates=[TARGET + timedelta(days=1)]))
    assert built.public_count() == 3
    assert built.horizon_dates == []  # never consulted
    assert builder.resolved_dates == [TARGET]
    assert all(t.horizon_days == 0 for t in (built.conservative, built.balanced, built.aggressive))


def test_build_fills_every_public_tier_from_the_horizon_on_an_empty_day():
    """The 2026-09-23 shape: today has a completed run but no legs at all."""
    d1, d2 = TARGET + timedelta(days=1), TARGET + timedelta(days=2)
    pools = {
        TARGET: [],
        d1: [_leg(10 + i, day_offset=1) for i in range(4)],
        d2: [_leg(20 + i, day_offset=2) for i in range(12)],
    }
    built = asyncio.run(_fake_builder(pools).build(TARGET, horizon_dates=[d1, d2]))
    tickets = (built.conservative, built.balanced, built.aggressive)
    assert built.public_count() == 3
    assert built.horizon_dates == [d1, d2]
    for ticket in tickets:
        assert 1 <= ticket.horizon_days <= 2
        assert ticket.horizon_days == max(_cat_day_offset(l.kickoff_at, TARGET) for l in ticket.legs)
        # Full strength sufficed on the wider pool: no gate was loosened.
        assert ticket.relaxed is False
    # Public portfolio still never repeats a match/market across tiers.
    keys = [(l.match_id, l.market) for t in tickets for l in t.legs]
    assert len(keys) == len(set(keys))
    assert built.selection_diagnostics["horizon"]["horizon_pool_count"] == 16


def test_horizon_legs_still_face_every_per_leg_gate():
    stale = [_leg(30 + i, day_offset=1) for i in range(15)]
    for leg in stale:
        leg.source_odds_at = datetime.now(timezone.utc) - timedelta(hours=6)
    d1 = TARGET + timedelta(days=1)
    built = asyncio.run(_fake_builder({TARGET: [], d1: stale}).build(TARGET, horizon_dates=[d1]))
    assert built.public_count() == 0


def test_horizon_disabled_for_research_qscore_builds():
    d1 = TARGET + timedelta(days=1)
    builder = _fake_builder({TARGET: [], d1: [_leg(40 + i, day_offset=1) for i in range(15)]})
    built = asyncio.run(builder.build(TARGET, research_min_qscore=70.0, horizon_dates=[d1]))
    assert built.public_count() == 0
    assert builder.resolved_dates == [TARGET]


# ── Orchestration: extend one day at a time, tolerate a bad day ─────────────

@asynccontextmanager
async def _null_session():
    yield None


def test_resolve_horizon_dates_extends_until_floor_then_stops(monkeypatch):
    counts = {0: 0, 1: 1, 2: 3}  # public tickets available by horizon length
    builds = []

    async def fake_build(self, target_date, model_run_id=None, research_min_qscore=None, horizon_dates=None):
        n = len(horizon_dates or [])
        builds.append(n)
        tickets = [object() if i < counts[n] else None for i in range(3)]
        return DailyTickets(target_date, 7, *tickets, None, 0)

    prepared, ingested = [], []

    async def prepare(factory, day):
        prepared.append(day)
        return {"date": day.isoformat()}

    async def ingest(factory, start, end):
        ingested.append((start, end))
        return {"ingested": 1}

    monkeypatch.setattr(AccumulatorBuilder, "build", fake_build)
    monkeypatch.setattr(settings, "ticket_horizon_max_days", 4)
    horizon, reports = asyncio.run(
        ticket_horizon.resolve_horizon_dates(_null_session, TARGET, 7, prepare=prepare, ingest=ingest)
    )
    assert horizon == [TARGET + timedelta(days=1), TARGET + timedelta(days=2)]
    assert prepared == horizon  # day 3 and 4 never prepared
    assert ingested == [(TARGET + timedelta(days=1), TARGET + timedelta(days=4))]
    assert builds == [0, 1, 2]


def test_resolve_horizon_dates_skips_a_failed_day_and_noops_on_full_day(monkeypatch):
    async def fake_build(self, target_date, model_run_id=None, research_min_qscore=None, horizon_dates=None):
        n = len(horizon_dates or [])
        tickets = [object() if n >= 1 else None for _ in range(3)]
        return DailyTickets(target_date, 7, *tickets, None, 0)

    async def prepare(factory, day):
        if day == TARGET + timedelta(days=1):
            raise RuntimeError("odds API down")
        return {"date": day.isoformat()}

    async def ingest(factory, start, end):
        return {}

    monkeypatch.setattr(AccumulatorBuilder, "build", fake_build)
    horizon, reports = asyncio.run(
        ticket_horizon.resolve_horizon_dates(_null_session, TARGET, 7, prepare=prepare, ingest=ingest)
    )
    assert horizon == [TARGET + timedelta(days=2)]
    assert any(r.get("error") == "odds API down" for r in reports)

    async def full_build(self, target_date, model_run_id=None, research_min_qscore=None, horizon_dates=None):
        return DailyTickets(target_date, 7, object(), object(), object(), None, 0)

    async def must_not_run(*args, **kwargs):
        raise AssertionError("horizon work on a full day")

    monkeypatch.setattr(AccumulatorBuilder, "build", full_build)
    assert asyncio.run(
        ticket_horizon.resolve_horizon_dates(_null_session, TARGET, 7, prepare=must_not_run, ingest=must_not_run)
    ) == ([], [])


def test_safe_wrapper_contains_horizon_failures_and_skips_refused_pipelines(monkeypatch):
    from app.services.ticket_publisher import TicketPublisher

    async def ok_context(self, *args):
        return None

    async def boom(*args, **kwargs):
        raise RuntimeError("db blip")

    monkeypatch.setattr(TicketPublisher, "_validate_pipeline_context", ok_context)
    monkeypatch.setattr(ticket_horizon, "resolve_horizon_dates", boom)
    assert asyncio.run(
        ticket_horizon.resolve_horizon_dates_safely(_null_session, TARGET, 7, 11)
    ) == ([], [{"horizon_error": "db blip"}])

    async def refused(self, *args):
        raise ValueError("Refusing publication: pipeline run is partial")

    async def must_not_run(*args, **kwargs):
        raise AssertionError("horizon work for a refused pipeline")

    monkeypatch.setattr(TicketPublisher, "_validate_pipeline_context", refused)
    monkeypatch.setattr(ticket_horizon, "resolve_horizon_dates", must_not_run)
    assert asyncio.run(
        ticket_horizon.resolve_horizon_dates_safely(_null_session, TARGET, 7, 11)
    ) == ([], [])


# ── Season calendar per league ──────────────────────────────────────────────

class _SeasonClient:
    def __init__(self, seasons):
        self.seasons = seasons

    async def get_league_seasons(self, league_id):
        if isinstance(self.seasons, Exception):
            raise self.seasons
        return self.seasons


_EURO = [
    {"year": 2025, "start": "2025-08-15", "end": "2026-05-24", "current": False},
    {"year": 2026, "start": "2026-08-14", "end": "2027-05-23", "current": True},
]
_CALENDAR = [
    {"year": 2025, "start": "2025-01-25", "end": "2025-12-07", "current": False},
    {"year": 2026, "start": "2026-01-28", "end": "2026-12-02", "current": True},
]


def test_season_segments_handle_calendar_year_leagues_in_spring():
    """The old July cut-over asked for season 2025 on 2026-03-10: empty for Brazil."""
    segments = asyncio.run(_season_segments(_SeasonClient(_CALENDAR), 71, date(2026, 3, 10), date(2026, 3, 10)))
    assert segments == [(2026, date(2026, 3, 10), date(2026, 3, 10))]


def test_season_segments_split_a_backfill_across_seasons():
    segments = asyncio.run(_season_segments(_SeasonClient(_EURO), 41, date(2026, 1, 1), date(2026, 9, 22)))
    assert segments == [
        (2025, date(2026, 1, 1), date(2026, 5, 24)),
        (2026, date(2026, 8, 14), date(2026, 9, 22)),
    ]


def test_season_segments_fall_back_between_seasons_and_on_errors():
    # Off-season gap: ask the season API-Football flags as current.
    assert asyncio.run(
        _season_segments(_SeasonClient(_EURO), 41, date(2026, 6, 10), date(2026, 6, 12))
    ) == [(2026, date(2026, 6, 10), date(2026, 6, 12))]
    # No calendar at all: the legacy July cut-over.
    segments = asyncio.run(
        _season_segments(_SeasonClient(RuntimeError("down")), 41, date(2026, 9, 24), date(2026, 9, 24))
    )
    assert segments[0][0] == 2026


def test_latest_season_is_open_ended_past_its_scheduled_end():
    """API "end" = last fixture scheduled so far (e.g. Serie C before playoffs)."""
    segments = asyncio.run(_season_segments(_SeasonClient(_EURO), 138, date(2027, 5, 20), date(2027, 6, 5)))
    assert segments == [(2026, date(2027, 5, 20), date(2027, 6, 5))]


# ── Review follow-ups: ledgers must not double count / re-time horizon picks ─

def test_published_horizon_leg_is_filed_under_its_match_day():
    from app.services.recommendation_ledger import _cat_date

    # 22:30 UTC on the 25th is 00:30 CAT on the 26th.
    assert _cat_date(datetime(2026, 9, 25, 22, 30, tzinfo=timezone.utc)) == date(2026, 9, 26)
    assert _cat_date(datetime(2026, 9, 25, 13, 0)) == date(2026, 9, 25)  # naive = UTC
    assert _cat_date(None) is None


def test_singles_ledger_excludes_rolling_horizon_runs():
    from unittest.mock import AsyncMock

    from sqlalchemy.dialects import postgresql

    from app.services.singles_report import load_rows

    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    asyncio.run(load_rows(db, date(2026, 9, 19), date(2026, 9, 19), "test"))
    statement = db.execute.call_args.args[0]
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "LEFT OUTER JOIN model_runs" in sql
    assert ticket_horizon.HORIZON_RUN_TRIGGER in sql
    assert "model_runs.id IS NULL" in sql  # historical rows still reach the adapter


# ── Seeding reaches already-seeded databases ────────────────────────────────

def test_seed_missing_competitions_only_adds_absent_leagues():
    from app.services.seed import COMPETITIONS, seed_missing_competitions

    present = {row["api_football_id"] for row in COMPETITIONS if row["api_football_id"] != 41}

    class _Result:
        def scalars(self):
            return SimpleNamespace(all=lambda: list(present))

    class _Session:
        def __init__(self):
            self.added, self.commits = [], 0

        async def execute(self, statement):
            return _Result()

        def add(self, row):
            self.added.append(row)

        async def commit(self):
            self.commits += 1

    session = _Session()
    assert asyncio.run(seed_missing_competitions(session)) == [41]
    assert [row.api_football_id for row in session.added] == [41]
    assert session.added[0].reliability_score == pytest.approx(0.8)
    assert session.commits == 1


def test_every_tracked_league_has_a_seed_row():
    from app.services.seed import COMPETITIONS

    seeded = {row["api_football_id"] for row in COMPETITIONS}
    assert set(settings.tracked_league_ids) <= seeded
    assert len(settings.tracked_league_ids) == len(set(settings.tracked_league_ids))
