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
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BinaryContent
from pydantic_ai.usage import RunUsage
from pypdf import PdfReader
from sqlalchemy.ext.asyncio import AsyncSession

from controllers.brave_search import brave_search as _brave_search_raw
from controllers.domain_throttle import wait_for_rate_limit
from controllers.llm import (
    FLASH_LITE_THINKING_SETTINGS,
    calc_usage_cost,
    gemini_flash,
    gemini_flash_lite,
)
from controllers.deep_research_agent import _jina_fetch
from controllers.research_agent import run_agent_research_pipeline
from controllers.robots import USER_AGENT, check_url_allowed

logger = logging.getLogger(__name__)

# Maximum characters of document text to send to the extraction model.
_MAX_DOC_CHARS = 200_000

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

StageName = Literal["reasoning", "researching", "responding"]


@dataclass
class StageEvent:
    stage: StageName


@dataclass
class DetailEvent:
    type: str  # "research", "search", "search_done", "fetch", "fetch_done", "result"
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
    extraction_usage: RunUsage = field(default_factory=RunUsage)


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
        "SEARCH — The input is a web search query. This is the DEFAULT for almost everything: "
        "lookups, definitions, factual questions, product searches, how-to queries, and anything "
        "a normal search engine handles well. When in doubt, classify as SEARCH. "
        'Examples: "python list comprehension", "define avant garde", "best pizza near me", '
        '"what is kubernetes", "how to center a div", "litestar framework"\n\n'
        "RESEARCH — The input explicitly asks for deep analysis, comparison, or a synthesized "
        "answer that requires reading and combining multiple sources. Reserve this for queries "
        "that clearly need multi-source investigation, not simple questions with direct answers. "
        'Examples: "compare React vs Svelte for SPAs in 2026", '
        '"what are the tradeoffs between microservices and monoliths for a 10-person team"\n\n'
        "Respond with exactly one word: URL, SEARCH, or RESEARCH. Nothing else."
    ),
)

title_agent = Agent(
    system_prompt=(
        "Generate a short, descriptive title for a research chat based on the "
        "user's query. The title should be concise (under 60 characters), "
        "capture the core topic, and read naturally. Do not use quotes or "
        "punctuation at the start/end. Just output the title, nothing else."
    ),
)


class SuggestResult(BaseModel):
    suggestions: list[str]


suggest_agent = Agent(
    system_prompt=(
        "You are an autocomplete engine for a smart search relay. "
        "Given a partial query, suggest 4 completions the user likely intends. "
        "Each suggestion should be a complete, natural query.\n\n"
        "Mode context:\n"
        "- launch: general purpose (search, navigate, or research)\n"
        "- discover: research-oriented questions\n"
        "- deep: in-depth research topics\n"
        "- search: web search queries"
    ),
    output_type=SuggestResult,
)


research_agent = Agent(
    system_prompt=(
        "You are a research assistant. Your job is to thoroughly answer the user's "
        "question by gathering information from the web.\n\n"
        "For each aspect you need to investigate, call the `research` tool with a "
        "focused topic string. You can call it multiple times for different aspects "
        "of the question.\n\n"
        "Use `send_message` to briefly narrate your progress — one short sentence "
        "explaining what you're about to do or what you just learned. Keep messages "
        "succinct; the user sees them inline with tool activity.\n\n"
        "If the question is ambiguous or requires information only the user can "
        "provide (personal preferences, specific constraints, etc.), call `ask_user` "
        "with clear, specific questions.\n\n"
        "After gathering enough information, write your final answer. Be thorough "
        "but concise. Use markdown formatting. Only cite sources whose content was "
        "successfully returned by the research tool — never cite URLs that failed "
        "to load or that you only saw in search result snippets."
    ),
    deps_type=ResearchDeps,
)


async def classify_query(query: str) -> str:
    """Classify a query using Pydantic AI + Gemini Flash."""
    result = await classify_agent.run(query, model=gemini_flash_lite(), model_settings=FLASH_LITE_THINKING_SETTINGS)
    text = result.output.strip().upper()
    if text not in ("URL", "SEARCH", "RESEARCH"):
        return "SEARCH"
    return text


