"""Per-domain rate limiting and response caching using Redis.

Provides:
- Per-domain request rate limiting (default: 1 request per 10 seconds)
- Response caching with configurable TTL (default: 24 hours, respects
  Cache-Control headers)

When Redis is unavailable, falls back to in-memory tracking.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import redis.asyncio as redis

logger = logging.getLogger(__name__)

_DEFAULT_RATE_LIMIT_SECONDS = 10.0
_DEFAULT_CACHE_TTL_SECONDS = 86400  # 24 hours

# ---------------------------------------------------------------------------
# In-memory cache fallback (when Redis is unavailable)
# ---------------------------------------------------------------------------

_mem_cache: dict[str, tuple[float, dict]] = {}  # key -> (expires_at, data)


def _mem_get(key: str) -> dict | None:
    entry = _mem_cache.get(key)
    if entry is None:
        return None
    expires_at, data = entry
    if time.monotonic() > expires_at:
        del _mem_cache[key]
        return None
    return data


def _mem_set(key: str, data: dict, ttl: int) -> None:
    # Evict expired entries when cache gets large
    if len(_mem_cache) > 500:
        now = time.monotonic()
        expired = [k for k, (exp, _) in _mem_cache.items() if now > exp]
        for k in expired:
            del _mem_cache[k]
    _mem_cache[key] = (time.monotonic() + ttl, data)


# ---------------------------------------------------------------------------
# Redis connection management
# ---------------------------------------------------------------------------

_redis_client: redis.Redis | None = None


async def get_redis(url: str = "") -> redis.Redis | None:
    """Get or create a Redis client. Returns None if URL is empty."""
    global _redis_client
    if not url:
        return None
    if _redis_client is None:
        _redis_client = redis.from_url(url, decode_responses=True)
    return _redis_client


async def close_redis() -> None:
    """Close the Redis connection if open."""
    global _redis_client
    if _redis_client is not None:
        await _redis_client.aclose()
        _redis_client = None


# ---------------------------------------------------------------------------
# In-memory fallback for when Redis is unavailable
# ---------------------------------------------------------------------------

# domain -> last request timestamp (monotonic)
_memory_rate_limits: dict[str, float] = {}
_memory_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


async def wait_for_rate_limit(
    domain: str,
    delay_seconds: float = _DEFAULT_RATE_LIMIT_SECONDS,
    redis_url: str = "",
) -> None:
    """Wait until the per-domain rate limit allows a new request.

    Args:
        domain: The domain to rate-limit against.
        delay_seconds: Minimum seconds between requests to this domain.
        redis_url: Redis connection URL. Falls back to in-memory if empty.
    """
    r = await get_redis(redis_url)

    if r is not None:
        await _wait_redis(r, domain, delay_seconds)
    else:
        await _wait_memory(domain, delay_seconds)


_MAX_RATE_LIMIT_ATTEMPTS = 10


async def _wait_redis(
    r: redis.Redis, domain: str, delay_seconds: float
) -> None:
    """Rate limit using Redis with a simple key-expiry approach."""
    key = f"scan:ratelimit:{domain}"

    for _attempt in range(_MAX_RATE_LIMIT_ATTEMPTS):
        # Try to set key with NX (only if not exists) and expiry
        ttl_ms = int(delay_seconds * 1000)
        acquired = await r.set(key, "1", nx=True, px=ttl_ms)
        if acquired:
            return

        # Key exists — wait for it to expire
        remaining_ms = await r.pttl(key)
        if remaining_ms <= 0:
            # Key expired between check and pttl
            continue

        wait_time = remaining_ms / 1000.0
        await asyncio.sleep(wait_time)

    logger.warning(
        "Rate limit wait exceeded %d attempts for domain %s, proceeding",
        _MAX_RATE_LIMIT_ATTEMPTS, domain,
    )


async def _wait_memory(domain: str, delay_seconds: float) -> None:
    """Rate limit using in-memory timestamps."""
    async with _memory_lock:
        now = time.monotonic()
        last = _memory_rate_limits.get(domain, 0.0)
        elapsed = now - last

        if elapsed < delay_seconds:
            wait_time = delay_seconds - elapsed
            # Release lock while sleeping so other domains aren't blocked
            _memory_rate_limits[domain] = now + wait_time

    # Sleep outside the lock if needed
    if elapsed < delay_seconds:
        await asyncio.sleep(wait_time)

    async with _memory_lock:
        _memory_rate_limits[domain] = time.monotonic()


# ---------------------------------------------------------------------------
# Response caching
# ---------------------------------------------------------------------------


def _cache_key(url: str) -> str:
    """Generate a Redis cache key for a URL."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    return f"scan:cache:{url_hash}"


def _parse_cache_ttl(headers: dict[str, str]) -> int:
    """Extract cache TTL from HTTP response headers.

    Checks Cache-Control max-age first, then Expires header.
    Returns TTL in seconds, defaulting to 24 hours.
    """
    cache_control = headers.get("cache-control", "")

    # Check for no-cache / no-store
    cc_lower = cache_control.lower()
    if "no-cache" in cc_lower or "no-store" in cc_lower:
        return 0

    # Extract max-age
    for part in cache_control.split(","):
        part = part.strip().lower()
        if part.startswith("max-age="):
            try:
                max_age = int(part.split("=", 1)[1])
                return max(0, max_age)
            except ValueError:
                pass

    # Check Expires header
    expires = headers.get("expires", "")
    if expires:
        try:
            expires_dt = parsedate_to_datetime(expires)
            import datetime
            now = datetime.datetime.now(datetime.timezone.utc)
            ttl = int((expires_dt - now).total_seconds())
            return max(0, ttl)
        except (ValueError, TypeError):
            pass

    return _DEFAULT_CACHE_TTL_SECONDS


async def get_cached_response(
    url: str, redis_url: str = ""
) -> dict | None:
    """Get a cached response for a URL.

    Tries Redis first, falls back to in-memory cache.
    Returns a dict with 'status_code', 'content', 'content_type'
    if cached, or None if not found.
    """
    key = _cache_key(url)

    r = await get_redis(redis_url)
    if r is not None:
        try:
            data = await r.get(key)
            if data is not None:
                return json.loads(data)
        except (redis.RedisError, json.JSONDecodeError):
            pass

    # Fall back to in-memory cache
    return _mem_get(key)


async def cache_response(
    url: str,
    status_code: int,
    headers: dict[str, str],
    text: str,
    content_type: str,
    redis_url: str = "",
) -> None:
    """Cache an HTTP response.

    Tries Redis first, falls back to in-memory cache.
    TTL is determined from the response's cache headers, defaulting
    to 24 hours.
    """
    ttl = _parse_cache_ttl(headers)
    if ttl <= 0:
        return

    key = _cache_key(url)
    cached = {
        "status_code": status_code,
        "content_type": content_type,
        "text": text[:500_000],  # Cap cached content size
    }

    r = await get_redis(redis_url)
    if r is not None:
        try:
            await r.set(key, json.dumps(cached), ex=ttl)
            return
        except redis.RedisError:
            pass

    # Fall back to in-memory cache
    _mem_set(key, cached, ttl)
