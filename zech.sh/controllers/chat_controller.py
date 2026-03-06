"""Chat controller for zech.sh — streaming conversational AI with tools.

Provides routes for the chat page and SSE streaming endpoint. Chat state
(messages, memory notes) is stored in the user's session via cookies/Redis.
"""

from __future__ import annotations

import json
import logging

from litestar import Controller, Request, get, post
from litestar.response import Response
from litestar.response import Template as TemplateResponse
from litestar.response.sse import ServerSentEvent, ServerSentEventMessage

from controllers.chat_agent import (
    CompactEvent,
    DoneEvent,
    ErrorEvent,
    NotesEvent,
    TextEvent,
    ThinkingEvent,
    ToolDoneEvent,
    ToolStartEvent,
    run_chat,
)

logger = logging.getLogger(__name__)

_SESSION_HISTORY = "chat_history"
_SESSION_NOTES = "chat_notes"
_MAX_SESSION_MESSAGES = 200


def _get_history(request: Request) -> list[dict]:
    """Read chat history from session."""
    raw = request.session.get(_SESSION_HISTORY)
    if not raw:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return raw


def _set_history(request: Request, history: list[dict]) -> None:
    """Save chat history to session."""
    # Keep bounded
    if len(history) > _MAX_SESSION_MESSAGES:
        history = history[-_MAX_SESSION_MESSAGES:]
    request.session[_SESSION_HISTORY] = history


def _get_notes(request: Request) -> str:
    """Read memory notes from session."""
    return request.session.get(_SESSION_NOTES, "")


def _set_notes(request: Request, notes: str) -> None:
    """Save memory notes to session."""
    request.session[_SESSION_NOTES] = notes


class ChatController(Controller):
    path = "/chat"

    @get("/")
    async def chat_page(self, request: Request) -> TemplateResponse:
        history = _get_history(request)
        notes = _get_notes(request)
        return TemplateResponse(
            "chat.html",
            context={
                "history": history,
                "notes": notes,
            },
        )

    @post("/send")
    async def send_message(self, request: Request) -> ServerSentEvent:
        """Accept a user message and stream back the AI response via SSE."""
        body = await request.json()
        user_message = body.get("message", "").strip()
        if not user_message:
            async def _empty():
                yield ServerSentEventMessage(event="error", data="Empty message")
            return ServerSentEvent(_empty())

        history = _get_history(request)
        notes = _get_notes(request)

        async def _stream():
            accumulated_text = ""
            new_notes = notes

            try:
                async for event in run_chat(user_message, history, notes):
                    if isinstance(event, ThinkingEvent):
                        yield ServerSentEventMessage(
                            event="thinking",
                            data=json.dumps({"thinking": event.thinking}),
                        )
                    elif isinstance(event, ToolStartEvent):
                        yield ServerSentEventMessage(
                            event="tool_start",
                            data=json.dumps({
                                "tool": event.tool,
                                "args": event.args,
                            }),
                        )
                    elif isinstance(event, ToolDoneEvent):
                        yield ServerSentEventMessage(
                            event="tool_done",
                            data=json.dumps({
                                "tool": event.tool,
                                "summary": event.summary,
                            }),
                        )
                    elif isinstance(event, TextEvent):
                        accumulated_text += event.text
                        yield ServerSentEventMessage(
                            event="text",
                            data=json.dumps({"text": event.text}),
                        )
                    elif isinstance(event, NotesEvent):
                        new_notes = event.notes
                        yield ServerSentEventMessage(
                            event="notes",
                            data=json.dumps({"notes": event.notes}),
                        )
                    elif isinstance(event, CompactEvent):
                        yield ServerSentEventMessage(
                            event="compact",
                            data=json.dumps({
                                "removed_messages": event.removed_messages,
                                "summary_tokens": event.summary_tokens,
                            }),
                        )
                    elif isinstance(event, DoneEvent):
                        # Update session with new history
                        history.append({"role": "user", "content": user_message})
                        history.append({"role": "model", "content": accumulated_text})
                        _set_history(request, history)
                        _set_notes(request, new_notes)
                        yield ServerSentEventMessage(event="done", data="")
                    elif isinstance(event, ErrorEvent):
                        yield ServerSentEventMessage(
                            event="error",
                            data=json.dumps({"error": event.error}),
                        )
            except Exception as exc:
                logger.exception("Chat stream error")
                yield ServerSentEventMessage(
                    event="error",
                    data=json.dumps({"error": str(exc)}),
                )

        return ServerSentEvent(_stream())

    @post("/clear")
    async def clear_chat(self, request: Request) -> Response:
        """Clear chat history and notes."""
        request.session.pop(_SESSION_HISTORY, None)
        request.session.pop(_SESSION_NOTES, None)
        return Response(content={"ok": True}, status_code=200)
