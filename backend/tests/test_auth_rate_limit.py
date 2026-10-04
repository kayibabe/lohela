from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import auth_rate_limit


class _Redis:
    def __init__(self):
        self.counts = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, seconds):
        return True

    async def ttl(self, key):
        return 42


def _request(ip="203.0.113.7"):
    return SimpleNamespace(headers={"x-forwarded-for": ip}, client=SimpleNamespace(host=ip))


@pytest.mark.asyncio
async def test_auth_limiter_rejects_only_after_the_configured_limit(monkeypatch):
    redis = _Redis()

    async def client():
        return redis

    monkeypatch.setattr(auth_rate_limit, "_get_client", client)
    await auth_rate_limit.enforce_auth_rate_limit(_request(), "login", 2)
    await auth_rate_limit.enforce_auth_rate_limit(_request(), "login", 2)
    with pytest.raises(HTTPException) as error:
        await auth_rate_limit.enforce_auth_rate_limit(_request(), "login", 2)
    assert error.value.status_code == 429
    assert error.value.headers["Retry-After"] == "42"


@pytest.mark.asyncio
async def test_auth_limiter_fails_open_when_redis_is_unavailable(monkeypatch):
    async def unavailable():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(auth_rate_limit, "_get_client", unavailable)
    await auth_rate_limit.enforce_auth_rate_limit(_request(), "register", 1)
