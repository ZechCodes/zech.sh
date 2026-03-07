"""Agent-based research pipeline for scan.zech.sh.

A Pydantic AI agent with tools for research and verification. The agent
researches the question, then writes the final answer directly — no
separate synthesis step.

Architecture:
  User Query → Agent (think → research → think → verify → think →
  write answer) → TextEvent + Sources footer
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from google.genai.types import GenerateContentConfig, ThinkingConfig, ThinkingLevel
from pydantic import BaseModel
from pydantic_ai import Agent, RunContext
from pydantic_ai._agent_graph import CallToolsNode, ModelRequestNode
from pydantic_ai.messages import (
    FunctionToolCallEvent,
    PartDeltaEvent,
    TextPartDelta,
    ThinkingPartDelta,
)

from controllers.deep_research_agent import (
    EXTRACTION_PROMPT,
    CostBudget,
    DetailEvent,
    Dispatch,
    DoneEvent,
    ErrorEvent,
    KnowledgeEntry,
    KnowledgeState,
    PipelineEvent,
    StageEvent,
    TextEvent,
    TokenCounter,
    _brave_search,
    _filter_results,
    _jina_fetch,
)
from controllers.llm import calc_usage_cost, gemini_flash, gemini_flash_lite, genai_client
from controllers.robots import check_url_allowed

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Agent dependencies
# ---------------------------------------------------------------------------


@dataclass
class AgentDeps:
    dispatch: Dispatch
    knowledge: KnowledgeState
    budget: CostBudget
    brave_api_key: str
    redis_url: str
    db_session: object | None
    already_fetched: set[str]
    extraction_counter: TokenCounter
    # Counters + limits
    research_calls: int = 0
    verify_calls: int = 0
    max_research_calls: int = 15
    max_verify_calls: int = 5
    # Stage tracking for event emission
    emitted_researching: bool = False
    # Config for sub-operations
    brave_results: int = 15
    jina_reads: int = 5
    extract_max_chars: int = 1200
    fetch_max_chars: int = 20_000
    extraction_model: str = "gemini-3.1-flash-lite-preview"


# ---------------------------------------------------------------------------
# Agent system prompt
# ---------------------------------------------------------------------------

_LITE_SYSTEM_PROMPT = """\
You are a research agent. You answer questions by searching the web, \
understanding the topic, and writing a clear response the user can \
immediately act on.

## How to research

You have two tools: `research` (web search + source extraction) and \
`verify_claim` (cross-check a specific claim). You can call `research` \
multiple times per turn and they run in parallel.

### Phase 1 — Survey and orient

Start with 2-3 broad parallel queries (max_sources 1-2) to get the lay \
of the land. Always include a recency query — if something has recently \
changed that affects the answer, the user needs to know immediately.

After the survey, identify the real question the user is trying to answer. \
It may not be exactly what they asked — understand what they actually need.

### Phase 2 — Research the answer

Research the question you identified and anything necessary to support a \
complete answer. Use focused queries with higher max_sources (2-4). \
Prioritize practical, actionable information over background context.

Stop when you have enough to give the user a clear, confident answer, or \
when your budget is running low (the tool will tell you).

## How to write the answer

Your answer IS the final output — there is no post-processing. Use \
markdown formatting.

Before writing, plan the piece. Decide:
1. **The lead** — what directly answers the user's question? Open with it.
2. **The close** — the actionable conclusion of it all.
3. **The support** — what evidence and context connect the lead to the \
close? Only include what earns its space.

Then write. Don't summarize what you found — pull out the most important \
details and supporting information, and build a compelling, original \
narrative that informs the user and answers their query. Natural prose, \
not a listicle. Cite every factual claim with [n]. Use tables when \
comparing parallel items. Keep it tight — say what needs saying and stop.

## Citations

Renumber sources sequentially as [1], [2], [3]. Every [n] in the text \
must appear in ## Sources, and every source must be cited at least once.

End with ## Sources as [n] Title — URL"""

