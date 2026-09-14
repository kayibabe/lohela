from unittest.mock import AsyncMock

import pytest

from app.services import fixture_ingestor
from app.services.fixture_ingestor import APIFootballClient


class _Response:
    status_code = 200
    headers = {}
    request = None

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "response": [
                {
                    "fixture": {"id": 123, "status": {"short": "FT"}},
                    "goals": {"home": 3, "away": 3},
                }
            ]
        }


class _HttpClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def get(self, endpoint: str, params: dict) -> _Response:
        self.calls.append((endpoint, params))
        return _Response()


@pytest.mark.asyncio
async def test_fixture_result_fetch_bypasses_stale_cache(monkeypatch):
    cache_get = AsyncMock(
        return_value={"response": [{"fixture": {"status": {"short": "NS"}}}]}
    )
    cache_set = AsyncMock()
    monkeypatch.setattr(fixture_ingestor._cache, "get", cache_get)
    monkeypatch.setattr(fixture_ingestor._cache, "set", cache_set)
    monkeypatch.setattr(fixture_ingestor.asyncio, "sleep", AsyncMock())

    http = _HttpClient()
    client = APIFootballClient()
    client._client = http

    fixture = await client.get_fixture_result(123)

    assert fixture["fixture"]["status"]["short"] == "FT"
    assert fixture["goals"] == {"home": 3, "away": 3}
    assert http.calls == [("/fixtures", {"id": 123})]
    cache_get.assert_not_awaited()
    cache_set.assert_not_awaited()
