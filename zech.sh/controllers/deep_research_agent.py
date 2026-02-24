"""Deep research agent with reasoning-first architecture.

Uses the google-genai SDK directly with Gemini models, Brave Search API,
and Jina Reader for content fetching. A reasoning LLM thinks out loud,
decides when to search, reflects on results, and loops until ready
to write a cited response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from google import genai
from google.genai.types import (
    GenerateContentConfig,
    ThinkingConfig,
    ThinkingLevel,
)
from genai_prices import calc_price
from genai_prices import Usage as GenAIUsage
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from controllers.domain_throttle import cache_response, get_cached_response
from controllers.robots import USER_AGENT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Google genai client
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _genai_client() -> genai.Client:
    return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


@lru_cache(maxsize=1)
def _google_provider() -> GoogleProvider:
    return GoogleProvider(api_key=os.environ["GOOGLE_API_KEY"])


@lru_cache(maxsize=1)
def _flash_lite_model() -> GoogleModel:
    return GoogleModel("gemini-2.5-flash-lite", provider=_google_provider())


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class KnowledgeEntry:
    source_id: str
    url: str
    title: str
    query: str
    key_points: str
    char_count: int


@dataclass
class KnowledgeState:
    entries: list[KnowledgeEntry] = field(default_factory=list)
    total_chars: int = 0

    def add(self, entry: KnowledgeEntry) -> None:
        self.entries.append(entry)
        self.total_chars += entry.char_count

    def needs_compression(self, max_chars: int) -> bool:
        return self.total_chars > max_chars

    def format_for_prompt(self) -> str:
        if not self.entries:
            return ""
        parts = []
        for i, e in enumerate(self.entries, 1):
            parts.append(
                f"[{i}] {e.title} ({e.url})\nQuery: {e.query}\n{e.key_points}"
            )
        return "\n\n---\n\n".join(parts)

    def format_source_list(self) -> str:
        if not self.entries:
            return ""
        return "\n".join(
            f"[{i}] {e.title} — {e.url}"
            for i, e in enumerate(self.entries, 1)
        )


# ---------------------------------------------------------------------------
# Pipeline event types (compatible with existing frontend)
# ---------------------------------------------------------------------------

StageName = Literal["reasoning", "researching", "responding"]


@dataclass
class StageEvent:
    stage: StageName


@dataclass
class DetailEvent:
    type: str
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


PipelineEvent = StageEvent | DetailEvent | TextEvent | DoneEvent | ErrorEvent


# ---------------------------------------------------------------------------
# Token tracking
# ---------------------------------------------------------------------------


@dataclass
class TokenCounter:
    input_tokens: int = 0
    output_tokens: int = 0

    def add_from_response(self, response) -> None:
        """Extract and accumulate token counts from a non-streaming response."""
        meta = getattr(response, "usage_metadata", None)
        if meta:
            self.input_tokens += meta.prompt_token_count or 0
            self.output_tokens += meta.candidates_token_count or 0

    def counted_stream(self, response):
        """Iterate a streaming response, recording usage once at the end.

        Streaming chunks report cumulative totals, not per-chunk
        increments.  This wrapper yields chunks unchanged and adds
        only the final chunk's usage to the counter — making token
        accounting idempotent regardless of chunk count.
        """
        last_meta = None
        for chunk in response:
            meta = getattr(chunk, "usage_metadata", None)
            if meta:
                last_meta = meta
            yield chunk
        if last_meta:
            self.input_tokens += last_meta.prompt_token_count or 0
            self.output_tokens += last_meta.candidates_token_count or 0


def _calc_cost(input_tokens: int, output_tokens: int, model_name: str) -> dict:
    """Calculate cost for a model call and return a usage dict."""
    usage = GenAIUsage(input_tokens=input_tokens, output_tokens=output_tokens)
    price = calc_price(usage, model_name)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "input_cost": f"{price.input_price:.4f}",
        "output_cost": f"{price.output_price:.4f}",
    }


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

REASONING_PROMPT = """\
You are a research reasoning engine. Your job is to THINK DEEPLY before \
deciding what to search for.

Write your reasoning as natural, exploratory prose. Do not summarize or \
bullet-point your way through it — actually think. Your reasoning is \
shown directly to the user, so make it substantive and worth reading.

HOW TO REASON:

Start by interrogating the question itself:
- What is actually being asked? What assumptions does it carry?
- What would a GOOD answer look like? What would make it authoritative?
- What do you already know about this? How confident are you?

