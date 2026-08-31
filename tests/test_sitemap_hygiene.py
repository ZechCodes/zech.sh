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
