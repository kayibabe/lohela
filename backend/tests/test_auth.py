from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.security import get_optional_current_user, require_pro_access, require_research_access
from app.services.auth import hash_password, session_token_hash, verify_password


def _request_with_cookie(token: str) -> Request:
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"cookie", f"lohela_session={token}".encode())],
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
    })


def test_passwords_are_argon2_hashes_and_round_trip():
    password = "a sufficiently long password"
    encoded = hash_password(password)
    assert encoded.startswith("$argon2")
    assert encoded != password
    assert verify_password(encoded, password)
    assert not verify_password(encoded, "wrong password")


def test_session_token_is_stored_as_a_sha256_digest():
    token = "opaque-browser-token"
    digest = session_token_hash(token)
    assert len(digest) == 64
    assert digest != token
    assert digest == session_token_hash(token)


@pytest.mark.asyncio
async def test_free_users_cannot_use_pro_endpoints():
    with pytest.raises(HTTPException) as error:
        await require_pro_access(SimpleNamespace(role="user", plan="free"))
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_pro_and_admin_users_can_use_pro_endpoints():
    pro = SimpleNamespace(role="user", plan="pro")
    admin = SimpleNamespace(role="admin", plan="free")
    assert await require_pro_access(pro) is pro
    assert await require_pro_access(admin) is admin


@pytest.mark.asyncio
async def test_research_key_normalizes_bom_and_rejects_non_ascii_without_500(monkeypatch):
    from app.api import security

    monkeypatch.setattr(
        security,
        "settings",
        SimpleNamespace(research_api_key="\ufeffresearch-secret", app_env="production"),
    )
    request = _request_with_cookie("token")
    await require_research_access(request, "research-secret")
    with pytest.raises(HTTPException) as error:
        await require_research_access(request, "research‑secret")
    assert error.value.status_code == 401


@pytest.mark.asyncio
async def test_revoked_or_expired_sessions_are_not_authenticated(monkeypatch):
    from app.api import security

    monkeypatch.setattr(security, "settings", SimpleNamespace(auth_cookie_name="lohela_session"))
    expired = SimpleNamespace(
        revoked_at=None,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        user_id=1,
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=expired), get=AsyncMock())
    request = _request_with_cookie("token")
    assert await get_optional_current_user(request, db) is None