Then map the knowledge landscape:
- What do you know for certain? What are you fuzzy on?
- What do you THINK you know but can't verify from memory?
- Where might your knowledge be outdated or wrong?
- What adjacent or tangential topics might illuminate this?
- What would someone with deep domain expertise think to check?

Think outside the obvious:
- What's the contrarian take? Who would disagree with the mainstream view?
- Are there historical parallels, cross-domain analogies, or edge cases?
- What context or nuance would most people miss?
- What's the difference between the surface-level answer and the real answer?

WHEN KNOWLEDGE HAS BEEN GATHERED (follow-up calls):
Don't just check boxes. Critically evaluate what you've learned:
- Do sources agree or contradict each other? Why?
- What's still weak, speculative, or based on a single source?
- Has anything you've found changed your understanding of the question?
- Are there follow-up threads worth pulling on?
- Is there a perspective or angle completely unrepresented?

ENDING YOUR RESPONSE:

After reasoning, end with exactly one of:

SEARCH: ["query one", "query two", "query three"]
  — A JSON array of 3-7 search queries, each 3-8 words.
  — Craft queries like a skilled researcher: specific, varied angles, \
not just restatements of the question.
  — Mix definitional, comparative, temporal, causal, and contrarian queries.
  — Never repeat a previous search.

READY
  — Only when you genuinely have enough to write a well-sourced, \
nuanced answer. Briefly explain why the evidence is sufficient.

RULES:
- ALWAYS write substantial reasoning before SEARCH or READY
- SEARCH or READY must appear on its own line at the very end
- The SEARCH JSON array must be on the same line as SEARCH:"""

EXTRACTION_PROMPT = """\
Extract the key information relevant to the query from this document. \
Be concise but preserve important facts, numbers, dates, and quotes. \
Focus on what directly addresses or illuminates the query. \
If nothing relevant, respond with "No relevant content." \
Do not add commentary — just extract."""

ARTICULATION_PROMPT = """\
You are a research synthesizer. Write a clear, well-cited answer \
from the accumulated knowledge.

RULES:
- Lead with the direct answer
- Natural prose, not bullet lists
- Integrate across sources — don't summarize each sequentially
- Present both sides when sources disagree
- Inline citations: [1], [2] keyed to the source list provided
- Every factual claim gets a citation
- End with ## Sources as [n] Title — URL
- State uncertainty when evidence is weak

AVOID:
- "It's important to note..."
- "In conclusion..."
- Filler, hedging, repeating the question"""


# ---------------------------------------------------------------------------
# LLM-based search result filtering
# ---------------------------------------------------------------------------


class FilteredResults(BaseModel):
    """Indices of search results worth reading, most relevant first."""
    indices: list[int]


_filter_agent = Agent(
    system_prompt=(
        "You are a search result relevance filter. Given a user's research "
        "query, context about what is already known, and a numbered list of "
        "search results (title, URL, snippet), return the indices of results "
        "most likely to contain useful, substantive information for the query. "
        "Prefer primary sources, authoritative references, and results whose "
        "snippets suggest real content over thin aggregator pages. "
        "Return indices in descending order of expected relevance."
    ),
    output_type=FilteredResults,
)


# ---------------------------------------------------------------------------
# Pipeline configuration
# ---------------------------------------------------------------------------

CONFIG = {
    "max_iterations": 5,
    "reasoning_model": "gemini-3-pro-preview",
    "articulation_model": "gemini-3-pro-preview",
    "extraction_model": "gemini-3-flash-preview",
    "articulation_thinking": "high",
    "brave_results": 15,
    "jina_reads": 5,
    "max_knowledge_chars": 100_000,
    "compress_target_chars": 70_000,
    "extract_max_chars": 1200,
    "fetch_max_chars": 20_000,
}


# ---------------------------------------------------------------------------
# Searcher — Brave Search + Jina Reader
# ---------------------------------------------------------------------------

_SKIP_DOMAINS = frozenset({
    "youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com",
})

_brave_lock: asyncio.Lock | None = None
_brave_last_call: float = 0.0


def _get_brave_lock() -> asyncio.Lock:
    global _brave_lock
    if _brave_lock is None:
        _brave_lock = asyncio.Lock()
    return _brave_lock


async def _brave_search(
    query: str,
    api_key: str,
    count: int = 5,
) -> list[dict]:
    """Run a Brave web search, returning raw result dicts."""
    global _brave_last_call
    lock = _get_brave_lock()
    async with lock:
        wait = 1.0 - (time.monotonic() - _brave_last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "User-Agent": USER_AGENT,
                    "X-Subscription-Token": api_key,
                },
                params={"q": query, "count": count},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        _brave_last_call = time.monotonic()

    return data.get("web", {}).get("results", [])


async def _jina_fetch(url: str, redis_url: str = "") -> str | None:
    """Fetch a URL's content as markdown via Jina Reader."""
    jina_url = f"https://r.jina.ai/{url}"

    # Check cache first
    cached = await get_cached_response(jina_url, redis_url=redis_url)
    if cached is not None:
        return cached.get("text")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                jina_url,
                headers={
                    "Accept": "text/markdown",
                    "User-Agent": USER_AGENT,
                },
                timeout=15.0,
            )
            if resp.status_code == 200:
                await cache_response(
                    jina_url,
                    resp.status_code,
                    dict(resp.headers),
                    resp.text,
                    resp.headers.get("content-type", "text/markdown"),
                    redis_url=redis_url,
                )
                return resp.text
            return None
    except Exception:
        return None


