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
