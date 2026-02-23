"""Deep research agent with iterative plan/search/evaluate/synthesize loop.

Uses the google-genai SDK directly with Gemini models, Brave Search API,
and Jina Reader for content fetching. Emits SSE-compatible events that
match the existing research pipeline UI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from google import genai
from google.genai.types import GenerateContentConfig, ThinkingConfig
from pydantic import BaseModel, Field

from controllers.robots import USER_AGENT

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Google genai client
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _genai_client() -> genai.Client:
    return genai.Client(api_key=os.environ["GOOGLE_API_KEY"])


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class SubQuery(BaseModel):
    query: str = Field(description="Search string, 3-8 words")
    intent: str = Field(description="Why this matters for the question")
    priority: int = Field(ge=1, le=3, description="1=essential, 3=nice-to-have")


class ResearchPlan(BaseModel):
    reasoning: str
    sub_queries: list[SubQuery]


class Source(BaseModel):
    id: str
    url: str
    title: str
    content: str
    retrieval_query: str
    relevance_score: float = 0.0


class Finding(BaseModel):
    claim: str = Field(description="Atomic factual statement")
    source_ids: list[str]
    confidence: float = Field(ge=0, le=1)


class Gap(BaseModel):
    description: str
    suggested_query: str


class Contradiction(BaseModel):
    claim_a: str
    claim_b: str
    source_ids: list[str]


class Evaluation(BaseModel):
    findings: list[Finding]
    sufficient: bool
    confidence: float = Field(ge=0, le=1)
    gaps: list[Gap]
    contradictions: list[Contradiction]


class ResearchState(BaseModel):
    original_query: str
    iteration: int = 0
    max_iterations: int = 3
    plan: ResearchPlan | None = None
    sources: dict[str, Source] = {}
    findings: list[Finding] = []
    gaps: list[Gap] = []
    confidence: float = 0.0
    token_budget: int = 100_000
    tokens_used: int = 0


# ---------------------------------------------------------------------------
# Pipeline event types (compatible with existing frontend)
# ---------------------------------------------------------------------------

StageName = Literal["planning", "researching", "evaluating", "responding"]


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
# System prompts
# ---------------------------------------------------------------------------

PLANNER_PROMPT = """\
You are a research planner. Given a question, produce a search plan —
sub-queries that will fully address it.

RULES:
- 3-7 sub-queries per plan
- Each sub-query: concise search string (3-8 words), not a full question
- Consider these angles:
    Definitional | Comparative | Temporal | Causal | Practical | Contrarian
- Priority: 1=essential, 2=adds depth, 3=nice-to-have

ON FOLLOW-UP ITERATIONS:
You'll receive previous findings and identified gaps.
Generate ONLY new sub-queries addressing the gaps.
Do not repeat previous searches."""

EVALUATOR_PROMPT = """\
You are a research evaluator. Given a question and search results,
extract findings and assess whether the question can be answered well.

TASKS:
1. EXTRACT FINDINGS — atomic claims, tagged with source IDs, confidence scored
2. ASSESS SUFFICIENCY — all angles covered? contradictions resolved? claims supported?
3. IDENTIFY GAPS — what's missing, with suggested search queries
4. FLAG CONTRADICTIONS — where sources disagree

RULES:
- Only extract what's in the sources. Never invent.
- "Sufficient" means genuinely sufficient, not "we found something."
- Contradictions are gaps that need resolution."""

SYNTHESIZER_PROMPT = """\
You are a research synthesizer. Write a clear, well-cited answer
from verified findings.

RULES:
- Lead with the direct answer
- Natural prose, not bullet lists
- Integrate across sources — don't summarize each sequentially
- Present both sides when sources disagree
- Inline citations: [1], [2] keyed to source list
- Every factual claim gets a citation
- End with ## Sources as [n] Title — URL
- State uncertainty when confidence is low