async def generate_chat_title(query: str) -> str:
    """Generate a concise chat title from a user query."""
    try:
        result = await title_agent.run(query, model=gemini_flash_lite(), model_settings=FLASH_LITE_THINKING_SETTINGS)
        return result.output.strip()[:500]
    except Exception:
        return query[:500]


async def generate_suggestions(query: str, mode: str) -> list[str]:
    """Generate autocomplete suggestions for a partial query."""
    try:
        prompt = f"Mode: {mode}\nPartial query: {query}"
        result = await suggest_agent.run(prompt, model=gemini_flash_lite(), model_settings=FLASH_LITE_THINKING_SETTINGS)
        return result.output.suggestions
    except Exception:
        logger.exception("Failed to generate suggestions for %r", query)
        return []


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
    await queue.put(DetailEvent(type="search", payload={"topic": topic, "query": search_query}))

    try:
        results = await brave_search(search_query, api_key)
        await queue.put(DetailEvent(
            type="search_done",
            payload={"topic": topic, "query": search_query, "num_results": len(results)},
        ))
    except Exception:
        await queue.put(DetailEvent(
            type="search_done",
            payload={"topic": topic, "query": search_query, "num_results": 0},
        ))
        return f"Search failed for: {topic}"

    if not results:
        return f"No search results found for: {topic}"

    urls = _pick_top_urls(results, ctx.deps.fetched_urls)
    logger.info("Picked %d URLs for topic %r: %s", len(urls), topic, urls)
    findings: list[str] = []

    fetched_urls_list: list[str] = []
    for url in urls:
        await queue.put(DetailEvent(type="fetch", payload={"topic": topic, "url": url}))
        try:
            result = await fetch_and_extract(
                url, topic,
                deps=ctx.deps,
                db_session=ctx.deps.db_session,
                redis_url=ctx.deps.redis_url,
            )
            if result.output is None:
                await queue.put(DetailEvent(type="fetch_done", payload={"topic": topic, "url": url, "failed": True}))
                continue
            ctx.deps.fetched_urls.add(url)
            fetched_urls_list.append(url)
            findings.append(f"Source: {url}\n{result.output}")
            payload: dict = {"topic": topic, "url": url, "content": result.output[:3000]}
            if result.usage:
                payload["usage"] = result.usage
            await queue.put(DetailEvent(type="fetch_done", payload=payload))
        except Exception:
            logger.exception("fetch_and_extract crashed for %s", url)
            await queue.put(DetailEvent(type="fetch_done", payload={"topic": topic, "url": url, "failed": True}))

    if not findings:
        await queue.put(DetailEvent(
            type="result",
            payload={"topic": topic, "urls": fetched_urls_list, "num_sources": 0},
        ))
        return f"No sources could be loaded for: {topic}"

    await queue.put(DetailEvent(
        type="result",
        payload={"topic": topic, "urls": fetched_urls_list, "num_sources": len(findings)},
    ))
    return "\n\n---\n\n".join(findings)


@research_agent.tool
async def send_message(ctx: RunContext[ResearchDeps], message: str) -> str:
    """Send a message to the user explaining your thought process."""
    await ctx.deps.event_queue.put(DetailEvent(type="message", payload={"text": message}))
    return "Message sent."


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
    """Execute a Brave web search and return structured results (1 req/sec)."""
    raw_results = await _brave_search_raw(query, api_key)
    return [
        SearchResultItem(
            title=item.get("title", ""),
            url=item.get("url", ""),
            description=item.get("description", ""),
        )
        for item in raw_results
    ]


@dataclass
class ExtractionResult:
    """Return value from fetch_and_extract."""
    output: str | None = None
    usage: dict | None = None


