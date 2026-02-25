"""Deep research agent with concurrent topic-based architecture.

Uses the google-genai SDK directly with Gemini models, Brave Search API,
and Jina Reader for content fetching. A planning LLM decomposes the
question into distinct research topics, which are investigated
concurrently with quality gates and a cost budget.

Three phases:
  Phase 1: PLAN       — Decompose question into research topics
  Phase 2: RESEARCH   — Run all topics concurrently, each iterating independently
  Phase 3: ARTICULATE — Synthesize all accumulated knowledge into a deep dive
"""

from __future__ import annotations

import os

import asyncio
import json
import logging
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from google.genai.types import (
    GenerateContentConfig,
    ThinkingConfig,
    ThinkingLevel,
)
from pydantic import BaseModel
from pydantic_ai import Agent

from controllers.brave_search import brave_search as _shared_brave_search
from controllers.domain_throttle import cache_response, get_cached_response
from controllers.llm import calc_usage_cost, gemini_flash_lite, genai_client
from controllers.robots import USER_AGENT, check_url_allowed

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
            thread_parts = [f"### Thread: {topic}"]
            for idx, e in entries:
                thread_parts.append(
                    f"[{idx}] {e.title} ({e.url})\n{e.key_points}"
                )
            parts.append("\n\n".join(thread_parts))
        return "\n\n---\n\n".join(parts)


@dataclass
class TopicPlan:
    id: str                    # "t1", "t2", etc.
    label: str                 # Short label for UI tool-group header
    description: str           # What to investigate and why
    queries: list[str]         # Starting search queries
    generation: int = 0        # 0 = original, 1 = spawned


@dataclass
class TopicResult:
    topic_id: str
    entries_added: int
    spawned: list[TopicPlan] = field(default_factory=list)
    iterations_used: int = 0


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
# System prompts
# ---------------------------------------------------------------------------

PLANNING_PROMPT = """\
You are a research planning engine. Your job is to THINK DEEPLY about \
the question, then decompose it into distinct research topics that can \
be investigated concurrently.

Write your reasoning as natural, exploratory prose — actually think, don't \
just list bullet points. Your reasoning is shown directly to the user, \
so make it substantive. Keep it to 2-4 dense paragraphs (roughly 150-300 \
words). Every sentence should earn its place.

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

ENDING YOUR RESPONSE:

After reasoning, end with a TOPICS directive that decomposes the question \
into 3-6 concurrent research threads:

TOPICS: [{"label": "Short label", "description": "What to investigate \
and why", "queries": ["search query 1", "search query 2"]}, ...]

RULES:
- ALWAYS write substantial reasoning before TOPICS
- 3-6 topics covering genuinely different dimensions of the question
- Each topic: short label, one-sentence description, 2-4 specific search queries
- Craft queries like a skilled researcher: specific, varied angles
- One topic should target contrarian/critical perspective
- One topic should seek primary/technical sources
- TOPICS: must appear on its own line at the very end
- The JSON array must be on the same line as TOPICS:"""


LIGHT_PLANNING_PROMPT = """\
You are a research planning engine. Think briefly about the question and \
decompose it into a few focused research topics.

CONSIDER THE PERSON:
- What are they actually trying to do or decide?
- What would they need to know that they wouldn't think to ask?

DECOMPOSE:
- Break the question into 2-4 distinct angles worth searching
- Cover the practical answer, not just definitions
- Include at least one angle the user probably hasn't considered

Keep your reasoning to 2-3 sentences — just enough to frame the question \
and explain your decomposition. Then the topics.

TOPICS: [{"label": "Short label", "description": "What to investigate", \
"queries": ["single best search query for this topic"]}, ...]

RULES:
- Write brief reasoning before the TOPICS directive
- 2-4 topics, each with exactly ONE search query — pick the single \
best query that will surface the most useful results for that angle
- Topics should cover different angles, not restatements
- TOPICS: must appear on its own line at the very end
- The JSON array must be on the same line as TOPICS:"""


