"""Database smoke test for generation -> publication -> settlement -> metrics."""

import os
from datetime import date, datetime, timezone

import pytest

from app.database import AsyncSessionLocal
from app.models import (
    Competition,
    LeagueTier,
    Match,
    MatchStatus,
    ModelRun,
    Odds,
    Prediction,
    QGrade,
    RunStatus,
    Team,
)
from app.services.performance import performance_summary
from app.services.calibration import aggregate_calibration_snapshots
from app.services.settlement import SettlementService
from app.services.ticket_publisher import TicketPublisher, get_latest_published_tickets


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION") != "1",
    reason="set RUN_DB_INTEGRATION=1 against a migrated disposable database",
)


@pytest.mark.asyncio
async def test_paper_ticket_generation_to_settlement():
    target = date(2026, 8, 28)
    markets = ["home_win", "over_1.5", "btts_yes", "double_chance_1x", "dnb_home"]
    async with AsyncSessionLocal() as db:
        transaction = await db.begin()
        try:
            model_run = ModelRun(
                target_date=target,
                model_version="test-flow",
                status=RunStatus.COMPLETED,
                config_snapshot={"test": True},
                completed_at=datetime.now(timezone.utc),
            )
            db.add(model_run)
            await db.flush()

            match_ids = []
            for index in range(10):
                competition = Competition(
                    api_football_id=900_000 + index,
                    name=f"Test League {index}",
                    country="Test",
                    tier=LeagueTier.TIER1,
                    reliability_score=0.95,
                )
                db.add(competition)
                await db.flush()
                home = Team(api_football_id=910_000 + index * 2, name=f"Home {index}", competition_id=competition.id)
                away = Team(api_football_id=910_001 + index * 2, name=f"Away {index}", competition_id=competition.id)
                db.add_all([home, away])
                await db.flush()
                match = Match(
                    api_football_id=920_000 + index,
                    competition_id=competition.id,
                    home_team_id=home.id,
                    away_team_id=away.id,
                    kickoff_at=datetime(2026, 8, 28, 12 + index % 8, tzinfo=timezone.utc),
                    status=MatchStatus.SCHEDULED,
                    season="2026",
                    data_quality_score=95,
                )
                db.add(match)
                await db.flush()
                market = markets[index % len(markets)]
                quote_at = datetime(2026, 8, 28, 9, tzinfo=timezone.utc)
                odds = Odds(
                    match_id=match.id,
                    bookmaker="Test Book",
                    market=market,
                    selection=market,
                    decimal_odds=1.55,
                    implied_probability=1 / 1.55,
                    source_type="test_closing",
                    is_fallback=False,
                    fetched_at=quote_at,
                )
                db.add(odds)
                await db.flush()
                db.add(
                    Prediction(
                        model_run_id=model_run.id,
                        match_id=match.id,
                        market=market,
                        selection=market,
                        model_version="test-flow",
                        poisson_prob=0.79,
                        bayes_prob=0.80,
                        xg_prob=0.81,
                        model_probability=0.80,
                        model_agreement=0.01,
                        edge=0.08,
                        expected_value=0.24,
                        q_score=88,
                        q_grade=QGrade.A,
                        q_component_weights={},
                        q_component_status={"model_set": "available"},
                        active_models=["poisson", "bayes", "xg"],
                        data_quality_snapshot={"score": 95},
                        source_odds_id=odds.id,
                        source_odds_at=quote_at,
                        source_decimal_odds=1.55,
                        source_implied_probability=1 / 1.55,
                        source_odds_provenance={"bookmaker": "Test Book"},
                    )
                )
                match_ids.append(match.id)
            await db.flush()

            generation = await TicketPublisher(db).generate_and_publish(target, model_run.id)
            assert generation.output_count == 4
            tickets = await get_latest_published_tickets(db, target)
            assert len(tickets) == 4
            assert all(len(ticket.publication_hash) == 64 for ticket in tickets)

            payloads = [
                {"match_id": match_id, "home_goals": 2, "away_goals": 1}
                for match_id in match_ids
            ]
            settlement = await SettlementService(db).ingest_results(payloads, "integration_test")
            assert settlement["tickets_settled"] == 4
            summary = await performance_summary(db)
            assert summary["settled_tickets"] == 4
            assert summary["wins"] == 4
            calibration = await aggregate_calibration_snapshots(db, target)
            assert calibration["prediction_sample_size"] == 10
            assert calibration["model_performance_rows"] > 0
            assert calibration["league_performance_rows"] == 10
            assert calibration["correlation_rows"] == 0
        finally:
            await transaction.rollback()
