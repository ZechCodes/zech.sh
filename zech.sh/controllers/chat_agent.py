"""Conversational chat agent using Gemini 3.1 Flash Lite with tools.

Provides a streaming chat with web_search and open_url tools, persistent
memory notes (1-3 paragraphs updated each turn), and automatic history
compaction when token usage exceeds ~100k tokens.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from google.genai import types as genai_types

from controllers.brave_search import brave_search as _brave_search
from controllers.llm import calc_usage_cost, genai_client

logger = logging.getLogger(__name__)

_MODEL = "gemini-3-flash-preview"
_MAX_HISTORY_TOKENS = 100_000
_COMPACT_TOKENS = 50_000
_MAX_FETCH_CHARS = 80_000

# ---------------------------------------------------------------------------
# SSE event types
# ---------------------------------------------------------------------------


@dataclass
class ThinkingEvent:
    """Agent is thinking / reasoning."""
    thinking: str = ""


@dataclass
class ToolStartEvent:
    """Agent started using a tool."""
    tool: str
    args: dict


@dataclass
class ToolDoneEvent:
    """Agent finished using a tool."""
    tool: str
    summary: str
    result: str = ""


@dataclass
class TextEvent:
    """Streamed text chunk."""
    text: str


@dataclass
class NotesEvent:
    """Updated memory notes."""
    notes: str


@dataclass
class CompactEvent:
    """History was compacted."""
    removed_messages: int
    summary_tokens: int


@dataclass
class DoneEvent:
    """Generation complete."""
    usage: dict | None = None


@dataclass
class ErrorEvent:
    """An error occurred."""
    error: str


ChatEvent = (
    ThinkingEvent | ToolStartEvent | ToolDoneEvent |
    TextEvent | NotesEvent | CompactEvent | DoneEvent | ErrorEvent
)

# ---------------------------------------------------------------------------
# Tool definitions for Gemini
# ---------------------------------------------------------------------------

_SEARCH_TOOL = genai_types.FunctionDeclaration(
    name="web_search",
    description=(
        "Search the web using Brave Search. Returns titles, URLs, and "
        "descriptions of the top results. Use this when you need current "
        "information, facts, or to find resources."
    ),
    parameters=genai_types.Schema(
        type="OBJECT",
        properties={
            "query": genai_types.Schema(
                type="STRING",
                description="The search query to look up.",
            ),
        },
        required=["query"],
    ),
)

_OPEN_URL_TOOL = genai_types.FunctionDeclaration(
    name="open_url",
    description=(
        "Open a URL and read its content. Returns the extracted text from "
        "the page. Use this to read articles, documentation, or any web "
        "page the user references or that appeared in search results."
    ),
    parameters=genai_types.Schema(
        type="OBJECT",
        properties={
            "url": genai_types.Schema(
                type="STRING",
                description="The full URL to open and read.",
            ),
        },
        required=["url"],
    ),
)

_TOOLS = genai_types.Tool(function_declarations=[_SEARCH_TOOL, _OPEN_URL_TOOL])

# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


async def _exec_web_search(query: str) -> str:
    """Run a Brave search and return formatted results."""
    api_key = os.environ.get("BRAVE_API_KEY", "")
    if not api_key:
        return "Error: Search is not configured."
    try:
        results = await _brave_search(query, api_key, count=8)
        if not results:
            return "No results found."
        lines = []
        for r in results:
            title = r.get("title", "")
            url = r.get("url", "")
            desc = r.get("description", "")
            lines.append(f"- [{title}]({url}): {desc}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("Search tool error for %r", query)
        return f"Search error: {exc}"


async def _exec_open_url(url: str) -> str:
    """Fetch a URL via Jina Reader and return extracted text."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return "Error: Only HTTP/HTTPS URLs are supported."

        jina_url = f"https://r.jina.ai/{url}"
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=30.0,
            headers={
                "Accept": "text/plain",
                "X-No-Cache": "true",
            },
        ) as client:
            resp = await client.get(jina_url)
            resp.raise_for_status()
            text = resp.text

        if len(text) > _MAX_FETCH_CHARS:
            text = text[:_MAX_FETCH_CHARS] + "\n\n[Content truncated]"
        return text if text.strip() else "Page loaded but no readable text content found."
    except Exception as exc:
        logger.exception("Open URL tool error for %r", url)
        return f"Error fetching URL: {exc}"