LIGHT_ARTICULATION_PROMPT = """\
You are a research synthesizer. Turn research threads into a clear, useful \
answer for someone making decisions.

STRUCTURE
Open with the sharpest finding, not a definition or topic overview. If the \
research uncovered a surprising data point, a recent structural change, or \
a key tension — lead with that.

Develop unevenly. A thread with strong evidence gets a full paragraph. A \
thread with only general claims gets one sentence or nothing. Don't pad thin \
threads to match the length of strong ones.

Include the "yes, but." Even in a concise answer, name the primary \
counterargument or obstacle. One sentence of honest pushback is worth more \
than three paragraphs of one-sided advocacy.

Close forward. End with what the reader should do, watch for, or consider — \
not a restatement of what you already said.

EVIDENCE
Cite inline with [1], [2]. Prefer concrete: names, versions, dollar amounts, \
specific tools. "Bun cold-starts in ~40ms vs Node's ~150ms on Lambda [3]" \
beats "Bun has faster cold starts." If sources disagree, say so in one \
sentence. If evidence is thin on a point, say so rather than asserting \
confidently.

VOICE
Knowledgeable colleague, not textbook. Natural prose paragraphs — no bullet \
lists unless presenting 4+ genuinely parallel items. No filler ("It's \
important to note," "Let's dive in," "In conclusion"). Don't restate the \
question.

CALIBRATE LENGTH TO EVIDENCE
A few strong paragraphs beat a long answer that stretches thin research. If \
the threads only support 400 words of substantive content, write 400 words. \
Never pad.

DO NOT
Use numbered sections (### 1, ### 2, ### 3). Include a comparison table \
unless the question is explicitly comparative with 4+ data-rich dimensions. \
End with a summary that restates the opening. Give every topic equal space.

End with ## Sources as [n] Title — URL"""

LIGHT_CONFIG = {
    "planning_model": "gemini-3-flash-preview",
    "extraction_model": "gemini-3-flash-preview",
    "articulation_model": "gemini-3-flash-preview",
    "articulation_thinking": "medium",
    "articulation_prompt": LIGHT_ARTICULATION_PROMPT,
    "max_topics": 4,
    "max_topic_sources": 5,
    "max_spawned_topics": 0,
    "max_total_topics": 4,
    "max_iterations": 1,
    "research_budget": 0.05,
    "brave_results": 5,
    "jina_reads": 3,
    "max_knowledge_chars": 30_000,
    "compress_target_chars": 20_000,
    "extract_max_chars": 1200,
    "fetch_max_chars": 20_000,
}

EXTRACTION_PROMPT = """\
Extract the key information relevant to the query from this document. \
Be concise but preserve important facts, numbers, dates, and quotes. \
Focus on what directly addresses or illuminates the query. \
If nothing relevant, respond with "No relevant content." \
Do not add commentary — just extract."""

