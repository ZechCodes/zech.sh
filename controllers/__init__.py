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
from skrift.lib.hooks import add_filter, SITEMAP_PAGE


def _sitemap_entry_filter(entry, page):
    loc = entry.loc
    if loc.startswith("http://"):
        loc = "https://" + loc[len("http://"):]
        entry.loc = loc
    host = loc.split("://", 1)[-1].split("/", 1)[0].lower()
    is_dump = host.startswith("dump.")
    is_post = getattr(page, "type", "page") == "post"
    if is_dump != is_post:
        return None  # this page is not served on this host; leave it out
    return entry


add_filter(SITEMAP_PAGE, _sitemap_entry_filter)