async def _execute_tool(name: str, args: dict) -> str:
    """Dispatch a tool call to its implementation."""
    if name == "web_search":
        return await _exec_web_search(args.get("query", ""))
    elif name == "open_url":
        return await _exec_open_url(args.get("url", ""))
    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Memory notes prompt
# ---------------------------------------------------------------------------

_NOTES_SYSTEM = (
    "Based on this conversation, write concise notes (1-3 short paragraphs) "
    "capturing the key facts, preferences, and context the user has shared. "
    "These notes will be provided to you in future messages to maintain "
    "continuity. Focus on what matters most for helping the user. "
    "Only output the notes, nothing else."
)

_SYSTEM_PROMPT = """\
You are Zech's AI assistant on zech.sh. You are helpful and concise. You have \
access to tools for searching the web and reading web pages.

IMPORTANT: Your training data is outdated. Always use web_search to verify \
facts, look up details, and get current information before answering. Do NOT \
rely on your own knowledge for specific claims, stats, versions, dates, names, \
or technical details — search first, then answer based on what you find. When \
constructing search queries, use the user's own words or go generic rather \
than inserting specific details from your training data that may be wrong.

When the user shares a URL or you find a relevant page in search results, use \
the open_url tool to read it before summarizing or answering questions about it.

NEVER punt to the user with "check the website for details" or "visit the \
pricing page for current info." If the user asked a question, YOU go get the \
answer. Use open_url to read pricing pages, feature lists, documentation, or \
whatever is needed to give a complete answer with real data. If a search result \
links to a page with the details the user wants, open it and extract the facts. \
Do the legwork — the user is asking you so they don't have to look it up \
themselves.

## Response guidelines

Match your response format to the type of request:

- **Comparisons** ("X vs Y", "which is better", "differences between"): Use a \
markdown table with clear columns. Summarize a recommendation below the table.
- **How-to / tutorials**: Numbered step-by-step instructions. Include commands \
or code snippets in fenced code blocks where relevant.
- **"What is" / explainers**: Lead with a one-sentence definition, then expand \
with key details. Use bullet points for features or characteristics.
- **Lists / recommendations** ("best X", "top Y", "options for"): Bulleted or \
numbered list with a brief note on each item. Bold the name/title.
- **Current events / news**: Lead with the latest facts. Include dates. Cite \
sources with inline links.
- **Debugging / errors**: Identify the cause first, then give the fix. Use code \
blocks for any code or commands.
- **Opinion / advice**: Be direct — state a clear recommendation, then explain \
the reasoning. Acknowledge trade-offs.
- **Math / calculations**: Show your work step by step. Use code blocks for \
formulas if complex.

Whenever you mention a tool, project, library, article, product, place, or \
anything the user might want to explore further, make it a markdown link to \
the relevant URL. Prefer linking to official sites, docs, or the source you \
found it from. Don't just name-drop — linkify it so the user can click through.

Keep responses focused and avoid unnecessary filler. Be direct — lead with the \
answer, not the preamble."""

# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English text."""
    return len(text) // 4


def _estimate_history_tokens(history: list[dict]) -> int:
    """Estimate total tokens across all messages in history."""
    total = 0
    for msg in history:
        total += _estimate_tokens(msg.get("content", ""))
    return total


# ---------------------------------------------------------------------------
# History compaction
# ---------------------------------------------------------------------------


