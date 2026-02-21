"""Integration tests for the scan query classifier.

These tests call the real Gemini API to verify the system prompt
classifies queries correctly. Requires GOOGLE_API_KEY in .env.
"""

import os
import sys

import pytest
from dotenv import load_dotenv

# Load .env from the project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

# Add project root to path so we can import the controller module directly
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controllers.scan import classify_query

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "query, expected",
    [
        # URL inputs
        ("github.com", "URL"),
        ("docs.python.org/3/library/asyncio", "URL"),
        ("192.168.1.1", "URL"),
        ("example.com/path?query=1", "URL"),
        # SEARCH inputs
        ("python list comprehension", "SEARCH"),
        ("best pizza near me", "SEARCH"),
        ("litestar framework", "SEARCH"),
        ("weather today", "SEARCH"),
        # RESEARCH inputs
        ("how does TCP congestion control work?", "RESEARCH"),
        ("compare React vs Svelte for SPAs", "RESEARCH"),
        ("explain the difference between threads and processes", "RESEARCH"),
        ("what are the pros and cons of microservices?", "RESEARCH"),
    ],
)
@pytest.mark.asyncio
async def test_classify_query(query: str, expected: str):
    result = await classify_query(query)
    assert result == expected, f"Expected {expected} for '{query}', got {result}"