_DEEP_SYSTEM_PROMPT = """\
You are a deep research agent. You answer questions by searching the web, \
building a thorough understanding of the topic, and writing a response \
that pushes the user's understanding in every direction the evidence supports. \
Your goal is not to summarize search results — it's to produce the answer \
a domain expert would write if they spent an afternoon researching the question.

## How to research

You have two tools: `research` (web search + source extraction) and \
`verify_claim` (cross-check a specific claim). You can call `research` \
multiple times per turn and they run in parallel.

### Phase 1 — Survey the landscape

Start broad. Run 2-4 parallel queries with low max_sources (1-2) to map \
the territory: what are the main positions, who are the key players, what \
has changed recently? Always include a recency query (e.g. "[topic] 2026" \
or "[topic] latest news") — if there are breaking developments, the user \
needs to know.

After the survey, stop and assess. What's the shape of this topic? Where \
are the fault lines, the disagreements, the open questions? What would the \
user most need to make high-level decisions?

### Phase 2 — Go deep

Based on what you learned, decide what threads matter most. Research each \
one with focused queries and higher max_sources (3-6). Look for:
- Primary sources over summaries
- Concrete data over general claims
- Dissenting views and counterarguments
- The conversations people are actually having (forums, discussions, debates)

Vary your query angles. Don't just rephrase — try different source types \
(docs, forums, academic, news). If the consensus says X, search for \
evidence against X.

### When to stop

Stop when additional queries return information you already have, or when \
your budget is running low (the tool will tell you). Don't research \
tangents that won't serve the user's question.

## How to write the answer

Your answer IS the final output — there is no post-processing. Use \
markdown formatting.

Before you start writing, plan the piece. Decide:
1. **The opening** — what is the sharpest, most important point? Lead \
with it. Never open with a definition or by restating the question. If \
the research reveals the question is wrong or incomplete, say so and \
reframe.
2. **The close** — what are the implications? What should the reader do \
or watch for next? Not a summary — new forward-looking insight.
3. **The bridge** — what arguments, evidence, and counterarguments \
connect the opening to the close? What threads need to be developed, \
and in what order, to make the conclusion feel inevitable?

Then write.

Write an argued, opinionated response — not a listicle, not a neutral \
summary. Present all sides and all relevant facts, then come to the \
correct conclusion and say why. Show causation between threads. Develop \
the strongest counterargument with the same rigor as the thesis.

Write in natural prose. Use headers only at genuine topic shifts — make \
them specific, not generic. Use tables when comparing parallel items. \
Cite every factual claim with [n]. When sources disagree, say so. When \
evidence is thin, say so.

Calibrate length to evidence — a tight 600-word answer that's honest \
about gaps beats a padded 1500-word answer. Do not speculate to fill space.

## Citations

Renumber sources sequentially as [1], [2], [3]. Every [n] in the text \
must appear in ## Sources, and every source must be cited at least once.

End with ## Sources as [n] Title — URL"""


# ---------------------------------------------------------------------------
# Agent definition
# ---------------------------------------------------------------------------

research_agent = Agent(
    deps_type=AgentDeps,
    output_type=str,
)


# ---------------------------------------------------------------------------
# Tool 1: research
# ---------------------------------------------------------------------------


