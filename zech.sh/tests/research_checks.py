"""Deterministic structural checks for research pipeline output.

Validates citation integrity, source formatting, response structure,
and writing style. Used by both eval_research.py and pytest tests.
"""

from __future__ import annotations

import re


def parse_body_and_sources(markdown: str) -> tuple[str, str]:
    """Split markdown into body text and sources footer."""
    pattern = r"^##\s+Sources\s*$"
    match = re.search(pattern, markdown, re.MULTILINE)
    if match:
        return markdown[: match.start()].strip(), markdown[match.start() :].strip()
    return markdown.strip(), ""


def parse_citations(text: str) -> set[int]:
    """Extract all [n] citation numbers from text.

    Excludes citations that appear inside the sources footer lines
    (e.g., "[1] Title — URL" patterns are source definitions, not citations).
    """
    return {int(m) for m in re.findall(r"\[(\d+)\]", text)}


def parse_sources_footer(sources_section: str) -> dict[int, str]:
    """Parse ## Sources section into {n: "Title — URL"} dict."""
    result = {}
    for line in sources_section.splitlines():
        m = re.match(r"^\[(\d+)\]\s+(.+)$", line.strip())
        if m:
            result[int(m.group(1))] = m.group(2)
    return result


def run_structural_checks(markdown: str, mode: str) -> dict[str, bool]:
    """Run all structural checks on a research output.

    Returns a dict of check_name -> pass/fail.
    """
    body, sources_section = parse_body_and_sources(markdown)
    body_citations = parse_citations(body)
    sources = parse_sources_footer(sources_section)
    source_numbers = set(sources.keys())
    word_count = len(markdown.split())

    checks = {}

    # 1. Sources footer exists
    checks["has_sources_footer"] = bool(sources_section)

    # 2. Every [n] in body has a matching source in footer
    checks["citations_have_matching_sources"] = (
        body_citations <= source_numbers if body_citations else bool(sources_section)
    )

    # 3. Every source in footer is cited in body
    checks["sources_are_all_cited"] = (
        source_numbers <= body_citations if source_numbers else True
    )

    # 4. Sequential numbering starting from 1, no gaps
    if source_numbers:
        expected = set(range(1, max(source_numbers) + 1))
        checks["sequential_numbering"] = source_numbers == expected
    else:
        checks["sequential_numbering"] = False

    # 5. Minimum source count
    min_sources = 2 if mode == "lite" else 4
    checks["min_source_count"] = len(sources) >= min_sources

    # 6. Source format: [n] Title — URL (em dash, en dash, or hyphen)
    if sources_section:
        source_lines = [
            line.strip()
            for line in sources_section.splitlines()
            if re.match(r"^\[\d+\]", line.strip())
        ]
        valid = all(
            re.match(r"^\[\d+\]\s+.+\s+[—–-]\s+https?://", line)
            for line in source_lines
        )
        checks["source_format_valid"] = valid and len(source_lines) > 0
    else:
        checks["source_format_valid"] = False

    # 7. Response length within expected ranges
    if mode == "lite":
        checks["response_length_reasonable"] = 150 <= word_count <= 3000
    else:
        checks["response_length_reasonable"] = 400 <= word_count <= 5000

    # 8. Not predominantly a listicle
    body_lines = [
        line
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if body_lines:
        list_lines = sum(
            1
            for line in body_lines
            if re.match(r"^\s*[-*]\s", line) or re.match(r"^\s*\d+\.\s", line)
        )
        checks["no_listicle_body"] = (list_lines / len(body_lines)) < 0.6
    else:
        checks["no_listicle_body"] = False

    # 9. Has markdown headers in body (beyond ## Sources)
    body_headers = re.findall(r"^#{2,4}\s+\S", body, re.MULTILINE)
    checks["has_markdown_headers"] = len(body_headers) >= 1

    return checks
