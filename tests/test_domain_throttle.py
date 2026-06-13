"""Tests for the domain throttle module (in-memory fallback path)."""

import pytest

from controllers.domain_throttle import _parse_cache_ttl


class TestParseCacheTtl:
    def test_max_age(self):
        headers = {"cache-control": "max-age=3600"}
        assert _parse_cache_ttl(headers) == 3600

    def test_no_cache(self):
        headers = {"cache-control": "no-cache"}
        assert _parse_cache_ttl(headers) == 0

    def test_no_store(self):
        headers = {"cache-control": "no-store"}
        assert _parse_cache_ttl(headers) == 0

    def test_default_ttl(self):
        headers = {}
        assert _parse_cache_ttl(headers) == 86400

    def test_max_age_with_other_directives(self):
        headers = {"cache-control": "public, max-age=600, must-revalidate"}
        assert _parse_cache_ttl(headers) == 600

    def test_zero_max_age(self):
        headers = {"cache-control": "max-age=0"}
        assert _parse_cache_ttl(headers) == 0
