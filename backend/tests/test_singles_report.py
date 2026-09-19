from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.security import require_research_access
from app.api.v1.singles import router
from app.database import get_db
from app.models import MatchStatus
from app.services.singles_report import adapt_rows, singles_report


NOW = datetime(2026, 9, 19, 10, tzinfo=timezone.utc)


def row(**changes):
    prediction = dict(id=1, market="over_1.5", selection="over_1.5",
                      model_probability=.75, source_decimal_odds=1.6,
                      created_at=NOW, source_odds_at=NOW-timedelta(minutes=5),
                      source_odds_id=12, model_version="test", as_of_at=None,
                      source_odds_provenance={"source_type": "api_football",
                                              "is_fallback": False, "bookmaker": "test"})
    prediction.update(changes)
    match = SimpleNamespace(id=1, kickoff_at=NOW+timedelta(hours=2),
                            status=MatchStatus.FINISHED, home_goals=2, away_goals=0)
    return SimpleNamespace(**prediction), match


def test_adapter_keeps_pending_losing_and_cancelled_candidates():
    p, match = row()
    candidates, outcomes, rejected = adapt_rows([(p, match)])
    assert len(candidates) == 1 and outcomes["1"] == "win" and not rejected
    match.home_goals = 0
    assert adapt_rows([(p, match)])[1]["1"] == "loss"
    match.status = MatchStatus.CANCELLED
    assert adapt_rows([(p, match)])[0] == candidates
    assert adapt_rows([(p, match)])[1]["1"] == "pending"


@pytest.mark.parametrize("changes", [
    {"source_odds_provenance": {}}, {"source_odds_id": None},
    {"source_odds_provenance": {"source_type": "historical_football_data", "is_fallback": False}},
    {"selection": "under_1.5"},
])
def test_adapter_rejects_unverified_price_and_market_mismatch(changes):
    candidates, _, rejected = adapt_rows([row(**changes)])
    assert not candidates and sum(rejected.values()) == 1


@pytest.mark.asyncio
async def test_report_uses_all_rows_and_returns_explicit_replay_status():
    result = SimpleNamespace(all=lambda: [row()])
    db = SimpleNamespace(execute=AsyncMock(return_value=result))
    report, selection = await singles_report(db, date(2026, 9, 19), date(2026, 9, 19),
                                              "test", as_of=NOW+timedelta(hours=3))
    assert report["wins"] == 1 and report["ready_for_promotion"] is False
    assert report["evidence_type"].startswith("retrospective")
    assert len(selection.picks) == 1
    query = str(db.execute.call_args.args[0])
    assert "matches.status =" not in query
    assert "home_goals IS NOT NULL" not in query


def test_api_auth_and_invalid_period(monkeypatch):
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr("app.api.security.settings.app_env", "production")
    monkeypatch.setattr("app.api.security.settings.research_api_key", "test-key")
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(execute=AsyncMock())
    client = TestClient(app)
    path = "/singles/research?start=2026-09-19&end=2026-09-18"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"X-Research-Key": "test-key"}).status_code == 400


def test_api_returns_no_bet_report():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_research_access] = lambda: None
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    response = TestClient(app).get("/singles/research?start=2026-09-19&end=2026-09-19")
    assert response.status_code == 200
    assert response.json()["selected"] == 0
    assert response.json()["no_bet_days"] == 1
    assert response.json()["roi"] is None
