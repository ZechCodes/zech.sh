"""Eval suite for research output quality.

Runs the research pipeline against a set of test queries, then applies
structural checks and LLM-as-judge content quality scoring.

Usage:
    uv run python tests/eval_research.py                  # all queries, both modes
    uv run python tests/eval_research.py --mode lite      # lite mode only
    uv run python tests/eval_research.py --mode deep      # deep mode only
    uv run python tests/eval_research.py --no-judge       # structural checks only, skip LLM judge
    uv run python tests/eval_research.py --judge-only     # re-judge cached outputs
    uv run python tests/eval_research.py --trials 3       # multiple trials per query
    uv run python tests/eval_research.py --model gemini-2.5-flash  # test a different model
    uv run python tests/eval_research.py --hybrid gpt-5.4-nano    # nano researches, flash synthesizes
"""

import argparse
import asyncio
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydantic import BaseModel
from pydantic_ai import Agent

from controllers.deep_research_agent import (
    ARTICULATION_PROMPT,
    DoneEvent,
    ErrorEvent,
    KnowledgeState,
    LIGHT_ARTICULATION_PROMPT,
    StageEvent,
    TextEvent,
)
from controllers.llm import gemini_flash, google_provider
from controllers.research_agent import (
    AgentResearchPipeline,
    _MODE_CONFIG,
    run_agent_research_pipeline,
)
from tests.research_checks import run_structural_checks

# ── Model configuration ──────────────────────────────────────────────────

# Default: use whatever the pipeline uses (gemini-3-flash-preview).
# Override with --model to test other models.
DEFAULT_AGENT_MODEL = None  # None = use pipeline default


def _is_openai_model(model_name: str) -> bool:
    """Check if a model name is an OpenAI model."""
    return model_name.startswith(("gpt-", "o1", "o3", "o4"))


def _make_model_fn(model_name: str):
    """Create a cached model factory for a given model name."""
    from functools import lru_cache

    if _is_openai_model(model_name):
        from pydantic_ai.models.openai import OpenAIChatModel
        from pydantic_ai.providers.openai import OpenAIProvider

        @lru_cache(maxsize=1)
        def openai_model_fn() -> OpenAIChatModel:
            return OpenAIChatModel(model_name, provider=OpenAIProvider(api_key=os.environ["OPENAI_API_KEY"]))

        return openai_model_fn
    else:
        from pydantic_ai.models.google import GoogleModel

        @lru_cache(maxsize=1)
        def google_model_fn() -> GoogleModel:
            return GoogleModel(model_name, provider=google_provider())

        return google_model_fn


def apply_model_override(model_name: str) -> None:
    """Patch _MODE_CONFIG to use a custom agent model for all modes."""
    model_fn = _make_model_fn(model_name)
    for mode_cfg in _MODE_CONFIG.values():
        mode_cfg["agent_model_fn"] = model_fn
    print(f"  Model override: {model_name}")

# ── Output directory ──────────────────────────────────────────────────────

EVAL_OUTPUTS_DIR = Path(__file__).parent / "eval_outputs"

# ── Test cases ────────────────────────────────────────────────────────────

EVAL_CASES = [
    # (query, mode, description)
    # Lite — quick factual queries
    (
        "What is the current mass of the Greenland ice sheet and how fast is it losing ice?",
        "lite",
        "factual-with-numbers",
    ),
    (
        "SQLite WAL mode vs default journal mode",
        "lite",
        "technical-comparison",
    ),
    (
        "How does mRNA vaccine technology work?",
        "lite",
        "explainer",
    ),
    # Deep — complex synthesis
    (
        "Compare Rust, Go, and Zig for systems programming in 2026",
        "deep",
        "multi-way-comparison",
    ),
    (
        "What are the economic and environmental tradeoffs of nuclear vs solar energy?",
        "deep",
        "contested-topic",
    ),
    (
        "How has the EU AI Act affected AI startups in Europe since it took effect?",
        "deep",
        "recent-events-policy",
    ),
]

# ── Data models ───────────────────────────────────────────────────────────


