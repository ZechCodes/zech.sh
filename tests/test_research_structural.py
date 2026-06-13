"""Pytest tests for research output structural checks.

Tests run against fixture data (no API calls) plus one live integration test.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.research_checks import (
    parse_body_and_sources,
    parse_citations,
    parse_sources_footer,
    run_structural_checks,
)

# ── Fixture: a well-formed lite-mode research output ──────────────────────


GOOD_LITE_OUTPUT = """\
## Understanding WebAssembly

WebAssembly (Wasm) is a binary instruction format designed as a portable \
compilation target for programming languages, enabling deployment on the \
web and other environments [1]. Unlike JavaScript, which is interpreted or \
JIT-compiled, WebAssembly runs at near-native speed by providing a compact \
binary format that modern browsers can execute directly [2].

### How It Works

The key innovation is a stack-based virtual machine that operates on typed \
values. Developers write code in languages like C, C++, or Rust, which is \
then compiled to the `.wasm` binary format. The browser's WebAssembly engine \
validates and compiles this binary to native machine code [1]. This two-stage \
approach — ahead-of-time compilation to Wasm, then platform-specific JIT — \
enables both portability and performance [3].

### Use Cases

WebAssembly has found traction beyond the browser. Server-side runtimes like \
Wasmtime and Wasmer allow Wasm modules to run outside browsers, and the WASI \
specification provides a system interface for file I/O, networking, and other \
OS capabilities [3]. Applications range from game engines and video codecs \
in the browser to plugin systems and edge computing workloads [2].

## Sources
[1] WebAssembly Concepts — https://developer.mozilla.org/en-US/docs/WebAssembly/Concepts
[2] WebAssembly — https://webassembly.org/
[3] Wasmtime Documentation — https://docs.wasmtime.dev/
"""

GOOD_DEEP_OUTPUT = """\
## Comparing Rust, Go, and Zig for Systems Programming

The systems programming landscape in 2026 offers three compelling options, \
each with distinct philosophies and tradeoffs. This analysis examines Rust, \
Go, and Zig across performance, safety, developer experience, and ecosystem \
maturity.

### Memory Safety

Rust's ownership system provides compile-time memory safety guarantees \
without a garbage collector, eliminating entire categories of bugs including \
use-after-free and data races [1]. Go takes a different approach with a \
garbage collector that prioritizes low-latency pauses, achieving sub-millisecond \
GC pauses in most workloads [2]. Zig opts for manual memory management with \
allocator-aware standard library design, giving developers explicit control \
while providing tools like GeneralPurposeAllocator to catch use-after-free \
in debug builds [3].

### Performance Characteristics

Benchmarks consistently show Rust and Zig achieving comparable performance \
to C, while Go typically trails by 10-30% depending on the workload [4]. \
Zig's comptime (compile-time execution) feature enables zero-cost abstractions \
that rival Rust's generics without the compilation time overhead [3]. Rust's \
zero-cost abstractions and LLVM backend produce highly optimized binaries, \
though compilation times remain a pain point for large projects [1].

### Developer Experience

Go's simplicity is its strongest selling point — the language can be learned \
in a weekend, and its toolchain (go build, go test, go fmt) is cohesive and \
fast [2]. Rust's learning curve is steeper due to the borrow checker, lifetimes, \
and trait system, though the compiler's error messages have improved \
substantially [1]. Zig positions itself as a "better C" with a focus on \
readability and debuggability, though its ecosystem is still maturing and \
documentation can be sparse [5].

### Ecosystem and Adoption

Rust has the most mature ecosystem among the three, with crates.io hosting \
over 150,000 packages and major adoption at companies like Amazon, Microsoft, \
and Google [1]. Go's ecosystem is similarly robust for networking and cloud \
infrastructure, powering projects like Kubernetes, Docker, and Terraform [2]. \
Zig's ecosystem is the smallest but growing, with notable adoption in the \
Bun JavaScript runtime and TigerBeetle database [5].

### When to Choose Each

The choice depends on the specific requirements. Rust excels when memory \
safety guarantees are non-negotiable, such as in security-critical code or \
concurrent systems [1]. Go is the pragmatic choice for networked services \
and tools where development velocity matters more than squeezing out every \
CPU cycle [2]. Zig fits projects that need C-level control with better \
ergonomics, particularly when interfacing with existing C codebases or \
targeting embedded systems [3].

