import pytest

from scripts import worker_start


def test_worker_start_allows_current_schema(monkeypatch):
    monkeypatch.setattr(worker_start, "schema_heads", lambda: ({"head"}, {"head"}))
    worker_start.ensure_schema_is_current()


def test_worker_start_blocks_stale_schema(monkeypatch):
    monkeypatch.setattr(worker_start, "schema_heads", lambda: ({"old"}, {"head"}))
    with pytest.raises(SystemExit, match="schema is not at"):
        worker_start.ensure_schema_is_current()


def test_worker_start_blocks_when_schema_cannot_be_verified(monkeypatch):
    def unavailable():
        raise OSError("database unavailable")

    monkeypatch.setattr(worker_start, "schema_heads", unavailable)
    with pytest.raises(SystemExit, match="could not verify"):
        worker_start.ensure_schema_is_current()