ARTICULATION_PROMPT = """\
You are a research synthesizer producing expert briefings from accumulated \
research threads.

THINK ABOUT THE PERSON
Consider intent, blind spots, and obstacles. What would they assume before \
reading this? What would change their thinking? If the research reveals the \
question is wrong or incomplete, say so and reframe.

STRUCTURE
Structure the answer as a narrative argument, not a reference document.

Open with the sharpest thing you found. A surprising data point, a structural \
shift, a quote that captures the whole thesis. Never open with a definition \
or by restating the question.

Develop unevenly. A thread with strong evidence and concrete examples deserves \
2-3 paragraphs. A thread with only general assertions deserves one sentence \
woven into another section, or nothing. Do not give every topic equal weight.

Integrate threads. Show causation between them — how the architecture enables \
the business model, how the obstacle explains the competitive landscape. The \
reader should feel the argument building.

Include the counterargument. What makes this harder than it sounds? Why hasn't \
the obvious conclusion already won? This is not a disclaimer — it's often the \
most valuable section. Develop it with the same rigor as the thesis.

Close with implications, not summary. What does this mean going forward? What \
should the reader do or watch for? Never restate what you already said. If the \
final paragraph could be deleted without losing information, rewrite it.

Use markdown headers sparingly — only at genuine topic shifts. Make them \
specific and descriptive ("The Monorepo Problem Nobody Warns You About") not \
generic ("Key Challenges"). Never number them.

EVIDENCE
Every factual claim gets an inline citation [n]. Concrete over abstract: name \
companies, cite dollar amounts, reference specific versions and tools. A claim \
with a named example is worth three without. When sources disagree, say so and \
explain why. When evidence is thin or single-sourced, say so. "Based on \
limited early data" is more credible than false confidence.

VOICE
Write as a knowledgeable colleague briefing someone smart. Natural prose \
paragraphs — no bullet-point lists in the body unless presenting genuinely \
parallel items (a set of 4+ tools or metrics). If you catch yourself writing \
bullets, convert to prose. Do not use filler phrases ("It's important to \
note," "Let's dive in," "In conclusion," "Here's what you need to know").

COMPARISON TABLES
Only include a table if the question is explicitly comparative AND the \
comparison has 4+ substantive dimensions. The table must contain specific \
data (versions, numbers, tool names) — not restatements of your prose. Never \
use a table to summarize your own argument.

CALIBRATE DEPTH TO EVIDENCE
If the research threads are thin, write a shorter, tighter answer. A 600-word \
answer that's honest about what's known is better than a 1500-word answer that \
pads thin research with generalities. Do not speculate to fill space.

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
# Topic evaluation model
# ---------------------------------------------------------------------------


class TopicEvaluation(BaseModel):
    """Evaluation of whether a topic needs more research."""
    should_continue: bool
    refined_queries: list[str]
    new_topic: dict | None = None


_eval_agent = Agent(
    system_prompt=(
        "You evaluate whether a research topic has been sufficiently covered. "
        "Given the topic description, what was searched, and what was found, "
        "decide:\n"
        "1. should_continue: true if more research would meaningfully improve "
        "the answer, false if diminishing returns\n"
        "2. refined_queries: if continuing, provide ALL NEW search queries "
        "targeting what's still missing (empty list if done)\n"
        "3. new_topic: if the research revealed an important new dimension "
        "not covered by any existing topic, return {\"label\": \"...\", "
        "\"description\": \"...\", \"queries\": [\"...\"]}. Otherwise null.\n\n"
        "Be conservative — only continue if there are clear gaps. Only spawn "
        "a new topic if it's genuinely a different angle, not a refinement "
        "of the current one."
    ),
    output_type=TopicEvaluation,
)


# ---------------------------------------------------------------------------
# Pipeline configuration
# ---------------------------------------------------------------------------

CONFIG = {
    "planning_model": "gemini-3-flash-preview",
    "extraction_model": "gemini-3-flash-preview",
    "articulation_model": "gemini-3-flash-preview",
    "articulation_thinking": "high",
    "max_topics": 8,
    "max_topic_sources": 10,
    "max_spawned_topics": 4,
    "max_total_topics": 10,
    "research_budget": 0.25,
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
    max_reads: int = 5,
) -> list[tuple[str, str]]:
    """Use flash-lite to filter search results for quality.

    Always runs the LLM filter to exclude low-quality results.
    Returns a list of (url, title) tuples for results worth reading.
    Falls back to first-5 on error.
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
            # Take only the top-ranked results
            top = filtered[:max_reads]
            logger.info(
                "Filter ranked %d/%d results, taking top %d for query %r",
                len(filtered), len(candidates), len(top), query,
            )
            return top

        logger.warning("Filter returned no valid indices, falling back to naive")

    except Exception:
        logger.exception("Filter agent failed, falling back to naive selection")

    # Fallback: first 5 candidates by rank order
    return [(url, title) for _, url, title, _ in candidates[:5]]


# ---------------------------------------------------------------------------
# Planning module (Phase 1)
# ---------------------------------------------------------------------------


