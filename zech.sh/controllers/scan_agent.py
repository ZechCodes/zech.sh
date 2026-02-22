"""Pydantic AI research agent with Brave Search for scan.zech.sh.

A single tool-using agent autonomously researches topics by calling a
`research` tool (Brave search -> fetch -> extract) and optionally asking
the user for clarification via `ask_user`. An asyncio.Queue bridges
tool-side events to the SSE generator.

Respects robots.txt rules and rate-limits requests per domain.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.domain_throttle import (
    cache_response,
    get_cached_response,
    wait_for_rate_limit,
)
from controllers.robots import USER_AGENT, check_url_allowed

logger = logging.getLogger(__name__)

# Maximum characters of document text to send to the extraction model.
_MAX_DOC_CHARS = 200_000

# ---------------------------------------------------------------------------
# Google / Gemini model helpers
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _google_provider() -> GoogleProvider:
    return GoogleProvider(api_key=os.environ["GOOGLE_API_KEY"])


@lru_cache(maxsize=1)
def gemini_flash() -> GoogleModel:
    """Build a Gemini Flash model using the existing GOOGLE_API_KEY env var."""
    return GoogleModel("gemini-3-flash-preview", provider=_google_provider())


@lru_cache(maxsize=1)
def gemini_flash_lite() -> GoogleModel:
    """Cheap/fast model for planning, extraction, and evaluation."""
    return GoogleModel("gemini-2.5-flash-lite", provider=_google_provider())


# ---------------------------------------------------------------------------
# Pydantic models for pipeline data
# ---------------------------------------------------------------------------


class SearchResultItem(BaseModel):
    title: str
    url: str
    description: str


class FetchedResource(BaseModel):
    url: str
    extracted_text: str


# ---------------------------------------------------------------------------
# Pipeline SSE event types
# ---------------------------------------------------------------------------

StageName = Literal["researching", "responding"]


@dataclass
class StageEvent:
    stage: StageName


@dataclass
class DetailEvent:
    type: str  # "research", "search", "fetch", "result"
    payload: dict


@dataclass
class TextEvent:
    text: str


@dataclass
class DoneEvent:
    pass


@dataclass
class ErrorEvent:
    error: str


@dataclass
class ClarificationEvent:
    questions: list[str]


PipelineEvent = StageEvent | DetailEvent | TextEvent | DoneEvent | ErrorEvent | ClarificationEvent


# ---------------------------------------------------------------------------
# Research agent dependencies and exception
# ---------------------------------------------------------------------------


class _ClarificationNeeded(Exception):
    """Raised by ask_user tool to break out of the agent run."""


@dataclass
class ResearchDeps:
    brave_api_key: str
    event_queue: asyncio.Queue
    db_session: AsyncSession
    redis_url: str = ""
    fetched_urls: set[str] = field(default_factory=set)


# ---------------------------------------------------------------------------
# Agents
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

classify_agent = Agent(
    system_prompt=(
        "You are a query classifier. Given a user input, classify it as exactly one of:\n\n"
        "URL — The input looks like a domain name, IP address, or URL (with or without a protocol). "
        'Examples: "github.com", "docs.python.org/3/library/asyncio", "192.168.1.1"\n\n'
        "SEARCH — The input is a simple web search query looking for results/links. "
        'Examples: "python list comprehension", "best pizza near me", "litestar framework"\n\n'
        "RESEARCH — The input is a question or request that needs a comprehensive, direct answer "
        "or in-depth analysis rather than a list of links. "
        'Examples: "how does TCP congestion control work?", "compare React vs Svelte for SPAs"\n\n'
        "Respond with exactly one word: URL, SEARCH, or RESEARCH. Nothing else."
    ),
)

research_agent = Agent(
    system_prompt=(
        "You are a research assistant. Your job is to thoroughly answer the user's "
        "question by gathering information from the web.\n\n"
        "For each aspect you need to investigate, call the `research` tool with a "
        "focused topic string. You can call it multiple times for different aspects "
        "of the question.\n\n"
        "If the question is ambiguous or requires information only the user can "
        "provide (personal preferences, specific constraints, etc.), call `ask_user` "
        "with clear, specific questions.\n\n"
        "After gathering enough information, write your final answer. Be thorough "
        "but concise. Use markdown formatting and cite sources with URLs."
    ),
    deps_type=ResearchDeps,
)


async def classify_query(query: str) -> str:
    """Classify a query using Pydantic AI + Gemini Flash."""
    result = await classify_agent.run(query, model=gemini_flash())
    text = result.output.strip().upper()
    if text not in ("URL", "SEARCH", "RESEARCH"):
        return "SEARCH"
    return text


# ---------------------------------------------------------------------------
# Research agent tools
# ---------------------------------------------------------------------------


@research_agent.tool
async def research(ctx: RunContext[ResearchDeps], topic: str, context: str = "") -> str:
    """Search the web for a topic and return extracted findings.

    Args:
        topic: The focused topic to research.
        context: Optional additional context to refine the search.
    """
    queue = ctx.deps.event_queue
    api_key = ctx.deps.brave_api_key

    await queue.put(DetailEvent(type="research", payload={"topic": topic}))

    search_query = f"{topic} {context}".strip() if context else topic
    await queue.put(DetailEvent(type="search", payload={"query": search_query}))

    try:
        results = await brave_search(search_query, api_key)
    except Exception:
        return f"Search failed for: {topic}"

    if not results:
        return f"No search results found for: {topic}"

    urls = _pick_top_urls(results, ctx.deps.fetched_urls)
    findings: list[str] = []

    for url in urls:
        await queue.put(DetailEvent(type="fetch", payload={"url": url}))
        try:
            extracted = await fetch_and_extract(
                url, topic,
                db_session=ctx.deps.db_session,
                redis_url=ctx.deps.redis_url,
            )
            ctx.deps.fetched_urls.add(url)
            if extracted is not None:
                findings.append(f"Source: {url}\n{extracted}")
        except Exception:
            pass

    if not findings:
        desc_parts = [f"- {r.title}: {r.description} ({r.url})" for r in results[:3]]
        summary = "\n".join(desc_parts)
        await queue.put(DetailEvent(
            type="result",
            payload={"summary": f"Found {len(results)} results for '{topic}'"},
        ))
        return f"Search results for '{topic}':\n{summary}"

    await queue.put(DetailEvent(
        type="result",
        payload={"summary": f"Extracted content from {len(findings)} sources for '{topic}'"},
    ))
    return "\n\n---\n\n".join(findings)


@research_agent.tool
async def ask_user(ctx: RunContext[ResearchDeps], questions: list[str]) -> str:
    """Ask the user clarifying questions when the query is ambiguous.

    Args:
        questions: List of specific questions to ask the user.
    """
    await ctx.deps.event_queue.put(ClarificationEvent(questions=questions))
    raise _ClarificationNeeded()


# ---------------------------------------------------------------------------
# Standalone functions
# ---------------------------------------------------------------------------


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


async def brave_search(query: str, api_key: str) -> list[SearchResultItem]:
    """Execute a Brave web search and return structured results."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "User-Agent": USER_AGENT,
                "X-Subscription-Token": api_key,
            },
            params={"q": query, "count": 5},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

    return [
        SearchResultItem(
            title=item.get("title", ""),
            url=item.get("url", ""),
            description=item.get("description", ""),
        )
        for item in data.get("web", {}).get("results", [])
    ]