async def _compact_history(
    history: list[dict],
    target_tokens: int = _COMPACT_TOKENS,
) -> tuple[list[dict], str, int]:
    """Compact the oldest messages into a summary.

    Returns (new_history, summary_text, removed_count).
    """
    # Find how many messages from the start to remove to free ~target_tokens
    tokens_so_far = 0
    split_idx = 0
    for i, msg in enumerate(history):
        tokens_so_far += _estimate_tokens(msg.get("content", ""))
        if tokens_so_far >= target_tokens:
            split_idx = i + 1
            break

    if split_idx < 2:
        return history, "", 0

    old_messages = history[:split_idx]
    remaining = history[split_idx:]

    # Build text to summarize
    summary_input = "\n\n".join(
        f"[{m['role'].upper()}]: {m['content']}" for m in old_messages
    )

    client = genai_client()
    resp = await client.aio.models.generate_content(
        model=_MODEL,
        contents=f"Summarize this conversation history into a concise ~5000 character summary that preserves all important details, decisions, and context:\n\n{summary_input}",
        config=genai_types.GenerateContentConfig(
            system_instruction=(
                "You are a summarization assistant. Create a detailed but concise "
                "summary that captures all key information from the conversation. "
                "Preserve specific facts, names, code snippets, URLs, decisions, "
                "and user preferences. Output only the summary."
            ),
            temperature=0.2,
            max_output_tokens=2000,
        ),
    )

    summary_text = resp.text or ""

    # Prepend summary as a system-like message
    summary_msg = {
        "role": "user",
        "content": f"[CONVERSATION SUMMARY - Earlier messages have been summarized]\n{summary_text}",
    }
    summary_ack = {
        "role": "model",
        "content": "I understand. I have the context from our earlier conversation. Let's continue.",
    }

    new_history = [summary_msg, summary_ack] + remaining
    return new_history, summary_text, len(old_messages)


# ---------------------------------------------------------------------------
# Main chat generator
# ---------------------------------------------------------------------------