@dataclass
class SearchRecord:
    """Tracks a single research() tool call and what it yielded."""
    search_query: str
    urls_fetched: list[str] = field(default_factory=list)
    read_attempts: int = 0  # total fetch attempts (success + failure)
    read_successes: int = 0  # fetches that returned content
    num_sources_added: int = 0
    cited_in_output: list[int] = field(default_factory=list)  # which [n] citations used these sources
    cumulative_reads_before: int = 0  # how many reads had completed before this search started


@dataclass
class EvalResult:
    query: str
    mode: str
    description: str
    markdown: str
    source_count: int
    latency_s: float
    cost_usd: float
    tool_calls: int = 0
    searches: list[SearchRecord] = field(default_factory=list)
    structural_checks: dict[str, bool] = field(default_factory=dict)
    judge_scores: dict[str, float | str] = field(default_factory=dict)


class JudgeScores(BaseModel):
    """LLM judge output schema."""

    relevance: int
    factual_grounding: int
    completeness: int
    writing_quality: int
    reasoning: str


# ── LLM Judge ─────────────────────────────────────────────────────────────

JUDGE_SYSTEM_PROMPT = """\
You are evaluating the quality of a research response. The user asked a \
question, and a research system produced a response with inline citations \
and a sources section. Score each dimension 1-5.

RELEVANCE (1-5): Does the response directly address the user's query?
  5 = Precisely answers what was asked, addresses the core question
  3 = Addresses the topic but misses the specific question asked
  1 = Off-topic or only tangentially related

FACTUAL GROUNDING (1-5): Are factual claims backed by citations?
  5 = Every claim cited, sources appear credible, numbers have context
  3 = Most claims cited but some unsupported assertions
  1 = Few or no citations, or citations appear mismatched

COMPLETENESS (1-5): Does it cover the key aspects a knowledgeable person would expect?
  5 = Covers main points, counterarguments, and relevant nuances
  3 = Covers the basics but misses important dimensions
  1 = Superficial or misses the core of the topic

WRITING QUALITY (1-5): Is it well-written, argued prose (not a listicle)?
  5 = Compelling narrative, concrete details, appropriate depth and length
  3 = Readable but generic, or uneven depth across sections
  1 = Listicle, filler-heavy, or poorly structured

Be strict but fair. A score of 4 means "good, minor issues". A score of 5 \
means "excellent, hard to improve". Provide brief reasoning.\
"""

judge_agent = Agent(
    model=gemini_flash(),
    system_prompt=JUDGE_SYSTEM_PROMPT,
    output_type=JudgeScores,
)


async def run_judge(query: str, markdown: str) -> dict[str, float | str]:
    """Score a research result using the LLM judge."""
    prompt = f"QUERY: {query}\n\nRESEARCH RESPONSE:\n{markdown}"
    result = await judge_agent.run(prompt)
    scores = result.output
    return {
        "relevance": scores.relevance,
        "factual_grounding": scores.factual_grounding,
        "completeness": scores.completeness,
        "writing_quality": scores.writing_quality,
        "reasoning": scores.reasoning,
    }


# ── Pipeline runner ───────────────────────────────────────────────────────