async def fetch_and_extract(
    url: str,
    query: str,
    *,
    db_session: AsyncSession | None = None,
    redis_url: str = "",
) -> str | None:
    """Fetch a URL and extract relevant content using the extraction agent.

    Checks robots.txt rules before fetching, rate-limits requests per domain,
    and caches responses in Redis when available.

    Returns None if the URL is disallowed by robots.txt.
    """
    parsed_url = urlparse(url)
    domain = parsed_url.hostname or ""

    # --- robots.txt check ---
    if db_session is not None:
        allowed, crawl_delay = await check_url_allowed(url, db_session)
        if not allowed:
            logger.info("Blocked by robots.txt: %s", url)
            return None
    else:
        crawl_delay = 10.0

    # --- Check response cache ---
    cached = await get_cached_response(url, redis_url=redis_url)
    if cached is not None:
        content_type = cached.get("content_type", "")
        text = cached.get("text", "")
        if "html" in content_type:
            text = _html_to_text(text)
        prompt = f"Query: {query}\n\nDocument:\n{text[:_MAX_DOC_CHARS]}"
        result = await extraction_agent.run(prompt, model=gemini_flash_lite())
        return result.output

    # --- Rate limit ---
    await wait_for_rate_limit(domain, delay_seconds=crawl_delay, redis_url=redis_url)

    # --- Fetch ---
    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return f"Could not fetch {url}: HTTP {exc.response.status_code}"
        except httpx.RequestError as exc:
            return f"Could not fetch {url}: {exc}"

    content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()

    # --- Cache the response ---
    if "html" in content_type or content_type.startswith("text/"):
        await cache_response(
            url,
            status_code=resp.status_code,
            headers=dict(resp.headers),
            text=resp.text,
            content_type=content_type,
            redis_url=redis_url,
        )

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


def _pick_top_urls(
    results: list[SearchResultItem],
    already_fetched: set[str],
    max_urls: int = 3,
) -> list[str]:
    """Pick the top URLs from search results that haven't been fetched yet."""
    urls: list[str] = []
    for r in results:
        if r.url and r.url not in already_fetched:
            urls.append(r.url)
            if len(urls) >= max_urls:
                break
    return urls


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def run_research_pipeline(
    query: str,
    brave_api_key: str,
    *,
    db_session: AsyncSession,
    redis_url: str = "",
    additional_context: str = "",
) -> AsyncGenerator[PipelineEvent, None]:
    """Run the research agent, yielding SSE-ready events via a queue."""
    full_query = (
        f"{query}\n\nAdditional context from user: {additional_context}"
        if additional_context
        else query
    )

    queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()
    deps = ResearchDeps(
        brave_api_key=brave_api_key,
        event_queue=queue,
        db_session=db_session,
        redis_url=redis_url,
    )

    yield StageEvent(stage="researching")

    async def _agent_task() -> None:
        try:
            async with research_agent.run_stream(
                full_query, model=gemini_flash(), deps=deps
            ) as stream:
                async for text in stream.stream_text(delta=True):
                    await queue.put(TextEvent(text=text))
            await queue.put(DoneEvent())
        except _ClarificationNeeded:
            pass  # ClarificationEvent already in queue
        except Exception as exc:
            await queue.put(ErrorEvent(error=str(exc)))

    task = asyncio.create_task(_agent_task())

    sent_responding = False
    while True:
        event = await queue.get()
        if isinstance(event, TextEvent) and not sent_responding:
            sent_responding = True
            yield StageEvent(stage="responding")
        yield event
        if isinstance(event, (DoneEvent, ErrorEvent, ClarificationEvent)):
            break

    await task
