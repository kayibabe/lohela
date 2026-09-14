"""Liveness must stay cheap; readiness must actually probe dependencies.

/health previously returned a static OK, so container healthchecks stayed green
while the app could not reach Postgres or Redis at all.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import Response, status

from app import main


@pytest.mark.asyncio
async def test_liveness_is_dependency_free():
    body = await main.health()
    assert body["status"] == "ok"


def _patch_dependencies(monkeypatch, *, db_ok: bool, redis_ok: bool):
    @asynccontextmanager
    async def _session():
        execute = AsyncMock()
        if not db_ok:
            execute.side_effect = ConnectionError("database down")
        yield type("Session", (), {"execute": execute})()

    monkeypatch.setattr(main, "AsyncSessionLocal", _session)

    client = AsyncMock()
    if not redis_ok:
        client.ping.side_effect = ConnectionError("redis down")
    monkeypatch.setattr(
        "app.services.cache._get_client", AsyncMock(return_value=client)
    )


@pytest.mark.asyncio
async def test_readiness_is_ok_when_both_dependencies_answer(monkeypatch):
    _patch_dependencies(monkeypatch, db_ok=True, redis_ok=True)
    response = Response()
    body = await main.readiness(response)

    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok", "redis": "ok"}
    assert response.status_code != status.HTTP_503_SERVICE_UNAVAILABLE


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("db_ok", "redis_ok", "failing"),
    [
        (False, True, "database"),
        (True, False, "redis"),
        (False, False, "database"),
    ],
)
async def test_readiness_reports_503_when_a_dependency_is_down(
    monkeypatch, db_ok, redis_ok, failing
):
    _patch_dependencies(monkeypatch, db_ok=db_ok, redis_ok=redis_ok)
    response = Response()
    body = await main.readiness(response)

    assert response.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
    assert body["status"] == "degraded"
    assert body["checks"][failing].startswith("error:")