async def _filter_results(
    results: list[dict],
    query: str,
    knowledge: KnowledgeState,
    already_fetched: set[str],
    cfg: dict,
    extraction_counter: TokenCounter,
) -> list[tuple[str, str]]:
    """Use flash-lite to pick the most relevant search results to read.

    Returns a list of (url, title) tuples, capped at cfg["jina_reads"].
    Falls back to naive first-N selection on any error.
    """
    # Build candidate list excluding already-fetched and skip domains
    candidates: list[tuple[int, str, str, str]] = []  # (idx, url, title, snippet)
    for i, r in enumerate(results):
        url = r.get("url", "")
        title = r.get("title", "")
        snippet = r.get("description", "")
        if not url or url in already_fetched:
            continue
        host = urlparse(url).hostname or ""
        if host in _SKIP_DOMAINS:
            continue
        candidates.append((i, url, title, snippet))

    if len(candidates) <= cfg["jina_reads"]:
        return [(url, title) for _, url, title, _ in candidates]

    # Build numbered list for the LLM
    numbered = "\n".join(
        f"[{idx}] {title} — {url}\n{snippet}"
        for idx, url, title, snippet in candidates
    )
    context = knowledge.format_for_prompt() if knowledge.entries else "(none yet)"
    prompt = (
        f"Research query: {query}\n\n"
        f"What we already know:\n{context}\n\n"
        f"Search results:\n{numbered}\n\n"
        f"Pick up to {cfg['jina_reads']} results most worth reading. "
        f"Return their index numbers."
    )

    try:
        result = await _filter_agent.run(prompt, model=_flash_lite_model())
        usage = result.usage()
        extraction_counter.input_tokens += usage.request_tokens or 0
        extraction_counter.output_tokens += usage.response_tokens or 0

        # Map returned indices to (url, title) tuples
        valid_indices = {idx for idx, _, _, _ in candidates}
        filtered: list[tuple[str, str]] = []
        for idx in result.output.indices:
            if idx in valid_indices and len(filtered) < cfg["jina_reads"]:
                # Find the candidate with this index
                for ci, curl, ctitle, _ in candidates:
                    if ci == idx:
                        filtered.append((curl, ctitle))
                        break

        if filtered:
            logger.info(
                "Filter picked %d/%d results for query %r",
                len(filtered), len(candidates), query,
            )
            return filtered

        # LLM returned empty or all-invalid indices — fall through
        logger.warning("Filter returned no valid indices, falling back to naive")

    except Exception:
        logger.exception("Filter agent failed, falling back to naive selection")

    # Fallback: first N candidates by rank order
    return [(url, title) for _, url, title, _ in candidates[: cfg["jina_reads"]]]


# ---------------------------------------------------------------------------
# Reasoning module
# ---------------------------------------------------------------------------


def _parse_reasoning_result(text: str) -> list[str] | None:
    """Parse accumulated reasoning text for SEARCH queries or READY signal.

    Returns a list of query strings if SEARCH found, None if READY or
    no directive found (proceed to articulate).
    """
    # Look at the last portion of text for the directive
    tail = text[-500:] if len(text) > 500 else text

    # Check for SEARCH: [...] pattern
    match = re.search(r"SEARCH:\s*(\[.*\])", tail, re.DOTALL)
    if match:
        try:
            queries = json.loads(match.group(1))
            if isinstance(queries, list) and queries:
                return [str(q) for q in queries]
        except (json.JSONDecodeError, ValueError):
            pass

    # Check for READY
    if re.search(r"\bREADY\b", tail):
        return None

    # Fallback: no directive found, proceed to articulate
    return None


