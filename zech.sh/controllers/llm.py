"""Shared LLM utilities: model initialization and cost calculation.

Centralises Google/Gemini model construction and token-cost
accounting so that scan_agent and deep_research_agent share a
single GoogleProvider, genai Client, and cost function.
"""

from __future__ import annotations

import os
from functools import lru_cache

from google import genai
from genai_prices import calc_price
from genai_prices import Usage as GenAIUsage
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider


@lru_cache(maxsize=1)
def google_provider() -> GoogleProvider:
    return GoogleProvider(api_key=os.environ["GOOGLE_API_KEY"])


@lru_cache(maxsize=1)
def genai_client() -> genai.Client:
    return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


@lru_cache(maxsize=1)
def gemini_pro() -> GoogleModel:
    """Gemini 3 Pro — research agent, title generation."""
    return GoogleModel("gemini-3-pro-preview", provider=google_provider())


@lru_cache(maxsize=1)
def gemini_flash() -> GoogleModel:
    """Gemini 3 Flash — extraction and evaluation."""
    return GoogleModel("gemini-3-flash-preview", provider=google_provider())


@lru_cache(maxsize=1)
def gemini_flash_lite() -> GoogleModel:
    """Gemini 3.1 Flash Lite — fast classification."""
    return GoogleModel("gemini-3.1-flash-lite-preview", provider=google_provider())


def calc_usage_cost(input_tokens: int, output_tokens: int, model_name: str) -> dict:
    """Calculate cost for a model call and return a usage dict."""
    usage = GenAIUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    price = calc_price(usage, model_name)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_cost": f"{price.input_price:.4f}",
        "output_cost": f"{price.output_price:.4f}",
    }
