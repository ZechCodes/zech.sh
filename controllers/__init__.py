import re

import skrift.app_factory

_original_render_markdown = skrift.app_factory.render_markdown


def _render_markdown_clean(content: str) -> str:
    """Pre-process markdown to fix Hashnode image alignment syntax."""
    if not content:
        return ""
    # Strip `align="..."` from image URLs: ![alt](url align="center") → ![alt](url)
    content = re.sub(
        r'(!\[[^\]]*\]\(\S+?)\s+align="[^"]*"(\))',
        r"\1\2",
        content,
    )
    return _original_render_markdown(content)


skrift.app_factory.render_markdown = _render_markdown_clean


# ---- sitemap hygiene ----------------------------------------------------------
# Skrift's sitemap lists every published page under the requesting host and uses
# whatever scheme the proxied request carried (http). Force https, and only list a
# page on the host that actually serves it: posts live on dump.*, regular pages on
# the main domain. Without this, each sitemap advertises the other site's URLs,
# which 404.
#
# Skrift 0.2.0 projects the sitemap query down to (slug, updated_at, created_at),
# so the row no longer carries `type` and the post/page split has to be looked up.
# The decision logic lives in controllers.sitemap_hygiene; what stays here is the
# database loader behind it and the hook wiring.
import logging

from sqlalchemy import select

from skrift.db.models.page import Page
from skrift.hooks import add_action, add_filter, APP_STARTUP, SITEMAP_PAGE

from controllers.sitemap_hygiene import POST_TYPE, PostSlugIndex, SitemapHostFilter

logger = logging.getLogger(__name__)

_session_factory = None


def _capture_session_factory(app) -> None:
    """Grab the app's session maker so the sitemap can query outside a request.

    The sitemap_page filter is handed an entry and a row, never a session, so
    the post-slug index has to source its own. Skrift builds exactly one
    SQLAlchemy plugin per app; its config owns the session maker.
    """
    global _session_factory
    from advanced_alchemy.extensions.litestar.plugins import SQLAlchemyPlugin

    for plugin in app.plugins:
        if isinstance(plugin, SQLAlchemyPlugin):
            config = plugin.config
            if isinstance(config, (list, tuple)):
                config = config[0]
            _session_factory = config.get_session
            return
    logger.warning("sitemap: no SQLAlchemy plugin on the app; post/page split will not apply")


async def _load_post_slugs() -> set[str]:
    """Every slug belonging to the `post` page type.

    Deliberately unfiltered by publication state: the index is only ever asked
    about slugs the sitemap already emitted, so restricting it further would buy
    nothing and risk disagreeing with skrift's own published-page query.
    """
    if _session_factory is None:
        raise RuntimeError("sitemap post-slug index queried before app startup")
    async with _session_factory() as db_session:
        rows = await db_session.execute(select(Page.slug).where(Page.type == POST_TYPE))
        return {slug.strip("/") for (slug,) in rows}


post_slug_index = PostSlugIndex(_load_post_slugs)

add_action(APP_STARTUP, _capture_session_factory)
add_filter(SITEMAP_PAGE, SitemapHostFilter(post_slug_index))