async def _reason(
    query: str,
    knowledge: KnowledgeState,
    cfg: dict,
    event_queue: asyncio.Queue[PipelineEvent],
    counter: TokenCounter,
) -> list[str] | None:
    """Stream reasoning text, return search queries or None if ready."""
    client = _genai_client()

    if knowledge.entries:
        user_msg = (
            f"QUESTION: {query}\n\n"
            f"ACCUMULATED KNOWLEDGE:\n{knowledge.format_for_prompt()}\n\n"
            f"Review what you know, identify gaps, and decide whether to "
            f"search more or articulate."
        )
    else:
        user_msg = (
            f"QUESTION: {query}\n\n"
            f"This is your first look at this question. "
            f"Think through what you need to know and search for it."
        )

    # No ThinkingConfig here — we WANT the model to reason in its
    # visible text output so we can stream it to the user.  With
    # ThinkingConfig the model puts reasoning into hidden thinking
    # tokens and chunk.text returns only a terse SEARCH/READY.
    response = client.models.generate_content_stream(
        model=cfg["reasoning_model"],
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=REASONING_PROMPT,
        ),
    )

    full_text = ""
    for chunk in counter.counted_stream(response):
        if chunk.text:
            await event_queue.put(DetailEvent(
                type="reasoning",
                payload={"text": chunk.text},
            ))
            full_text += chunk.text

    result = _parse_reasoning_result(full_text)
    logger.info(
        "Reasoning result: %s queries, tail: %s",
        len(result) if result else "READY/None",
        repr(full_text[-200:]) if full_text else "(empty)",
    )
    return result


# ---------------------------------------------------------------------------
# Search + extract module
# ---------------------------------------------------------------------------


async def _search_and_extract(
    queries: list[str],
    knowledge: KnowledgeState,
    brave_api_key: str,
    already_fetched: set[str],
    cfg: dict,
    event_queue: asyncio.Queue[PipelineEvent],
    extraction_counter: TokenCounter,
    redis_url: str = "",
) -> None:
    """Execute search queries, fetch pages, extract knowledge entries."""
    client = _genai_client()

    for query_text in queries:
        # Emit research group
        await event_queue.put(DetailEvent(
            type="research",
            payload={"topic": query_text},
        ))

        # --- Brave search ---
        await event_queue.put(DetailEvent(
            type="search",
            payload={"topic": query_text, "query": query_text},
        ))

        try:
            results = await _brave_search(
                query_text, brave_api_key, count=cfg["brave_results"],
            )
            await event_queue.put(DetailEvent(
                type="search_done",
                payload={
                    "topic": query_text,
                    "query": query_text,
                    "num_results": len(results),
                },
            ))
        except Exception:
            await event_queue.put(DetailEvent(
                type="search_done",
                payload={"topic": query_text, "query": query_text, "num_results": 0},
            ))
            await event_queue.put(DetailEvent(
                type="result",
                payload={"topic": query_text, "urls": [], "num_sources": 0},
            ))
            continue

        # --- Pick top URLs via LLM filtering ---
        urls_to_fetch = await _filter_results(
            results, query_text, knowledge, already_fetched,
            cfg, extraction_counter,
        )

        # Emit fetch start events
        for url, _title in urls_to_fetch:
            await event_queue.put(DetailEvent(
                type="fetch",
                payload={"topic": query_text, "url": url},
            ))

        # --- Fetch in parallel via Jina ---
        fetch_results = await asyncio.gather(
            *[_jina_fetch(url, redis_url=redis_url) for url, _ in urls_to_fetch],
            return_exceptions=True,
        )

        # --- Extract knowledge from each document ---
        source_urls: list[str] = []
        for (url, title), content in zip(urls_to_fetch, fetch_results):
            if isinstance(content, Exception):
                content = None

            if not content:
                await event_queue.put(DetailEvent(
                    type="fetch_done",
                    payload={"topic": query_text, "url": url, "failed": True},
                ))
                continue

            already_fetched.add(url)
            truncated = content[: cfg["fetch_max_chars"]]

            # Extract via gemini-flash
            extraction_prompt = (
                f"Query: {query_text}\n\n"
                f"Document from {title} ({url}):\n{truncated}"
            )

            try:
                extract_resp = client.models.generate_content(
                    model=cfg["extraction_model"],
                    contents=extraction_prompt,
                    config=GenerateContentConfig(
                        system_instruction=EXTRACTION_PROMPT,
                        thinking_config=ThinkingConfig(
                            thinking_level=ThinkingLevel.MINIMAL,
                        ),
                    ),
                )
                extraction_counter.add_from_response(extract_resp)

                extracted = extract_resp.text or ""
                if len(extracted) > cfg["extract_max_chars"]:
                    extracted = extracted[: cfg["extract_max_chars"]]

                if extracted and "no relevant content" not in extracted.lower():
                    entry = KnowledgeEntry(
                        source_id=str(uuid4())[:8],
                        url=url,
                        title=title,
                        query=query_text,
                        key_points=extracted,
                        char_count=len(extracted),
                    )
                    knowledge.add(entry)

                    # Build extraction usage info
                    ext_meta = getattr(extract_resp, "usage_metadata", None)
                    usage_dict = None
                    if ext_meta:
                        usage_dict = _calc_cost(
                            ext_meta.prompt_token_count or 0,
                            ext_meta.candidates_token_count or 0,
                            cfg["extraction_model"],
                        )

                    await event_queue.put(DetailEvent(
                        type="fetch_done",
                        payload={
                            "topic": query_text,
                            "url": url,
                            "content": extracted[:3000],
                            **({"usage": usage_dict} if usage_dict else {}),
                        },
                    ))
                else:
                    await event_queue.put(DetailEvent(
                        type="fetch_done",
                        payload={
                            "topic": query_text,
                            "url": url,
                            "content": "(no relevant content)",
                        },
                    ))

                source_urls.append(url)

            except Exception:
                logger.exception("Extraction failed for %s", url)
                await event_queue.put(DetailEvent(
                    type="fetch_done",
                    payload={"topic": query_text, "url": url, "failed": True},
                ))

        # Collapse research group
        await event_queue.put(DetailEvent(
            type="result",
            payload={
                "topic": query_text,
                "urls": source_urls,
                "num_sources": len(source_urls),
            },
        ))