@research_agent.tool
async def research(ctx: RunContext[AgentDeps], query: str, max_sources: int = 3) -> str:
    """Search the web for a query and return extracted findings.

    Args:
        query: A focused search query to investigate.
        max_sources: How many sources to read and extract (1-10). Use 1-2 for
            quick facts, 3-5 for supporting evidence, 6-10 for deep understanding.
    """
    deps = ctx.deps

    # Budget check
    if deps.research_calls >= deps.max_research_calls:
        return "Research budget exhausted — no more research calls available. Work with what you have."
    if deps.budget.exhausted:
        return "Cost budget exhausted. Work with what you have."

    deps.research_calls += 1

    # Emit researching stage on first call
    if not deps.emitted_researching:
        deps.emitted_researching = True
        await deps.dispatch(StageEvent(stage="researching"))

    # Emit research group start
    await deps.dispatch(DetailEvent(
        type="research",
        payload={"topic": query},
    ))

    # --- Brave search ---
    await deps.dispatch(DetailEvent(
        type="search",
        payload={"topic": query, "query": query},
    ))

    try:
        results = await _brave_search(query, deps.brave_api_key, count=deps.brave_results)
        await deps.dispatch(DetailEvent(
            type="search_done",
            payload={"topic": query, "query": query, "num_results": len(results)},
        ))
    except Exception:
        logger.exception("Brave search failed for %r", query)
        await deps.dispatch(DetailEvent(
            type="search_done",
            payload={"topic": query, "query": query, "num_results": 0},
        ))
        await deps.dispatch(DetailEvent(
            type="result",
            payload={"topic": query, "urls": [], "num_sources": 0},
        ))
        return f"Search failed for: {query}"

    if not results:
        await deps.dispatch(DetailEvent(
            type="result",
            payload={"topic": query, "urls": [], "num_sources": 0},
        ))
        return f"No search results found for: {query}"

    # --- Filter and rank results ---
    all_candidates = await _filter_results(
        results, query, deps.knowledge, deps.already_fetched,
        deps.extraction_counter,
    )

    if not all_candidates:
        await deps.dispatch(DetailEvent(
            type="result",
            payload={"topic": query, "urls": [], "num_sources": 0},
        ))
        return f"No viable sources found for: {query}"

    # --- Fetch and extract in batches ---
    client = genai_client()
    target_sources = max(1, min(max_sources, deps.jina_reads))
    source_urls: list[str] = []
    findings: list[str] = []
    entries_before = len(deps.knowledge.entries)
    offset = 0

    while (
        offset < len(all_candidates)
        and (len(deps.knowledge.entries) - entries_before) < target_sources
        and not deps.budget.exhausted
    ):
        needed = target_sources - (len(deps.knowledge.entries) - entries_before)
        batch = all_candidates[offset:offset + needed]
        offset += len(batch)

        # Robots.txt check
        urls_allowed: list[tuple[str, str]] = []
        for url, title in batch:
            if deps.db_session is not None:
                try:
                    allowed, _ = await check_url_allowed(url, deps.db_session)
                except Exception:
                    allowed = True
                if not allowed:
                    logger.info("Blocked by robots.txt: %s", url)
                    await deps.dispatch(DetailEvent(
                        type="fetch_done",
                        payload={"topic": query, "url": url, "failed": True},
                    ))
                    continue
            urls_allowed.append((url, title))

        if not urls_allowed:
            continue

        # Emit fetch start events
        for url, _ in urls_allowed:
            await deps.dispatch(DetailEvent(
                type="fetch",
                payload={"topic": query, "url": url},
            ))

        # Fetch via Jina (rate-limited, parallel)
        fetch_results = await asyncio.gather(
            *[_jina_fetch(url, redis_url=deps.redis_url) for url, _ in urls_allowed],
            return_exceptions=True,
        )

        # Separate successes from failures, then extract
        docs_to_extract: list[tuple[str, str, str]] = []
        for (url, title), content in zip(urls_allowed, fetch_results):
            if isinstance(content, BaseException):
                content = None
            if not content:
                await deps.dispatch(DetailEvent(
                    type="fetch_done",
                    payload={"topic": query, "url": url, "failed": True},
                ))
                continue
            deps.already_fetched.add(url)
            docs_to_extract.append((url, title, content[:deps.fetch_max_chars]))

        # Extract knowledge from fetched docs (parallel)
        async def _extract_one(url: str, title: str, truncated: str) -> None:
            extraction_prompt = (
                f"Query: {query}\n\nDocument from {title} ({url}):\n{truncated}"
            )
            try:
                extract_cfg = GenerateContentConfig(
                    system_instruction=EXTRACTION_PROMPT,
                )
                extract_cfg.thinking_config = ThinkingConfig(
                    thinking_level=ThinkingLevel.MINIMAL,
                )
                extract_resp = await client.aio.models.generate_content(
                    model=deps.extraction_model,
                    contents=extraction_prompt,
                    config=extract_cfg,
                )
                deps.extraction_counter.add_from_response(extract_resp)

                ext_meta = getattr(extract_resp, "usage_metadata", None)
                if ext_meta:
                    deps.budget.add(
                        ext_meta.prompt_token_count or 0,
                        ext_meta.candidates_token_count or 0,
                        deps.extraction_model,
                    )

                extracted = extract_resp.text or ""
                if len(extracted) > deps.extract_max_chars:
                    extracted = extracted[:deps.extract_max_chars]

                if extracted and "no relevant content" not in extracted.lower():
                    entry = KnowledgeEntry(
                        source_id=str(uuid4())[:8],
                        url=url,
                        title=title,
                        query=query,
                        key_points=extracted,
                        char_count=len(extracted),
                        topic=query,
                    )
                    deps.knowledge.add(entry)
                    source_num = len(deps.knowledge.entries)
                    findings.append(f"[{source_num}] {title} ({url}): {extracted[:500]}")

                    usage_dict = None
                    if ext_meta:
                        usage_dict = calc_usage_cost(
                            ext_meta.prompt_token_count or 0,
                            ext_meta.candidates_token_count or 0,
                            deps.extraction_model,
                        )

                    await deps.dispatch(DetailEvent(
                        type="fetch_done",
                        payload={
                            "topic": query,
                            "url": url,
                            "content": extracted[:3000],
                            **({"usage": usage_dict} if usage_dict else {}),
                        },
                    ))
                else:
                    await deps.dispatch(DetailEvent(
                        type="fetch_done",
                        payload={"topic": query, "url": url, "content": "(no relevant content)"},
                    ))

                source_urls.append(url)

            except Exception:
                logger.exception("Extraction failed for %s", url)
                await deps.dispatch(DetailEvent(
                    type="fetch_done",
                    payload={"topic": query, "url": url, "failed": True},
                ))

        await asyncio.gather(
            *[_extract_one(u, t, c) for u, t, c in docs_to_extract],
            return_exceptions=True,
        )

    # Emit result event
    new_entries = len(deps.knowledge.entries) - entries_before
    await deps.dispatch(DetailEvent(
        type="result",
        payload={"topic": query, "urls": source_urls, "num_sources": new_entries},
    ))

    if not findings:
        return f"No sources could be loaded for: {query}"

    # Build response for the agent
    gaps = ""
    if new_entries < target_sources:
        gaps = f"Only {new_entries}/{target_sources} target sources were usable."

    return (
        f"Found {new_entries} sources for '{query}'.\n"
        f"Use [n] inline to cite these sources in your answer.\n\n"
        f"Findings:\n" + "\n\n".join(findings)
        + (f"\n\nGaps: {gaps}" if gaps else "")
    )