async def run_chat(
    user_message: str,
    history: list[dict],
    memory_notes: str = "",
    user_timezone: str = "",
) -> AsyncGenerator[ChatEvent, None]:
    """Run a single chat turn with tool use, streaming events.

    Args:
        user_message: The user's new message.
        history: Previous messages as list of {"role": "user"|"model", "content": str}.
        memory_notes: Current memory notes to include as context.
        user_timezone: IANA timezone string (e.g. "America/New_York") for date/time context.

    Yields ChatEvent instances.
    """
    client = genai_client()

    # Check if history needs compaction
    total_tokens = _estimate_history_tokens(history) + _estimate_tokens(user_message)
    if total_tokens > _MAX_HISTORY_TOKENS:
        try:
            history, summary, removed = await _compact_history(history)
            if removed > 0:
                yield CompactEvent(
                    removed_messages=removed,
                    summary_tokens=_estimate_tokens(summary),
                )
        except Exception as exc:
            logger.exception("History compaction failed")

    # Build system instruction with memory notes and current time
    system = _SYSTEM_PROMPT
    try:
        from datetime import datetime, timezone as tz
        import zoneinfo
        if user_timezone:
            user_tz = zoneinfo.ZoneInfo(user_timezone)
        else:
            user_tz = tz.utc
        now = datetime.now(user_tz)
        system += f"\n\nCurrent date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')} ({user_timezone or 'UTC'})"
    except Exception:
        pass
    if memory_notes:
        system += f"\n\n## Memory Notes\nThese are your notes from the conversation so far:\n{memory_notes}"

    # Build contents from history
    contents = []
    for msg in history:
        role = msg["role"]
        if role == "assistant":
            role = "model"
        contents.append(genai_types.Content(
            role=role,
            parts=[genai_types.Part(text=msg["content"])],
        ))

    # Add new user message
    contents.append(genai_types.Content(
        role="user",
        parts=[genai_types.Part(text=user_message)],
    ))

    config = genai_types.GenerateContentConfig(
        system_instruction=system,
        tools=[_TOOLS],
        temperature=0.7,
        thinking_config=genai_types.ThinkingConfig(
            thinking_level=genai_types.ThinkingLevel.HIGH,
        ),
    )

    accumulated_text = ""
    max_tool_rounds = 10
    round_num = 0
    total_input_tokens = 0
    total_output_tokens = 0

    try:
        while round_num < max_tool_rounds:
            round_num += 1
            has_tool_calls = False

            # Collect all parts from the streamed response so we can
            # preserve thought_signature on function_call parts.
            response_parts: list[genai_types.Part] = []
            pending_tool_calls: list[tuple[genai_types.Part, str, dict]] = []
            last_usage_meta = None

            async for chunk in await client.aio.models.generate_content_stream(
                model=_MODEL,
                contents=contents,
                config=config,
            ):
                meta = getattr(chunk, "usage_metadata", None)
                if meta:
                    last_usage_meta = meta

                if not chunk.candidates:
                    continue

                for part in chunk.candidates[0].content.parts:
                    if hasattr(part, "thought") and part.thought:
                        yield ThinkingEvent(thinking=part.text or "")
                        response_parts.append(part)
                    elif hasattr(part, "function_call") and part.function_call:
                        has_tool_calls = True
                        fc = part.function_call
                        tool_name = fc.name
                        tool_args = dict(fc.args) if fc.args else {}
                        yield ToolStartEvent(tool=tool_name, args=tool_args)
                        response_parts.append(part)
                        pending_tool_calls.append((part, tool_name, tool_args))
                    elif part.text:
                        accumulated_text += part.text
                        yield TextEvent(text=part.text)
                        response_parts.append(part)

            # Accumulate token counts from this round
            if last_usage_meta:
                total_input_tokens += last_usage_meta.prompt_token_count or 0
                total_output_tokens += last_usage_meta.candidates_token_count or 0

            # Process tool calls after the full response is collected
            if pending_tool_calls:
                # Add the model's full response (preserving thought_signature)
                contents.append(genai_types.Content(
                    role="model",
                    parts=response_parts,
                ))

                # Execute tools and add results
                tool_response_parts: list[genai_types.Part] = []
                for _part, tool_name, tool_args in pending_tool_calls:
                    result = await _execute_tool(tool_name, tool_args)

                    if tool_name == "web_search":
                        summary = f"Searched '{tool_args.get('query', '')}'"
                    else:
                        summary = f"Read '{tool_args.get('url', '')}'"
                    yield ToolDoneEvent(tool=tool_name, summary=summary, result=result)

                    tool_response_parts.append(genai_types.Part(
                        function_response=genai_types.FunctionResponse(
                            name=tool_name,
                            response={"result": result},
                        )
                    ))

                contents.append(genai_types.Content(
                    role="user",
                    parts=tool_response_parts,
                ))

            if not has_tool_calls:
                break

        # Update memory notes
        try:
            notes_input = ""
            for msg in history[-10:]:
                notes_input += f"[{msg['role'].upper()}]: {msg['content']}\n\n"
            notes_input += f"[USER]: {user_message}\n\n[ASSISTANT]: {accumulated_text}"

            notes_resp = await client.aio.models.generate_content(
                model=_MODEL,
                contents=notes_input,
                config=genai_types.GenerateContentConfig(
                    system_instruction=_NOTES_SYSTEM,
                    temperature=0.3,
                    max_output_tokens=500,
                ),
            )
            notes_meta = getattr(notes_resp, "usage_metadata", None)
            if notes_meta:
                total_input_tokens += notes_meta.prompt_token_count or 0
                total_output_tokens += notes_meta.candidates_token_count or 0
            new_notes = notes_resp.text or ""
            if new_notes.strip():
                yield NotesEvent(notes=new_notes.strip())
        except Exception:
            logger.exception("Memory notes update failed")

        usage = calc_usage_cost(total_input_tokens, total_output_tokens, _MODEL)
        yield DoneEvent(usage=usage)

    except Exception as exc:
        logger.exception("Chat agent error")
        yield ErrorEvent(error=str(exc))