async def run_pipeline(query: str, mode: str) -> tuple[str, int, float, float, int, list[SearchRecord]]:
    """Run the research pipeline and return (markdown, source_count, latency_s, cost_usd, tool_calls, searches)."""
    brave_key = os.environ["BRAVE_API_KEY"]

    text_parts: list[str] = []
    cost_usd = 0.0
    source_count = 0
    tool_calls = 0
    searches: list[SearchRecord] = []
    current_search: SearchRecord | None = None
    # Track source index ranges per search to map citations later
    source_index_before = 0
    source_index_ranges: list[tuple[str, int, int]] = []  # (search_query, start_idx, end_idx)
    total_reads_so_far = 0  # cumulative reads across all searches

    start = time.monotonic()
    async for event in run_agent_research_pipeline(
        query, brave_key, mode=mode
    ):
        if isinstance(event, TextEvent):
            text_parts.append(event.text)
        elif isinstance(event, ErrorEvent):
            raise RuntimeError(f"Pipeline error: {event.error}")
        elif hasattr(event, "type") and hasattr(event, "payload"):
            # DetailEvent
            if event.type == "usage":
                payload = event.payload
                cost_usd = sum(
                    float(payload.get(k, 0))
                    for k in ("input_cost", "output_cost")
                )
            elif event.type == "research":
                # New research() tool call starting
                current_search = SearchRecord(
                    search_query=event.payload.get("topic", ""),
                    cumulative_reads_before=total_reads_so_far,
                )
                source_index_before = source_count
            elif event.type == "fetch_done":
                if current_search:
                    current_search.read_attempts += 1
                    url = event.payload.get("url", "")
                    failed = event.payload.get("failed", False)
                    if url and not failed:
                        current_search.urls_fetched.append(url)
                        current_search.read_successes += 1
            elif event.type == "result":
                num_new = event.payload.get("num_sources", 0)
                if current_search:
                    current_search.num_sources_added = num_new
                    total_reads_so_far += current_search.read_successes
                    searches.append(current_search)
                    if num_new > 0:
                        source_index_ranges.append((
                            current_search.search_query,
                            source_count + 1,  # 1-indexed
                            source_count + num_new,
                        ))
                    source_count += num_new
                    current_search = None
            elif event.type == "tool_call":
                tool_calls += 1
        elif isinstance(event, DoneEvent):
            break
    latency_s = time.monotonic() - start

    markdown = "".join(text_parts)

    # Map citations in the final output back to searches
    from tests.research_checks import parse_body_and_sources, parse_citations
    body, _ = parse_body_and_sources(markdown)
    cited_numbers = parse_citations(body)
    for search_query, start_idx, end_idx in source_index_ranges:
        for s in searches:
            if s.search_query == search_query:
                s.cited_in_output = [n for n in range(start_idx, end_idx + 1) if n in cited_numbers]
                break

    return markdown, source_count, latency_s, cost_usd, tool_calls, searches


# ── Hybrid pipeline (research with one model, synthesize with another) ────