def _parse_plan_result(text: str) -> list[TopicPlan]:
    """Parse accumulated planning text for TOPICS: [JSON] directive.

    Returns a list of TopicPlan objects, or empty list if parsing fails.
    Tries multiple strategies: direct regex, markdown code block extraction,
    and bare JSON array detection.
    """
    tail = text[-4000:] if len(text) > 4000 else text

    # Strategy 1: TOPICS: [...] on one or more lines
    match = re.search(r"TOPICS:\s*(\[.*\])", tail, re.DOTALL)

    # Strategy 2: TOPICS: followed by a markdown code block
    if not match:
        match = re.search(
            r"TOPICS:\s*```(?:json)?\s*(\[.*?\])\s*```", tail, re.DOTALL,
        )

    # Strategy 3: Just find the last JSON array in the text
    if not match:
        # Find all [...] blocks and try the last one
        arrays = list(re.finditer(r"\[[\s\S]*?\]", tail))
        for candidate in reversed(arrays):
            try:
                raw = json.loads(candidate.group(0))
                if (
                    isinstance(raw, list)
                    and raw
                    and isinstance(raw[0], dict)
                    and "label" in raw[0]
                ):
                    match = candidate
                    break
            except (json.JSONDecodeError, ValueError):
                continue

    if match:
        json_str = match.group(1) if match.lastindex else match.group(0)
        try:
            raw = json.loads(json_str)
            if isinstance(raw, list) and raw:
                topics = []
                for i, item in enumerate(raw):
                    if not isinstance(item, dict):
                        continue
                    topics.append(TopicPlan(
                        id=f"t{i + 1}",
                        label=item.get("label", f"Topic {i + 1}"),
                        description=item.get("description", ""),
                        queries=item.get("queries", []),
                    ))
                if topics:
                    return topics
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "TOPICS JSON parse failed. Matched text: %s",
                repr(json_str[:500]),
            )

    logger.warning(
        "No TOPICS directive found in planning output. Tail: %s",
        repr(tail[-500:]),
    )
    return []


async def _plan(
    full_query: str,
    raw_query: str,
    cfg: dict,
    event_queue: asyncio.Queue[PipelineEvent],
    budget: CostBudget,
    planning_counter: TokenCounter,
    planning_prompt: str = "",
) -> list[TopicPlan]:
    """Stream planning reasoning, return decomposed research topics.

    Args:
        full_query: Query with datetime preamble and conversation history
            (used as LLM context).
        raw_query: Original user question without preamble (used for
            fallback search queries).
        planning_prompt: Override for the system prompt (defaults to
            PLANNING_PROMPT).
    """
    client = genai_client()

    user_msg = (
        f"QUESTION: {full_query}\n\n"
        f"Analyze this question deeply, then decompose it into distinct "
        f"research topics that can be investigated concurrently."
    )

    response = await client.aio.models.generate_content_stream(
        model=cfg["planning_model"],
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=planning_prompt or PLANNING_PROMPT,
        ),
    )

    full_text = ""
    emitted_len = 0
    _HOLDBACK = 500  # Hold back tail to strip TOPICS: directive
    async for chunk in planning_counter.counted_stream(response):
        if chunk.text:
            full_text += chunk.text
            safe_len = max(0, len(full_text) - _HOLDBACK)
            if safe_len > emitted_len:
                await event_queue.put(DetailEvent(
                    type="reasoning",
                    payload={"text": full_text[emitted_len:safe_len]},
                ))
                emitted_len = safe_len

    # Flush remaining text with directive stripped
    remaining = full_text[emitted_len:]
    remaining = re.sub(r"\s*TOPICS:\s*\[.*\]\s*$", "", remaining, flags=re.DOTALL)
    # Also strip markdown code block variants
    remaining = re.sub(
        r"\s*TOPICS:\s*```(?:json)?\s*\[.*?\]\s*```\s*$", "",
        remaining, flags=re.DOTALL,
    )
    if remaining.strip():
        await event_queue.put(DetailEvent(
            type="reasoning",
            payload={"text": remaining},
        ))

    # Track budget
    budget.add(
        planning_counter.input_tokens,
        planning_counter.output_tokens,
        cfg["planning_model"],
    )

    topics = _parse_plan_result(full_text)

    # Fallback: single topic with the raw user query (not full_query
    # which includes datetime preamble and conversation history)
    if not topics:
        logger.warning("Planning fallback triggered — using raw query as single topic")
        topics = [TopicPlan(
            id="t1",
            label="General Research",
            description=raw_query,
            queries=[raw_query],
        )]

    # Cap at max_topics
    topics = topics[:cfg["max_topics"]]

    logger.info(
        "Planning produced %d topics: %s",
        len(topics),
        [t.label for t in topics],
    )
    return topics


# ---------------------------------------------------------------------------
# Topic evaluation (between iterations)
# ---------------------------------------------------------------------------