AVOID:
- "It's important to note..."
- "In conclusion..."
- Filler, hedging, repeating the question"""

# ---------------------------------------------------------------------------
# Mode configurations
# ---------------------------------------------------------------------------

MODES = {
    "fast": {
        "max_iter": 1,
        "synth_model": "gemini-3-flash-preview",
        "synth_thinking": "medium",
        "token_budget": 50_000,
        "confidence_threshold": 0.6,
        "brave_results": 3,
        "jina_reads": 2,
        "chunk_max_chars": 4000,
    },
    "standard": {
        "max_iter": 3,
        "synth_model": "gemini-3-flash-preview",
        "synth_thinking": "high",
        "token_budget": 100_000,
        "confidence_threshold": 0.75,
        "brave_results": 5,
        "jina_reads": 3,
        "chunk_max_chars": 8000,
    },
    "deep": {
        "max_iter": 5,
        "synth_model": "gemini-3-pro-preview",
        "synth_thinking": "high",
        "token_budget": 200_000,
        "confidence_threshold": 0.85,
        "brave_results": 7,
        "jina_reads": 5,
        "chunk_max_chars": 12000,
    },
}

# ---------------------------------------------------------------------------
# Searcher — Brave Search + Jina Reader (no LLM)
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


async def _jina_fetch(url: str) -> str | None:
    """Fetch a URL's content as markdown via Jina Reader."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://r.jina.ai/{url}",
                headers={
                    "Accept": "text/markdown",
                    "User-Agent": USER_AGENT,
                },
                timeout=15.0,
            )
            if resp.status_code == 200:
                return resp.text
            return None
    except Exception:
        return None


async def _search_and_fetch(
    sub_query: SubQuery,
    brave_api_key: str,
    already_fetched: set[str],
    brave_count: int,
    jina_reads: int,
    chunk_max_chars: int,
    event_queue: asyncio.Queue[PipelineEvent],
    db_session=None,
    redis_url: str = "",
) -> list[Source]:
    """Execute a single sub-query: Brave search → Jina fetch → Sources."""
    topic = sub_query.query

    # Emit search event
    await event_queue.put(DetailEvent(
        type="search",
        payload={"topic": topic, "query": topic},
    ))

    try:
        results = await _brave_search(topic, brave_api_key, count=brave_count)
        await event_queue.put(DetailEvent(
            type="search_done",
            payload={"topic": topic, "query": topic, "num_results": len(results)},
        ))
    except Exception:
        await event_queue.put(DetailEvent(
            type="search_done",
            payload={"topic": topic, "query": topic, "num_results": 0},
        ))
        return []

    # Pick top URLs not yet fetched
    urls_to_fetch: list[tuple[str, str]] = []
    for r in results:
        url = r.get("url", "")
        title = r.get("title", "")
        if not url or url in already_fetched:
            continue
        host = urlparse(url).hostname or ""
        if host in _SKIP_DOMAINS:
            continue
        urls_to_fetch.append((url, title))
        if len(urls_to_fetch) >= jina_reads:
            break

    # Fetch pages via Jina in parallel
    sources: list[Source] = []
    for url, title in urls_to_fetch:
        await event_queue.put(DetailEvent(
            type="fetch",
            payload={"topic": topic, "url": url},
        ))

        content = await _jina_fetch(url)
        if content:
            already_fetched.add(url)
            truncated = content[:chunk_max_chars]
            source = Source(
                id=str(uuid4())[:8],
                url=url,
                title=title,
                content=truncated,
                retrieval_query=topic,
            )
            sources.append(source)
            await event_queue.put(DetailEvent(
                type="fetch_done",
                payload={
                    "topic": topic,
                    "url": url,
                    "content": truncated[:3000],
                },
            ))
        else:
            await event_queue.put(DetailEvent(
                type="fetch_done",
                payload={"topic": topic, "url": url, "failed": True},
            ))

    return sources


# ---------------------------------------------------------------------------
# LLM steps: Plan, Evaluate, Synthesize
# ---------------------------------------------------------------------------