async def run_hybrid_pipeline(
    query: str,
    mode: str,
    research_model: str,
    synthesis_model: str = "gemini-3-flash-preview",
) -> tuple[str, int, float, float, int, list[SearchRecord]]:
    """Run research with one model, then synthesize with another.

    Returns (markdown, source_count, latency_s, cost_usd, tool_calls, searches).
    """
    import asyncio

    brave_key = os.environ["BRAVE_API_KEY"]

    # --- Phase 1: Research with the cheap/fast model ---
    # Temporarily override the model for research
    original_fn = _MODE_CONFIG[mode]["agent_model_fn"]
    _MODE_CONFIG[mode]["agent_model_fn"] = _make_model_fn(research_model)

    # Collect events and provenance during research
    cost_usd = 0.0
    source_count = 0
    tool_calls = 0
    searches: list[SearchRecord] = []
    current_search: SearchRecord | None = None
    source_index_before = 0
    source_index_ranges: list[tuple[str, int, int]] = []
    total_reads_so_far = 0

    start = time.monotonic()

    # Create pipeline directly so we can access knowledge after research
    event_queue: asyncio.Queue = asyncio.Queue()
    pipeline = AgentResearchPipeline(
        query,
        event_queue.put,
        brave_api_key=brave_key,
        mode=mode,
    )

    # Run the pipeline (Nano does research + writes its own answer)
    task = asyncio.create_task(pipeline.run())

    nano_text_parts: list[str] = []
    while True:
        event = await event_queue.get()
        if isinstance(event, TextEvent):
            nano_text_parts.append(event.text)
        elif isinstance(event, ErrorEvent):
            _MODE_CONFIG[mode]["agent_model_fn"] = original_fn
            raise RuntimeError(f"Pipeline error: {event.error}")
        elif hasattr(event, "type") and hasattr(event, "payload"):
            if event.type == "usage":
                payload = event.payload
                cost_usd = sum(
                    float(payload.get(k, 0))
                    for k in ("input_cost", "output_cost")
                )
            elif event.type == "research":
                current_search = SearchRecord(
                    search_query=event.payload.get("topic", ""),
                    cumulative_reads_before=total_reads_so_far,
                )
                source_index_before = source_count
            elif event.type == "fetch_done":
                if current_search:
                    current_search.read_attempts += 1
                    url = event.payload.get("url", "")
                    failed = event.payload.get("failed", False)
                    if url and not failed:
                        current_search.urls_fetched.append(url)
                        current_search.read_successes += 1
            elif event.type == "result":
                num_new = event.payload.get("num_sources", 0)
                if current_search:
                    current_search.num_sources_added = num_new
                    total_reads_so_far += current_search.read_successes
                    searches.append(current_search)
                    if num_new > 0:
                        source_index_ranges.append((
                            current_search.search_query,
                            source_count + 1,
                            source_count + num_new,
                        ))
                    source_count += num_new
                    current_search = None
            elif event.type == "tool_call":
                tool_calls += 1
        elif isinstance(event, (DoneEvent,)):
            break

    await task
    research_time = time.monotonic() - start

    # Restore original model
    _MODE_CONFIG[mode]["agent_model_fn"] = original_fn

    # --- Phase 2: Synthesize with Flash ---
    knowledge = pipeline.knowledge
    if not knowledge.entries:
        return "", 0, research_time, cost_usd, tool_calls, searches

    # Build synthesis prompt
    articulation = LIGHT_ARTICULATION_PROMPT if mode == "lite" else ARTICULATION_PROMPT
    source_list = knowledge.format_source_list()
    knowledge_dump = knowledge.format_for_prompt()

    synthesis_prompt = (
        f"{articulation}\n\n"
        f"QUESTION: {query}\n\n"
        f"ACCUMULATED RESEARCH:\n{knowledge_dump}\n\n"
        f"AVAILABLE SOURCES:\n{source_list}\n\n"
        f"Write your answer now. Cite sources as [n] inline. "
        f"End with ## Sources as [n] Title — URL."
    )

    # Use Flash for synthesis
    synthesis_model_fn = _make_model_fn(synthesis_model)
    synth_agent = Agent(
        model=synthesis_model_fn(),
        system_prompt="You are a research synthesizer.",
    )

    synth_start = time.monotonic()
    synth_result = await synth_agent.run(synthesis_prompt)
    synth_time = time.monotonic() - synth_start

    markdown = synth_result.output
    total_time = research_time + synth_time

    # Map citations back to searches
    from tests.research_checks import parse_body_and_sources, parse_citations
    body, _ = parse_body_and_sources(markdown)
    cited_numbers = parse_citations(body)
    for search_query, start_idx, end_idx in source_index_ranges:
        for s in searches:
            if s.search_query == search_query:
                s.cited_in_output = [n for n in range(start_idx, end_idx + 1) if n in cited_numbers]
                break

    return markdown, source_count, total_time, cost_usd, tool_calls, searches


# ── Caching ───────────────────────────────────────────────────────────────


def save_result(result: EvalResult, model_name: str | None = None) -> Path:
    """Save an eval result to disk."""
    EVAL_OUTPUTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    model_tag = f"_{model_name.replace('/', '-')}" if model_name else ""
    filename = f"{ts}_{result.mode}_{result.description}{model_tag}.json"
    path = EVAL_OUTPUTS_DIR / filename
    data = {
        "query": result.query,
        "mode": result.mode,
        "description": result.description,
        "markdown": result.markdown,
        "source_count": result.source_count,
        "latency_s": result.latency_s,
        "cost_usd": result.cost_usd,
        "tool_calls": result.tool_calls,
        "searches": [
            {
                "search_query": s.search_query,
                "urls_fetched": s.urls_fetched,
                "read_attempts": s.read_attempts,
                "read_successes": s.read_successes,
                "num_sources_added": s.num_sources_added,
                "cited_in_output": s.cited_in_output,
                "cumulative_reads_before": s.cumulative_reads_before,
            }
            for s in result.searches
        ],
        "model": model_name,
        "timestamp": ts,
    }
    path.write_text(json.dumps(data, indent=2))
    return path


