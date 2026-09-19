import httpx
import pytest

from app.main import app


@pytest.mark.asyncio
async def test_responses_include_correlation_and_baseline_security_headers():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers={"X-Request-ID": "not-a-uuid"})

    assert response.status_code == 200
    assert response.headers["x-request-id"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


@pytest.mark.asyncio
async def test_valid_correlation_id_is_preserved():
    request_id = "12345678-1234-5678-1234-567812345678"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health", headers={"X-Request-ID": request_id})

    assert response.headers["x-request-id"] == request_id
