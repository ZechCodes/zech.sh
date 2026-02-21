"""Pydantic AI research agent with Brave Search for scan.zech.sh."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

import httpx
from pydantic_ai import Agent, RunContext
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google_gla import GoogleGLAProvider

from skrift.lib.notifications import NotificationMode, notify_session


@lru_cache(maxsize=1)
def gemini_flash() -> GoogleModel:
    """Build a Gemini Flash model using the existing GOOGLE_API_KEY env var."""
    provider = GoogleGLAProvider(api_key=os.environ["GOOGLE_API_KEY"])
    return GoogleModel("gemini-2.0-flash", provider=provider)


@dataclass
class ResearchDeps:
    """Dependencies injected into the research agent."""

    nid: str
    brave_api_key: str


research_agent = Agent(
    system_prompt=(
        "You are a research assistant. Answer the user's question thoroughly "
        "using web search results. Cite your sources with URLs. "
        "Be concise but comprehensive. Use markdown formatting."
    ),
    deps_type=ResearchDeps,
)


@research_agent.tool
async def web_search(ctx: RunContext[ResearchDeps], query: str) -> str:
    """Search the web for current information.

    Args:
        query: The search query to look up.
    """
    await notify_session(
        ctx.deps.nid,
        "research_status",
        status="searching",
        query=query,
        mode=NotificationMode.EPHEMERAL,
    )

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": ctx.deps.brave_api_key,
            },
            params={"q": query, "count": 5},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for item in data.get("web", {}).get("results", []):
        title = item.get("title", "")
        description = item.get("description", "")
        url = item.get("url", "")
        results.append(f"**{title}**\n{description}\nURL: {url}")

    return "\n\n".join(results) if results else "No results found."


classify_agent = Agent(
    system_prompt=(
        "You are a query classifier. Given a user input, classify it as exactly one of:\n\n"
        "URL \u2014 The input looks like a domain name, IP address, or URL (with or without a protocol). "
        'Examples: "github.com", "docs.python.org/3/library/asyncio", "192.168.1.1"\n\n'
        "SEARCH \u2014 The input is a simple web search query looking for results/links. "
        'Examples: "python list comprehension", "best pizza near me", "litestar framework"\n\n'
        "RESEARCH \u2014 The input is a question or request that needs a comprehensive, direct answer "
        "or in-depth analysis rather than a list of links. "
        'Examples: "how does TCP congestion control work?", "compare React vs Svelte for SPAs"\n\n'
        "Respond with exactly one word: URL, SEARCH, or RESEARCH. Nothing else."
    ),
)


async def classify_query(query: str) -> str:
    """Classify a query using Pydantic AI + Gemini Flash."""
    result = await classify_agent.run(query, model=gemini_flash())
    text = result.output.strip().upper()
    if text not in ("URL", "SEARCH", "RESEARCH"):
        return "SEARCH"
    return text
