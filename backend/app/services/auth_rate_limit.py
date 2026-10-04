"""Small fail-open Redis rate limiter for public authentication endpoints."""

from __future__ import annotations

import hashlib
import logging

from fastapi import HTTPException, Request, status

from app.config import settings
from app.services.cache import _get_client

logger = logging.getLogger(__name__)


def _client_identity(request: Request) -> str:
    # Uvicorn resolves the trusted proxy address into request.client. Prefer
    # that normalized value over a directly supplied X-Forwarded-For header;
    # only use the header when no client value exists (e.g. a small test app).
    forwarded = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    client = request.client.host if request.client else ""
    return client or forwarded or "unknown"


async def enforce_auth_rate_limit(request: Request, endpoint: str, limit: int) -> None:
    """Allow a bounded number of attempts per client/window.

    Auth availability is more important than throttling during a Redis outage,
    so operational failure is logged and allowed rather than locking everyone
    out. A valid limiter rejection is always explicit and retryable.
    """
    if limit <= 0:
        return
    identity = hashlib.sha256(_client_identity(request).encode("utf-8")).hexdigest()[:24]
    key = f"lohela:auth-rate:{endpoint}:{identity}"
    try:
        client = await _get_client()
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, settings.auth_rate_limit_window_seconds)
        if count > limit:
            ttl = await client.ttl(key)
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please try again later.",
                headers={"Retry-After": str(max(ttl, 1))},
            )
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Authentication rate limiter unavailable for %s: %s", endpoint, exc)
