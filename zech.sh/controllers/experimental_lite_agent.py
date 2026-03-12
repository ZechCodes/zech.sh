"""Experimental lite research pipeline for scan.zech.sh.

Two-agent architecture:
  1. Researcher agent — curates 10 quality sources with direct search,
     verify, and read tools. Returns a structured ResearchPlan.
  2. Writer agent — receives the plan + full source content, streams
     a cited answer token-by-token.

This is isolated from the stable research_agent.py for experimentation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from pydantic_ai import Agent, ModelMessagesTypeAdapter, RunContext
from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ThinkingPartDelta,
)

from controllers.deep_research_agent import (
    CostBudget,
    DetailEvent,
    Dispatch,
    DoneEvent,
    ErrorEvent,
    PipelineEvent,
    StageEvent,
    TextEvent,
    TokenCounter,
    _brave_search,
    _jina_fetch,
)
from controllers.llm import calc_usage_cost, gemini_flash, gemini_flash_lite
from controllers.research_agent import _compact_history
from controllers.robots import check_url_allowed

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


class SourceEntry(BaseModel):
    url: str
    title: str


class SupportingSource(BaseModel):
    url: str
    title: str
    summarization_plan: str
    """What information to extract and preserve from this source."""


class ResearchPlan(BaseModel):
    primary_sources: list[SourceEntry]
    """3 key sources that will be read in full by the writer."""
    supporting_sources: list[SupportingSource]
    """2–7 additional sources that will be summarized before the writer sees them."""
    writing_plan: str


@dataclass
class ExpLiteDeps:
    dispatch: Dispatch
    budget: CostBudget
    brave_api_key: str
    redis_url: str
    db_session: object | None
    jina_cache: dict[str, str] = field(default_factory=dict)
    read_calls: int = 0
    max_read_calls: int = 3
    search_calls: int = 0
    max_search_calls: int = 5
    emitted_researching: bool = False
    current_topic: str = ""  # Active research group topic for frontend


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

_RESEARCHER_SYSTEM_PROMPT = """\
You are a research curator. Your job is to find high-quality, readable \
web sources for a user's query and produce a detailed writing plan.

## First search: fill knowledge gaps

Your training data has a cutoff. **Always start** with a search designed \
to surface recent news, updates, or changes related to the query since \
your knowledge cutoff. This ensures the answer reflects the current state \
of the world, not stale information.

## Search query style

Do NOT add dates or years to your search queries unless the user \
specifically asked about a time period or the topic genuinely requires \
recent information. Most queries are better served by general searches \
that let the search engine rank by relevance.

## Source quality hierarchy (strictly prefer higher tiers)

1. **Primary / official** — documentation, specs, official blogs, \
government/org publications, peer-reviewed papers
2. **News / industry** — established journalism, trade publications, \
well-sourced analysis pieces
3. **Community Q&A** — Stack Overflow, Quora, HackerNews (only when \
they contain substantive technical answers, not opinions)
4. **Avoid** — Reddit threads, listicles, social media, anecdotal blog \
posts, SEO content farms, affiliate roundups

Always prefer a tier-1 or tier-2 source over a tier-3 or tier-4 source, \
even if the lower-tier result appears more directly relevant at first glance.

## Workflow

1. **Search** — use `brave_search` to find candidates. You have a \
maximum of 5 searches, so make each query count. Start with a \
recency-focused search, then cover key angles with the remaining queries.
2. **Verify** — use `verify_readable` on promising URLs to confirm they \
are accessible. This is cheap — verify liberally.
3. **Read** (optional, max 3) — use `read` to see the full content of a \
source when you need deeper understanding to judge its quality or to plan \
the writing structure. Use this sparingly.
4. **Return** — output a `ResearchPlan` with your sources split into \
primary and supporting, plus a detailed writing plan.

## Source selection

You must select two tiers of sources:

- **Primary sources (exactly 3)** — the most important, highest-quality \
sources for answering the query. These will be read in full by the writer.
- **Supporting sources (2–7)** — additional sources that provide context, \
corroboration, or supplementary detail. These will be summarized before \
the writer sees them.

