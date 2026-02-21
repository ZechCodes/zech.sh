"""Pydantic AI research agent with Brave Search for scan.zech.sh."""

from __future__ import annotations

import io
import os
from dataclasses import dataclass
from functools import lru_cache

import httpx
from bs4 import BeautifulSoup
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google_gla import GoogleGLAProvider
from pypdf import PdfReader

from skrift.lib.notifications import NotificationMode, notify_session

# Maximum characters of document text to send to the extraction model.
_MAX_DOC_CHARS = 200_000


@lru_cache(maxsize=1)
def _google_provider() -> GoogleGLAProvider:
    return GoogleGLAProvider(api_key=os.environ["GOOGLE_API_KEY"])


@lru_cache(maxsize=1)
def gemini_flash() -> GoogleModel:
    """Build a Gemini Flash model using the existing GOOGLE_API_KEY env var."""
    return GoogleModel("gemini-2.0-flash", provider=_google_provider())


@lru_cache(maxsize=1)
def gemini_flash_lite() -> GoogleModel:
    """Cheap/fast model for document extraction."""
    return GoogleModel("gemini-2.5-flash-lite", provider=_google_provider())


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


# ---------------------------------------------------------------------------
# Extraction sub-agent (Gemini 2.5 Flash Lite) — searches fetched documents
# ---------------------------------------------------------------------------

extraction_agent = Agent(
    system_prompt=(
        "You are a document extraction assistant. "
        "Given a document and a query, find and extract the sections that are "
        "relevant to the query. Return the relevant portions verbatim, preserving "
        "original formatting. If the document is an image, describe the relevant "
        "content in detail. If nothing relevant is found, say so briefly."
    ),
)


def _html_to_text(html: str) -> str:
    """Strip HTML to readable plain text."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)


def _pdf_to_text(data: bytes) -> str:
    """Extract text from PDF bytes."""
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@research_agent.tool
async def fetch_url(ctx: RunContext[ResearchDeps], url: str, query: str) -> str:
    """Fetch a URL and extract the sections relevant to a query.

    Supports HTML pages, PDFs, and images. A lightweight model reads the
    fetched document and returns only the parts that answer the query.

    Args:
        url: The URL to fetch.
        query: What to look for in the document.
    """
    await notify_session(
        ctx.deps.nid,
        "research_status",
        status="reading",
        query=url,
        mode=NotificationMode.EPHEMERAL,
    )

    async with httpx.AsyncClient(follow_redirects=True) as client:
        resp = await client.get(url, timeout=30.0)
        resp.raise_for_status()

    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()

    if content_type == "application/pdf":
        text = _pdf_to_text(resp.content)
        prompt = f"Query: {query}\n\nDocument:\n{text[:_MAX_DOC_CHARS]}"
    elif content_type.startswith("image/"):
        prompt = [
            f"Query: {query}\n\nDescribe the relevant content from this image:",
            BinaryContent(data=resp.content, media_type=content_type),
        ]
    elif "html" in content_type or content_type.startswith("text/"):
        text = _html_to_text(resp.text) if "html" in content_type else resp.text
        prompt = f"Query: {query}\n\nDocument:\n{text[:_MAX_DOC_CHARS]}"
    else:
        return f"Unsupported content type: {content_type}"

    result = await extraction_agent.run(prompt, model=gemini_flash_lite())
    return result.output


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
