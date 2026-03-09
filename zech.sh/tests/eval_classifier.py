"""Eval suite for the scan query classifier.

Runs the classifier against a broad set of test cases at both HIGH and LOW
thinking levels, measuring accuracy and latency. Reports a comparison table.

Usage:
    uv run python tests/eval_classifier.py
"""

import asyncio
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from controllers.scan_agent import classify_agent
from controllers.llm import gemini_flash_lite

# ── Test cases ──────────────────────────────────────────────────────────────

CASES = [
    # URL — clear domains, IPs, paths
    ("github.com", "URL"),
    ("docs.python.org/3/library/asyncio", "URL"),
    ("192.168.1.1", "URL"),
    ("example.com/path?query=1", "URL"),
    ("https://news.ycombinator.com", "URL"),
    ("stackoverflow.com/questions/12345", "URL"),
    ("10.0.0.1:8080", "URL"),
    ("myapp.localhost:3000/api/v1", "URL"),

    # SEARCH — quick facts, definitions, single-answer lookups, navigation
    ("python list comprehension", "SEARCH"),
    ("best pizza near me", "SEARCH"),
    ("litestar framework", "SEARCH"),
    ("weather today", "SEARCH"),
    ("numpy array reshape", "SEARCH"),
    ("define avant garde", "SEARCH"),
    ("latest iphone price", "SEARCH"),
    ("python 3.13 release date", "SEARCH"),
    ("tailwind css grid layout", "SEARCH"),
    ("who won the super bowl", "SEARCH"),
    ("convert fahrenheit to celsius formula", "SEARCH"),
    ("async await javascript", "SEARCH"),
    ("docker", "SEARCH"),
    ("fastapi", "SEARCH"),
    ("openai api pricing", "SEARCH"),
    ("what does CORS stand for", "SEARCH"),
    ("HTTP status codes", "SEARCH"),

    # RESEARCH — questions, explanations, comparisons, how-to, synthesis
    ("what is kubernetes", "RESEARCH"),
    ("how to center a div", "RESEARCH"),
    ("rust ownership rules", "RESEARCH"),
    ("sqlite vs postgres", "RESEARCH"),
    ("how to deploy to aws", "RESEARCH"),
    ("is rust faster than go", "RESEARCH"),
    ("compare React vs Svelte for SPAs", "RESEARCH"),
    ("what are the tradeoffs between microservices and monoliths for a small team", "RESEARCH"),
    ("how does TCP congestion control work?", "RESEARCH"),
    ("explain the difference between threads and processes", "RESEARCH"),
    ("what are the pros and cons of microservices?", "RESEARCH"),
    ("analyze the performance implications of using ORMs vs raw SQL in Python", "RESEARCH"),
    ("what are the best practices for securing a REST API in production", "RESEARCH"),
    ("compare different state management approaches in modern frontend frameworks", "RESEARCH"),
    ("explain how garbage collection differs between Go, Java, and Python", "RESEARCH"),
    ("what are the architectural tradeoffs of event sourcing vs CRUD", "RESEARCH"),

    # ── Borderline / tricky cases ──────────────────────────────────────────
    ("google.com", "URL"),
]


async def run_eval(thinking_level: str | None) -> list[dict]:
    """Run all cases at a given thinking level, return results."""
    settings = {}
    if thinking_level is not None:
        settings = {"google_thinking_config": {"thinking_level": thinking_level}}
    model = gemini_flash_lite()
    results = []

    for query, expected in CASES:
        start = time.monotonic()
        try:
            result = await classify_agent.run(query, model=model, model_settings=settings)
            text = result.output.strip().upper()
            if text not in ("URL", "SEARCH", "RESEARCH"):
                text = "SEARCH"
        except Exception as e:
            text = f"ERROR: {e}"
        elapsed = time.monotonic() - start

        correct = text == expected
        results.append({
            "query": query,
            "expected": expected,
            "got": text,
            "correct": correct,
            "time_ms": round(elapsed * 1000),
        })

    return results


