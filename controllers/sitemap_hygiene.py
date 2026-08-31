"""Which host serves a given page — the sitemap's one piece of domain logic.

zech.sh serves two kinds of page on two hosts: posts on ``dump.zech.sh``,
everything else on the main domain. Each host's sitemap must list only what
that host actually serves, or it advertises URLs that 404 there.

Skrift <= 0.1.0a81 handed the ``sitemap_page`` filter the full ``Page`` ORM
object, so "is this a post?" was one attribute read. Skrift 0.2.0 projects the
sitemap query down to ``(slug, updated_at, created_at)`` — deliberately, to keep
page bodies out of a crawler-hammered public route — so ``type`` is no longer on
the row and the question has become a lookup this app has to own.

:class:`PostSlugIndex` is that lookup. It takes its loader by injection so the
decision logic is testable without a database, and so the only part that needs
one is a single small function.
"""

import logging
import time
from asyncio import Lock
from typing import Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)

DUMP_HOST_PREFIX = "dump."
POST_TYPE = "post"

SlugLoader = Callable[[], Awaitable[set[str]]]


class SupportsPostLookup(Protocol):
    """The one question :class:`SitemapHostFilter` asks about a slug."""

    async def is_post(self, slug: str) -> bool: ...


class PostSlugIndex:
    """Cached set of slugs belonging to the ``post`` page type.

    The sitemap is crawler-facing and lists every published page, so this is
    asked once per entry. A short TTL keeps that to one query per sitemap
    render without letting a newly published post stay mispartitioned for long.
    """

    def __init__(
        self,
        load: SlugLoader,
        *,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._load = load
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = Lock()
        self._slugs: set[str] | None = None
        self._loaded_at: float = 0.0

    def _is_fresh(self) -> bool:
        return self._slugs is not None and (self._clock() - self._loaded_at) < self._ttl

    async def slugs(self) -> set[str]:
        """Return the post slugs, refreshing from the loader when stale."""
        if self._is_fresh():
            return self._slugs  # type: ignore[return-value]
        async with self._lock:
            # Another waiter may have refreshed while we queued for the lock.
            if self._is_fresh():
                return self._slugs  # type: ignore[return-value]
            self._slugs = await self._load()
            self._loaded_at = self._clock()
            return self._slugs

    async def is_post(self, slug: str) -> bool:
        return slug in await self.slugs()

    def invalidate(self) -> None:
        self._slugs = None


class SitemapHostFilter:
    """``sitemap_page`` filter: force https, and drop entries this host cannot serve.

    Works with both row shapes skrift has handed the hook: a full ``Page`` (which
    carries ``type``) and the projected sitemap row (which does not, and needs
    the index).
    """

    def __init__(self, index: SupportsPostLookup) -> None:
        self._index = index

    async def __call__(self, entry, page):
        entry.loc = _force_https(entry.loc)
        host = _host_of(entry.loc)
        served_by_dump = host.startswith(DUMP_HOST_PREFIX)
        try:
            is_post = await self._is_post(page)
        except Exception:
            # An over-inclusive sitemap is a soft SEO problem; an empty one is a
            # silent outage. Keep the entry, and make the failure visible.
            logger.warning(
                "sitemap: could not resolve the page type for %r; listing it on every host",
                entry.loc,
                exc_info=True,
            )
            return entry
        if served_by_dump != is_post:
            return None  # this host does not serve this page; leave it out
        return entry

    async def _is_post(self, page) -> bool:
        page_type = getattr(page, "type", None)
        if page_type is not None:
            return page_type == POST_TYPE
        return await self._index.is_post(_slug_of(page))


def _force_https(loc: str) -> str:
    """Skrift builds ``loc`` from the proxied request, which arrives as http."""
    if loc.startswith("http://"):
        return "https://" + loc[len("http://"):]
    return loc


def _host_of(loc: str) -> str:
    return loc.split("://", 1)[-1].split("/", 1)[0].lower()


def _slug_of(page) -> str:
    return (getattr(page, "slug", "") or "").strip("/")
