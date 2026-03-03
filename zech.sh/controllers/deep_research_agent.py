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
from collections.abc import AsyncGenerator, Awaitable, Callable
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
from controllers.llm import calc_usage_cost, gemini_flash_lite, genai_client, google_provider
from pydantic_ai.models.google import GoogleModel
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
            thread_parts = []
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
- The current date is provided in the QUESTION. Use it when crafting queries \
that need recent or time-sensitive information.
- ALWAYS write substantial reasoning before TOPICS
- 3-6 topics covering genuinely different dimensions of the question
- Each topic: short label, one-sentence description, 2-4 specific search queries
- Craft queries like a skilled researcher: specific, varied angles
- One topic should target contrarian/critical perspective
- One topic should seek primary/technical sources
- Include one query that stress-tests the biggest assumption in your plan. \
Every research plan has a foundational assumption — a tool is still maintained, \
a technology is still the standard approach, a company still exists, a policy \
hasn't changed. Identify what your plan takes for granted and include a query \
that would surface it if it's wrong. This query should target the most recent \
information available — use the current year or "latest" rather than prior years.
- Include one "what changed recently" thread. After identifying your research \
threads, add one that targets recent shifts, disruptions, or surprises in the \
landscape you're researching. What might have changed in the last 6 months \
that would alter the conventional wisdom? Use queries that pair the core \
subject with recency — "[subject] news [current year]," "[subject] major \
changes latest." If nothing significant has changed, this thread returns \
noise and the synthesis step ignores it. That's fine — one low-yield thread \
is cheaper than an outdated answer.
- Every topic and query must be research-focused — searching for new \
information, evidence, or sources. NEVER create topics about synthesizing, \
summarizing, or combining. Synthesis happens later.
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
- The current date is provided in the QUESTION. Use it when crafting queries \
that need recent or time-sensitive information.
- Write brief reasoning before the TOPICS directive
- 2-4 topics, each with exactly ONE search query — pick the single \
best query that will surface the most useful results for that angle
- Topics should cover different angles, not restatements
- Include one query that stress-tests the biggest assumption in your plan. \
Every research plan has a foundational assumption — a tool is still maintained, \
a technology is still the standard approach, a company still exists, a policy \
hasn't changed. Identify what your plan takes for granted and include a query \
that would surface it if it's wrong. This query should target the most recent \
information available — use the current year or "latest" rather than prior years.
- Include one "what changed recently" topic targeting recent shifts or \
surprises in the landscape. Use queries like "[subject] news [current year]" \
or "[subject] major changes latest." One low-yield thread is cheaper than \
an outdated answer.
- Every topic and query must be research-focused — searching for new \
information, not synthesizing or summarizing. Synthesis happens later.
- TOPICS: must appear on its own line at the very end
- The JSON array must be on the same line as TOPICS:"""


GROUNDED_PLANNING_PROMPT = """\
You are a research survey planner. This is NOT the research step — your \
job is to map the landscape so the actual research knows where to dig. \
The queries you produce should help us understand what exists, who the \
key players are, and what the current state of things looks like.

