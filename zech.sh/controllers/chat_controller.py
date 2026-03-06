"""Chat controller for zech.sh — notification-driven conversational AI with tools.

Chat history is persisted via ChatSession/ChatMessage models. The AI agent
runs as a background asyncio task, pushing events through Skrift's
time-series notification system for resilient, reconnectable streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from uuid import UUID

from litestar import Controller, Request, get, post
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from skrift.auth.session_keys import SESSION_USER_ID
from skrift.config import get_settings
from skrift.db.models.user import User
from skrift.lib.notifications import NotificationMode, notify_user

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
_NM = NotificationMode.TIMESERIES

# ---------------------------------------------------------------------------
# Background task infrastructure
# ---------------------------------------------------------------------------

_session_factory: async_sessionmaker | None = None


def _get_session_factory() -> async_sessionmaker:
    global _session_factory
    if _session_factory is None:
        engine = create_async_engine(
            get_settings().db.url, pool_pre_ping=True, pool_recycle=300
        )
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


_active_chats: dict[UUID, asyncio.Task] = {}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Background chat agent task
# ---------------------------------------------------------------------------


async def _run_chat_bg(
    user_id: UUID,
    chat_id: UUID,
    memory_notes: str = "",
) -> None:
    """Run the chat agent as a background task, pushing notifications."""
    uid = str(user_id)
    cid = str(chat_id)

    async def _notify(ntype: str, **payload: object) -> None:
        payload["chat_id"] = cid
        await notify_user(uid, ntype, mode=_NM, **payload)

    try:
        async with _get_session_factory()() as db_session:
            # Load messages for history
            result = await db_session.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id == chat_id)
                .order_by(ChatMessage.created_at.asc())
            )
            messages = list(result.scalars().all())
            if not messages or messages[-1].role != "user":
                return

            user_message = messages[-1].content
            history = []
            for msg in messages[:-1]:
                role = "model" if msg.role == "assistant" else msg.role
                if msg.content:
                    history.append({"role": role, "content": msg.content})

            accumulated_text = ""
            accumulated_events: list[dict] = []
            new_notes = memory_notes

            try:
                async for event in run_chat(user_message, history, memory_notes):
                    if isinstance(event, ThinkingEvent):
                        await _notify("chat:thinking", thinking=event.thinking)
                    elif isinstance(event, ToolStartEvent):
                        await _notify(
                            "chat:tool_start",
                            tool=event.tool,
                            args=event.args,
                        )
                    elif isinstance(event, ToolDoneEvent):
                        accumulated_events.append({
                            "tool": event.tool,
                            "summary": event.summary,
                        })
                        await _notify(
                            "chat:tool_done",
                            tool=event.tool,
                            summary=event.summary,
                        )
                    elif isinstance(event, TextEvent):
                        accumulated_text += event.text
                        await _notify("chat:text", text=event.text)
                    elif isinstance(event, NotesEvent):
                        new_notes = event.notes
                    elif isinstance(event, CompactEvent):
                        await _notify(
                            "chat:compact",
                            removed_messages=event.removed_messages,
                            summary_tokens=event.summary_tokens,
                        )
                    elif isinstance(event, DoneEvent):
                        # Save assistant message to DB
                        assistant_msg = ChatMessage(
                            chat_id=chat_id,
                            role="assistant",
                            content=accumulated_text,
                            events_json=json.dumps(accumulated_events),
                        )
                        db_session.add(assistant_msg)

                        # Update last_notification_at for replay cursor
                        chat_result = await db_session.execute(
                            select(ChatSession).where(ChatSession.id == chat_id)
                        )
                        chat_obj = chat_result.scalar_one_or_none()
                        if chat_obj:
                            chat_obj.last_notification_at = time.time()

                        await db_session.commit()
                        await _notify(
                            "chat:done",
                            events=accumulated_events,
                            notes=new_notes,
                        )
                    elif isinstance(event, ErrorEvent):
                        await _notify("chat:error", error=event.error)
            except Exception as exc:
                logger.exception("Chat agent error for chat %s", cid)
                await _notify("chat:error", error=str(exc))
    except Exception:
        logger.exception("Chat background task error for chat %s", cid)
        try:
            await _notify("chat:error", error="Internal error")
        except Exception:
            pass
    finally:
        _active_chats.pop(chat_id, None)


def _start_chat_task(
    user_id: UUID, chat_id: UUID, memory_notes: str = ""
) -> None:
    """Create and register a background chat task."""
    if chat_id in _active_chats:
        return
    task = asyncio.create_task(_run_chat_bg(user_id, chat_id, memory_notes))
    _active_chats[chat_id] = task


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


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
                "needs_stream": False,
                "last_notification_at": 0,
                "notes": "",
            })
        recent_chats = await _get_recent_chats(user.id, db_session)
        return TemplateResponse("chat.html", context={
            "messages": [],
            "recent_chats": recent_chats,
            "chat_id": None,
            "needs_stream": False,
            "last_notification_at": 0,
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

        needs_stream = bool(db_messages) and db_messages[-1].role == "user"

        # Crash recovery: needs_stream but no active task → restart
        if needs_stream and chat_id not in _active_chats:
            _start_chat_task(user.id, chat_id, notes)

        return TemplateResponse("chat.html", context={
            "messages": _messages_to_dicts(db_messages),
            "recent_chats": recent_chats,
            "chat_id": str(chat_id),
            "chat_title": chat.title,
            "needs_stream": needs_stream,
            "last_notification_at": chat.last_notification_at or 0,
            "notes": notes,
        })

    @post("/send")
    async def send_message(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        body = await request.json()
        user_message = body.get("message", "").strip()
        chat_id_str = body.get("chat_id", "")

        if not user_message:
            return Response(content={"error": "Empty message"}, status_code=400)

        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "Not authenticated"}, status_code=401)

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
                return Response(content={"error": "Chat not found"}, status_code=404)
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

        # Get memory notes and start background task
        notes = _get_notes(request, chat_id)
        _start_chat_task(user.id, chat_id, notes)

        return Response(
            content={
                "chat_id": str(chat_id),
                "is_new_chat": is_new_chat,
            },
            status_code=201,
        )

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

        # Cancel active task if any
        task = _active_chats.pop(chat_id, None)
        if task:
            task.cancel()

        # Delete messages then session
        messages = await _get_chat_messages(chat_id, db_session)
        for msg in messages:
            await db_session.delete(msg)
        await db_session.delete(chat)
        await db_session.commit()

        request.session.pop(f"{_NOTES_SESSION_PREFIX}{chat_id}", None)

        return Response(content={"ok": True}, status_code=200)
