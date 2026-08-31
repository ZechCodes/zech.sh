"""Characterization tests for the sitemap_page hook zech.sh installs.

These pin the behavior of ``controllers/__init__.py``'s sitemap filter:

* every ``loc`` is forced to https, and
* a page is only listed on the host that actually serves it — posts on
  ``dump.*``, everything else on the main domain.

They are a safety net for the skrift 0.1.0a81 -> 0.2.0a18 upgrade, not tests of
a new feature.
"""

from dataclasses import dataclass
from datetime import datetime

import pytest

import controllers  # noqa: F401  (importing registers the filter)
from skrift.controllers.sitemap import SitemapEntry
from skrift.hooks import hooks, SITEMAP_PAGE

from controllers.sitemap_hygiene import PostSlugIndex, SitemapHostFilter


@dataclass
class FakePage:
    """Stands in for the ORM ``Page`` skrift 0.1.0a81 hands the filter."""

    slug: str
    type: str = "page"


async def apply(loc: str, page) -> SitemapEntry | None:
    """Run the real hook chain, exactly as SitemapController does."""
    entry = SitemapEntry(loc=loc, lastmod=datetime(2026, 1, 1), changefreq="weekly", priority=0.8)
    return await hooks.apply_filters(SITEMAP_PAGE, entry, page)


def test_filter_is_registered():
    assert hooks._filters[SITEMAP_PAGE], "zech.sh must register a sitemap_page filter"


@pytest.mark.asyncio
async def test_http_is_rewritten_to_https():
    entry = await apply("http://zech.sh/about", FakePage(slug="about", type="page"))
    assert entry is not None
    assert entry.loc == "https://zech.sh/about"


@pytest.mark.asyncio
async def test_https_is_left_alone():
    entry = await apply("https://zech.sh/about", FakePage(slug="about", type="page"))
    assert entry is not None
    assert entry.loc == "https://zech.sh/about"


@pytest.mark.asyncio
async def test_page_is_listed_on_the_main_domain():
    assert await apply("https://zech.sh/about", FakePage(slug="about", type="page")) is not None


@pytest.mark.asyncio
async def test_page_is_excluded_from_the_dump_sitemap():
    assert await apply("https://dump.zech.sh/about", FakePage(slug="about", type="page")) is None


@pytest.mark.asyncio
async def test_post_is_listed_on_the_dump_subdomain():
    assert await apply("https://dump.zech.sh/hello", FakePage(slug="hello", type="post")) is not None


@pytest.mark.asyncio
async def test_post_is_excluded_from_the_main_sitemap():
    assert await apply("https://zech.sh/hello", FakePage(slug="hello", type="post")) is None


@pytest.mark.asyncio
async def test_partitioning_survives_an_http_loc():
    """The host check runs against the rewritten https loc, not the original."""
    assert await apply("http://dump.zech.sh/hello", FakePage(slug="hello", type="post")) is not None
    assert await apply("http://dump.zech.sh/about", FakePage(slug="about", type="page")) is None

# --- skrift 0.2.0 hands the filter a projected row, not the Page ORM object ----
#
# ``list_page_sitemap_entries`` selects only (slug, updated_at, created_at) so a
# crawler-facing route never pulls page bodies into memory. There is no ``type``
# on that row, so the host/type partitioning above comes from a slug lookup
# instead. These drive the filter directly with a stubbed index — the loader is
# the only part that needs a database, and it is injected.


@dataclass
class ProjectedRow:
    """The shape skrift 0.2.0's sitemap actually passes: no ``type``."""

    slug: str


class StubIndex:
    def __init__(self, post_slugs: set[str]) -> None:
        self.post_slugs = post_slugs
        self.lookups: list[str] = []

    async def is_post(self, slug: str) -> bool:
        self.lookups.append(slug)
        return slug in self.post_slugs


async def apply_direct(host_filter, loc: str, page) -> SitemapEntry | None:
    entry = SitemapEntry(loc=loc, lastmod=datetime(2026, 1, 1), changefreq="weekly", priority=0.8)
    return await host_filter(entry, page)


@pytest.fixture
def index() -> StubIndex:
    return StubIndex({"hello"})


@pytest.fixture
def host_filter(index) -> SitemapHostFilter:
    return SitemapHostFilter(index)


@pytest.mark.asyncio
async def test_projected_post_row_is_listed_on_the_dump_subdomain(host_filter):
    assert await apply_direct(host_filter, "https://dump.zech.sh/hello", ProjectedRow("hello")) is not None


@pytest.mark.asyncio
async def test_projected_post_row_is_excluded_from_the_main_sitemap(host_filter):
    assert await apply_direct(host_filter, "https://zech.sh/hello", ProjectedRow("hello")) is None


@pytest.mark.asyncio
async def test_projected_page_row_is_listed_on_the_main_domain(host_filter):
    assert await apply_direct(host_filter, "https://zech.sh/about", ProjectedRow("about")) is not None


@pytest.mark.asyncio
async def test_projected_page_row_is_excluded_from_the_dump_sitemap(host_filter):
    assert await apply_direct(host_filter, "https://dump.zech.sh/about", ProjectedRow("about")) is None


@pytest.mark.asyncio
async def test_projected_home_row_matches_on_the_stripped_slug(host_filter, index):
    """``loc`` is built from a stripped slug, so the lookup must strip too."""
    await apply_direct(host_filter, "https://zech.sh/about", ProjectedRow("/about/"))
    assert index.lookups == ["about"]


@pytest.mark.asyncio
async def test_a_row_carrying_type_never_hits_the_index(index, host_filter):
    """The full ``Page`` still answers for itself — no query, as in 0.1.0a81."""
    await apply_direct(host_filter, "https://zech.sh/about", FakePage(slug="about", type="page"))
    assert index.lookups == []


@pytest.mark.asyncio
async def test_a_failing_lookup_keeps_the_entry_and_warns(caplog):
    """An over-inclusive sitemap is a soft SEO problem; an empty one is an outage."""

    class Broken:
        async def is_post(self, slug: str) -> bool:
            raise RuntimeError("no database")

    entry = await apply_direct(SitemapHostFilter(Broken()), "https://zech.sh/hello", ProjectedRow("hello"))
    assert entry is not None
    assert "could not resolve the page type" in caplog.text


# --- the index itself ---------------------------------------------------------


@pytest.mark.asyncio
async def test_index_caches_within_its_ttl():
    calls = []

    async def load() -> set[str]:
        calls.append(1)
        return {"hello"}

    now = [1000.0]
    idx = PostSlugIndex(load, ttl_seconds=300.0, clock=lambda: now[0])
    assert await idx.is_post("hello") is True
    assert await idx.is_post("other") is False
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_index_reloads_once_the_ttl_expires():
    calls = []

    async def load() -> set[str]:
        calls.append(1)
        return {"hello"}

    now = [1000.0]
    idx = PostSlugIndex(load, ttl_seconds=300.0, clock=lambda: now[0])
    await idx.slugs()
    now[0] += 301.0
    await idx.slugs()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_invalidate_forces_a_reload():
    calls = []

    async def load() -> set[str]:
        calls.append(1)
        return set()

    idx = PostSlugIndex(load, ttl_seconds=300.0)
    await idx.slugs()
    idx.invalidate()
    await idx.slugs()
    assert len(calls) == 2
