"""Quick one-off: run just the Nano research step and print its curation output."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from controllers.research_agent import (
    AgentDeps,
    ResearchCuration,
    lite_research_agent,
    _LITE_SYSTEM_PROMPT,
    _MODE_CONFIG,
)
from controllers.deep_research_agent import (
    CostBudget,
    DetailEvent,
    KnowledgeState,
    TokenCounter,
)
from controllers.llm import gpt_nano

QUERIES = [
    "compare the latest GPT mini and nano to the latest haiku and Flash/Flash Lite",
]


async def noop_dispatch(event):
    if isinstance(event, DetailEvent):
        if event.type == "tool_call":
            print(f"  🔧 {event.payload['name']}({event.payload.get('args', {})})")
        elif event.type == "search_done":
            n = len(event.payload.get("results", []))
            print(f"  📋 search returned {n} results")
        elif event.type == "fetch_done":
            print(f"  📄 read: {event.payload.get('url', '?')[:80]}")


async def run_query(query: str):
    cfg = _MODE_CONFIG["lite"]

    deps = AgentDeps(
        dispatch=noop_dispatch,
        knowledge=KnowledgeState(),
        budget=CostBudget(limit=cfg["budget_limit"]),
        brave_api_key=os.environ["BRAVE_API_KEY"],
        redis_url="",
        db_session=None,
        already_fetched=set(),
        extraction_counter=TokenCounter(),
        max_search_calls=cfg["max_search_calls"],
        max_read_calls=cfg["max_read_calls"],
        max_research_calls=cfg["max_research_calls"],
        max_verify_calls=cfg["max_verify_calls"],
        brave_results=cfg["brave_results"],
        jina_reads=cfg["jina_reads"],
        extract_max_chars=cfg["extract_max_chars"],
        fetch_max_chars=cfg["fetch_max_chars"],
        extraction_model=cfg["extraction_model"],
    )

    print(f"\n{'='*60}")
    print(f"Query: {query}")
    print(f"{'='*60}\n")
    print("Running Nano research step...\n")

    result = await lite_research_agent.run(
        query,
        model=gpt_nano(),
        deps=deps,
        instructions=_LITE_SYSTEM_PROMPT,
    )

    curation = result.output
    usage = result.usage()

    print(f"\n{'='*60}")
    print("NANO OUTPUT")
    print(f"{'='*60}\n")

    if isinstance(curation, ResearchCuration):
        print("📌 Selected URLs:")
        for i, url in enumerate(curation.selected_urls, 1):
            entry = deps.staged_entries.get(url)
            label = entry.title if entry else "?"
            print(f"  [{i}] {label}")
            print(f"      {url}")

        print(f"\n🔑 Key Points:")
        for point in curation.key_points:
            print(f"  • {point}")

        print(f"\n📓 Research Notes:")
        print(f"  {curation.research_notes}")
    else:
        print(f"  (unexpected output type: {type(curation)})")
        print(f"  {curation}")

    print(f"\n📊 Nano tokens: {usage.input_tokens} in / {usage.output_tokens} out")
    print(f"📊 Extraction tokens: {deps.extraction_counter.input_tokens} in / {deps.extraction_counter.output_tokens} out")
    print(f"📊 Staged entries: {len(deps.staged_entries)}")


async def main():
    for query in QUERIES:
        await run_query(query)
        print("\n")


if __name__ == "__main__":
    asyncio.run(main())
