"""Shared LLM utilities: model initialization and cost calculation.

Centralises Google/Gemini model construction and token-cost
accounting so that scan_agent and deep_research_agent share a
single GoogleProvider, genai Client, and cost function.
"""

from __future__ import annotations

import os
from functools import lru_cache

from decimal import Decimal

from google import genai
from genai_prices import calc_price
from genai_prices import Usage as GenAIUsage
from genai_prices.data import providers as _pricing_providers
from genai_prices.types import ClauseStartsWith, ModelInfo, ModelPrice
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider


# ---------------------------------------------------------------------------
# Patch genai_prices with models it doesn't know about yet
# ---------------------------------------------------------------------------

_EXTRA_MODELS: dict[str, list[ModelInfo]] = {
    "google": [
        ModelInfo(
            id="gemini-3.1-flash-lite-preview",
            match=ClauseStartsWith(starts_with="gemini-3.1-flash-lite"),
            name="gemini 3.1 flash lite",
            prices=ModelPrice(
                input_mtok=Decimal("0.075"),
                output_mtok=Decimal("0.3"),
            ),
        ),
    ],
    "openai": [
        ModelInfo(
            id="gpt-5.4-nano",
            match=ClauseStartsWith(starts_with="gpt-5.4-nano"),
            name="gpt 5.4 nano",
            prices=ModelPrice(
                input_mtok=Decimal("0.05"),
                output_mtok=Decimal("0.4"),
            ),
        ),
        ModelInfo(
            id="gpt-5.4-mini",
            match=ClauseStartsWith(starts_with="gpt-5.4-mini"),
            name="gpt 5.4 mini",
            prices=ModelPrice(
                input_mtok=Decimal("0.25"),
                output_mtok=Decimal("2"),
            ),
        ),
    ],
}

for _provider in _pricing_providers:
    _extras = _EXTRA_MODELS.get(_provider.id)
    if _extras:
        _existing_ids = {m.id for m in _provider.models}
        for _model in _extras:
            if _model.id not in _existing_ids:
                _provider.models.append(_model)


FLASH_LITE_THINKING_SETTINGS = {
    "google_thinking_config": {"thinking_level": "HIGH"},
}
"""Model settings to enable HIGH thinking on Flash Lite agents."""


@lru_cache(maxsize=1)
def google_provider() -> GoogleProvider:
    return GoogleProvider(api_key=os.environ["GOOGLE_API_KEY"])


@lru_cache(maxsize=1)
def genai_client() -> genai.Client:
    return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


@lru_cache(maxsize=1)
def gemini_pro() -> GoogleModel:
    """Gemini 3.1 Flash Lite (testing replacement for Pro)."""
    return GoogleModel("gemini-3.1-flash-lite-preview", provider=google_provider())


@lru_cache(maxsize=1)
def gemini_flash() -> GoogleModel:
    """Gemini 3 Flash — main agent model."""
    return GoogleModel("gemini-3-flash-preview", provider=google_provider())


@lru_cache(maxsize=1)
def gemini_flash_lite() -> GoogleModel:
    """Gemini 3.1 Flash Lite — fast classification."""
    return GoogleModel("gemini-3.1-flash-lite-preview", provider=google_provider())


@lru_cache(maxsize=1)
def openai_provider() -> OpenAIProvider:
    return OpenAIProvider(api_key=os.environ["OPENAI_API_KEY"])


@lru_cache(maxsize=1)
def gpt_nano() -> OpenAIChatModel:
    """GPT 5.4 Nano — fast, cheap research agent."""
    return OpenAIChatModel("gpt-5.4-nano", provider=openai_provider())


def calc_usage_cost(input_tokens: int, output_tokens: int, model_name: str) -> dict:
    """Calculate cost for a model call and return a usage dict."""
    usage = GenAIUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    try:
        price = calc_price(usage, model_name)
        input_cost = f"{price.input_price:.4f}"
        output_cost = f"{price.output_price:.4f}"
    except LookupError:
        input_cost = "0.0000"
        output_cost = "0.0000"
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_cost": input_cost,
        "output_cost": output_cost,
    }