For each **supporting source**, write a `summarization_plan` — a clear \
instruction for a summarizer that tells it exactly what information to \
extract and preserve from the source. Be specific: which claims, data \
points, quotes, or context are relevant to the writing plan.

## Writing plan guidelines

The writing plan should be detailed enough for a separate writer to produce \
a complete answer without doing any additional research. Include:
- The key points / claims to cover, in logical order
- Which sources support each point (reference by URL)
- The narrative structure: what to lead with, how to build the argument, \
what to close with
- Any important caveats, counterarguments, or nuances to address
- Suggested use of tables or comparisons if applicable

## Important

- Exactly 3 primary sources. 2–7 supporting sources.
- Every source must have been verified as readable.
- Do not fabricate URLs or titles."""

_SUMMARIZER_SYSTEM_PROMPT = """\
You are a source summarizer for a research pipeline. You receive a web \
page and a summarization plan that tells you what to extract.

## Rules

- Follow the summarization plan closely — extract exactly the information \
it asks for.
- Pay very close attention to context around every fact you include. \
Preserve the original meaning, intent, and nuance.
- If a statement is anecdotal, opinion, or unverified, label it as such.
- Include relevant quotes when they add authority or precision.
- Note any caveats, conditions, or qualifications that surround key claims.
- Preserve dates, version numbers, and specifics — do not generalize.
- Keep the summary focused and structured. Use bullet points or short \
paragraphs."""

_WRITER_SYSTEM_PROMPT = """\
You are a research writer. You receive a writing plan, full source \
documents (primary), and expert summaries (supporting). Your job is to \
write a clear, well-cited answer.

## How to write

Your answer IS the final output — there is no post-processing. Use \
markdown formatting.

Before writing, review the plan. Then write:
1. **The lead** — what directly answers the user's question? Open with it.
2. **The support** — evidence and context connecting the lead to the \
conclusion. Only include what earns its space.
3. **The close** — the actionable conclusion.

Write in natural prose — not a listicle. Cite every factual claim with \
[n] referencing the source number. Use tables when comparing parallel \
items. Keep it tight — say what needs saying and stop.

## Citations

Renumber sources sequentially as [1], [2], [3]... matching the source \
numbers provided. Every [n] in the text must appear in ## Sources, and \
every source must be cited at least once.

End with:

## Sources