async def fetch_and_extract(
    url: str,
    query: str,
    *,
    deps: ResearchDeps | None = None,
    db_session: AsyncSession | None = None,
    redis_url: str = "",
) -> ExtractionResult:
    """Fetch a URL and extract relevant content using the extraction agent.

    Tries Jina Reader first for clean markdown. Falls back to direct fetch
    for PDFs, images, or when Jina fails. Checks robots.txt rules before
    fetching and caches responses in Redis.

    Returns ExtractionResult with output=None if blocked by robots.txt.
    """
    # --- robots.txt check ---
    crawl_delay = 10.0
    if db_session is not None:
        allowed, crawl_delay = await check_url_allowed(url, db_session)
        if not allowed:
            logger.info("Blocked by robots.txt: %s", url)
            return ExtractionResult()

    def _track(run_result) -> dict | None:
        """Accumulate extraction usage on *deps* and return a cost dict."""
        if deps is None:
            return None
        run_usage = run_result.usage()
        deps.extraction_usage.incr(run_usage)
        return calc_usage_cost(
            run_usage.input_tokens, run_usage.output_tokens, "gemini-3.1-flash-lite-preview",
        )

    # --- Try Jina Reader first (handles HTML → markdown, has Redis cache) ---
    jina_text = await _jina_fetch(url, redis_url=redis_url)
    logger.info("Jina fetch for %s: %s", url, f"{len(jina_text)} chars" if jina_text else "None")
    if jina_text:
        prompt = f"Query: {query}\n\nDocument:\n{jina_text[:_MAX_DOC_CHARS]}"
        result = await extraction_agent.run(prompt, model=gemini_flash(), model_settings=FLASH_LITE_THINKING_SETTINGS)
        return ExtractionResult(output=result.output, usage=_track(result))

    # --- Jina failed — fall back to direct fetch (PDFs, images, etc.) ---
    parsed_url = urlparse(url)
    domain = parsed_url.hostname or ""
    await wait_for_rate_limit(domain, delay_seconds=crawl_delay, redis_url=redis_url)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            resp = await client.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=30.0,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            return ExtractionResult(output=f"Could not fetch {url}: HTTP {exc.response.status_code}")
        except httpx.RequestError as exc:
            return ExtractionResult(output=f"Could not fetch {url}: {exc}")

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
        return ExtractionResult(output=f"Unsupported content type: {content_type}")

    result = await extraction_agent.run(prompt, model=gemini_flash(), model_settings=FLASH_LITE_THINKING_SETTINGS)
    return ExtractionResult(output=result.output, usage=_track(result))


def _pick_top_urls(
    results: list[SearchResultItem],
    already_fetched: set[str],
    max_urls: int = 3,
) -> list[str]:
    """Pick the top URLs from search results that haven't been fetched yet."""
    _SKIP_DOMAINS = {"youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com"}
    urls: list[str] = []
    for r in results:
        if not r.url or r.url in already_fetched:
            continue
        host = urlparse(r.url).hostname or ""
        if host in _SKIP_DOMAINS:
            continue
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
    conversation_history: list[dict] | None = None,
    user_timezone: str = "",
) -> AsyncGenerator[PipelineEvent, None]:
    """Run the light research pipeline using the agent architecture.

    Delegates to the agent-based pipeline in lite mode for fast but
    comprehensive answers.
    """
    from controllers.deep_research_agent import (
        DetailEvent as DeepDetailEvent,
        DoneEvent as DeepDoneEvent,
        ErrorEvent as DeepErrorEvent,
        StageEvent as DeepStageEvent,
        TextEvent as DeepTextEvent,
    )

    combined_query = query
    if additional_context:
        combined_query = f"{query}\n\nAdditional context: {additional_context}"

    async for event in run_agent_research_pipeline(
        combined_query,
        brave_api_key,
        db_session=db_session,
        redis_url=redis_url,
        user_timezone=user_timezone,
        conversation_history=conversation_history,
        mode="lite",
    ):
        # Re-wrap deep events as scan_agent events for compatibility
        if isinstance(event, DeepStageEvent):
            yield StageEvent(stage=event.stage)
        elif isinstance(event, DeepDetailEvent):
            yield DetailEvent(type=event.type, payload=event.payload)
        elif isinstance(event, DeepTextEvent):
            yield TextEvent(text=event.text)
        elif isinstance(event, DeepDoneEvent):
            yield DoneEvent()
        elif isinstance(event, DeepErrorEvent):
            yield ErrorEvent(error=event.error)
