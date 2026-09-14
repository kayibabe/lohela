"""
Redis-backed TTL cache for external API responses.

All external API calls (API-Football) pass through here so
that repeated pipeline runs within the TTL window never hit the remote
endpoint again â€” preserving quota.

TTLs (tunable via env):
  CACHE_TTL_FIXTURES_SECONDS  (default 21600 = 6 h)  â€” fixture lists
  CACHE_TTL_ODDS_SECONDS      (default 1800  = 30 min) â€” live odds
  CACHE_TTL_STATS_SECONDS     (default 86400 = 24 h)  â€” per-fixture stats/injuries

Keys follow the pattern:  lohela:<prefix>:<md5(params)>
"""

import hashlib
import json
import logging
import asyncio
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

# Shared connection pool â€” created lazily on first use
_redis: aioredis.Redis | None = None
_redis_loop: asyncio.AbstractEventLoop | None = None


async def _get_client() -> aioredis.Redis:
    global _redis, _redis_loop
    loop = asyncio.get_running_loop()
    if _redis is None or _redis_loop is not loop:
        if _redis is not None:
            try:
                await _redis.aclose()
            except Exception as exc:
                logger.debug("Ignoring stale Redis client close error: %s", exc)
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        _redis_loop = loop
    return _redis


async def close() -> None:
    """Close the active client when a Celery asyncio loop is ending."""
    global _redis, _redis_loop
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception as exc:
            logger.debug("Ignoring Redis client close error: %s", exc)
        finally:
            _redis = None
            _redis_loop = None


def make_key(prefix: str, params: dict) -> str:
    payload = json.dumps(params, sort_keys=True)
    digest = hashlib.md5(payload.encode()).hexdigest()[:12]
    return f"lohela:{prefix}:{digest}"


async def get(key: str) -> Any | None:
    try:
        value = await (await _get_client()).get(key)
        if value is not None:
            logger.debug("Cache HIT %s", key)
            return json.loads(value)
    except Exception as exc:
        logger.warning("Cache get error (%s): %s", key, exc)
    return None


async def set(key: str, value: Any, ttl: int) -> None:
    try:
        await (await _get_client()).set(key, json.dumps(value), ex=ttl)
        logger.debug("Cache SET %s ttl=%ds", key, ttl)
    except Exception as exc:
        logger.warning("Cache set error (%s): %s", key, exc)


async def delete_prefix(prefix: str) -> int:
    """Delete all keys under lohela:<prefix>:* â€” used by admin cache-clear."""
    try:
        client = await _get_client()
        keys = await client.keys(f"lohela:{prefix}:*")
        if keys:
            count = await client.delete(*keys)
            logger.info("Cache cleared %d keys under prefix '%s'", count, prefix)
            return count
    except Exception as exc:
        logger.warning("Cache clear error (prefix=%s): %s", prefix, exc)
    return 0


async def flush_all() -> int:
    """Delete every lohela:* key â€” admin-only nuclear option."""
    try:
        client = await _get_client()
        keys = await client.keys("lohela:*")
        if keys:
            count = await client.delete(*keys)
            logger.info("Cache flushed %d keys", count)
            return count
    except Exception as exc:
        logger.warning("Cache flush error: %s", exc)
    return 0


async def stats() -> dict:
    """Return cache key counts by prefix for the admin page."""
    try:
        client = await _get_client()
        prefixes = ["fixtures", "odds", "stats", "injuries"]
        result = {}
        for p in prefixes:
            keys = await client.keys(f"lohela:{p}:*")
            result[p] = len(keys)
        result["total"] = sum(result.values())
        return result
    except Exception as exc:
        logger.warning("Cache stats error: %s", exc)
        return {"error": str(exc)}