def load_cached_results() -> list[EvalResult]:
    """Load the most recent cached result for each (mode, description) pair."""
    if not EVAL_OUTPUTS_DIR.exists():
        return []

    # Group by (mode, description), keep latest by filename (timestamp prefix)
    latest: dict[tuple[str, str], Path] = {}
    for path in sorted(EVAL_OUTPUTS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        key = (data["mode"], data["description"])
        latest[key] = path  # sorted order means last = latest

    results = []
    for path in latest.values():
        data = json.loads(path.read_text())
        results.append(
            EvalResult(
                query=data["query"],
                mode=data["mode"],
                description=data["description"],
                markdown=data["markdown"],
                source_count=data["source_count"],
                latency_s=data.get("latency_s", 0),
                cost_usd=data.get("cost_usd", 0),
                tool_calls=data.get("tool_calls", 0),
                searches=[
                    SearchRecord(
                        search_query=s["search_query"],
                        urls_fetched=s.get("urls_fetched", []),
                        read_attempts=s.get("read_attempts", 0),
                        read_successes=s.get("read_successes", 0),
                        num_sources_added=s.get("num_sources_added", 0),
                        cited_in_output=s.get("cited_in_output", []),
                        cumulative_reads_before=s.get("cumulative_reads_before", 0),
                    )
                    for s in data.get("searches", [])
                ],
            )
        )
    return results


# ── Reporting ─────────────────────────────────────────────────────────────


def print_report(results: list[EvalResult]) -> None:
    """Print a formatted evaluation report."""
    print(f"\n{'=' * 80}")
    print(f"  Research Eval Results — {len(results)} queries")
    print(f"{'=' * 80}")

    # Structural checks
    print(f"\n  STRUCTURAL CHECKS")
    print(f"  {'─' * 70}")
    print(f"  {'Query':<45} {'Pass':>6} {'Fail':>6} {'Score':>8}")
    print(f"  {'─' * 70}")

    total_pass = 0
    total_checks = 0
    for r in results:
        if not r.structural_checks:
            continue
        passed = sum(1 for v in r.structural_checks.values() if v)
        failed = len(r.structural_checks) - passed
        total_pass += passed
        total_checks += len(r.structural_checks)
        pct = 100 * passed / len(r.structural_checks) if r.structural_checks else 0
        label = f"\"{r.query[:38]}...\" ({r.mode})" if len(r.query) > 38 else f"\"{r.query}\" ({r.mode})"
        print(f"  {label:<45} {passed:>4}/{len(r.structural_checks):<2} {failed:>4}/{len(r.structural_checks):<2} {pct:>6.0f}%")

    # Print structural failures
    failures = [
        (r, name)
        for r in results
        for name, passed in r.structural_checks.items()
        if not passed
    ]
    if failures:
        print(f"\n  STRUCTURAL FAILURES ({len(failures)}):")
        for r, name in failures:
            print(f"    ✗ \"{r.query[:50]}\" ({r.mode}): {name}")

    # Judge scores (only if any results have them)
    has_judge = any(r.judge_scores for r in results)
    all_avgs = []
    if has_judge:
        print(f"\n  LLM JUDGE SCORES (1-5 scale)")
        print(f"  {'─' * 70}")
        print(f"  {'Query':<35} {'Rel':>5} {'Fact':>5} {'Comp':>5} {'Writ':>5} {'Avg':>6}")
        print(f"  {'─' * 70}")

        for r in results:
            if not r.judge_scores:
                continue
            scores = {k: v for k, v in r.judge_scores.items() if k != "reasoning"}
            avg = sum(scores.values()) / len(scores) if scores else 0
            all_avgs.append(avg)
            label = f"\"{r.query[:30]}...\"" if len(r.query) > 30 else f"\"{r.query}\""
            print(
                f"  {label:<35} "
                f"{r.judge_scores.get('relevance', '-'):>5} "
                f"{r.judge_scores.get('factual_grounding', '-'):>5} "
                f"{r.judge_scores.get('completeness', '-'):>5} "
                f"{r.judge_scores.get('writing_quality', '-'):>5} "
                f"{avg:>6.1f}"
            )

        # Print reasoning for low scores
        low_scores = [
            r
            for r in results
            if r.judge_scores
            and any(
                v < 4
                for k, v in r.judge_scores.items()
                if k != "reasoning" and isinstance(v, (int, float))
            )
        ]
        if low_scores:
            print(f"\n  LOW SCORE REASONING:")
            for r in low_scores:
                print(f"    \"{r.query[:50]}\" ({r.mode}):")
                print(f"      {r.judge_scores.get('reasoning', 'N/A')}")

    # Search provenance (only if any results have searches)
    has_searches = any(r.searches for r in results)
    if has_searches:
        print(f"\n  SEARCH → READ → CITATION PROVENANCE")
        print(f"  {'─' * 70}")
        total_searches = 0
        total_with_sources = 0
        total_with_citations = 0
        total_fruitless = 0
        total_with_reads = 0
        total_after_reads = 0  # searches that happened after at least one read
        total_after_reads_cited = 0  # of those, how many got cited
        total_read_attempts = 0
        total_read_successes = 0

        for r in results:
            if not r.searches:
                continue
            label = f"\"{r.query[:45]}...\"" if len(r.query) > 45 else f"\"{r.query}\""
            print(f"\n  {label} ({r.mode})")
            for i, s in enumerate(r.searches, 1):
                total_searches += 1
                has_sources = s.num_sources_added > 0
                has_cites = len(s.cited_in_output) > 0
                has_reads = s.read_successes > 0
                after_reads = s.cumulative_reads_before > 0

                if has_sources:
                    total_with_sources += 1
                if has_cites:
                    total_with_citations += 1
                if not has_sources:
                    total_fruitless += 1
                if has_reads:
                    total_with_reads += 1
                if after_reads:
                    total_after_reads += 1
                    if has_cites:
                        total_after_reads_cited += 1
                total_read_attempts += s.read_attempts
                total_read_successes += s.read_successes

                # Build status string
                read_info = f"{s.read_successes}/{s.read_attempts} reads"
                after_tag = f" [after {s.cumulative_reads_before} prior reads]" if after_reads else " [first]"

                if has_cites:
                    cite_str = f"→ cited [{', '.join(str(n) for n in s.cited_in_output)}]"
                elif has_sources:
                    cite_str = "→ sources found but NONE CITED"
                else:
                    cite_str = "→ NO SOURCES extracted"

                print(f"    {i}. \"{s.search_query[:45]}\" "
                      f"({read_info}, {s.num_sources_added} sources){after_tag} {cite_str}")

        print(f"\n  SEARCH & READ EFFICIENCY SUMMARY")
        print(f"  {'─' * 70}")
        print(f"  Total searches: {total_searches}")
        if total_searches:
            print(f"  Searches with reads: {total_with_reads}/{total_searches} ({100*total_with_reads/total_searches:.0f}%)")
            print(f"  Searches that produced sources: {total_with_sources}/{total_searches} ({100*total_with_sources/total_searches:.0f}%)")
            print(f"  Searches with cited results: {total_with_citations}/{total_searches} ({100*total_with_citations/total_searches:.0f}%)")
            print(f"  Fruitless searches (no sources): {total_fruitless}/{total_searches} ({100*total_fruitless/total_searches:.0f}%)")
            wasted = total_with_sources - total_with_citations
            if wasted > 0:
                print(f"  Wasted searches (sources found but uncited): {wasted}")
        print(f"\n  Total read attempts: {total_read_attempts} ({total_read_successes} succeeded, {total_read_attempts - total_read_successes} failed)")
        if total_after_reads:
            print(f"  Searches after prior reads: {total_after_reads} (cited: {total_after_reads_cited}/{total_after_reads})")
        else:
            print(f"  No searches occurred after prior reads (all searches were first-round)")

    # Aggregate
    print(f"\n  {'─' * 70}")
    if total_checks:
        print(f"  Structural pass rate: {100 * total_pass / total_checks:.0f}% ({total_pass}/{total_checks})")
    if all_avgs:
        print(f"  Mean judge score: {sum(all_avgs) / len(all_avgs):.1f} / 5.0")

    # Latency and cost by mode
    for mode in ("lite", "deep"):
        mode_results = [r for r in results if r.mode == mode and r.latency_s > 0]
        if mode_results:
            avg_lat = sum(r.latency_s for r in mode_results) / len(mode_results)
            avg_cost = sum(r.cost_usd for r in mode_results) / len(mode_results)
            print(f"  Mean latency ({mode}): {avg_lat:.1f}s  |  Mean cost ({mode}): ${avg_cost:.4f}")

    print()


# ── Main ──────────────────────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(description="Research quality eval suite")
    parser.add_argument("--mode", choices=["lite", "deep"], help="Only run one mode")
    parser.add_argument("--no-judge", action="store_true", help="Skip LLM judge, structural checks only")
    parser.add_argument("--judge-only", action="store_true", help="Re-judge cached outputs")
    parser.add_argument("--trials", type=int, default=1, help="Trials per query")
    parser.add_argument("--model", type=str, default=None, help="Model name to test (e.g. gemini-2.5-flash)")
    parser.add_argument(
        "--hybrid", type=str, default=None, metavar="RESEARCH_MODEL",
        help="Hybrid mode: use RESEARCH_MODEL for research, Flash for synthesis (e.g. --hybrid gpt-5.4-nano)",
    )
    args = parser.parse_args()

    if args.model and args.hybrid:
        print("ERROR: --model and --hybrid are mutually exclusive")
        sys.exit(1)

    if args.model:
        apply_model_override(args.model)

    cases = EVAL_CASES
    if args.mode:
        cases = [(q, m, d) for q, m, d in cases if m == args.mode]

    if args.judge_only:
        print("Loading cached results...")
        results = load_cached_results()
        if args.mode:
            results = [r for r in results if r.mode == args.mode]
        if not results:
            print("No cached results found. Run without --judge-only first.")
            return
        print(f"Loaded {len(results)} cached results")
    else:
        results = []
        for query, mode, description in cases:
            for trial in range(args.trials):
                trial_label = f" (trial {trial + 1}/{args.trials})" if args.trials > 1 else ""
                hybrid_label = f" [hybrid: {args.hybrid}→flash]" if args.hybrid else ""
                print(f"\n▶ Running: \"{query[:50]}...\" [{mode}]{trial_label}{hybrid_label}")

                try:
                    if args.hybrid:
                        markdown, source_count, latency_s, cost_usd, tool_calls, searches = await run_hybrid_pipeline(
                            query, mode, research_model=args.hybrid
                        )
                    else:
                        markdown, source_count, latency_s, cost_usd, tool_calls, searches = await run_pipeline(
                            query, mode
                        )
                    print(f"  ✓ {len(markdown.split())} words, {source_count} sources, {tool_calls} tool calls, {latency_s:.1f}s, ${cost_usd:.4f}")
                except Exception as e:
                    print(f"  ✗ Pipeline error: {e}")
                    continue

                result = EvalResult(
                    query=query,
                    mode=mode,
                    description=description,
                    markdown=markdown,
                    source_count=source_count,
                    latency_s=latency_s,
                    cost_usd=cost_usd,
                    tool_calls=tool_calls,
                    searches=searches,
                )
                model_tag = args.model or (f"{args.hybrid}+flash" if args.hybrid else None)
                save_result(result, model_name=model_tag)
                results.append(result)

    # Run structural checks
    print("\nRunning structural checks...")
    for result in results:
        result.structural_checks = run_structural_checks(result.markdown, result.mode)

    # Run judge (unless --no-judge)
    if not args.no_judge:
        print("Running LLM judge...")
        for result in results:
            try:
                result.judge_scores = await run_judge(result.query, result.markdown)
                avg = sum(
                    v for k, v in result.judge_scores.items()
                    if k != "reasoning" and isinstance(v, (int, float))
                ) / 4
                print(f"  ✓ \"{result.query[:40]}...\" avg={avg:.1f}")
            except Exception as e:
                print(f"  ✗ Judge error for \"{result.query[:40]}...\": {e}")
    else:
        print("Skipping LLM judge (--no-judge)")

    print_report(results)


if __name__ == "__main__":
    asyncio.run(main())
