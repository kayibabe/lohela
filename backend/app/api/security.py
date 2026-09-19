"""Minimal internal research access gate for state-changing endpoints."""

import hmac
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import User, UserSession
from app.services.auth import session_token_hash


def _normalize_research_key(value: str | None) -> str:
    """Remove transport-added markers without weakening the secret comparison."""
    if not value:
        return ""
    # Railway/PowerShell environments can preserve a UTF-8 BOM or its common
    # mojibake representation at the beginning of a copied secret. Secrets
    # themselves are expected to be ASCII; interior non-ASCII characters are
    # left intact and will fail closed below.
    return value.strip().lstrip("\ufeff").lstrip("ï»¿").strip()


async def require_research_access(
    request: Request,
    x_research_key: str | None = Header(default=None),
) -> None:
    configured = _normalize_research_key(settings.research_api_key)
    supplied = _normalize_research_key(x_research_key)
    if configured and supplied:
        try:
            if configured.isascii() and supplied.isascii() and hmac.compare_digest(configured, supplied):
                return
        except TypeError:
            # Never turn malformed secret material into a 500 response.
            pass
    if settings.app_env == "development" and not configured:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid X-Research-Key required",
    )


async def get_optional_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User | None:
    token = request.cookies.get(settings.auth_cookie_name)
    if not token:
        return None
    session = await db.scalar(
        select(UserSession)
        .where(
            UserSession.token_hash == session_token_hash(token),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > datetime.now(timezone.utc),
        )
    )
    if not session:
        return None
    user = await db.get(User, session.user_id)
    if not user or user.account_status != "active":
        return None
    session.last_seen_at = datetime.now(timezone.utc)
    return user


async def require_authenticated_user(
    user: User | None = Depends(get_optional_current_user),
) -> User:
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    return user


async def require_admin_access(
    request: Request,
    x_research_key: str | None = Header(default=None),
    user: User | None = Depends(get_optional_current_user),
) -> None:
    if user and user.role == "admin":
        return
    # Keep the existing operational key as a temporary service-to-service path.
    await require_research_access(request, x_research_key)


async def require_pro_access(
    user: User = Depends(require_authenticated_user),
) -> User:
    if user.role != "admin" and user.plan != "pro":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Pro plan required")
    return user