async def _plan(
    state: ResearchState,
    event_queue: asyncio.Queue[PipelineEvent],
) -> ResearchPlan:
    """Generate a research plan from the query (or gaps)."""
    client = _genai_client()

    user_msg = f"Research question: {state.original_query}"

    if state.iteration > 1 and state.gaps:
        findings_str = "\n".join(
            f"- [{f.confidence:.0%}] {f.claim}" for f in state.findings
        )
        gaps_str = "\n".join(
            f"- {g.description} (try: {g.suggested_query})" for g in state.gaps
        )
        user_msg += (
            f"\n\nPREVIOUS FINDINGS:\n{findings_str}"
            f"\n\nIDENTIFIED GAPS:\n{gaps_str}"
            f"\n\nNew sub-queries for gaps only."
        )

    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=PLANNER_PROMPT,
            thinking_config=ThinkingConfig(thinking_budget=1024),
            response_mime_type="application/json",
            response_schema=ResearchPlan,
        ),
    )

    if response.usage_metadata:
        state.tokens_used += response.usage_metadata.total_token_count or 0

    return ResearchPlan.model_validate_json(response.text)


async def _evaluate(
    state: ResearchState,
    event_queue: asyncio.Queue[PipelineEvent],
) -> Evaluation:
    """Evaluate collected sources against the original question."""
    client = _genai_client()

    sources_str = "\n---\n".join(
        f"[Source: {s.id}] {s.title}\nURL: {s.url}\n"
        f"Query: \"{s.retrieval_query}\"\n\n{s.content}"
        for s in state.sources.values()
    )
    user_msg = (
        f"ORIGINAL QUESTION: {state.original_query}\n\n"
        f"SEARCH RESULTS:\n---\n{sources_str}\n---\n\n"
        f"Evaluate. Extract findings, assess sufficiency, identify gaps."
    )

    response = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=EVALUATOR_PROMPT,
            thinking_config=ThinkingConfig(thinking_budget=4096),
            response_mime_type="application/json",
            response_schema=Evaluation,
        ),
    )

    if response.usage_metadata:
        state.tokens_used += response.usage_metadata.total_token_count or 0

    return Evaluation.model_validate_json(response.text)


async def _synthesize_stream(
    state: ResearchState,
    cfg: dict,
    event_queue: asyncio.Queue[PipelineEvent],
) -> None:
    """Stream the final synthesis response."""
    client = _genai_client()

    # Build numbered source list for citation
    source_list = list(state.sources.values())
    source_map = {s.id: i + 1 for i, s in enumerate(source_list)}

    findings_str = "\n".join(
        f"- [{f.confidence:.0%}] {f.claim} (sources: {', '.join(f'[{source_map.get(sid, sid)}]' for sid in f.source_ids)})"
        for f in state.findings
    )
    sources_str = "\n".join(
        f"[{i + 1}] {s.title} — {s.url}" for i, s in enumerate(source_list)
    )
    user_msg = (
        f"QUESTION: {state.original_query}\n\n"
        f"FINDINGS:\n{findings_str}\n\n"
        f"SOURCES:\n{sources_str}\n\n"
        f"CONFIDENCE: {state.confidence:.0%}\n\n"
        f"Write the answer. Cite inline with [n]."
    )

    thinking_budget = 8192 if cfg["synth_thinking"] == "high" else 4096

    response = client.models.generate_content_stream(
        model=cfg["synth_model"],
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=SYNTHESIZER_PROMPT,
            thinking_config=ThinkingConfig(thinking_budget=thinking_budget),
        ),
    )

    for chunk in response:
        if chunk.text:
            await event_queue.put(TextEvent(text=chunk.text))


# ---------------------------------------------------------------------------
# Budget filter
# ---------------------------------------------------------------------------


def _budget_filter(
    sub_queries: list[SubQuery],
    state: ResearchState,
) -> list[SubQuery]:
    """Filter sub-queries based on remaining token budget."""
    remaining = 1 - (state.tokens_used / state.token_budget)
    if remaining < 0.3:
        return [sq for sq in sub_queries if sq.priority == 1]
    elif remaining < 0.6:
        return [sq for sq in sub_queries if sq.priority <= 2]
    return sub_queries


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------