def print_results(label: str, results: list[dict]):
    """Print a formatted results table."""
    correct = sum(1 for r in results if r["correct"])
    total = len(results)
    avg_ms = sum(r["time_ms"] for r in results) / total
    total_ms = sum(r["time_ms"] for r in results)

    print(f"\n{'=' * 80}")
    print(f"  {label}  —  {correct}/{total} correct ({100*correct/total:.1f}%)  "
          f"avg {avg_ms:.0f}ms  total {total_ms/1000:.1f}s")
    print(f"{'=' * 80}")

    # Show failures
    failures = [r for r in results if not r["correct"]]
    if failures:
        print(f"\n  FAILURES ({len(failures)}):")
        for r in failures:
            print(f"    ✗ [{r['time_ms']}ms] \"{r['query']}\"")
            print(f"      expected {r['expected']}, got {r['got']}")
    else:
        print("\n  All cases passed!")

    # Per-category breakdown
    for cat in ("URL", "SEARCH", "RESEARCH"):
        cat_results = [r for r in results if r["expected"] == cat]
        cat_correct = sum(1 for r in cat_results if r["correct"])
        cat_avg = sum(r["time_ms"] for r in cat_results) / len(cat_results) if cat_results else 0
        print(f"\n  {cat}: {cat_correct}/{len(cat_results)} correct, avg {cat_avg:.0f}ms")


async def main():
    print("Running classifier eval suite...")
    print(f"Total cases: {len(CASES)}")

    # Run HIGH thinking
    print("\n▶ Running with HIGH thinking...")
    high_results = await run_eval("HIGH")
    print_results("HIGH thinking", high_results)

    # Run LOW thinking
    print("\n▶ Running with LOW thinking...")
    low_results = await run_eval("LOW")
    print_results("LOW thinking", low_results)

    # Run with no thinking config at all
    print("\n▶ Running with NO thinking config...")
    none_results = await run_eval(None)
    print_results("NO thinking config", none_results)

    # Comparison summary
    high_correct = sum(1 for r in high_results if r["correct"])
    low_correct = sum(1 for r in low_results if r["correct"])
    none_correct = sum(1 for r in none_results if r["correct"])
    high_avg = sum(r["time_ms"] for r in high_results) / len(high_results)
    low_avg = sum(r["time_ms"] for r in low_results) / len(low_results)
    none_avg = sum(r["time_ms"] for r in none_results) / len(none_results)
    total = len(CASES)

    print(f"\n{'=' * 80}")
    print("  COMPARISON SUMMARY")
    print(f"{'=' * 80}")
    print(f"  {'Level':<12} {'Accuracy':<15} {'Avg Latency':<15} {'Total Time':<15}")
    print(f"  {'─' * 57}")
    print(f"  {'HIGH':<12} {high_correct}/{total} ({100*high_correct/total:.1f}%)     {high_avg:.0f}ms          {sum(r['time_ms'] for r in high_results)/1000:.1f}s")
    print(f"  {'LOW':<12} {low_correct}/{total} ({100*low_correct/total:.1f}%)     {low_avg:.0f}ms          {sum(r['time_ms'] for r in low_results)/1000:.1f}s")
    print(f"  {'NONE':<12} {none_correct}/{total} ({100*none_correct/total:.1f}%)     {none_avg:.0f}ms          {sum(r['time_ms'] for r in none_results)/1000:.1f}s")

    # Show where they disagree
    disagreements = []
    for h, l, n in zip(high_results, low_results, none_results):
        if h["got"] != l["got"] or h["got"] != n["got"]:
            disagreements.append((h, l, n))

    if disagreements:
        print(f"\n  DISAGREEMENTS ({len(disagreements)}):")
        for h, l, n in disagreements:
            marker = lambda r: "✓" if r["correct"] else "✗"
            print(f"    \"{h['query']}\" (expected: {h['expected']})")
            print(f"      HIGH={h['got']} {marker(h)}  LOW={l['got']} {marker(l)}  NONE={n['got']} {marker(n)}")


if __name__ == "__main__":
    asyncio.run(main())