# ---------------------------------------------------------------------------
# Tool 2: verify_claim
# ---------------------------------------------------------------------------


class VerificationResult(BaseModel):
    verdict: str  # "supported", "partially_supported", "unsupported"
    evidence: str


_verify_agent = Agent(
    system_prompt=(
        "You verify factual claims. Given a claim and a source excerpt, "
        "determine whether the claim is supported by the evidence.\n\n"
        "Respond with:\n"
        "- verdict: 'supported', 'partially_supported', or 'unsupported'\n"
        "- evidence: brief explanation of why"
    ),
    output_type=VerificationResult,
)


@research_agent.tool
async def verify_claim(
    ctx: RunContext[AgentDeps], claim: str, source_excerpt: str,
) -> str:
    """Verify a specific factual claim against a source excerpt.

    Args:
        claim: The factual claim to verify.
        source_excerpt: The source text to check the claim against.
    """
    deps = ctx.deps

    if deps.verify_calls >= deps.max_verify_calls:
        return "Verification budget exhausted. Proceed with available evidence."
    if deps.budget.exhausted:
        return "Cost budget exhausted."

    deps.verify_calls += 1

    await deps.dispatch(DetailEvent(
        type="verify",
        payload={"claim": claim[:200]},
    ))

    try:
        prompt = f"Claim: {claim}\n\nSource excerpt:\n{source_excerpt}"
        result = await _verify_agent.run(prompt, model=gemini_flash_lite())
        usage = result.usage()
        deps.extraction_counter.input_tokens += usage.request_tokens or 0
        deps.extraction_counter.output_tokens += usage.response_tokens or 0
        verdict = result.output.verdict
        evidence = result.output.evidence
        await deps.dispatch(DetailEvent(
            type="verify_done",
            payload={"claim": claim[:200], "verdict": verdict, "evidence": evidence},
        ))
        return f"Verdict: {verdict}\nEvidence: {evidence}"
    except Exception:
        logger.exception("Verification failed for claim: %s", claim[:100])
        await deps.dispatch(DetailEvent(
            type="verify_done",
            payload={"claim": claim[:200], "verdict": "error", "evidence": "Verification call failed."},
        ))
        return "Verification failed — proceed with caution on this claim."


