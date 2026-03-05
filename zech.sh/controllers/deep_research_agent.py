"""Research infrastructure: events, data models, search, and fetch.

Provides the shared building blocks for the agent research pipeline:
- Event types for SSE streaming (StageEvent, DetailEvent, TextEvent, etc.)
- Knowledge accumulation (KnowledgeState, KnowledgeEntry)
- Search and fetch (Brave Search, Jina Reader)
- LLM-based result filtering
"""

from __future__ import annotations

import os

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel
from pydantic_ai import Agent

from controllers.brave_search import brave_search as _shared_brave_search
from controllers.domain_throttle import cache_response, get_cached_response
from controllers.llm import calc_usage_cost, gemini_flash_lite
from controllers.robots import USER_AGENT

logger = logging.getLogger(__name__)

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
    topic: str = ""


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

    def format_by_thread(self) -> str:
        """Format entries grouped by research thread/topic."""
        if not self.entries:
            return ""
        # Group entries by topic, preserving insertion order
        threads: dict[str, list[tuple[int, KnowledgeEntry]]] = {}
        for i, e in enumerate(self.entries, 1):
            topic = e.topic or "General"
            if topic not in threads:
                threads[topic] = []
            threads[topic].append((i, e))
        parts = []
        for topic, entries in threads.items():
            thread_parts = []
            for idx, e in entries:
                thread_parts.append(
                    f"[{idx}] {e.title} ({e.url})\n{e.key_points}"
                )
            parts.append("\n\n".join(thread_parts))
        return "\n\n---\n\n".join(parts)


@dataclass
class CostBudget:
    limit: float = 1.00
    spent: float = 0.0

    def add(self, input_tokens: int, output_tokens: int, model_name: str) -> None:
        cost = calc_usage_cost(input_tokens, output_tokens, model_name)
        self.spent += float(cost["input_cost"]) + float(cost["output_cost"])

    @property
    def exhausted(self) -> bool:
        return self.spent >= self.limit


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

Dispatch = Callable[[PipelineEvent], Awaitable[None]]


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

    async def counted_stream(self, response):
        """Async-iterate a streaming response, recording usage at the end.

        Streaming chunks report cumulative totals, not per-chunk
        increments.  This wrapper yields chunks unchanged and adds
        only the final chunk's usage to the counter — making token
        accounting idempotent regardless of chunk count.
        """
        last_meta = None
        async for chunk in response:
            meta = getattr(chunk, "usage_metadata", None)
            if meta:
                last_meta = meta
            yield chunk
        if last_meta:
            self.input_tokens += last_meta.prompt_token_count or 0
            self.output_tokens += last_meta.candidates_token_count or 0


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

EXTRACTION_PROMPT = """\
Summarize this document in context of the query. Extract facts, numbers, \
dates, quotes, product details, comparisons, and anything else that could \
inform an answer — even indirectly.

When extracting numbers, preserve the conditions attached to them. \
A number without its conditions is misleading. "$15/mo" and "$15/mo \
introductory rate, regular price $30/mo" are different facts. Durations, \
eligibility requirements, thresholds, exceptions, and fine print that \
modify a claim are part of the claim.

Treat structured data (tables, spec sheets, regulatory disclosures, \
footnotes) as higher-signal than marketing copy. When they conflict, \
extract both and note the discrepancy.

Only respond with "No relevant content." if the document is entirely \
unrelated to the query's subject area OR is meaningless content (login \
walls, empty pages, cookie notices, etc.).
If the document is even loosely related, summarize what's there. Let the \
synthesis step decide what matters — your job is to not lose information.
Do not add commentary — just extract."""

LIGHT_ARTICULATION_PROMPT = """\
You are a research synthesizer. Turn research threads into a clear, useful answer for someone making decisions.

STRUCTURE
- Open with the sharpest finding, not a definition or topic overview. If the research uncovered a surprising data point, a recent structural change, or a key tension — lead with that.
- Develop unevenly. A thread with strong evidence gets a full paragraph. A thread with only general claims gets one sentence or nothing. Don't pad thin threads to match the length of strong ones.
- Close forward. End with what the reader should do, watch for, or consider — not a restatement of what you already said.

EVIDENCE
- Cite inline with [1], [2]
- Prefer concrete: names, versions, dollar amounts, specific tools. "Bun cold-starts in ~40ms vs Node's ~150ms on Lambda [3]" beats "Bun has faster cold starts."
- If sources disagree, say so in one sentence
- If evidence is thin on a point, say so rather than asserting confidently

VOICE
- Knowledgeable colleague, not textbook
- Natural prose paragraphs — no bullet lists unless presenting 4+ genuinely parallel items
- No filler ("It's important to note," "Let's dive in," "In conclusion")
- Don't restate the question

CALIBRATE LENGTH TO EVIDENCE
A few strong paragraphs beat a long answer that stretches thin research. If the threads only support 400 words of substantive content, write 400 words. Never pad.

TABLES
When comparing multiple items (tools, frameworks, options, providers, etc.), use a markdown table. Tables make comparisons scannable. Include specific data in cells — versions, numbers, tool names — not vague summaries. Even 2-3 items benefit from a table if the comparison has multiple dimensions.

DO NOT
- Use numbered sections (### 1, ### 2, ### 3)
- End with a summary that restates the opening
- Give every topic equal space

End with ## Sources as [n] Title — URL"""