async def run_deep_research_pipeline(
    query: str,
    brave_api_key: str,
    *,
    db_session=None,
    redis_url: str = "",
    research_mode: str = "standard",
    user_timezone: str = "",
) -> AsyncGenerator[PipelineEvent, None]:
    """Run the deep research pipeline, yielding SSE-compatible events.

    This is the main entry point, matching the same generator pattern
    as ``run_research_pipeline`` in scan_agent.py.
    """
    cfg = MODES.get(research_mode, MODES["standard"])

    state = ResearchState(
        original_query=query,
        max_iterations=cfg["max_iter"],
        token_budget=cfg["token_budget"],
    )

    event_queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()
    already_fetched: set[str] = set()
    planner_tokens = 0
    evaluator_tokens = 0

    async def _run() -> None:
        nonlocal planner_tokens, evaluator_tokens

        try:
            while state.iteration < state.max_iterations:
                state.iteration += 1

                # --- PLAN ---
                await event_queue.put(StageEvent(stage="planning"))
                await event_queue.put(DetailEvent(
                    type="iteration",
                    payload={
                        "iteration": state.iteration,
                        "max_iterations": state.max_iterations,
                    },
                ))

                tokens_before = state.tokens_used
                plan = await _plan(state, event_queue)
                planner_tokens += state.tokens_used - tokens_before
                state.plan = plan

                queries = _budget_filter(plan.sub_queries, state)

                # Emit the plan as a research group with sub-queries
                for sq in queries:
                    await event_queue.put(DetailEvent(
                        type="research",
                        payload={"topic": sq.query},
                    ))

                # --- SEARCH ---
                await event_queue.put(StageEvent(stage="researching"))

                for sq in queries:
                    sources = await _search_and_fetch(
                        sq,
                        brave_api_key,
                        already_fetched,
                        brave_count=cfg["brave_results"],
                        jina_reads=cfg["jina_reads"],
                        chunk_max_chars=cfg["chunk_max_chars"],
                        event_queue=event_queue,
                        db_session=db_session,
                        redis_url=redis_url,
                    )
                    for s in sources:
                        state.sources[s.id] = s

                    # Collapse this research group
                    await event_queue.put(DetailEvent(
                        type="result",
                        payload={
                            "topic": sq.query,
                            "urls": [s.url for s in sources],
                            "num_sources": len(sources),
                        },
                    ))

                # --- EVALUATE ---
                if not state.sources:
                    break

                await event_queue.put(StageEvent(stage="evaluating"))

                tokens_before = state.tokens_used
                evaluation = await _evaluate(state, event_queue)
                evaluator_tokens += state.tokens_used - tokens_before

                state.findings = evaluation.findings
                state.gaps = evaluation.gaps
                state.confidence = evaluation.confidence

                await event_queue.put(DetailEvent(
                    type="evaluation",
                    payload={
                        "confidence": evaluation.confidence,
                        "num_findings": len(evaluation.findings),
                        "num_gaps": len(evaluation.gaps),
                        "num_contradictions": len(evaluation.contradictions),
                        "sufficient": evaluation.sufficient,
                    },
                ))

                # Check stopping conditions
                if evaluation.sufficient or evaluation.confidence >= cfg["confidence_threshold"]:
                    break
                if not evaluation.gaps:
                    break
                if state.tokens_used >= state.token_budget * 0.8:
                    break

            # --- SYNTHESIZE ---
            await _synthesize_stream(state, cfg, event_queue)

            # --- USAGE ---
            await event_queue.put(DetailEvent(
                type="usage",
                payload={
                    "research": {
                        "input_tokens": planner_tokens,
                        "output_tokens": 0,
                        "input_cost": "0.0000",
                        "output_cost": "0.0000",
                    },
                    "extraction": {
                        "input_tokens": evaluator_tokens,
                        "output_tokens": 0,
                        "input_cost": "0.0000",
                        "output_cost": "0.0000",
                    },
                    "total": {
                        "input_tokens": state.tokens_used,
                        "output_tokens": 0,
                        "input_cost": "0.0000",
                        "output_cost": "0.0000",
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
