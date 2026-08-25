"""Render the town theme templates and pin the booking page split:
/book carries the heading, engagement copy, and Cal.com embed; the
landing page carries none of it and its CTA points at /book."""

from datetime import datetime
from pathlib import Path

import jinja2

THEME_TEMPLATES = Path(__file__).parent.parent / "themes" / "town" / "templates"


def render_theme_template(name: str, **context) -> str:
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(THEME_TEMPLATES))
    env.globals.update(
        site_name=lambda: "zech.sh",
        theme_url=lambda path: f"/static/town/{path}",
        now=datetime.now,
        csp_nonce=lambda: "test-nonce",
    )
    return env.get_template(name).render(**context)


def test_book_page_has_heading_copy_and_embed():
    html = render_theme_template("book.html")
    assert "<title>Book a call · Zech Zimmerman</title>" in html
    assert "Book a call</h1>" in html
    assert "fixed-scope audit of your agent architecture" in html
    assert "This call is where we figure out fit." in html
    assert 'id="cal-inline"' in html
    assert "js/booking.js" in html


def test_book_page_fallback_only_shows_on_failure():
    html = render_theme_template("book.html")
    assert 'id="booking-fallback" hidden' in html
    assert "<noscript>" in html
    assert html.count("https://cal.com/zech-zimmerman/intro") == 2


def test_landing_page_has_no_booking_section():
    html = render_theme_template("index.html")
    assert "cal-inline" not in html
    assert "booking" not in html
    assert 'id="book"' not in html
    assert "fixed-scope audit" not in html


def test_landing_cta_links_to_book_page():
    html = render_theme_template("index.html")
    assert 'href="/book">Book a call' in html
    assert 'href="#book"' not in html