[1] Title — URL
[2] Title — URL
..."""


# ---------------------------------------------------------------------------
# Researcher agent + tools
# ---------------------------------------------------------------------------

researcher_agent = Agent(
    deps_type=ExpLiteDeps,
    output_type=ResearchPlan,
)


@researcher_agent.tool
async def brave_search(ctx: RunContext[ExpLiteDeps], query: str) -> str:
    """Search the web for a query. Returns up to 5 results with URL, title, and description.

    You have a maximum of 5 searches total — make each one count.

    Args:
        query: A focused search query.
    """
    deps = ctx.deps

    if deps.budget.exhausted:
        return "Cost budget exhausted. Work with the sources you have."

    if deps.search_calls >= deps.max_search_calls:
        return "Search limit reached (5). Work with the sources you have."

    deps.search_calls += 1

    # Emit researching stage + create tool group on first search
    if not deps.emitted_researching:
        deps.emitted_researching = True
        deps.current_topic = query
        await deps.dispatch(StageEvent(stage="researching"))
        await deps.dispatch(DetailEvent(
            type="research",
            payload={"topic": query},
        ))

    topic = deps.current_topic

    await deps.dispatch(DetailEvent(
        type="search",
        payload={"topic": topic, "query": query},
    ))

    try:
        results = await _brave_search(query, deps.brave_api_key, count=5)
        await deps.dispatch(DetailEvent(
            type="search_done",
            payload={"topic": topic, "query": query, "num_results": len(results)},
        ))
    except Exception:
        logger.exception("Brave search failed for %r", query)
        await deps.dispatch(DetailEvent(
            type="search_done",
            payload={"topic": query, "query": query, "num_results": 0},
        ))
        return f"Search failed for: {query}"

    if not results:
        return f"No results found for: {query}"

    # Format results for the agent
    lines = []
    for i, r in enumerate(results, 1):
        url = r.get("url", "")
        title = r.get("title", "")
        desc = r.get("description", "")
        lines.append(f"{i}. [{title}]({url})\n   {desc}")

    return "\n\n".join(lines)


@researcher_agent.tool
async def verify_readable(ctx: RunContext[ExpLiteDeps], url: str) -> str:
    """Verify that a URL is readable (not rate-limited, paywalled, or erroring).

    On success, caches the content for later use. No call limit — verify
    liberally to find 10 readable sources.

    Args:
        url: The URL to verify.
    """
    deps = ctx.deps

    # Return cached result immediately
    if url in deps.jina_cache:
        return "readable (already cached)"

    topic = deps.current_topic

    # Robots.txt check
    if deps.db_session is not None:
        try:
            allowed, _ = await check_url_allowed(url, deps.db_session)
        except Exception:
            allowed = True
        if not allowed:
            await deps.dispatch(DetailEvent(
                type="fetch_done",
                payload={"topic": topic, "url": url, "failed": True},
            ))
            return f"failed: blocked by robots.txt"

    await deps.dispatch(DetailEvent(
        type="fetch",
        payload={"topic": topic, "url": url},
    ))

    try:
        content = await _jina_fetch(url, redis_url=deps.redis_url)
    except Exception:
        logger.exception("Jina fetch failed for %s", url)
        content = None

    if not content:
        await deps.dispatch(DetailEvent(
            type="fetch_done",
            payload={"topic": topic, "url": url, "failed": True},
        ))
        return f"failed: could not fetch content"

    # Cache the content
    deps.jina_cache[url] = content
    await deps.dispatch(DetailEvent(
        type="fetch_done",
        payload={"topic": topic, "url": url, "content": content[:200]},
    ))
    return "readable"


@researcher_agent.tool
async def read(ctx: RunContext[ExpLiteDeps], url: str) -> str:
    """Read the full content of a URL for deeper understanding.

    Hard limit: 3 calls per session. Use sparingly — only when you need
    to understand a source's content to make selection or planning decisions.

    Args:
        url: The URL to read.
    """
    deps = ctx.deps

    if deps.read_calls >= deps.max_read_calls:
        return f"Read limit reached ({deps.max_read_calls}/{deps.max_read_calls}). No more read calls available."

    deps.read_calls += 1

    # Check cache first
    if url in deps.jina_cache:
        content = deps.jina_cache[url]
        await deps.dispatch(DetailEvent(
            type="read",
            payload={"url": url, "from_cache": True, "read_calls": deps.read_calls},
        ))
        return content[:20_000]

    topic = deps.current_topic

    # Fetch fresh
    await deps.dispatch(DetailEvent(
        type="fetch",
        payload={"topic": topic, "url": url},
    ))

    try:
        content = await _jina_fetch(url, redis_url=deps.redis_url)
    except Exception:
        logger.exception("Jina fetch failed for %s", url)
        content = None

    if not content:
        await deps.dispatch(DetailEvent(
            type="fetch_done",
            payload={"topic": topic, "url": url, "failed": True},
        ))
        return "Failed to fetch content for this URL."

    deps.jina_cache[url] = content
    await deps.dispatch(DetailEvent(
        type="read",
        payload={"url": url, "from_cache": False, "read_calls": deps.read_calls},
    ))
    return content[:20_000]


# ---------------------------------------------------------------------------
# Summarizer agent (no tools — runs in parallel via Flash Lite)
# ---------------------------------------------------------------------------

summarizer_agent = Agent(output_type=str)


# ---------------------------------------------------------------------------
# Writer agent (no tools)
# ---------------------------------------------------------------------------

writer_agent = Agent(output_type=str)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class ExperimentalLitePipeline:
    def __init__(
        self,
        query: str,
        dispatch: Dispatch,
        *,
        brave_api_key: str,
        db_session: object | None = None,
        redis_url: str = "",
        user_timezone: str = "",
        prior_agent_messages: list[ModelMessage] | None = None,
        prior_fetched_urls: set[str] | None = None,
    ) -> None:
        self.query = query
        self.dispatch = dispatch
        self.brave_api_key = brave_api_key
        self.db_session = db_session
        self.redis_url = redis_url
        self.user_timezone = user_timezone
        self.prior_agent_messages = prior_agent_messages

        # Pipeline state
        self.budget = CostBudget(limit=0.15)
        self.jina_cache: dict[str, str] = {}
        self.researcher_counter = TokenCounter()
        self.writer_counter = TokenCounter()

    def _build_system_prompt(self, base_prompt: str) -> str:
        try:
            tz = ZoneInfo(self.user_timezone) if self.user_timezone else timezone.utc
        except (KeyError, ValueError):
            tz = timezone.utc
        now = datetime.now(tz)
        year = now.year
        date_block = (
            f"**TODAY IS {now.strftime('%A, %B %d, %Y')} ({self.user_timezone or 'UTC'}).** "
            f"The current year is {year}. It is NOT {year - 2} or {year - 1}. "
            f"Do not use {year - 2} or {year - 1} in search queries unless the "
            f"user specifically asked about those years."
        )
        return f"{date_block}\n\n{base_prompt}"

    async def run(self) -> None:
        try:
            logger.info("Experimental pipeline starting for query: %s", self.query[:80])
            # === Phase 1: Researcher agent ===
            await self.dispatch(StageEvent(stage="reasoning"))

            deps = ExpLiteDeps(
                dispatch=self.dispatch,
                budget=self.budget,
                brave_api_key=self.brave_api_key,
                redis_url=self.redis_url,
                db_session=self.db_session,
                jina_cache=self.jina_cache,
            )

            researcher_model = gemini_flash()
            researcher_prompt = self._build_system_prompt(_RESEARCHER_SYSTEM_PROMPT)

            async with researcher_agent.iter(
                self.query,
                model=researcher_model,
                deps=deps,
                instructions=researcher_prompt,
                message_history=self.prior_agent_messages or None,
            ) as agent_run:
                async for node in agent_run:
                    if isinstance(node, ModelRequestNode):
                        async with node.stream(agent_run.ctx) as stream:
                            async for event in stream:
                                if isinstance(event, PartDeltaEvent):
                                    delta = event.delta
                                    if isinstance(delta, ThinkingPartDelta) and delta.content_delta:
                                        await self.dispatch(DetailEvent(
                                            type="thinking",
                                            payload={"text": delta.content_delta},
                                        ))
                                    elif isinstance(delta, TextPartDelta) and delta.content_delta:
                                        await self.dispatch(DetailEvent(
                                            type="reasoning",
                                            payload={"text": delta.content_delta},
                                        ))
                    elif isinstance(node, CallToolsNode):
                        async with node.stream(agent_run.ctx) as stream:
                            async for event in stream:
                                if isinstance(event, FunctionToolCallEvent):
                                    await self.dispatch(DetailEvent(
                                        type="tool_call",
                                        payload={
                                            "name": event.part.tool_name,
                                            "args": event.part.args_as_dict(),
                                        },
                                    ))

                researcher_result = agent_run.result

            plan: ResearchPlan = researcher_result.output
            researcher_usage = researcher_result.usage()

            # Collapse the research tool group
            all_sources = list(plan.primary_sources) + [
                SourceEntry(url=s.url, title=s.title) for s in plan.supporting_sources
            ]
            topic = deps.current_topic
            if topic:
                await self.dispatch(DetailEvent(
                    type="result",
                    payload={"topic": topic, "num_sources": len(all_sources)},
                ))

            # Track researcher costs
            self.budget.add(
                researcher_usage.request_tokens or 0,
                researcher_usage.response_tokens or 0,
                "gemini-3-flash-preview",
            )
            self.researcher_counter.input_tokens = researcher_usage.request_tokens or 0
            self.researcher_counter.output_tokens = researcher_usage.response_tokens or 0

            # === Phase 2: Fetch sources + summarize supporting ===
            await self.dispatch(StageEvent(stage="researching"))

            # Batch-fetch any sources not already in cache
            uncached = [
                s for s in all_sources if s.url not in self.jina_cache
            ]
            if uncached:
                fetch_results = await asyncio.gather(
                    *[_jina_fetch(s.url, redis_url=self.redis_url) for s in uncached],
                    return_exceptions=True,
                )
                for source, content in zip(uncached, fetch_results):
                    if isinstance(content, BaseException) or not content:
                        logger.warning("Failed to fetch source for writer: %s", source.url)
                        continue
                    self.jina_cache[source.url] = content

            # Summarize supporting sources in parallel via Flash Lite
            summarizer_model = gemini_flash_lite()
            summaries: dict[str, str] = {}

            _sum_topic = "Summarizing sources"

            async def _summarize(source: SupportingSource) -> tuple[str, str]:
                content = self.jina_cache.get(source.url)
                if not content:
                    await self.dispatch(DetailEvent(
                        type="summarize_done",
                        payload={
                            "topic": _sum_topic,
                            "url": source.url,
                            "plan": source.summarization_plan,
                            "failed": True,
                        },
                    ))
                    return source.url, "(content unavailable)"

                await self.dispatch(DetailEvent(
                    type="summarize",
                    payload={
                        "topic": _sum_topic,
                        "url": source.url,
                        "title": source.title,
                        "plan": source.summarization_plan,
                    },
                ))

                truncated = content[:15_000]
                prompt = (
                    f"# Summarization Plan\n{source.summarization_plan}\n\n"
                    f"# Source: {source.title}\n{source.url}\n\n"
                    f"# Content\n{truncated}"
                )
                result = await summarizer_agent.run(
                    prompt,
                    model=summarizer_model,
                    instructions=_SUMMARIZER_SYSTEM_PROMPT,
                )

                await self.dispatch(DetailEvent(
                    type="summarize_done",
                    payload={
                        "topic": _sum_topic,
                        "url": source.url,
                        "plan": source.summarization_plan,
                        "summary": result.output,
                    },
                ))
                return source.url, result.output

            if plan.supporting_sources:
                # Emit a tool group for the summarization phase
                await self.dispatch(DetailEvent(
                    type="research",
                    payload={"topic": "Summarizing sources"},
                ))

                summary_results = await asyncio.gather(
                    *[_summarize(s) for s in plan.supporting_sources],
                    return_exceptions=True,
                )
                for item in summary_results:
                    if isinstance(item, BaseException):
                        logger.warning("Summarization failed: %s", item)
                        continue
                    url, summary = item
                    summaries[url] = summary

                # Collapse the summarization group
                await self.dispatch(DetailEvent(
                    type="result",
                    payload={
                        "topic": "Summarizing sources",
                        "num_sources": len(summaries),
                    },
                ))

            # Build writer input: plan + primary (full) + supporting (summarized)
            source_parts = []
            idx = 1
            for source in plan.primary_sources:
                content = self.jina_cache.get(source.url, "(content unavailable)")
                truncated = content[:15_000] if content != "(content unavailable)" else content
                source_parts.append(
                    f"[{idx}] {source.title} ({source.url}) [FULL SOURCE]\n{truncated}"
                )
                idx += 1
            for source in plan.supporting_sources:
                summary = summaries.get(source.url, "(summary unavailable)")
                source_parts.append(
                    f"[{idx}] {source.title} ({source.url}) [SUMMARY]\n{summary}"
                )
                idx += 1

            writer_input = (
                f"# User Query\n{self.query}\n\n"
                f"# Writing Plan\n{plan.writing_plan}\n\n"
                f"# Sources\n\n{'=' * 60}\n\n"
                + f"\n\n{'=' * 60}\n\n".join(source_parts)
            )

            # === Phase 3: Writer agent (streamed) ===
            await self.dispatch(StageEvent(stage="responding"))

            writer_model = gemini_flash()
            writer_prompt = self._build_system_prompt(_WRITER_SYSTEM_PROMPT)

            async with writer_agent.iter(
                writer_input,
                model=writer_model,
                instructions=writer_prompt,
            ) as writer_run:
                async for node in writer_run:
                    if isinstance(node, ModelRequestNode):
                        async with node.stream(writer_run.ctx) as stream:
                            async for event in stream:
                                if isinstance(event, PartStartEvent):
                                    if isinstance(event.part, TextPart) and event.part.content:
                                        await self.dispatch(TextEvent(text=event.part.content))
                                elif isinstance(event, PartDeltaEvent):
                                    delta = event.delta
                                    if isinstance(delta, TextPartDelta) and delta.content_delta:
                                        await self.dispatch(TextEvent(text=delta.content_delta))

                writer_result = writer_run.result

            writer_usage = writer_result.usage()
            self.budget.add(
                writer_usage.request_tokens or 0,
                writer_usage.response_tokens or 0,
                "gemini-3-flash-preview",
            )
            self.writer_counter.input_tokens = writer_usage.request_tokens or 0
            self.writer_counter.output_tokens = writer_usage.response_tokens or 0

            # === Emit agent messages for follow-up replay ===
            all_messages = researcher_result.all_messages()
            request_tokens = researcher_usage.request_tokens or 0
            compacted = _compact_history(all_messages, request_tokens)
            compacted_json = ModelMessagesTypeAdapter.dump_json(compacted).decode()
            await self.dispatch(DetailEvent(
                type="agent_messages",
                payload={"json": compacted_json},
            ))

            # === Usage reporting ===
            agent_model_name = "gemini-3-flash-preview"

            researcher_cost = calc_usage_cost(
                self.researcher_counter.input_tokens,
                self.researcher_counter.output_tokens,
                agent_model_name,
            )
            writer_cost = calc_usage_cost(
                self.writer_counter.input_tokens,
                self.writer_counter.output_tokens,
                agent_model_name,
            )

            total_in = self.researcher_counter.input_tokens + self.writer_counter.input_tokens
            total_out = self.researcher_counter.output_tokens + self.writer_counter.output_tokens
            total_input_cost = (
                float(researcher_cost["input_cost"]) + float(writer_cost["input_cost"])
            )
            total_output_cost = (
                float(researcher_cost["output_cost"]) + float(writer_cost["output_cost"])
            )

            await self.dispatch(DetailEvent(
                type="usage",
                payload={
                    "research": {
                        "input_tokens": self.researcher_counter.input_tokens,
                        "output_tokens": self.researcher_counter.output_tokens,
                        "input_cost": researcher_cost["input_cost"],
                        "output_cost": researcher_cost["output_cost"],
                    },
                    "agent": {
                        "input_tokens": self.writer_counter.input_tokens,
                        "output_tokens": self.writer_counter.output_tokens,
                        "input_cost": writer_cost["input_cost"],
                        "output_cost": writer_cost["output_cost"],
                    },
                    "total": {
                        "input_tokens": total_in,
                        "output_tokens": total_out,
                        "input_cost": f"{total_input_cost:.4f}",
                        "output_cost": f"{total_output_cost:.4f}",
                    },
                    "budget": {
                        "limit": self.budget.limit,
                        "spent": round(self.budget.spent, 4),
                    },
                },
            ))

            logger.info("Experimental pipeline complete, dispatching DoneEvent")
            await self.dispatch(DoneEvent())

        except Exception as exc:
            logger.exception("Experimental lite pipeline error: %s", exc)
            await self.dispatch(ErrorEvent(error=str(exc)))


# ---------------------------------------------------------------------------
# Async generator wrapper
# ---------------------------------------------------------------------------


async def run_experimental_lite_pipeline(
    query: str,
    brave_api_key: str,
    *,
    db_session: object | None = None,
    redis_url: str = "",
    user_timezone: str = "",
    prior_agent_messages: list[ModelMessage] | None = None,
    prior_fetched_urls: set[str] | None = None,
    **_kw,
) -> AsyncGenerator[PipelineEvent, None]:
    """Run the experimental lite pipeline, yielding SSE-compatible events."""
    event_queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()

    pipeline = ExperimentalLitePipeline(
        query,
        event_queue.put,
        brave_api_key=brave_api_key,
        db_session=db_session,
        redis_url=redis_url,
        user_timezone=user_timezone,
        prior_agent_messages=prior_agent_messages,
        prior_fetched_urls=prior_fetched_urls,
    )

    task = asyncio.create_task(pipeline.run())

    while True:
        event = await event_queue.get()
        yield event
        if isinstance(event, (DoneEvent, ErrorEvent)):
            break

    await task
