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
from controllers.llm import genai_client

logger = logging.getLogger(__name__)

_MODEL = "gemini-3.1-flash-lite-preview"
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
    pass


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
You are Zech's AI assistant on zech.sh. You are helpful, knowledgeable, and \
concise. You have access to tools for searching the web and reading web pages.

When the user asks questions that need current information or facts you don't \
know, use the web_search tool. When the user shares a URL or you need to read \
a specific page from search results, use the open_url tool.

Be direct and clear in your responses. Use markdown formatting when helpful. \
Keep responses focused and avoid unnecessary filler."""

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
) -> AsyncGenerator[ChatEvent, None]:
    """Run a single chat turn with tool use, streaming events.

    Args:
        user_message: The user's new message.
        history: Previous messages as list of {"role": "user"|"model", "content": str}.
        memory_notes: Current memory notes to include as context.

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

    # Build system instruction with memory notes
    system = _SYSTEM_PROMPT
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

    try:
        while round_num < max_tool_rounds:
            round_num += 1
            has_tool_calls = False

            async for chunk in await client.aio.models.generate_content_stream(
                model=_MODEL,
                contents=contents,
                config=config,
            ):
                if not chunk.candidates:
                    continue

                for part in chunk.candidates[0].content.parts:
                    if hasattr(part, "thought") and part.thought:
                        yield ThinkingEvent(thinking=part.text or "")
                    elif hasattr(part, "function_call") and part.function_call:
                        has_tool_calls = True
                        fc = part.function_call
                        tool_name = fc.name
                        tool_args = dict(fc.args) if fc.args else {}
                        yield ToolStartEvent(tool=tool_name, args=tool_args)

                        # Execute the tool
                        result = await _execute_tool(tool_name, tool_args)

                        # Summarize for display
                        if tool_name == "web_search":
                            summary = f'Searched for "{tool_args.get("query", "")}"'
                        else:
                            summary = f'Opened {tool_args.get("url", "")}'
                        yield ToolDoneEvent(tool=tool_name, summary=summary)

                        # Add assistant's function call and result to contents
                        contents.append(genai_types.Content(
                            role="model",
                            parts=[genai_types.Part(function_call=genai_types.FunctionCall(
                                name=tool_name,
                                args=tool_args,
                            ))],
                        ))
                        contents.append(genai_types.Content(
                            role="user",
                            parts=[genai_types.Part(function_response=genai_types.FunctionResponse(
                                name=tool_name,
                                response={"result": result},
                            ))],
                        ))
                    elif part.text:
                        accumulated_text += part.text
                        yield TextEvent(text=part.text)

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
            new_notes = notes_resp.text or ""
            if new_notes.strip():
                yield NotesEvent(notes=new_notes.strip())
        except Exception:
            logger.exception("Memory notes update failed")

        yield DoneEvent()

    except Exception as exc:
        logger.exception("Chat agent error")
        yield ErrorEvent(error=str(exc))
