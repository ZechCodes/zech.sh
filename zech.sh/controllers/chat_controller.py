"""Chat controller for zech.sh — streaming conversational AI with tools.

Provides routes for the chat page and SSE streaming endpoint. Chat history
is persisted to the database using ChatSession/ChatMessage models.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from litestar import Controller, Request, get, post
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse
from litestar.response.sse import ServerSentEvent, ServerSentEventMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.auth.session_keys import SESSION_USER_ID
from skrift.db.models.user import User

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
from models.chat import ChatMessage, ChatSession

logger = logging.getLogger(__name__)

_RECENT_CHATS_LIMIT = 20
_NOTES_SESSION_PREFIX = "chat_notes:"


async def _get_user(request: Request, db_session: AsyncSession) -> User | None:
    user_id = request.session.get(SESSION_USER_ID)
    if not user_id:
        return None
    result = await db_session.execute(select(User).where(User.id == UUID(user_id)))
    return result.scalar_one_or_none()


async def _get_recent_chats(
    user_id: UUID, db_session: AsyncSession, limit: int = _RECENT_CHATS_LIMIT
) -> list[ChatSession]:
    result = await db_session.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user_id, ChatSession.mode == "chat")
        .order_by(ChatSession.updated_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def _get_chat_messages(
    chat_id: UUID, db_session: AsyncSession
) -> list[ChatMessage]:
    result = await db_session.execute(
        select(ChatMessage)
        .where(ChatMessage.chat_id == chat_id)
        .order_by(ChatMessage.created_at.asc())
    )
    return list(result.scalars().all())


def _messages_to_dicts(messages: list[ChatMessage]) -> list[dict]:
    """Convert ChatMessage models to template-friendly dicts with parsed events."""
    out = []
    for msg in messages:
        events = []
        if msg.events_json and msg.events_json != "[]":
            try:
                events = json.loads(msg.events_json)
            except (json.JSONDecodeError, TypeError):
                pass
        out.append({
            "role": msg.role,
            "content": msg.content,
            "events": events,
        })
    return out


def _get_notes(request: Request, chat_id: UUID) -> str:
    return request.session.get(f"{_NOTES_SESSION_PREFIX}{chat_id}", "")


def _set_notes(request: Request, chat_id: UUID, notes: str) -> None:
    request.session[f"{_NOTES_SESSION_PREFIX}{chat_id}"] = notes


class ChatController(Controller):
    path = "/chat"

    @get("/")
    async def chat_index(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        user = await _get_user(request, db_session)
        if not user:
            return TemplateResponse("chat.html", context={
                "messages": [],
                "recent_chats": [],
                "chat_id": None,
                "notes": "",
            })
        recent_chats = await _get_recent_chats(user.id, db_session)
        return TemplateResponse("chat.html", context={
            "messages": [],
            "recent_chats": recent_chats,
            "chat_id": None,
            "notes": "",
        })

    @get("/{chat_id:uuid}")
    async def chat_view(
        self, request: Request, db_session: AsyncSession, chat_id: UUID
    ) -> TemplateResponse | Redirect:
        user = await _get_user(request, db_session)
        if not user:
            return Redirect(path="/chat/")

        # Verify ownership
        result = await db_session.execute(
            select(ChatSession).where(
                ChatSession.id == chat_id, ChatSession.user_id == user.id
            )
        )
        chat = result.scalar_one_or_none()
        if not chat:
            return Redirect(path="/chat/")

        db_messages = await _get_chat_messages(chat_id, db_session)
        recent_chats = await _get_recent_chats(user.id, db_session)
        notes = _get_notes(request, chat_id)

        return TemplateResponse("chat.html", context={
            "messages": _messages_to_dicts(db_messages),
            "recent_chats": recent_chats,
            "chat_id": str(chat_id),
            "chat_title": chat.title,
            "notes": notes,
        })

    @post("/send")
    async def send_message(
        self, request: Request, db_session: AsyncSession
    ) -> ServerSentEvent:
        body = await request.json()
        user_message = body.get("message", "").strip()
        chat_id_str = body.get("chat_id", "")

        if not user_message:
            async def _empty():
                yield ServerSentEventMessage(event="error", data="Empty message")
            return ServerSentEvent(_empty())

        user = await _get_user(request, db_session)
        if not user:
            async def _no_auth():
                yield ServerSentEventMessage(
                    event="error",
                    data=json.dumps({"error": "Not authenticated"}),
                )
            return ServerSentEvent(_no_auth())

        # Resolve or create chat session
        chat_id: UUID | None = None
        is_new_chat = False

        if chat_id_str:
            chat_id = UUID(chat_id_str)
            # Verify ownership
            result = await db_session.execute(
                select(ChatSession).where(
                    ChatSession.id == chat_id, ChatSession.user_id == user.id
                )
            )
            if not result.scalar_one_or_none():
                async def _not_found():
                    yield ServerSentEventMessage(
                        event="error",
                        data=json.dumps({"error": "Chat not found"}),
                    )
                return ServerSentEvent(_not_found())
        else:
            # Create new chat session
            is_new_chat = True
            chat = ChatSession(
                user_id=user.id,
                title=user_message[:100],
                mode="chat",
            )
            db_session.add(chat)
            await db_session.flush()
            chat_id = chat.id

        # Save user message
        user_msg = ChatMessage(
            chat_id=chat_id,
            role="user",
            content=user_message,
        )
        db_session.add(user_msg)
        await db_session.commit()

        # Load history from DB
        all_messages = await _get_chat_messages(chat_id, db_session)
        history = []
        for msg in all_messages[:-1]:  # Exclude the message we just added
            role = "model" if msg.role == "assistant" else msg.role
            if msg.content:
                history.append({"role": role, "content": msg.content})

        notes = _get_notes(request, chat_id)
        final_chat_id = chat_id

        async def _stream():
            accumulated_text = ""
            accumulated_events: list[dict] = []
            new_notes = notes

            try:
                # Send chat_id first so client can update URL
                if is_new_chat:
                    yield ServerSentEventMessage(
                        event="chat_created",
                        data=json.dumps({"chat_id": str(final_chat_id)}),
                    )

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
                        accumulated_events.append({
                            "tool": event.tool,
                            "summary": event.summary,
                        })
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
                        # Save assistant message to DB
                        assistant_msg = ChatMessage(
                            chat_id=final_chat_id,
                            role="assistant",
                            content=accumulated_text,
                            events_json=json.dumps(accumulated_events),
                        )
                        db_session.add(assistant_msg)
                        await db_session.commit()
                        _set_notes(request, final_chat_id, new_notes)
                        yield ServerSentEventMessage(
                            event="done",
                            data=json.dumps({
                                "chat_id": str(final_chat_id),
                                "events": accumulated_events,
                            }),
                        )
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

    @post("/{chat_id:uuid}/delete")
    async def delete_chat(
        self, request: Request, db_session: AsyncSession, chat_id: UUID
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "Not authenticated"}, status_code=401)

        result = await db_session.execute(
            select(ChatSession).where(
                ChatSession.id == chat_id, ChatSession.user_id == user.id
            )
        )
        chat = result.scalar_one_or_none()
        if not chat:
            return Response(content={"error": "Not found"}, status_code=404)

        # Delete messages then session
        messages = await _get_chat_messages(chat_id, db_session)
        for msg in messages:
            await db_session.delete(msg)
        await db_session.delete(chat)
        await db_session.commit()

        # Clean up session notes
        request.session.pop(f"{_NOTES_SESSION_PREFIX}{chat_id}", None)

        return Response(content={"ok": True}, status_code=200)