async def _evaluate_topic_progress(
    topic: TopicPlan,
    topic_entries: list[KnowledgeEntry],
    queries_used: list[str],
    extraction_counter: TokenCounter,
    budget: CostBudget,
) -> TopicEvaluation:
    """Evaluate whether a topic needs more research iterations.

    Receives the full extracted entries (up to 1200 chars each) so the
    evaluator can make an informed decision about coverage gaps.
    """
    prompt = (
        f"Topic: {topic.label}\n"
        f"Description: {topic.description}\n\n"
        f"Queries searched: {json.dumps(queries_used)}\n\n"
        f"Sources found ({len(topic_entries)}):\n\n"
    )
    for i, e in enumerate(topic_entries, 1):
        prompt += f"[{i}] {e.title} ({e.url})\n{e.key_points}\n\n"

    prompt += (
        "Based on the topic description and the sources gathered above, "
        "are there clear gaps in coverage? What specific angles, claims, "
        "or perspectives are missing that new searches could fill?"
    )

    try:
        result = await _eval_agent.run(prompt, model=gemini_flash_lite())
        usage = result.usage()
        extraction_counter.input_tokens += usage.request_tokens or 0
        extraction_counter.output_tokens += usage.response_tokens or 0
        budget.add(
            usage.request_tokens or 0,
            usage.response_tokens or 0,
            "gemini-2.5-flash-lite",
        )
        return result.output
    except Exception:
        logger.exception("Topic evaluation failed for %s", topic.label)
        return TopicEvaluation(
            should_continue=False,
            refined_queries=[],
            new_topic=None,
        )


# ---------------------------------------------------------------------------
# Per-topic research coroutine (Phase 2)
# ---------------------------------------------------------------------------