ARTICULATION_PROMPT = """\
You are a research synthesizer producing expert briefings from accumulated research threads.

THINK ABOUT THE PERSON
Consider intent, blind spots, and obstacles. What would they assume before reading this? What would change their thinking? If the research reveals the question is wrong or incomplete, say so and reframe.

STRUCTURE
Structure the answer as a narrative argument, not a reference document.

- Open with the sharpest thing you found. A surprising data point, a structural shift, a quote that captures the whole thesis. Never open with a definition or by restating the question.
- Develop unevenly. A thread with strong evidence and concrete examples deserves 2-3 paragraphs. A thread with only general assertions deserves one sentence woven into another section, or nothing. Do not give every topic equal weight.
- Integrate threads. Show causation between them — how the architecture enables the business model, how the obstacle explains the competitive landscape. The reader should feel the argument building.
- Include the counterargument. What makes this harder than it sounds? Why hasn't the obvious conclusion already won? This is not a disclaimer — it's often the most valuable section. Develop it with the same rigor as the thesis.
- Close with implications, not summary. What does this mean going forward? What should the reader do or watch for? Never restate what you already said. If the final paragraph could be deleted without losing information, rewrite it.

Use markdown headers sparingly — only at genuine topic shifts. Make them specific and descriptive ("The Monorepo Problem Nobody Warns You About") not generic ("Key Challenges"). Never number them.

EVIDENCE
- Every factual claim gets an inline citation [n]
- Concrete over abstract: name companies, cite dollar amounts, reference specific versions and tools. A claim with a named example is worth three without.
- When sources disagree, say so and explain why
- When evidence is thin or single-sourced, say so. "Based on limited early data" is more credible than false confidence.

VOICE
Write as a knowledgeable colleague briefing someone smart. Natural prose paragraphs — no bullet-point lists in the body unless presenting genuinely parallel items (a set of 4+ tools or metrics). If you catch yourself writing bullets, convert to prose. Do not use filler phrases ("It's important to note," "Let's dive in," "In conclusion," "Here's what you need to know").

TABLES
When comparing multiple items (tools, frameworks, options, providers, etc.), use a markdown table. Tables make comparisons scannable and are always preferred over inline prose for side-by-side evaluation. Include specific data in cells — versions, numbers, tool names — not restatements of your prose. Even 2-3 items benefit from a table if the comparison has multiple dimensions. Never use a table to summarize your own argument.

CALIBRATE DEPTH TO EVIDENCE
If the research threads are thin, write a shorter, tighter answer. A 600-word answer that's honest about what's known is better than a 1500-word answer that pads thin research with generalities. Do not speculate to fill space.

End with ## Sources as [n] Title — URL"""


# ---------------------------------------------------------------------------
# LLM-based search result filtering
# ---------------------------------------------------------------------------


class FilteredResults(BaseModel):
    """Indices of search results worth reading, most relevant first."""
    indices: list[int]


_filter_agent = Agent(
    system_prompt=(
        "You are a search result relevance ranker. Given a user's research "
        "query, context about what is already known, and a numbered list of "
        "search results (title, URL, snippet), RANK the results by relevance "
        "and return ONLY the indices of high-quality, relevant results.\n\n"
        "RANK ORDER: Return indices sorted from MOST relevant to LEAST "
        "relevant. The first index should be the single best result.\n\n"
        "EXCLUDE completely:\n"
        "- SEO spam, thin aggregator pages, listicles with no substance\n"
        "- Paywalled landing pages with no real content in the snippet\n"
        "- Duplicates of content already known\n"
        "- Results clearly unrelated to the research query\n"
        "- Generic overviews when we already have overview-level knowledge\n\n"
        "PRIORITIZE:\n"
        "- Primary sources and original research\n"
        "- Expert analysis with depth and nuance\n"
        "- Technical documentation and official references\n"
        "- Data-rich sources (studies, reports, datasets)\n"
        "- Perspectives not yet represented in existing knowledge\n\n"
        "Be selective — only include results that will genuinely advance "
        "the research. Quality over quantity."
    ),
    output_type=FilteredResults,
)



