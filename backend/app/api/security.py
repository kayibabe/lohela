"""Minimal internal research access gate for state-changing endpoints."""

import hmac

from fastapi import Header, HTTPException, Request, status

from app.config import settings


async def require_research_access(
    request: Request,
    x_research_key: str | None = Header(default=None),
) -> None:
    configured = settings.research_api_key
    if configured and x_research_key and hmac.compare_digest(configured, x_research_key):
        return
    if settings.app_env == "development" and not configured:
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Valid X-Research-Key required",
    )