async def _search_and_extract_query(
    query_text: str,
    topic: TopicPlan,
    knowledge: KnowledgeState,
    brave_api_key: str,
    already_fetched: set[str],
    cfg: dict,
    event_queue: asyncio.Queue[PipelineEvent],
    extraction_counter: TokenCounter,
    budget: CostBudget,
    topic_entries: list[KnowledgeEntry],
    redis_url: str = "",
    db_session=None,
) -> None:
    """Run a single search query: brave → filter → fetch → extract.

    Appends new KnowledgeEntry objects to both ``knowledge`` (shared)
    and ``topic_entries`` (per-topic tracking for the evaluator).
    """
    client = genai_client()

    # Emit research group
    await event_queue.put(DetailEvent(
        type="research",
        payload={"topic": topic.label},
    ))

    # --- Brave search ---
    await event_queue.put(DetailEvent(
        type="search",
        payload={"topic": topic.label, "query": query_text},
    ))

    try:
        results = await _brave_search(
            query_text, brave_api_key, count=cfg["brave_results"],
        )
        await event_queue.put(DetailEvent(
            type="search_done",
            payload={
                "topic": topic.label,
                "query": query_text,
                "num_results": len(results),
            },
        ))
    except Exception:
        await event_queue.put(DetailEvent(
            type="search_done",
            payload={
                "topic": topic.label,
                "query": query_text,
                "num_results": 0,
            },
        ))
        await event_queue.put(DetailEvent(
            type="result",
            payload={
                "topic": topic.label,
                "urls": [],
                "num_sources": 0,
            },
        ))
        return

    # --- Pick top URLs via LLM filtering ---
    urls_to_fetch = await _filter_results(
        results, query_text, knowledge, already_fetched,
        extraction_counter, max_reads=cfg["jina_reads"],
    )

    # --- Filter by robots.txt ---
    urls_allowed: list[tuple[str, str]] = []
    for url, title in urls_to_fetch:
        if db_session is not None:
            try:
                allowed, _ = await check_url_allowed(url, db_session)
            except Exception:
                allowed = True
            if not allowed:
                logger.info("Blocked by robots.txt: %s", url)
                await event_queue.put(DetailEvent(
                    type="fetch_done",
                    payload={
                        "topic": topic.label,
                        "url": url,
                        "failed": True,
                    },
                ))
                continue
        urls_allowed.append((url, title))

    # Emit fetch start events
    for url, _title in urls_allowed:
        await event_queue.put(DetailEvent(
            type="fetch",
            payload={"topic": topic.label, "url": url},
        ))

    # --- Fetch in parallel via Jina (rate-limited) ---
    fetch_results = await asyncio.gather(
        *[
            _jina_fetch(url, redis_url=redis_url)
            for url, _ in urls_allowed
        ],
        return_exceptions=True,
    )

    # --- Extract knowledge from each document ---
    source_urls: list[str] = []
    for (url, title), content in zip(urls_allowed, fetch_results):
        if isinstance(content, Exception):
            content = None

        if not content:
            await event_queue.put(DetailEvent(
                type="fetch_done",
                payload={
                    "topic": topic.label,
                    "url": url,
                    "failed": True,
                },
            ))
            continue

        already_fetched.add(url)
        truncated = content[: cfg["fetch_max_chars"]]

        extraction_prompt = (
            f"Query: {query_text}\n\n"
            f"Document from {title} ({url}):\n{truncated}"
        )

        try:
            extract_resp = await client.aio.models.generate_content(
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

            # Track extraction cost in budget
            ext_meta = getattr(extract_resp, "usage_metadata", None)
            if ext_meta:
                budget.add(
                    ext_meta.prompt_token_count or 0,
                    ext_meta.candidates_token_count or 0,
                    cfg["extraction_model"],
                )

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
                    topic=topic.label,
                )
                knowledge.add(entry)
                topic_entries.append(entry)

                usage_dict = None
                if ext_meta:
                    usage_dict = calc_usage_cost(
                        ext_meta.prompt_token_count or 0,
                        ext_meta.candidates_token_count or 0,
                        cfg["extraction_model"],
                    )

                await event_queue.put(DetailEvent(
                    type="fetch_done",
                    payload={
                        "topic": topic.label,
                        "url": url,
                        "content": extracted[:3000],
                        **({"usage": usage_dict} if usage_dict else {}),
                    },
                ))
            else:
                await event_queue.put(DetailEvent(
                    type="fetch_done",
                    payload={
                        "topic": topic.label,
                        "url": url,
                        "content": "(no relevant content)",
                    },
                ))

            source_urls.append(url)

        except Exception:
            logger.exception("Extraction failed for %s", url)
            await event_queue.put(DetailEvent(
                type="fetch_done",
                payload={
                    "topic": topic.label,
                    "url": url,
                    "failed": True,
                },
            ))

    # Collapse research group for this query
    await event_queue.put(DetailEvent(
        type="result",
        payload={
            "topic": topic.label,
            "urls": source_urls,
            "num_sources": len(source_urls),
        },
    ))


async def _research_topic(
    topic: TopicPlan,
    knowledge: KnowledgeState,
    brave_api_key: str,
    already_fetched: set[str],
    queries_searched: set[str],
    cfg: dict,
    event_queue: asyncio.Queue[PipelineEvent],
    extraction_counter: TokenCounter,
    budget: CostBudget,
    redis_url: str = "",
    db_session=None,
) -> TopicResult:
    """Research a single topic until source cap or budget exhausted.

    Runs all initial queries, then asks the evaluator (with full
    extracted content) whether to continue. Loops until one of:
    - max_topic_sources reached
    - budget exhausted
    - evaluator says stop
    - no more queries to run

    ``queries_searched`` is shared across all topics to prevent
    duplicate searches (safe in single-threaded asyncio).
    """
    max_sources = cfg["max_topic_sources"]
    result = TopicResult(topic_id=topic.id, entries_added=0)
    queries = list(topic.queries)
    all_queries_used: list[str] = []
    topic_entries: list[KnowledgeEntry] = []

    while queries and len(topic_entries) < max_sources and not budget.exhausted:
        # Run all current queries, skipping duplicates
        for query_text in queries:
            if budget.exhausted or len(topic_entries) >= max_sources:
                break

            # Normalize and deduplicate
            q_key = query_text.strip().lower()
            if q_key in queries_searched:
                logger.info("Skipping duplicate query: %r", query_text)
                continue
            queries_searched.add(q_key)

            all_queries_used.append(query_text)
            await _search_and_extract_query(
                query_text, topic, knowledge, brave_api_key,
                already_fetched, cfg, event_queue, extraction_counter,
                budget, topic_entries,
                redis_url=redis_url, db_session=db_session,
            )

        result.entries_added = len(topic_entries)
        result.iterations_used += 1

        # Stop conditions before evaluation
        max_iters = cfg.get("max_iterations", 0)
        if max_iters and result.iterations_used >= max_iters:
            break
        if budget.exhausted or len(topic_entries) >= max_sources:
            break

        # --- Evaluate with full entries ---
        evaluation = await _evaluate_topic_progress(
            topic, topic_entries, all_queries_used,
            extraction_counter, budget,
        )

        if not evaluation.should_continue:
            break

        queries = evaluation.refined_queries if evaluation.refined_queries else []

        # Check for spawned topic
        if evaluation.new_topic and isinstance(evaluation.new_topic, dict):
            spawned_id = f"{topic.id}s{len(result.spawned) + 1}"
            spawned = TopicPlan(
                id=spawned_id,
                label=evaluation.new_topic.get("label", "Follow-up"),
                description=evaluation.new_topic.get("description", ""),
                queries=evaluation.new_topic.get("queries", []),
                generation=1,
            )
            result.spawned.append(spawned)

    logger.info(
        "Topic %r finished: %d sources, %d iterations",
        topic.label, len(topic_entries), result.iterations_used,
    )
    return result


