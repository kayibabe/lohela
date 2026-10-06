from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1.admin import _ensure_active_admin_continuity


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"role": "user"}, {"account_status": "suspended"}])
async def test_last_active_admin_cannot_be_demoted_or_suspended(changes):
    db = SimpleNamespace(scalar=AsyncMock(return_value=0))
    user = SimpleNamespace(id=7, role="admin", account_status="active")

    with pytest.raises(HTTPException, match="last active admin") as error:
        await _ensure_active_admin_continuity(db, user, changes)

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_active_admin_change_is_allowed_when_another_active_admin_exists():
    db = SimpleNamespace(scalar=AsyncMock(return_value=1))
    user = SimpleNamespace(id=7, role="admin", account_status="active")

    await _ensure_active_admin_continuity(db, user, {"account_status": "suspended"})


@pytest.mark.asyncio
async def test_non_access_affecting_admin_update_does_not_query_for_other_admins():
    db = SimpleNamespace(scalar=AsyncMock())
    user = SimpleNamespace(id=7, role="admin", account_status="active")

    await _ensure_active_admin_continuity(db, user, {"plan": "pro"})

    db.scalar.assert_not_awaited()
