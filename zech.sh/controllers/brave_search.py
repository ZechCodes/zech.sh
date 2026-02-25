"""Shared Brave Search API client with rate limiting.

Rate-limits requests to 1 per second across all callers in the process.
"""

from __future__ import annotations

import asyncio
import logging
import time

import httpx

from controllers.robots import USER_AGENT

logger = logging.getLogger(__name__)

_brave_lock: asyncio.Lock | None = None
_brave_last_call: float = 0.0


def _get_brave_lock() -> asyncio.Lock:
    """Lazily create the Brave rate-limit lock inside the running event loop."""
    global _brave_lock
    if _brave_lock is None:
        _brave_lock = asyncio.Lock()
    return _brave_lock


async def brave_search(
    query: str,
    api_key: str,
    count: int = 5,
) -> list[dict]:
    """Execute a Brave web search and return raw result dicts (1 req/sec).

    Returns a list of dicts with 'title', 'url', 'description' keys.
    """
    global _brave_last_call
    lock = _get_brave_lock()
    async with lock:
        wait = 1.0 - (time.monotonic() - _brave_last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "User-Agent": USER_AGENT,
                    "X-Subscription-Token": api_key,
                },
                params={"q": query, "count": count},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        _brave_last_call = time.monotonic()

    return data.get("web", {}).get("results", [])