# ---------------------------------------------------------------------------
# Articulation module (Phase 3)
# ---------------------------------------------------------------------------


async def _articulate(
    query: str,
    knowledge: KnowledgeState,
    cfg: dict,
    event_queue: asyncio.Queue[PipelineEvent],
    counter: TokenCounter,
) -> None:
    """Stream the final cited response."""
    client = genai_client()

    user_msg = (
        f"QUESTION: {query}\n\n"
        f"RESEARCH THREADS:\n{knowledge.format_by_thread()}\n\n"
        f"SOURCES:\n{knowledge.format_source_list()}\n\n"
        f"Write the answer. Cite inline with [n]."
    )

    thinking_level = (
        ThinkingLevel.HIGH if cfg["articulation_thinking"] == "high"
        else ThinkingLevel.MEDIUM
    )

    response = await client.aio.models.generate_content_stream(
        model=cfg["articulation_model"],
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=cfg.get("articulation_prompt", ARTICULATION_PROMPT),
            thinking_config=ThinkingConfig(thinking_level=thinking_level),
        ),
    )

    async for chunk in counter.counted_stream(response):
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
    """Compress knowledge entries oldest-first to reduce context size."""
    client = genai_client()

    idx = 0
    while knowledge.total_chars > target_chars and idx < len(knowledge.entries):
        entry = knowledge.entries[idx]

        compress_prompt = (
            f"Compress this extracted knowledge to roughly half its length, "
            f"preserving the most important facts:\n\n{entry.key_points}"
        )

        try:
            response = await client.aio.models.generate_content(
                model=extraction_model,
                contents=compress_prompt,
                config=GenerateContentConfig(
                    thinking_config=ThinkingConfig(
                        thinking_level=ThinkingLevel.MINIMAL,
                    ),
                ),
            )
            counter.add_from_response(response)

            compressed = response.text or entry.key_points
            old_chars = entry.char_count
            new_chars = len(compressed)

            entry.key_points = compressed
            entry.char_count = new_chars
            knowledge.total_chars += new_chars - old_chars

            # Move to next entry when compression isn't shrinking much
            if new_chars > old_chars * 0.8:
                idx += 1
        except Exception:
            idx += 1


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
    conversation_history: list[dict] | None = None,
    config_override: dict | None = None,
    planning_prompt_override: str | None = None,
) -> AsyncGenerator[PipelineEvent, None]:
    """Run the deep research pipeline, yielding SSE-compatible events.

    This is the main entry point, matching the same generator pattern
    as ``run_research_pipeline`` in scan_agent.py.

    Pass ``config_override`` and ``planning_prompt_override`` to run a
    lighter variant (e.g. the basic researcher uses LIGHT_CONFIG).
    """
    cfg = config_override or CONFIG

    # Build timezone-aware date preamble
    try:
        tz = ZoneInfo(user_timezone) if user_timezone else timezone.utc
    except (KeyError, ValueError):
        tz = timezone.utc
    now = datetime.now(tz)
    full_query = (
        f"Current date/time: {now.strftime('%A, %B %d, %Y %H:%M')} "
        f"({user_timezone or 'UTC'})\n\n{query}"
    )

    # Prepend conversation history for multi-turn context
    if conversation_history:
        parts = ["Previous conversation:"]
        for msg in conversation_history:
            role = "User" if msg["role"] == "user" else "Assistant"
            parts.append(f"{role}: {msg['content']}")
        full_query = "\n".join(parts) + "\n\n" + full_query

    event_queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()
    knowledge = KnowledgeState()
    already_fetched: set[str] = set()
    queries_searched: set[str] = set()
    budget = CostBudget(limit=cfg["research_budget"])

    planning_counter = TokenCounter()
    extraction_counter = TokenCounter()
    articulation_counter = TokenCounter()

    async def _run() -> None:
        try:
            # --- Phase 1: PLAN ---
            await event_queue.put(StageEvent(stage="reasoning"))

            topics = await _plan(
                full_query, query, cfg, event_queue, budget,
                planning_counter,
                planning_prompt=planning_prompt_override or "",
            )

            # --- Phase 2: RESEARCH (wave 1 — all topics concurrent) ---
            await event_queue.put(StageEvent(stage="researching"))

            wave1_results = await asyncio.gather(
                *[
                    _research_topic(
                        topic, knowledge, brave_api_key, already_fetched,
                        queries_searched, cfg, event_queue,
                        extraction_counter, budget,
                        redis_url=redis_url, db_session=db_session,
                    )
                    for topic in topics
                ],
                return_exceptions=True,
            )

            # Collect spawned topics from wave 1
            spawned: list[TopicPlan] = []
            total_topics = len(topics)
            for r in wave1_results:
                if isinstance(r, TopicResult) and r.spawned:
                    for s in r.spawned:
                        if (
                            len(spawned) < cfg["max_spawned_topics"]
                            and total_topics + len(spawned) < cfg["max_total_topics"]
                        ):
                            spawned.append(s)
                elif isinstance(r, BaseException):
                    logger.error("Topic research failed: %s", r)

            # Wave 2: spawned topics (if budget allows)
            if spawned and not budget.exhausted:
                logger.info(
                    "Launching wave 2 with %d spawned topics: %s",
                    len(spawned),
                    [s.label for s in spawned],
                )
                await asyncio.gather(
                    *[
                        _research_topic(
                            topic, knowledge, brave_api_key, already_fetched,
                            queries_searched, cfg, event_queue,
                            extraction_counter, budget,
                            redis_url=redis_url, db_session=db_session,
                        )
                        for topic in spawned
                    ],
                    return_exceptions=True,
                )

            # --- Compress if needed ---
            if knowledge.needs_compression(cfg["max_knowledge_chars"]):
                await _compress_knowledge(
                    knowledge,
                    cfg["compress_target_chars"],
                    cfg["extraction_model"],
                    extraction_counter,
                )

            # --- Phase 3: ARTICULATE (always runs regardless of budget) ---
            await _articulate(
                full_query, knowledge, cfg, event_queue, articulation_counter,
            )

            # --- USAGE ---
            planning_cost = calc_usage_cost(
                planning_counter.input_tokens,
                planning_counter.output_tokens,
                cfg["planning_model"],
            )
            extraction_cost = calc_usage_cost(
                extraction_counter.input_tokens,
                extraction_counter.output_tokens,
                cfg["extraction_model"],
            )

            research_in = (
                planning_counter.input_tokens + extraction_counter.input_tokens
            )
            research_out = (
                planning_counter.output_tokens + extraction_counter.output_tokens
            )
            research_input_cost = (
                float(planning_cost["input_cost"])
                + float(extraction_cost["input_cost"])
            )
            research_output_cost = (
                float(planning_cost["output_cost"])
                + float(extraction_cost["output_cost"])
            )

            articulation_cost = calc_usage_cost(
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
                    "budget": {
                        "limit": budget.limit,
                        "spent": round(budget.spent, 4),
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
