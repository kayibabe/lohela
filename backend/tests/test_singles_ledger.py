"""Local ledger integrity, prospective boundaries, and outcome handling."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import MatchStatus
from app.services import singles_ledger as ledger
from app.services.singles_research import Candidate, Policy, Selection


def context():
    now = datetime.now(timezone.utc) - timedelta(seconds=1)
    row = Candidate("1", "10", "over_1.5", .75, 1.6, now, now, now,
                    now + timedelta(hours=2), "test-v1")
    return now, row


def write(path, row, now):
    return ledger.write_snapshot(path, Selection((row,), {}, Policy()), start=now.date(),
                                 end=(now + timedelta(days=1)).date(), captured_at=now,
                                 model_version="test-v1")


def test_roundtrip_refuses_overwrite_and_detects_tampering(tmp_path):
    now, row = context()
    path = tmp_path / "paper.json"
    digest = write(path, row, now)
    payload, selection = ledger.read_snapshot(path)
    assert selection.picks == (row,)
    assert payload["source_hashes"]
    with pytest.raises(FileExistsError):
        write(path, row, now)
    document = json.loads(path.read_text())
    assert document["sha256"] == digest
    document["payload"]["picks"][0]["odds"] = 10
    path.write_text(json.dumps(document))
    with pytest.raises(ValueError, match="integrity"):
        ledger.read_snapshot(path)


@pytest.mark.parametrize("change", ["future", "started", "bad_quote", "bad_odds", "wrong_model"])
def test_write_validates_at_actual_capture(tmp_path, change):
    now, row = context()
    if change == "future":
        now += timedelta(hours=1)
        row = replace(row, decision_at=now)
    elif change == "started":
        row = replace(row, kickoff_at=now + timedelta(milliseconds=1))
    elif change == "bad_quote":
        row = replace(row, quote_at=now + timedelta(seconds=1))
    elif change == "bad_odds":
        row = replace(row, odds=1.4)
    elif change == "wrong_model":
        row = replace(row, source_revision="other")
    with pytest.raises(ValueError):
        write(tmp_path / "paper.json", row, now)
    assert not (tmp_path / "paper.json").exists()


@pytest.mark.asyncio
async def test_freeze_excludes_stale_and_future_predictions(monkeypatch, tmp_path):
    now, row = context()
    candidates = [row, replace(row, id="2", match_id="20", created_at=now - timedelta(hours=7)),
                  replace(row, id="3", match_id="30", created_at=now + timedelta(hours=1))]
    monkeypatch.setattr(ledger, "load_rows", AsyncMock(return_value=[]))
    monkeypatch.setattr(ledger, "adapt_rows", lambda rows: (candidates, {}, {}))
    path = tmp_path / "paper.json"
    result = await ledger.freeze_snapshot(None, path, now.date(), (now + timedelta(days=1)).date(), "test-v1")
    assert result["picks"] == 1
    assert result["rejections"]["stale_or_invalid_prediction_at_capture"] == 2
    payload, selection = ledger.read_snapshot(path)
    assert selection.picks[0].decision_at == datetime.fromisoformat(payload["captured_at"])
    assert selection.picks[0].created_at == now


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(MatchStatus.CANCELLED, "pending"),
                                           (MatchStatus.FINISHED, "wins")])
async def test_review_only_settles_finished_results(tmp_path, status, expected):
    now, row = context()
    path = tmp_path / "paper.json"
    write(path, row, now)
    match = SimpleNamespace(id=10, status=status, home_goals=2, away_goals=0)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [match]))))
    report = await ledger.review_snapshot(db, path)
    assert report[expected] == 1
    assert report["voids"] == 0
    assert report["evidence_class"] == "frozen_local_paper_decisions"
    assert not report["ready_for_promotion"]
