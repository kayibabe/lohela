from types import SimpleNamespace

import pytest

from scripts import worker_start


def test_worker_start_allows_current_schema(monkeypatch):
    monkeypatch.setattr(worker_start.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    worker_start.ensure_schema_is_current()


def test_worker_start_blocks_stale_schema(monkeypatch):
    monkeypatch.setattr(worker_start.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    with pytest.raises(SystemExit, match="schema is not at"):
        worker_start.ensure_schema_is_current()