Before generating queries, think about:
- What does the person actually need to walk away with?
- What are the obvious dimensions of this question? (e.g., if it's \
a comparison, what are the axes? If it's a recommendation, what \
are the constraints? If it's a "how to build," what are the \
components?)
- What would you need to see in search results to know you've \
mapped the landscape?

Write 2-4 sentences framing the question and what an initial survey \
should cover. Then output search queries.

QUERIES: ["search query 1", "search query 2", ...]

RULES:
- DATES: The current date is in the QUESTION. NEVER use prior-year dates \
(2025, 2024, etc.) in queries — only use the current year. If the user \
didn't mention a specific year, prefer dropping the year entirely or using \
"latest" over guessing. The only exception is if the user's query \
explicitly references a prior year.
- Write brief reasoning before the QUERIES directive
- 4-6 search queries covering different angles of the question
- These are survey queries — they should map the territory, not answer the \
question. "what are the main Python async testing frameworks 2026" maps \
the space; "best Python async testing framework" tries to answer it.
- Queries should be specific enough to return useful results on the \
first try. Include version numbers or named tools when relevant.
- At least one query should target what practitioners or reviewers \
say — not just official docs or marketing. Forums, benchmarks, \
case studies, and comparisons tend to surface practical signal.
- Include one query that checks whether the landscape has shifted \
recently — "[subject] news [current year]" or "[subject] major changes \
latest." The survey needs to know if conventional wisdom is outdated \
before the deep research commits to a direction.
- Do NOT include speculative or contrarian angles — those come later \
after we see what exists
- Every query must be a search for information, NOT synthesis or \
summarization. "how X compares to Y" is research; "summarize X" is not.
- QUERIES: must appear on its own line at the very end
- The JSON array must be on the same line as QUERIES:"""


EVALUATE_PROMPT = """\
You are continuing your research analysis. You planned an initial survey and \
the results are in. Now think through what you found and what needs deeper \
investigation.

Based on these results, work through:
- What did the initial research actually reveal? Any surprises?
- Where are the knowledge gaps — what important aspects weren't covered?
- Are there contradictions between sources that need resolution?
- What depth opportunities exist — topics where surface-level results hint \
at something more substantive underneath?
- What perspectives are missing? Who hasn't been heard from?
- Are there primary sources, technical documentation, or original research \
that should be consulted directly?
- What's the contrarian take? What would someone with deep domain expertise \
think to check that a generalist would miss?
- Are there cross-domain parallels or historical precedents worth exploring?

Think through this as a continuous internal monologue — you're reasoning \
about what you found and what's still missing. Write 2-4 dense paragraphs.

ENDING YOUR RESPONSE:

After reasoning, end with a TOPICS directive decomposing the deeper \
investigation into 3-8 concurrent research threads:

TOPICS: [{"label": "Short label", "description": "What to investigate \
and why", "queries": ["search query 1", "search query 2"]}, ...]

RULES:
- The current date is provided in the QUESTION. Use it when crafting queries \
that need recent or time-sensitive information.
- ALWAYS write substantial reasoning before TOPICS
- 3-8 topics covering genuinely different dimensions that need depth
- Each topic: short label, one-sentence description, 2-4 specific search queries
- These topics should be GROUNDED in what you actually found — reference \
specific findings, gaps, or contradictions from the initial research
- Every topic and query must be research-focused — searching for new \
information, evidence, or sources. NEVER create topics about synthesizing, \
summarizing, or combining what you already have. Synthesis happens later.
- One topic should target contrarian/critical perspective
- One topic should seek primary/technical sources
- Include one query that stress-tests the biggest assumption in your plan. \
Every research plan has a foundational assumption — a tool is still maintained, \
a technology is still the standard approach, a company still exists, a policy \
hasn't changed. Identify what your plan takes for granted and include a query \
that would surface it if it's wrong. This query should target the most recent \
information available — use the current year or "latest" rather than prior years.
- TOPICS: must appear on its own line at the very end
- The JSON array must be on the same line as TOPICS:"""


LITE_GROUNDED_PLANNING_PROMPT = """\
You are a research survey planner. This is NOT the research step — your \
job is to map the landscape so the actual research knows where to dig.

Think briefly: what does this person need, and what are the 3 most \
useful searches to understand what's out there? Write 1-2 sentences, \
then output exactly 3 search queries.

QUERIES: ["search query 1", "search query 2", "search query 3"]

RULES:
- DATES: The current date is in the QUESTION. NEVER use prior-year dates \
(2025, 2024, etc.) in queries — only use the current year. If the user \
didn't mention a specific year, prefer dropping the year entirely or using \
"latest" over guessing. The only exception is if the user's query \
explicitly references a prior year.
- Write 1-2 sentences of reasoning before QUERIES
- Exactly 3 search queries that map the territory, not answer the question
- Queries should be specific — include names or versions when relevant
- One query should check whether the landscape has shifted recently — \
use the current year or "latest" to catch outdated assumptions.
- Every query must search for information, NOT synthesize or summarize. \
Research only — synthesis happens later.
- QUERIES: must appear on its own line at the very end
- The JSON array must be on the same line as QUERIES:"""


LITE_EVALUATE_PROMPT = """\
You are continuing your research analysis. You ran an initial survey of \
the space and now need to decide what to drill into to actually answer \
the user's question.

Look at what the survey turned up and think through:
- What directly answers the question? Where do we need more depth?
- What are the supporting arguments or evidence — and what's the \
strongest counterargument or caveat?
- Did anything in the results contradict something else, or hint at \
a nuance the surface results glossed over?

Keep this to 2-3 sentences — just enough to explain your reasoning. \
Then output focused research topics.

TOPICS: [{"label": "Short label", "description": "What to investigate \
and why", "queries": ["search query 1", "search query 2"]}, ...]

RULES:
- The current date is provided in the QUESTION. Use it when crafting queries \
that need recent or time-sensitive information.
- Write brief reasoning before TOPICS
- 3-5 topics that directly serve answering the user's question
- At least one topic should gather supporting evidence or examples
- At least one topic should seek counterarguments, limitations, or \
critical perspectives
- Each topic: short label, one-sentence description, 1-2 specific queries
- Topics should be grounded in what the survey actually found
- Include one query that stress-tests the biggest assumption in your plan — \
target the most recent information using the current year or "latest."
- Every topic and query must be research-focused — searching for new \
information or evidence. NEVER create topics about synthesizing or \
summarizing. Synthesis happens later.
- TOPICS: must appear on its own line at the very end
- The JSON array must be on the same line as TOPICS:"""


SURVEY_RECONSIDERATION_PROMPT = """\
You are reviewing a SURVEY plan — not the final research plan. These queries \
are meant to map the landscape so the actual deep research knows where to \
dig. They should NOT try to answer the user's question directly.

CRITICAL — DATES: The current year is stated at the top of the user message. \
It is NOT speculative — that year is the real, actual current year. If any \
query includes a year, it MUST match that year. Replace 2025, 2024, or any \
prior year with the current year. The ONLY exception is if the user \
explicitly asked about a specific prior year. When in doubt, drop the year \
entirely rather than use a stale one.

Given the user's query and the proposed survey queries, check:

- Do the queries cover enough dimensions to understand what's out there? \
If the question is a comparison, do we survey all the candidates? If it's \
a recommendation, do we map the constraints?
- Is there a recency query? The survey needs to know if the landscape has \
shifted before the deep research commits to a direction. Do NOT remove this.
- Is there anything the user takes for granted that might be wrong? The \
survey should include at least one query that would surface it.
- Are any queries already trying to answer the question instead of mapping \
the space? Reframe them to survey instead.

RESPOND WITH:
1. What landscape this survey needs to map (one sentence)
2. Whether the queries cover it: APPROVED or REVISE
3. If REVISE: specific instructions on which queries to add, remove, or reframe

Keep your response under 150 words. Be direct."""

RECONSIDERATION_PROMPT = """\
You are a research plan reviewer. You receive a user's original query and a set of proposed research threads. Your ONLY job is to check whether the threads, if researched well, would produce an answer the user would actually be satisfied with.

Think about the person who wrote this query. Imagine handing them the finished answer.

- What would they expect to see in the FIRST TWO SENTENCES?
- What would they be DISAPPOINTED to not find anywhere in the answer?
- Is there anything in the research plan that the user didn't ask about but would materially change their decision if they knew? This should remain as supporting context, not a primary thread.
- Is there a gap between what these threads investigate and what the person is actually trying to accomplish?

Threads that are intellectually interesting but don't serve the user's actual goal should be demoted to supporting context or removed entirely. However, do NOT remove or demote "what changed recently" threads that check for recent shifts in the landscape — an outdated answer is worse than one extra low-yield thread.

RESPOND WITH:
1. What the user most likely wants from this answer (one sentence)
2. Whether the threads deliver that: APPROVED or REVISE
3. If REVISE: specific instructions on which threads to add, remove, or reframe

Keep your response under 150 words. Be direct."""


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
Summarize this document in context of the query. Extract facts, numbers, \
dates, quotes, product details, comparisons, and anything else that could \
inform an answer — even indirectly.

Only respond with "No relevant content." if the document is entirely \
unrelated to the query's subject area OR is meaningless content (login \
walls, empty pages, cookie notices, etc.).

If the document is even loosely related, summarize what's there. Let the \
synthesis step decide what matters — your job is to not lose information.

Do not add commentary — just extract."""

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


class OutlineSection(BaseModel):
    heading: str
    key_points: list[str]


class ArticulationOutline(BaseModel):
    thesis: str
    sections: list[OutlineSection]
    closing_direction: str


class CitationRef(BaseModel):
    source_number: int
    fact: str


class SectionCitations(BaseModel):
    heading: str
    citations: list[CitationRef]


class CitationPlan(BaseModel):
    section_citations: list[SectionCitations]


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

_outline_agent = Agent(
    system_prompt=(
        "You are a research outline planner. Given a question and accumulated "
        "research threads, produce a structured outline for the final answer.\n\n"
        "THESIS: Identify the single sharpest finding from the research — "
        "a surprising data point, a structural shift, or a key tension.\n\n"
        "SECTIONS: Create sections weighted unevenly by evidence strength. "
        "A thread with strong evidence deserves a full section. A thread with "
        "only general claims should be folded into another section or omitted.\n"
        "- Use specific, descriptive headings (e.g., 'The Monorepo Problem "
        "Nobody Warns You About'), not generic ones ('Key Challenges').\n"
        "- Include a counterargument section if the evidence warrants it.\n"
        "- Each section's key_points should name specific facts, data points, "
        "or claims to cover — not vague descriptions.\n\n"
        "EXAMPLES: Before outlining, consider what form the most useful parts "
        "of the answer would take. Not every point is best expressed as prose. "
        "If the person would realistically copy, adapt, or reference something "
        "directly — a command, a configuration, a formula, a template, a query, "
        "a file structure — mark it in the outline as [EXAMPLE: what to show]. "
        "If the answer is entirely conceptual, analytical, or comparative, no "
        "examples are needed.\n"
        "The test: if explaining something in prose would force the reader to "
        "mentally translate your words back into the thing itself, show the "
        "thing instead.\n\n"
        "CLOSING: Describe the forward-looking implication to close with — "
        "what the reader should do or watch for, not a summary."
    ),
    output_type=ArticulationOutline,
)

_lite_outline_agent = Agent(
    system_prompt=(
        "You are a research outline planner for concise, action-oriented "
        "answers.\n\n"
        "OPENING: Open with the sharpest thing the user needs. If they're "
        "trying to understand something, lead with the most surprising or "
        "important insight. If they're trying to do something, lead with the "
        "recommendation — then immediately explain what makes it the right "
        "choice. Context that changes the conventional wisdom belongs right "
        "after the answer, not before it.\n\n"
        "THESIS: Distill the opening into a single sharp statement.\n\n"
        "SECTIONS: Keep it tight — only sections with real evidence behind "
        "them. Thin threads get folded in or dropped, not padded.\n"
        "- Use specific, descriptive headings, not generic ones.\n"
        "- Each section's key_points should name specific facts or claims.\n\n"
        "EXAMPLES: If the person would realistically copy, adapt, or "
        "reference something directly — a command, a configuration, a "
        "template — mark it as [EXAMPLE: what to show]. If the answer is "
        "conceptual or comparative, no examples are needed.\n\n"
        "CLOSING: What should the reader do or watch for next — not a summary."
    ),
    output_type=ArticulationOutline,
)

_citation_agent = Agent(
    system_prompt=(
        "You are a citation mapper. Given a structured outline and a numbered "
        "source list with key findings, map specific citations to each section.\n\n"
        "RULES:\n"
        "- Every factual claim in the outline's key_points must get at least "
        "one citation.\n"
        "- Use the [n] source numbers exactly as they appear in the source list.\n"
        "- For each citation, include the specific fact from that source that "
        "supports the claim.\n"
        "- A single source can appear in multiple sections.\n"
        "- Do not invent source numbers — only use numbers present in the "
        "provided source list.\n"
        "- Prefer primary sources over secondary when both cover the same fact."
    ),
    output_type=CitationPlan,
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

GROUNDED_CONFIG = {
    "planning_model": "gemini-3-flash-preview",
    "extraction_model": "gemini-2.0-flash-lite",
    "articulation_model": "gemini-3-flash-preview",
    "articulation_thinking": "high",
    "max_topics": 5,
    "shallow_brave_results": 10,
    "shallow_jina_reads": 3,
    "shallow_budget_fraction": 0.30,
    "evaluate_max_topics": 8,
    "max_topic_sources": 5,
    "max_spawned_topics": 4,
    "max_total_topics": 8,
    "research_budget": 0.25,
    "brave_results": 15,
    "jina_reads": 5,
    "max_knowledge_chars": 100_000,
    "compress_target_chars": 70_000,
    "extract_max_chars": 1200,
    "fetch_max_chars": 20_000,
}

LITE_GROUNDED_CONFIG = {
    "planning_model": "gemini-3-flash-preview",
    "extraction_model": "gemini-2.0-flash-lite",
    "articulation_model": "gemini-3-flash-preview",
    "articulation_thinking": "medium",
    "articulation_prompt": LIGHT_ARTICULATION_PROMPT,
    "evaluate_prompt": LITE_EVALUATE_PROMPT,
    "planning_prompt": LITE_GROUNDED_PLANNING_PROMPT,
    "max_topics": 3,               # 3 survey searches
    "shallow_brave_results": 8,    # Brave results per shallow query
    "shallow_jina_reads": 2,       # 2 sources per survey query
    "shallow_budget_fraction": 0.35,
    "evaluate_max_topics": 5,      # 3-5 drill-down topics
    "max_topic_sources": 4,        # 4 sources per deep topic
    "max_spawned_topics": 0,
    "max_total_topics": 5,
    "max_iterations": 1,
    "research_budget": 0.15,
    "brave_results": 10,           # Deep phase Brave results
    "jina_reads": 2,               # Deep phase reads
    "max_knowledge_chars": 50_000,
    "compress_target_chars": 35_000,
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


def _parse_survey_result(text: str) -> list[TopicPlan]:
    """Parse accumulated planning text for QUERIES: ["q1", "q2", ...] directive.

    Returns a list of TopicPlan objects (one per query), or empty list if
    parsing fails. Falls back to TOPICS: parsing if QUERIES: not found.
    """
    tail = text[-4000:] if len(text) > 4000 else text

    # Strategy 1: QUERIES: [...] on one line
    match = re.search(r"QUERIES:\s*(\[.*\])", tail, re.DOTALL)

    # Strategy 2: QUERIES: followed by markdown code block
    if not match:
        match = re.search(
            r"QUERIES:\s*```(?:json)?\s*(\[.*?\])\s*```", tail, re.DOTALL,
        )

    if match:
        json_str = match.group(1)
        try:
            raw = json.loads(json_str)
            if isinstance(raw, list) and raw:
                topics = []
                for i, item in enumerate(raw):
                    if isinstance(item, str):
                        topics.append(TopicPlan(
                            id=f"t{i + 1}",
                            label=f"Survey {i + 1}",
                            description=item,
                            queries=[item],
                        ))
                if topics:
                    return topics
        except (json.JSONDecodeError, ValueError):
            logger.warning(
                "QUERIES JSON parse failed. Matched text: %s",
                repr(json_str[:500]),
            )

    # Fallback: try TOPICS: format
    topics = _parse_plan_result(text)
    if topics:
        return topics

    logger.warning(
        "No QUERIES directive found in planning output. Tail: %s",
        repr(tail[-500:]),
    )
    return []


async def _plan_survey(
    full_query: str,
    raw_query: str,
    cfg: dict,
    dispatch: Dispatch,
    budget: CostBudget,
    planning_counter: TokenCounter,
    planning_prompt: str = "",
) -> list[TopicPlan]:
    """Stream planning reasoning for a flat survey (QUERIES: format).

    Like ``_plan()`` but parses QUERIES: ["q1", "q2", ...] instead of
    the TOPICS: format. Each query becomes a single TopicPlan.
    """
    client = genai_client()

    user_msg = (
        f"QUESTION: {full_query}\n\n"
        f"Figure out what to search to survey this question."
    )

    response = await client.aio.models.generate_content_stream(
        model=cfg["planning_model"],
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=planning_prompt or GROUNDED_PLANNING_PROMPT,
        ),
    )

    full_text = ""
    emitted_len = 0
    _HOLDBACK = 300  # Shorter holdback — QUERIES is shorter than TOPICS
    async for chunk in planning_counter.counted_stream(response):
        if chunk.text:
            full_text += chunk.text
            safe_len = max(0, len(full_text) - _HOLDBACK)
            if safe_len > emitted_len:
                await dispatch(DetailEvent(
                    type="reasoning",
                    payload={"text": full_text[emitted_len:safe_len]},
                ))
                emitted_len = safe_len

    # Flush remaining text with directive stripped
    remaining = full_text[emitted_len:]
    remaining = re.sub(r"\s*QUERIES:\s*\[.*\]\s*$", "", remaining, flags=re.DOTALL)
    remaining = re.sub(
        r"\s*QUERIES:\s*```(?:json)?\s*\[.*?\]\s*```\s*$", "",
        remaining, flags=re.DOTALL,
    )
    if remaining.strip():
        await dispatch(DetailEvent(
            type="reasoning",
            payload={"text": remaining},
        ))

    # Track budget
    budget.add(
        planning_counter.input_tokens,
        planning_counter.output_tokens,
        cfg["planning_model"],
    )

    topics = _parse_survey_result(full_text)

    # Fallback: single topic with the raw user query
    if not topics:
        logger.warning("Survey planning fallback — using raw query")
        topics = [TopicPlan(
            id="t1",
            label="Survey",
            description=raw_query,
            queries=[raw_query],
        )]

    # Cap at max_topics
    topics = topics[:cfg["max_topics"]]

    logger.info(
        "Survey planning produced %d queries: %s",
        len(topics),
        [t.queries[0] for t in topics],
    )
    return topics


async def _plan(
    full_query: str,
    raw_query: str,
    cfg: dict,
    dispatch: Dispatch,
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
                await dispatch(DetailEvent(
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
        await dispatch(DetailEvent(
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


async def _reconsider(
    raw_query: str,
    topics: list[TopicPlan],
    cfg: dict,
    budget: CostBudget,
    planning_counter: TokenCounter,
    dispatch: Dispatch | None = None,
    output_format: Literal["topics", "queries"] = "topics",
) -> list[TopicPlan]:
    """Review planned topics and revise if they don't serve the user's goal.

    Single-pass reconsideration: the reviewer sees ONLY the original query
    and the thread list (not the reasoning LLM's full analysis) to avoid
    being biased by the reasoning's framing.

    When ``dispatch`` is provided and REVISE is triggered, the reviewer's
    feedback and revision reasoning are streamed as DetailEvent("reasoning")
    so the user sees the course correction as a natural internal monologue.

    Returns revised topics if the reviewer says REVISE, otherwise the
    original topics unchanged.
    """
    client = genai_client()
    current_year = datetime.now(timezone.utc).year

    # Format thread list for the reviewer
    thread_list = "\n".join(
        f"- {t.label}: {t.description}" for t in topics
    )

    user_msg = (
        f"THE CURRENT YEAR IS {current_year}.\n\n"
        f"USER QUERY: {raw_query}\n\n"
        f"PROPOSED RESEARCH THREADS:\n{thread_list}"
    )

    reconsider_prompt = (
        SURVEY_RECONSIDERATION_PROMPT if output_format == "queries"
        else RECONSIDERATION_PROMPT
    )

    response = await client.aio.models.generate_content(
        model=cfg["planning_model"],
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=reconsider_prompt,
        ),
    )

    # Track tokens
    if response.usage_metadata:
        meta = response.usage_metadata
        input_tokens = meta.prompt_token_count or 0
        output_tokens = meta.candidates_token_count or 0
        planning_counter.input_tokens += input_tokens
        planning_counter.output_tokens += output_tokens
        budget.add(input_tokens, output_tokens, cfg["planning_model"])

    review_text = (response.text or "").strip()
    logger.info("Reconsideration response: %s", review_text[:200])

    # If approved, pass topics through unchanged
    if "APPROVED" in review_text.upper() and "REVISE" not in review_text.upper():
        logger.info("Reconsideration: APPROVED — topics unchanged")
        return topics

    # REVISE: ask the planning model to produce revised topics
    logger.info("Reconsideration: REVISE — regenerating topics")

    if dispatch:
        await dispatch(DetailEvent(
            type="reasoning",
            payload={"text": "\n\n---\n\n"},
        ))

    if output_format == "queries":
        directive_example = 'QUERIES: ["revised query 1", "revised query 2", ...]'
        directive_name = "QUERIES"
    else:
        directive_example = (
            'TOPICS: [{"label": "...", "description": "...", '
            '"queries": ["..."]}]'
        )
        directive_name = "TOPICS"

    revision_prompt = (
        f"THE CURRENT YEAR IS {current_year}. Use {current_year} in any "
        f"date-sensitive queries, never prior years.\n\n"
        f"QUESTION: {raw_query}\n\n"
        f"ORIGINAL RESEARCH THREADS:\n{thread_list}\n\n"
        f"You just reviewed these threads and realized they have problems. "
        f"Your review:\n{review_text}\n\n"
        f"Think through what needs to change in 2-3 sentences — what did "
        f"the original plan miss or get wrong? Then output revised searches.\n\n"
        f"{directive_example}"
    )

    revision_system = (
        "You are a research planning engine mid-thought. You just realized "
        "your initial research plan has a problem. Continue your internal "
        "monologue — explain what you caught and how you're adjusting, "
        f"then output a revised {directive_name} directive. Write in first "
        "person as a continuation of your earlier reasoning (e.g., 'I think "
        "the threads are missing...' or 'Actually, looking at this again...'). "
        f"Keep reasoning to 2-3 sentences, then the {directive_name}: line.\n\n"
        f"CRITICAL — DATES: The current year is {current_year}. This is NOT "
        f"speculative — it IS {current_year} right now. Any query that needs "
        f"a year MUST use {current_year}. NEVER use 2025, 2024, or any prior "
        f"year unless the user explicitly asked about that year."
    )

    if dispatch:
        # Stream the revision reasoning
        revision_response = await client.aio.models.generate_content_stream(
            model=cfg["planning_model"],
            contents=revision_prompt,
            config=GenerateContentConfig(system_instruction=revision_system),
        )

        revision_text = ""
        emitted_len = 0
        _HOLDBACK = 500
        async for chunk in planning_counter.counted_stream(revision_response):
            if chunk.text:
                revision_text += chunk.text
                safe_len = max(0, len(revision_text) - _HOLDBACK)
                if safe_len > emitted_len:
                    await dispatch(DetailEvent(
                        type="reasoning",
                        payload={"text": revision_text[emitted_len:safe_len]},
                    ))
                    emitted_len = safe_len

        # Flush remaining with directive stripped
        remaining = revision_text[emitted_len:]
        remaining = re.sub(
            rf"\s*{directive_name}:\s*\[.*\]\s*$", "", remaining, flags=re.DOTALL,
        )
        remaining = re.sub(
            rf"\s*{directive_name}:\s*```(?:json)?\s*\[.*?\]\s*```\s*$", "",
            remaining, flags=re.DOTALL,
        )
        if remaining.strip():
            await dispatch(DetailEvent(
                type="reasoning",
                payload={"text": remaining},
            ))

        budget.add(
            planning_counter.input_tokens,
            planning_counter.output_tokens,
            cfg["planning_model"],
        )
    else:
        # Silent revision (original behavior)
        revision_response = await client.aio.models.generate_content(
            model=cfg["planning_model"],
            contents=revision_prompt,
            config=GenerateContentConfig(system_instruction=revision_system),
        )

        if revision_response.usage_metadata:
            meta = revision_response.usage_metadata
            input_tokens = meta.prompt_token_count or 0
            output_tokens = meta.candidates_token_count or 0
            planning_counter.input_tokens += input_tokens
            planning_counter.output_tokens += output_tokens
            budget.add(input_tokens, output_tokens, cfg["planning_model"])

        revision_text = revision_response.text or ""

    if output_format == "queries":
        revised_topics = _parse_survey_result(revision_text)
    else:
        revised_topics = _parse_plan_result(revision_text)

    if revised_topics:
        revised_topics = revised_topics[:cfg["max_topics"]]
        logger.info(
            "Reconsideration produced %d revised topics: %s",
            len(revised_topics),
            [t.label for t in revised_topics],
        )
        return revised_topics

    # If parsing failed, fall back to original topics
    logger.warning("Reconsideration revision parsing failed — keeping original topics")
    return topics


# ---------------------------------------------------------------------------
# Shallow research evaluation (grounded pipeline)
# ---------------------------------------------------------------------------


async def _evaluate_shallow_research(
    raw_query: str,
    knowledge: KnowledgeState,
    cfg: dict,
    dispatch: Dispatch,
    budget: CostBudget,
    planning_counter: TokenCounter,
) -> list[TopicPlan]:
    """Evaluate shallow research findings and produce deep research topics.

    A single holistic LLM call that receives the full KnowledgeState from
    shallow research (formatted by thread + source list), streams reasoning
    as DetailEvent(type="reasoning"), and parses TOPICS: directive for
    deep iteration.
    """
    client = genai_client()

    user_msg = (
        f"QUESTION: {raw_query}\n\n"
        f"INITIAL RESEARCH FINDINGS:\n{knowledge.format_by_thread()}\n\n"
        f"SOURCES CONSULTED:\n{knowledge.format_source_list()}\n\n"
        f"Based on these initial findings, reason through what needs deeper "
        f"investigation and produce research topics for the deep phase."
    )

    response = await client.aio.models.generate_content_stream(
        model=cfg["planning_model"],
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=cfg.get("evaluate_prompt", EVALUATE_PROMPT),
        ),
    )

    full_text = ""
    emitted_len = 0
    _HOLDBACK = 500
    async for chunk in planning_counter.counted_stream(response):
        if chunk.text:
            full_text += chunk.text
            safe_len = max(0, len(full_text) - _HOLDBACK)
            if safe_len > emitted_len:
                await dispatch(DetailEvent(
                    type="reasoning",
                    payload={"text": full_text[emitted_len:safe_len]},
                ))
                emitted_len = safe_len

    # Flush remaining text with directive stripped
    remaining = full_text[emitted_len:]
    remaining = re.sub(r"\s*TOPICS:\s*\[.*\]\s*$", "", remaining, flags=re.DOTALL)
    remaining = re.sub(
        r"\s*TOPICS:\s*```(?:json)?\s*\[.*?\]\s*```\s*$", "",
        remaining, flags=re.DOTALL,
    )
    if remaining.strip():
        await dispatch(DetailEvent(
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

    # Fallback: single topic with the raw user query
    if not topics:
        logger.warning("Evaluate fallback triggered — using raw query as single topic")
        topics = [TopicPlan(
            id="t1",
            label="Deep Research",
            description=raw_query,
            queries=[raw_query],
        )]

    # Cap at evaluate_max_topics
    max_topics = cfg.get("evaluate_max_topics", cfg["max_topics"])
    topics = topics[:max_topics]

    logger.info(
        "Evaluate produced %d deep topics: %s",
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
    dispatch: Dispatch,
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
    await dispatch(DetailEvent(
        type="research",
        payload={"topic": topic.label},
    ))

    # --- Brave search ---
    await dispatch(DetailEvent(
        type="search",
        payload={"topic": topic.label, "query": query_text},
    ))

    try:
        results = await _brave_search(
            query_text, brave_api_key, count=cfg["brave_results"],
        )
        await dispatch(DetailEvent(
            type="search_done",
            payload={
                "topic": topic.label,
                "query": query_text,
                "num_results": len(results),
            },
        ))
    except Exception:
        await dispatch(DetailEvent(
            type="search_done",
            payload={
                "topic": topic.label,
                "query": query_text,
                "num_results": 0,
            },
        ))
        await dispatch(DetailEvent(
            type="result",
            payload={
                "topic": topic.label,
                "urls": [],
                "num_sources": 0,
            },
        ))
        return

    # --- Pick ALL ranked candidates via LLM filtering ---
    target_sources = cfg["jina_reads"]
    entries_before = len(topic_entries)

    all_candidates = await _filter_results(
        results, query_text, knowledge, already_fetched,
        extraction_counter,
    )

    source_urls: list[str] = []
    offset = 0

    async def _extract_one(url: str, title: str, truncated: str) -> None:
        extraction_prompt = (
            f"Query: {query_text}\n\n"
            f"Document from {title} ({url}):\n{truncated}"
        )

        try:
            extract_cfg = GenerateContentConfig(
                system_instruction=EXTRACTION_PROMPT,
            )
            if "lite" not in cfg["extraction_model"]:
                extract_cfg.thinking_config = ThinkingConfig(
                    thinking_level=ThinkingLevel.MINIMAL,
                )
            extract_resp = await client.aio.models.generate_content(
                model=cfg["extraction_model"],
                contents=extraction_prompt,
                config=extract_cfg,
            )
            extraction_counter.add_from_response(extract_resp)

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

                await dispatch(DetailEvent(
                    type="fetch_done",
                    payload={
                        "topic": topic.label,
                        "url": url,
                        "content": extracted[:3000],
                        **({"usage": usage_dict} if usage_dict else {}),
                    },
                ))
            else:
                await dispatch(DetailEvent(
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
            await dispatch(DetailEvent(
                type="fetch_done",
                payload={
                    "topic": topic.label,
                    "url": url,
                    "failed": True,
                },
            ))

    # --- Iterate through ranked candidates in batches ---
    while (
        offset < len(all_candidates)
        and (len(topic_entries) - entries_before) < target_sources
        and not budget.exhausted
    ):
        needed = target_sources - (len(topic_entries) - entries_before)
        batch = all_candidates[offset:offset + needed]
        offset += len(batch)

        # --- Filter batch by robots.txt ---
        urls_allowed: list[tuple[str, str]] = []
        for url, title in batch:
            if db_session is not None:
                try:
                    allowed, _ = await check_url_allowed(url, db_session)
                except Exception:
                    allowed = True
                if not allowed:
                    logger.info("Blocked by robots.txt: %s", url)
                    await dispatch(DetailEvent(
                        type="fetch_done",
                        payload={
                            "topic": topic.label,
                            "url": url,
                            "failed": True,
                        },
                    ))
                    continue
            urls_allowed.append((url, title))

        if not urls_allowed:
            continue

        # Emit fetch start events
        for url, _ in urls_allowed:
            await dispatch(DetailEvent(
                type="fetch",
                payload={"topic": topic.label, "url": url},
            ))

        # --- Fetch batch in parallel via Jina (rate-limited) ---
        fetch_results = await asyncio.gather(
            *[
                _jina_fetch(url, redis_url=redis_url)
                for url, _ in urls_allowed
            ],
            return_exceptions=True,
        )

        # Separate successful fetches from failures
        docs_to_extract: list[tuple[str, str, str]] = []
        for (url, title), content in zip(urls_allowed, fetch_results):
            if isinstance(content, BaseException):
                content = None

            if not content:
                await dispatch(DetailEvent(
                    type="fetch_done",
                    payload={
                        "topic": topic.label,
                        "url": url,
                        "failed": True,
                    },
                ))
                continue

            already_fetched.add(url)
            docs_to_extract.append((url, title, content[: cfg["fetch_max_chars"]]))

        # --- Extract knowledge from batch (parallel) ---
        await asyncio.gather(
            *[_extract_one(u, t, c) for u, t, c in docs_to_extract],
            return_exceptions=True,
        )

    # Collapse research group for this query
    await dispatch(DetailEvent(
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
    dispatch: Dispatch,
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
        # Run all current queries concurrently, skipping duplicates
        batch = []
        for query_text in queries:
            q_key = query_text.strip().lower()
            if q_key in queries_searched:
                logger.info("Skipping duplicate query: %r", query_text)
                continue
            queries_searched.add(q_key)
            all_queries_used.append(query_text)

            batch.append(
                _search_and_extract_query(
                    query_text, topic, knowledge, brave_api_key,
                    already_fetched, cfg, dispatch, extraction_counter,
                    budget, topic_entries,
                    redis_url=redis_url, db_session=db_session,
                )
            )

        if batch:
            await asyncio.gather(*batch, return_exceptions=True)

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


def _build_generation_prompt(articulation_prompt: str) -> str:
    """Strip the 'End with ## Sources' line and append plan-following instructions."""
    cleaned = re.sub(r"(?m)^End with ## Sources.*$", "", articulation_prompt).rstrip()
    return (
        cleaned + "\n\n"
        "RESPONSE PLAN\n"
        "You have been given a structured outline and citation plan. Follow them:\n"
        "- Cover each section in the outline in order, using its heading.\n"
        "- Weave in the mapped citations [n] where indicated by the plan.\n"
        "- You may adjust prose flow, merge small sections, or reorder "
        "slightly for narrative quality, but do not drop sections or citations.\n"
        "- Do NOT write a ## Sources section — it will be appended automatically."
    )


async def _articulate(
    query: str,
    knowledge: KnowledgeState,
    cfg: dict,
    dispatch: Dispatch,
    counter: TokenCounter,
) -> None:
    """Stream the final cited response via outline → citations → generate pipeline."""
    extraction_model_name = cfg["extraction_model"]
    extraction_model = GoogleModel(extraction_model_name, provider=google_provider())

    # --- Step 1: Outline (non-streaming) ---
    is_lite = cfg.get("articulation_prompt") is LIGHT_ARTICULATION_PROMPT
    outline_agent = _lite_outline_agent if is_lite else _outline_agent

    outline_prompt = (
        f"QUESTION: {query}\n\n"
        f"RESEARCH THREADS:\n{knowledge.format_by_thread()}\n\n"
        "Create a structured outline for the answer."
    )

    try:
        outline_result = await outline_agent.run(
            outline_prompt, model=extraction_model,
        )
        usage = outline_result.usage()
        counter.input_tokens += usage.request_tokens or 0
        counter.output_tokens += usage.response_tokens or 0
        outline = outline_result.output
    except Exception:
        logger.warning("Outline agent failed, falling back to single-pass articulation")
        outline = ArticulationOutline(
            thesis="Answer the question based on research findings.",
            sections=[OutlineSection(heading="Analysis", key_points=["Cover all research threads"])],
            closing_direction="Summarize implications.",
        )

    # --- Step 2: Citations (non-streaming) ---
    sections_text = "\n".join(
        f"## {s.heading}\n" + "\n".join(f"- {p}" for p in s.key_points)
        for s in outline.sections
    )
    citation_prompt = (
        f"QUESTION: {query}\n\n"
        f"OUTLINE:\nThesis: {outline.thesis}\n\n{sections_text}\n\n"
        f"Closing: {outline.closing_direction}\n\n"
        f"SOURCES WITH KEY FINDINGS:\n{knowledge.format_for_prompt()}\n\n"
        "Map citations to each outline section."
    )

    try:
        citation_result = await _citation_agent.run(
            citation_prompt, model=extraction_model,
        )
        usage = citation_result.usage()
        counter.input_tokens += usage.request_tokens or 0
        counter.output_tokens += usage.response_tokens or 0
        citation_plan = citation_result.output
    except Exception:
        logger.warning("Citation agent failed, falling back to empty citation plan")
        citation_plan = CitationPlan(section_citations=[])

    # --- Step 3: Generate (streaming) ---
    # Merge outline + citation plan into a response plan
    citation_map: dict[str, list[CitationRef]] = {
        sc.heading: sc.citations for sc in citation_plan.section_citations
    }
    plan_parts = [f"THESIS: {outline.thesis}\n"]
    for section in outline.sections:
        plan_parts.append(f"## {section.heading}")
        plan_parts.append("Key points: " + "; ".join(section.key_points))
        refs = citation_map.get(section.heading, [])
        if refs:
            cite_strs = [f"[{r.source_number}] {r.fact}" for r in refs]
            plan_parts.append("Citations: " + "; ".join(cite_strs))
        plan_parts.append("")
    plan_parts.append(f"CLOSING DIRECTION: {outline.closing_direction}")
    response_plan = "\n".join(plan_parts)

    user_msg = (
        f"QUESTION: {query}\n\n"
        f"RESEARCH THREADS:\n{knowledge.format_by_thread()}\n\n"
        f"SOURCES:\n{knowledge.format_source_list()}\n\n"
        f"RESPONSE PLAN:\n{response_plan}\n\n"
        f"Write the answer following the response plan. Cite inline with [n]."
    )

    articulation_prompt = cfg.get("articulation_prompt", ARTICULATION_PROMPT)
    system_prompt = _build_generation_prompt(articulation_prompt)

    thinking_level = (
        ThinkingLevel.HIGH if cfg["articulation_thinking"] == "high"
        else ThinkingLevel.MEDIUM
    )

    client = genai_client()
    response = await client.aio.models.generate_content_stream(
        model=cfg["articulation_model"],
        contents=user_msg,
        config=GenerateContentConfig(
            system_instruction=system_prompt,
            thinking_config=ThinkingConfig(thinking_level=thinking_level),
        ),
    )

    async for chunk in counter.counted_stream(response):
        if chunk.text:
            await dispatch(TextEvent(text=chunk.text))

    # --- Step 4: Deterministic Sources footer ---
    source_list = knowledge.format_source_list()
    if source_list:
        await dispatch(TextEvent(text=f"\n\n## Sources\n{source_list}"))


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
            compress_cfg = GenerateContentConfig()
            if "lite" not in extraction_model:
                compress_cfg.thinking_config = ThinkingConfig(
                    thinking_level=ThinkingLevel.MINIMAL,
                )
            response = await client.aio.models.generate_content(
                model=extraction_model,
                contents=compress_prompt,
                config=compress_cfg,
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


class ResearchPipeline:
    """Standalone research pipeline component.

    Takes a query and an event dispatch callable, then runs the full
    plan → research → articulate pipeline, emitting tool uses and text
    chunks through the dispatch.

    Can be pulled out, tested independently with a mock dispatch, or
    swapped for a different implementation at runtime.

    Usage::

        collected = []
        pipeline = ResearchPipeline(
            query="How does TCP work?",
            dispatch=lambda event: collected.append(event),
            brave_api_key="...",
        )
        await pipeline.run()
    """

    def __init__(
        self,
        query: str,
        dispatch: Dispatch,
        *,
        brave_api_key: str,
        db_session=None,
        redis_url: str = "",
        user_timezone: str = "",
        conversation_history: list[dict] | None = None,
        config: dict | None = None,
        planning_prompt: str = "",
    ) -> None:
        self.query = query
        self.dispatch = dispatch
        self.brave_api_key = brave_api_key
        self.db_session = db_session
        self.redis_url = redis_url
        self.user_timezone = user_timezone
        self.conversation_history = conversation_history
        self.config = config or CONFIG
        self.planning_prompt = planning_prompt

        # Pipeline state — accessible for inspection/testing after run()
        self.knowledge = KnowledgeState()
        self.already_fetched: set[str] = set()
        self.queries_searched: set[str] = set()
        self.budget = CostBudget(limit=self.config["research_budget"])
        self.planning_counter = TokenCounter()
        self.extraction_counter = TokenCounter()
        self.articulation_counter = TokenCounter()

    def _build_full_query(self) -> str:
        """Build the full query with timestamp preamble and history."""
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
        """Execute the full pipeline, emitting events through dispatch.

        Phases:
          1. PLAN — decompose query into research topics
          2. RESEARCH — investigate all topics concurrently
          3. ARTICULATE — synthesize findings into a response

        All events (StageEvent, DetailEvent, TextEvent, DoneEvent,
        ErrorEvent) are emitted via ``self.dispatch``.
        """
        cfg = self.config
        full_query = self._build_full_query()

        try:
            # --- Phase 1: PLAN ---
            await self.dispatch(StageEvent(stage="reasoning"))

            topics = await _plan(
                full_query, self.query, cfg, self.dispatch, self.budget,
                self.planning_counter,
                planning_prompt=self.planning_prompt,
            )

            # --- Phase 1b: RECONSIDER ---
            topics = await _reconsider(
                self.query, topics, cfg, self.budget, self.planning_counter,
            )

            # --- Phase 2: RESEARCH (wave 1 — all topics concurrent) ---
            await self.dispatch(StageEvent(stage="researching"))

            wave1_results = await asyncio.gather(
                *[
                    _research_topic(
                        topic, self.knowledge, self.brave_api_key,
                        self.already_fetched, self.queries_searched, cfg,
                        self.dispatch, self.extraction_counter, self.budget,
                        redis_url=self.redis_url, db_session=self.db_session,
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
            if spawned and not self.budget.exhausted:
                logger.info(
                    "Launching wave 2 with %d spawned topics: %s",
                    len(spawned),
                    [s.label for s in spawned],
                )
                await asyncio.gather(
                    *[
                        _research_topic(
                            topic, self.knowledge, self.brave_api_key,
                            self.already_fetched, self.queries_searched, cfg,
                            self.dispatch, self.extraction_counter, self.budget,
                            redis_url=self.redis_url, db_session=self.db_session,
                        )
                        for topic in spawned
                    ],
                    return_exceptions=True,
                )

            # --- Compress if needed ---
            if self.knowledge.needs_compression(cfg["max_knowledge_chars"]):
                await _compress_knowledge(
                    self.knowledge,
                    cfg["compress_target_chars"],
                    cfg["extraction_model"],
                    self.extraction_counter,
                )

            # --- Phase 3: ARTICULATE (always runs regardless of budget) ---
            await _articulate(
                full_query, self.knowledge, cfg, self.dispatch,
                self.articulation_counter,
            )

            # --- USAGE ---
            planning_cost = calc_usage_cost(
                self.planning_counter.input_tokens,
                self.planning_counter.output_tokens,
                cfg["planning_model"],
            )
            extraction_cost = calc_usage_cost(
                self.extraction_counter.input_tokens,
                self.extraction_counter.output_tokens,
                cfg["extraction_model"],
            )

            research_in = (
                self.planning_counter.input_tokens
                + self.extraction_counter.input_tokens
            )
            research_out = (
                self.planning_counter.output_tokens
                + self.extraction_counter.output_tokens
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
                self.articulation_counter.input_tokens,
                self.articulation_counter.output_tokens,
                cfg["articulation_model"],
            )

            total_in = research_in + self.articulation_counter.input_tokens
            total_out = research_out + self.articulation_counter.output_tokens
            total_input_cost = (
                research_input_cost + float(articulation_cost["input_cost"])
            )
            total_output_cost = (
                research_output_cost + float(articulation_cost["output_cost"])
            )

            await self.dispatch(DetailEvent(
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
                        "limit": self.budget.limit,
                        "spent": round(self.budget.spent, 4),
                    },
                },
            ))

            await self.dispatch(DoneEvent())

        except Exception as exc:
            logger.exception("Research pipeline error")
            await self.dispatch(ErrorEvent(error=str(exc)))


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

    Thin wrapper around ``ResearchPipeline`` that bridges the dispatch
    callable to an async generator for backward compatibility.

    Pass ``config_override`` and ``planning_prompt_override`` to run a
    lighter variant (e.g. the basic researcher uses LIGHT_CONFIG).
    """
    event_queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()

    pipeline = ResearchPipeline(
        query,
        event_queue.put,
        brave_api_key=brave_api_key,
        db_session=db_session,
        redis_url=redis_url,
        user_timezone=user_timezone,
        conversation_history=conversation_history,
        config=config_override,
        planning_prompt=planning_prompt_override or "",
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


class GroundedResearchPipeline:
    """Research pipeline that pushes boundaries after an initial research pass.

    Flow: Plan → Align → Shallow Research → Evaluate → Deep Iterate → Synthesize

    Unlike ``ResearchPipeline`` which speculatively generates creative angles
    before any research, this pipeline first runs a conservative shallow pass
    and then uses the actual findings to inform deeper exploration.
    """

    def __init__(
        self,
        query: str,
        dispatch: Dispatch,
        *,
        brave_api_key: str,
        db_session=None,
        redis_url: str = "",
        user_timezone: str = "",
        conversation_history: list[dict] | None = None,
        config: dict | None = None,
        planning_prompt: str = "",
    ) -> None:
        self.query = query
        self.dispatch = dispatch
        self.brave_api_key = brave_api_key
        self.db_session = db_session
        self.redis_url = redis_url
        self.user_timezone = user_timezone
        self.conversation_history = conversation_history
        self.config = config or GROUNDED_CONFIG
        self.planning_prompt = (
            planning_prompt
            or self.config.get("planning_prompt", "")
            or GROUNDED_PLANNING_PROMPT
        )

        # Pipeline state
        self.knowledge = KnowledgeState()
        self.already_fetched: set[str] = set()
        self.queries_searched: set[str] = set()
        self.budget = CostBudget(limit=self.config["research_budget"])
        self.planning_counter = TokenCounter()
        self.extraction_counter = TokenCounter()
        self.articulation_counter = TokenCounter()

    def _build_full_query(self) -> str:
        """Build the full query with timestamp preamble and history."""
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

    async def _run_shallow_research(
        self, topics: list[TopicPlan],
    ) -> None:
        """Single-pass research across all topic queries with reduced limits.

        Uses shallow_brave_results and shallow_jina_reads config values.
        Runs all queries concurrently via asyncio.gather().
        """
        cfg = self.config
        shallow_cfg = dict(cfg)
        shallow_cfg["brave_results"] = cfg.get("shallow_brave_results", 10)
        shallow_cfg["jina_reads"] = cfg.get("shallow_jina_reads", 3)

        topic_entries: list[KnowledgeEntry] = []

        # Collect unique queries across all topics
        tasks = []
        for topic in topics:
            for query_text in topic.queries:
                q_key = query_text.strip().lower()
                if q_key in self.queries_searched:
                    continue
                self.queries_searched.add(q_key)

                tasks.append(
                    _search_and_extract_query(
                        query_text, topic, self.knowledge, self.brave_api_key,
                        self.already_fetched, shallow_cfg, self.dispatch,
                        self.extraction_counter, self.budget, topic_entries,
                        redis_url=self.redis_url, db_session=self.db_session,
                    )
                )

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self) -> None:
        """Execute the grounded pipeline.

        Phases:
          1. PLAN — conservative decomposition
          2. SHALLOW RESEARCH — initial survey pass
          3. EVALUATE — analyze findings, produce deep topics
          4. DEEP RESEARCH — investigate evaluate's topics concurrently
          5. COMPRESS — if needed
          6. ARTICULATE — synthesize into response
        """
        cfg = self.config
        full_query = self._build_full_query()

        try:
            # --- Phase 1: PLAN (survey — flat queries) ---
            await self.dispatch(StageEvent(stage="reasoning"))

            topics = await _plan_survey(
                full_query, self.query, cfg, self.dispatch, self.budget,
                self.planning_counter,
                planning_prompt=self.planning_prompt,
            )

            topics = await _reconsider(
                self.query, topics, cfg, self.budget, self.planning_counter,
                dispatch=self.dispatch, output_format="queries",
            )

            # --- Phase 2: SHALLOW RESEARCH ---
            await self.dispatch(StageEvent(stage="researching"))

            await self._run_shallow_research(topics)

            # --- Phase 3: EVALUATE ---
            await self.dispatch(StageEvent(stage="reasoning"))

            deep_topics = await _evaluate_shallow_research(
                self.query, self.knowledge, cfg, self.dispatch,
                self.budget, self.planning_counter,
            )

            deep_topics = await _reconsider(
                self.query, deep_topics, cfg, self.budget, self.planning_counter,
                dispatch=self.dispatch,
            )

            # --- Phase 4: DEEP RESEARCH ---
            await self.dispatch(StageEvent(stage="researching"))

            wave1_results = await asyncio.gather(
                *[
                    _research_topic(
                        topic, self.knowledge, self.brave_api_key,
                        self.already_fetched, self.queries_searched, cfg,
                        self.dispatch, self.extraction_counter, self.budget,
                        redis_url=self.redis_url, db_session=self.db_session,
                    )
                    for topic in deep_topics
                ],
                return_exceptions=True,
            )

            # Collect spawned topics
            spawned: list[TopicPlan] = []
            total_topics = len(deep_topics)
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

            # Wave 2: spawned topics
            if spawned and not self.budget.exhausted:
                await asyncio.gather(
                    *[
                        _research_topic(
                            topic, self.knowledge, self.brave_api_key,
                            self.already_fetched, self.queries_searched, cfg,
                            self.dispatch, self.extraction_counter, self.budget,
                            redis_url=self.redis_url, db_session=self.db_session,
                        )
                        for topic in spawned
                    ],
                    return_exceptions=True,
                )

            # --- Phase 5: COMPRESS ---
            if self.knowledge.needs_compression(cfg["max_knowledge_chars"]):
                await _compress_knowledge(
                    self.knowledge,
                    cfg["compress_target_chars"],
                    cfg["extraction_model"],
                    self.extraction_counter,
                )

            # --- Phase 6: ARTICULATE ---
            await _articulate(
                full_query, self.knowledge, cfg, self.dispatch,
                self.articulation_counter,
            )

            # --- USAGE ---
            planning_cost = calc_usage_cost(
                self.planning_counter.input_tokens,
                self.planning_counter.output_tokens,
                cfg["planning_model"],
            )
            extraction_cost = calc_usage_cost(
                self.extraction_counter.input_tokens,
                self.extraction_counter.output_tokens,
                cfg["extraction_model"],
            )

            research_in = (
                self.planning_counter.input_tokens
                + self.extraction_counter.input_tokens
            )
            research_out = (
                self.planning_counter.output_tokens
                + self.extraction_counter.output_tokens
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
                self.articulation_counter.input_tokens,
                self.articulation_counter.output_tokens,
                cfg["articulation_model"],
            )

            total_in = research_in + self.articulation_counter.input_tokens
            total_out = research_out + self.articulation_counter.output_tokens
            total_input_cost = (
                research_input_cost + float(articulation_cost["input_cost"])
            )
            total_output_cost = (
                research_output_cost + float(articulation_cost["output_cost"])
            )

            await self.dispatch(DetailEvent(
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
                        "limit": self.budget.limit,
                        "spent": round(self.budget.spent, 4),
                    },
                },
            ))

            await self.dispatch(DoneEvent())

        except Exception as exc:
            logger.exception("Grounded research pipeline error")
            await self.dispatch(ErrorEvent(error=str(exc)))


async def run_grounded_research_pipeline(
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
    """Run the grounded research pipeline, yielding SSE-compatible events.

    Thin wrapper around ``GroundedResearchPipeline`` that bridges the dispatch
    callable to an async generator.

    Pass ``config_override`` and ``planning_prompt_override`` to run a
    lighter variant (e.g. LITE_GROUNDED_CONFIG).
    """
    event_queue: asyncio.Queue[PipelineEvent] = asyncio.Queue()

    pipeline = GroundedResearchPipeline(
        query,
        event_queue.put,
        brave_api_key=brave_api_key,
        db_session=db_session,
        redis_url=redis_url,
        user_timezone=user_timezone,
        conversation_history=conversation_history,
        config=config_override,
        planning_prompt=planning_prompt_override or "",
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