# ---------------------------------------------------------------------------
# Articulation module
# ---------------------------------------------------------------------------


async def _articulate(
    query: str,
    knowledge: KnowledgeState,
    cfg: dict,
    event_queue: asyncio.Queue[PipelineEvent],
    counter: TokenCounter,
) -> None:
    """Stream the final cited response."""
    client = _genai_client()

    user_msg = (
        f"QUESTION: {query}\n\n"
        f"KNOWLEDGE:\n{knowledge.format_for_prompt()}\n\n"
        f"SOURCES:\n{knowledge.format_source_list()}\n\n"
        f"Write the answer. Cite inline with [n]."
    )

    thinking_level = (
        ThinkingLevel.HIGH if cfg["articulation_thinking"] == "high"
        else ThinkingLevel.MEDIUM
    )

    response = client.models.generate_content_stream(
        model=cfg["articulation_model"],
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=ARTICULATION_PROMPT,
            thinking_config=ThinkingConfig(thinking_level=thinking_level),
        ),
    )

    for chunk in counter.counted_stream(response):
        if chunk.text:
            await event_queue.put(TextEvent(text=chunk.text))


# ---------------------------------------------------------------------------
# Knowledge compression
# ---------------------------------------------------------------------------


async def _compress_knowledge(
    knowledge: KnowledgeState,
    target_chars: int,
    extraction_model: str,
    counter: TokenCounter,
) -> None:
    """Compress oldest knowledge entries to reduce context size."""
    client = _genai_client()

    while knowledge.total_chars > target_chars and len(knowledge.entries) > 1:
        oldest = knowledge.entries[0]

        compress_prompt = (
            f"Compress this extracted knowledge to roughly half its length, "
            f"preserving the most important facts:\n\n{oldest.key_points}"
        )

        try:
            response = client.models.generate_content(
                model=extraction_model,
                contents=compress_prompt,
                config=GenerateContentConfig(
                    thinking_config=ThinkingConfig(
                        thinking_level=ThinkingLevel.MINIMAL,
                    ),
                ),
            )
            counter.add_from_response(response)

            compressed = response.text or oldest.key_points
            old_chars = oldest.char_count
            new_chars = len(compressed)

            oldest.key_points = compressed
            oldest.char_count = new_chars
            knowledge.total_chars += new_chars - old_chars

            # If compression didn't help much, stop
            if new_chars > old_chars * 0.8:
                break
        except Exception:
            break


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def run_deep_research_pipeline(
    query: str,
    brave_api_key: str,
    *,
    db_session=None,
    redis_url: str = "",
    user_timezone: str = "",
) -> AsyncGenerator[PipelineEvent, None]:
    """Run the deep research pipeline, yielding SSE-compatible events.

    This is the main entry point, matching the same generator pattern
    as ``run_research_pipeline`` in scan_agent.py.
    """
    cfg = CONFIG

    event_queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()
    knowledge = KnowledgeState()
    already_fetched: set[str] = set()

    reasoning_counter = TokenCounter()
    extraction_counter = TokenCounter()
    articulation_counter = TokenCounter()

    async def _run() -> None:
        try:
            for _iteration in range(1, cfg["max_iterations"] + 1):
                # --- REASON ---
                await event_queue.put(StageEvent(stage="reasoning"))

                queries = await _reason(
                    query, knowledge, cfg, event_queue,
                    reasoning_counter,
                )

                if queries is None:
                    # Model decided it has enough info
                    break

                # --- RESEARCH ---
                await event_queue.put(StageEvent(stage="researching"))

                await _search_and_extract(
                    queries,
                    knowledge,
                    brave_api_key,
                    already_fetched,
                    cfg,
                    event_queue,
                    extraction_counter,
                    redis_url=redis_url,
                )

                # --- COMPRESS if needed ---
                if knowledge.needs_compression(cfg["max_knowledge_chars"]):
                    await _compress_knowledge(
                        knowledge,
                        cfg["compress_target_chars"],
                        cfg["extraction_model"],
                        extraction_counter,
                    )

            # --- ARTICULATE ---
            await _articulate(
                query, knowledge, cfg, event_queue, articulation_counter,
            )

            # --- USAGE ---
            # Reasoning + extraction = "research" for display
            reasoning_cost = _calc_cost(
                reasoning_counter.input_tokens,
                reasoning_counter.output_tokens,
                cfg["reasoning_model"],
            )
            extraction_cost = _calc_cost(
                extraction_counter.input_tokens,
                extraction_counter.output_tokens,
                cfg["extraction_model"],
            )

            research_in = (
                reasoning_counter.input_tokens + extraction_counter.input_tokens
            )
            research_out = (
                reasoning_counter.output_tokens + extraction_counter.output_tokens
            )
            research_input_cost = (
                float(reasoning_cost["input_cost"])
                + float(extraction_cost["input_cost"])
            )
            research_output_cost = (
                float(reasoning_cost["output_cost"])
                + float(extraction_cost["output_cost"])
            )

            # Articulation = "extraction" label for display compatibility
            articulation_cost = _calc_cost(
                articulation_counter.input_tokens,
                articulation_counter.output_tokens,
                cfg["articulation_model"],
            )

            total_in = research_in + articulation_counter.input_tokens
            total_out = research_out + articulation_counter.output_tokens
            total_input_cost = (
                research_input_cost + float(articulation_cost["input_cost"])
            )
            total_output_cost = (
                research_output_cost + float(articulation_cost["output_cost"])
            )

            await event_queue.put(DetailEvent(
                type="usage",
                payload={
                    "research": {
                        "input_tokens": research_in,
                        "output_tokens": research_out,
                        "input_cost": f"{research_input_cost:.4f}",
                        "output_cost": f"{research_output_cost:.4f}",
                    },
                    "extraction": articulation_cost,
                    "total": {
                        "input_tokens": total_in,
                        "output_tokens": total_out,
                        "input_cost": f"{total_input_cost:.4f}",
                        "output_cost": f"{total_output_cost:.4f}",
                    },
                },
            ))

            await event_queue.put(DoneEvent())

        except Exception as exc:
            logger.exception("Deep research pipeline error")
            await event_queue.put(ErrorEvent(error=str(exc)))

    task = asyncio.create_task(_run())

    sent_responding = False
    while True:
        event = await event_queue.get()
        if isinstance(event, TextEvent) and not sent_responding:
            sent_responding = True
            yield StageEvent(stage="responding")
        yield event
        if isinstance(event, (DoneEvent, ErrorEvent)):
            break

    await task