# ---------------------------------------------------------------------------
# Pipeline configuration per mode
# ---------------------------------------------------------------------------

_MODE_CONFIG = {
    "lite": {
        "agent_model_fn": gemini_flash,
        "system_prompt": _LITE_SYSTEM_PROMPT,
        "max_research_calls": 8,
        "max_verify_calls": 3,
        "budget_limit": 0.15,
        "brave_results": 8,
        "jina_reads": 2,
        "extract_max_chars": 1200,
        "fetch_max_chars": 20_000,
        "extraction_model": "gemini-3.1-flash-lite-preview",
    },
    "deep": {
        "agent_model_fn": gemini_flash,
        "system_prompt": _DEEP_SYSTEM_PROMPT,
        "max_research_calls": 15,
        "max_verify_calls": 5,
        "budget_limit": 0.25,
        "brave_results": 15,
        "jina_reads": 5,
        "extract_max_chars": 1200,
        "fetch_max_chars": 20_000,
        "extraction_model": "gemini-3.1-flash-lite-preview",
    },
}


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------


class AgentResearchPipeline:
    def __init__(
        self,
        query: str,
        dispatch: Dispatch,
        *,
        brave_api_key: str,
        db_session: object | None = None,
        redis_url: str = "",
        user_timezone: str = "",
        conversation_history: list[dict] | None = None,
        mode: str = "deep",
    ) -> None:
        self.query = query
        self.dispatch = dispatch
        self.brave_api_key = brave_api_key
        self.db_session = db_session
        self.redis_url = redis_url
        self.user_timezone = user_timezone
        self.conversation_history = conversation_history
        self.mode = mode
        self.cfg = _MODE_CONFIG[mode]

        # Pipeline state
        self.knowledge = KnowledgeState()
        self.already_fetched: set[str] = set()
        self.extraction_counter = TokenCounter()
        self.budget = CostBudget(limit=self.cfg["budget_limit"])

    def _build_full_query(self) -> str:
        try:
            tz = ZoneInfo(self.user_timezone) if self.user_timezone else timezone.utc
        except (KeyError, ValueError):
            tz = timezone.utc
        now = datetime.now(tz)
        full_query = (
            f"Current date/time: {now.strftime('%A, %B %d, %Y %H:%M')} "
            f"({self.user_timezone or 'UTC'})\n\n{self.query}"
        )
        if self.conversation_history:
            parts = ["Previous conversation:"]
            for msg in self.conversation_history:
                role = "User" if msg["role"] == "user" else "Assistant"
                parts.append(f"{role}: {msg['content']}")
            full_query = "\n".join(parts) + "\n\n" + full_query
        return full_query

    async def run(self) -> None:
        cfg = self.cfg
        full_query = self._build_full_query()

        try:
            # --- Emit reasoning stage ---
            await self.dispatch(StageEvent(stage="reasoning"))

            # --- Build agent deps ---
            deps = AgentDeps(
                dispatch=self.dispatch,
                knowledge=self.knowledge,
                budget=self.budget,
                brave_api_key=self.brave_api_key,
                redis_url=self.redis_url,
                db_session=self.db_session,
                already_fetched=self.already_fetched,
                extraction_counter=self.extraction_counter,
                max_research_calls=cfg["max_research_calls"],
                max_verify_calls=cfg["max_verify_calls"],
                brave_results=cfg["brave_results"],
                jina_reads=cfg["jina_reads"],
                extract_max_chars=cfg["extract_max_chars"],
                fetch_max_chars=cfg["fetch_max_chars"],
                extraction_model=cfg["extraction_model"],
            )

            # --- Run the agent with streaming ---
            agent_model = cfg["agent_model_fn"]()
            async with research_agent.iter(
                full_query,
                model=agent_model,
                deps=deps,
                instructions=cfg["system_prompt"],
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

                result = agent_run.result

            # --- Emit the agent's answer as text ---
            answer = result.output
            await self.dispatch(TextEvent(text=answer))

            # --- Usage reporting ---
            agent_usage = result.usage()
            agent_model_name = "gemini-3-flash-preview"
            self.budget.add(
                agent_usage.request_tokens or 0,
                agent_usage.response_tokens or 0,
                agent_model_name,
            )

            extraction_in = self.extraction_counter.input_tokens
            extraction_out = self.extraction_counter.output_tokens
            extraction_cost = calc_usage_cost(
                extraction_in, extraction_out, cfg["extraction_model"],
            )

            agent_in = agent_usage.request_tokens or 0
            agent_out = agent_usage.response_tokens or 0
            agent_cost = calc_usage_cost(agent_in, agent_out, agent_model_name)

            total_in = extraction_in + agent_in
            total_out = extraction_out + agent_out
            total_input_cost = (
                float(extraction_cost["input_cost"]) + float(agent_cost["input_cost"])
            )
            total_output_cost = (
                float(extraction_cost["output_cost"]) + float(agent_cost["output_cost"])
            )

            await self.dispatch(DetailEvent(
                type="usage",
                payload={
                    "research": {
                        "input_tokens": extraction_in,
                        "output_tokens": extraction_out,
                        "input_cost": extraction_cost["input_cost"],
                        "output_cost": extraction_cost["output_cost"],
                    },
                    "agent": {
                        "input_tokens": agent_in,
                        "output_tokens": agent_out,
                        "input_cost": agent_cost["input_cost"],
                        "output_cost": agent_cost["output_cost"],
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

            await self.dispatch(DoneEvent())

        except Exception as exc:
            logger.exception("Agent research pipeline error")
            await self.dispatch(ErrorEvent(error=str(exc)))


# ---------------------------------------------------------------------------
# Async generator wrapper
# ---------------------------------------------------------------------------


async def run_agent_research_pipeline(
    query: str,
    brave_api_key: str,
    *,
    db_session: object | None = None,
    redis_url: str = "",
    user_timezone: str = "",
    conversation_history: list[dict] | None = None,
    mode: str = "deep",
) -> AsyncGenerator[PipelineEvent, None]:
    """Run the agent research pipeline, yielding SSE-compatible events.

    Same interface as ``run_grounded_research_pipeline`` for drop-in replacement.
    """
    event_queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()

    pipeline = AgentResearchPipeline(
        query,
        event_queue.put,
        brave_api_key=brave_api_key,
        db_session=db_session,
        redis_url=redis_url,
        user_timezone=user_timezone,
        conversation_history=conversation_history,
        mode=mode,
    )

    task = asyncio.create_task(pipeline.run())

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