# ---------------------------------------------------------------------------
# Searcher — Brave Search + Jina Reader
# ---------------------------------------------------------------------------

_SKIP_DOMAINS = frozenset({
    "youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com",
})

async def _brave_search(
    query: str,
    api_key: str,
    count: int = 5,
) -> list[dict]:
    """Run a Brave web search, returning raw result dicts."""
    return await _shared_brave_search(query, api_key, count=count)


class _JinaRateLimiter:
    """Token-bucket rate limiter for Jina Reader API.

    Allows bursts up to ``burst`` concurrent requests but enforces
    a minimum ``interval`` seconds between request starts to stay
    under Jina's rate limit.
    """

    def __init__(self, burst: int = 2, interval: float = 1.0) -> None:
        self._sem: asyncio.Semaphore | None = None
        self._lock: asyncio.Lock | None = None
        self._last: float = 0.0
        self._burst = burst
        self._interval = interval

    def _ensure_init(self) -> None:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._burst)
            self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        self._ensure_init()
        assert self._sem is not None and self._lock is not None
        await self._sem.acquire()
        async with self._lock:
            import time
            now = time.monotonic()
            wait = self._interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()

    def release(self) -> None:
        assert self._sem is not None
        self._sem.release()


_jina_limiter = _JinaRateLimiter(burst=5, interval=0.5)


async def _jina_fetch(url: str, redis_url: str = "") -> str | None:
    """Fetch a URL's content as markdown via Jina Reader."""
    jina_url = f"https://r.jina.ai/{url}"

    # Check cache first
    cached = await get_cached_response(jina_url, redis_url=redis_url)
    if cached is not None:
        return cached.get("text")

    _MAX_RETRIES = 3
    _headers: dict[str, str] = {"Accept": "text/markdown", "User-Agent": USER_AGENT}
    jina_key = os.environ.get("JINA_API_KEY", "")
    if jina_key:
        _headers["Authorization"] = f"Bearer {jina_key}"

    await _jina_limiter.acquire()
    try:
        async with httpx.AsyncClient() as client:
            for attempt in range(_MAX_RETRIES):
                try:
                    resp = await client.get(
                        jina_url, headers=_headers, timeout=15.0,
                    )
                except Exception:
                    logger.exception("Jina fetch error for %s (attempt %d)", url, attempt)
                    if attempt < _MAX_RETRIES - 1:
                        await asyncio.sleep(2 ** attempt * 2)
                        continue
                    return None

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

                if resp.status_code == 429 and attempt < _MAX_RETRIES - 1:
                    backoff = 2 ** attempt * 3  # 3s, 6s
                    logger.info("Jina 429 for %s, backing off %ds", url, backoff)
                    await asyncio.sleep(backoff)
                    continue

                # Non-retryable status
                logger.warning("Jina non-retryable %d for %s", resp.status_code, url)
                return None
    except Exception:
        logger.exception("Jina outer error for %s", url)
        return None
    finally:
        _jina_limiter.release()
    return None


async def _filter_results(
    results: list[dict],
    query: str,
    knowledge: KnowledgeState,
    already_fetched: set[str],
    extraction_counter: TokenCounter,
) -> list[tuple[str, str]]:
    """Use flash-lite to filter and rank search results for quality.

    Always runs the LLM filter to exclude low-quality results.
    Returns the full ranked list of (url, title) tuples — the caller
    controls batch sizing.  Falls back to all candidates on error.
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

    if not candidates:
        return []

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
        f"Rank these results from MOST to LEAST relevant. Return only "
        f"high-quality, substantive results — exclude SEO spam, thin "
        f"aggregator pages, listicles, paywalled landing pages, and "
        f"duplicates of what we already know. Put the best source first."
    )

    try:
        result = await _filter_agent.run(prompt, model=gemini_flash_lite())
        usage = result.usage()
        extraction_counter.input_tokens += usage.request_tokens or 0
        extraction_counter.output_tokens += usage.response_tokens or 0

        # Map returned indices to (url, title) tuples
        valid_indices = {idx for idx, _, _, _ in candidates}
        filtered: list[tuple[str, str]] = []
        for idx in result.output.indices:
            if idx in valid_indices:
                for ci, curl, ctitle, _ in candidates:
                    if ci == idx:
                        filtered.append((curl, ctitle))
                        break

        if filtered:
            logger.info(
                "Filter ranked %d/%d candidates for query %r",
                len(filtered), len(candidates), query,
            )
            return filtered

        logger.warning("Filter returned no valid indices, falling back to naive")

    except Exception:
        logger.exception("Filter agent failed, falling back to naive selection")

    # Fallback: all candidates by rank order
    return [(url, title) for _, url, title, _ in candidates]


