"""Characterization tests for the Hashnode ``align="..."`` strip.

``controllers/__init__.py`` rebinds ``skrift.app_factory.render_markdown`` so
that image URLs imported from Hashnode render as images instead of literal
text. app_factory registers the jinja ``markdown`` filter by reading that
module global (``filters = {"markdown": render_markdown}``), so the rebinding is
an undeclared coupling to a skrift internal.

These pin both halves of it — that the name is still the one app_factory reads,
and that the patched callable strips the alignment attribute — as a safety net
for the skrift 0.1.0a81 -> 0.2.0a18 upgrade.
"""

import skrift.app_factory

import controllers  # noqa: F401  (importing installs the monkeypatch)


def render(markdown: str) -> str:
    """Render through the exact callable app_factory installs as ``markdown``."""
    return skrift.app_factory.render_markdown(markdown)


def test_app_factory_still_exposes_the_name_the_patch_rebinds():
    assert hasattr(skrift.app_factory, "render_markdown")
    assert callable(skrift.app_factory.render_markdown)


def test_align_attribute_is_stripped_from_image_urls():
    html = render('![shot](https://cdn.example/a.png align="center")')
    assert 'align="center"' not in html
    assert "https://cdn.example/a.png" in html
    assert "<img" in html


def test_plain_images_are_untouched():
    html = render("![shot](https://cdn.example/a.png)")
    assert "https://cdn.example/a.png" in html
    assert "<img" in html


def test_empty_content_renders_empty():
    assert render("") == ""


def test_ordinary_markdown_still_renders():
    assert "<strong>" in render("**bold**")