## Sources
[1] The Rust Programming Language — https://doc.rust-lang.org/book/
[2] Go Documentation — https://go.dev/doc/
[3] Zig Language Reference — https://ziglang.org/documentation/master/
[4] Benchmarks Game — https://benchmarksgame-team.pages.debian.net/benchmarksgame/
[5] Zig Community Wiki — https://github.com/ziglang/zig/wiki
"""


# ── Parsing tests ─────────────────────────────────────────────────────────


class TestParseBodyAndSources:
    def test_splits_on_sources_header(self):
        body, sources = parse_body_and_sources(GOOD_LITE_OUTPUT)
        assert "## Understanding WebAssembly" in body
        assert "## Sources" in sources

    def test_no_sources_section(self):
        body, sources = parse_body_and_sources("Just some text without sources.")
        assert body == "Just some text without sources."
        assert sources == ""


class TestParseCitations:
    def test_extracts_citations(self):
        text = "Some text [1] and more [2] and again [1]."
        assert parse_citations(text) == {1, 2}

    def test_no_citations(self):
        assert parse_citations("No citations here.") == set()


class TestParseSourcesFooter:
    def test_parses_sources(self):
        section = (
            "## Sources\n"
            "[1] Title One — https://example.com/one\n"
            "[2] Title Two — https://example.com/two\n"
        )
        result = parse_sources_footer(section)
        assert len(result) == 2
        assert 1 in result
        assert 2 in result

    def test_empty_section(self):
        assert parse_sources_footer("") == {}


# ── Structural check tests on good output ─────────────────────────────────


class TestStructuralChecksGoodOutput:
    def test_all_checks_pass_lite(self):
        checks = run_structural_checks(GOOD_LITE_OUTPUT, "lite")
        for name, passed in checks.items():
            assert passed, f"Check '{name}' failed on good lite output"

    def test_all_checks_pass_deep(self):
        checks = run_structural_checks(GOOD_DEEP_OUTPUT, "deep")
        for name, passed in checks.items():
            assert passed, f"Check '{name}' failed on good deep output"


# ── Negative tests: malformed outputs ─────────────────────────────────────


class TestStructuralChecksNegative:
    def test_missing_sources_footer(self):
        bad = "Some text with [1] citation but no sources section."
        checks = run_structural_checks(bad, "lite")
        assert not checks["has_sources_footer"]

    def test_orphaned_citation(self):
        bad = (
            "Text with [1] and [3] refs.\n\n"
            "## Sources\n"
            "[1] Title — https://example.com\n"
        )
        checks = run_structural_checks(bad, "lite")
        assert not checks["citations_have_matching_sources"]

    def test_uncited_source(self):
        bad = (
            "Text with only [1] ref.\n\n"
            "## Sources\n"
            "[1] Title One — https://example.com/one\n"
            "[2] Title Two — https://example.com/two\n"
        )
        checks = run_structural_checks(bad, "lite")
        assert not checks["sources_are_all_cited"]

    def test_gap_in_numbering(self):
        bad = (
            "Text with [1] and [3] refs.\n\n"
            "## Sources\n"
            "[1] Title — https://example.com/one\n"
            "[3] Title — https://example.com/three\n"
        )
        checks = run_structural_checks(bad, "lite")
        assert not checks["sequential_numbering"]

    def test_too_few_sources_deep(self):
        bad = (
            "Text [1] and [2].\n\n"
            "## Sources\n"
            "[1] A — https://a.com\n"
            "[2] B — https://b.com\n"
        )
        checks = run_structural_checks(bad, "deep")
        assert not checks["min_source_count"]

    def test_bad_source_format(self):
        bad = (
            "Text [1].\n\n"
            "## Sources\n"
            "[1] Just a title without a URL\n"
        )
        checks = run_structural_checks(bad, "lite")
        assert not checks["source_format_valid"]

    def test_listicle_body(self):
        lines = "\n".join(f"- Point {i} about something [1]" for i in range(20))
        bad = (
            f"## Topic\n\n{lines}\n\n"
            "## Sources\n"
            "[1] Source — https://example.com\n"
        )
        checks = run_structural_checks(bad, "lite")
        assert not checks["no_listicle_body"]

    def test_no_headers(self):
        bad = (
            "Just plain text with [1] citation.\n\n"
            "## Sources\n"
            "[1] Title — https://example.com\n"
        )
        checks = run_structural_checks(bad, "lite")
        assert not checks["has_markdown_headers"]


# ── Live integration test ─────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
async def test_live_lite_research_structural():
    """Run one lite query and verify structural integrity."""
    from dotenv import load_dotenv

    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

    from controllers.deep_research_agent import DoneEvent, ErrorEvent, TextEvent
    from controllers.research_agent import run_agent_research_pipeline

    text_parts = []
    async for event in run_agent_research_pipeline(
        "What is WebAssembly?",
        brave_api_key=os.environ["BRAVE_API_KEY"],
        mode="lite",
    ):
        if isinstance(event, TextEvent):
            text_parts.append(event.text)
        elif isinstance(event, ErrorEvent):
            pytest.fail(f"Pipeline error: {event.error}")
        elif isinstance(event, DoneEvent):
            break

    markdown = "".join(text_parts)
    assert len(markdown) > 100, "Pipeline produced too little text"

    checks = run_structural_checks(markdown, "lite")
    for name, passed in checks.items():
        assert passed, f"Structural check '{name}' failed on live output"
