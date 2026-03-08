import os
import secrets
from datetime import datetime, timezone
from uuid import UUID

from litestar import Controller, Request, get, post
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.handlers import BaseRouteHandler
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.auth.guards import ADMINISTRATOR_PERMISSION
from skrift.auth.roles import register_role
from skrift.auth.services import get_user_permissions
from skrift.auth.session_keys import SESSION_USER_ID
from skrift.db.models.user import User
from skrift.lib.notifications import NotificationMode, notify_user

from models.ai_chat import AiChatMessage

# ---------------------------------------------------------------------------
# Role registration
# ---------------------------------------------------------------------------

register_role(
    "ai-chatter", "use-ai-chat",
    display_name="AI Chatter",
    description="Access to AI Chat",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_user(request: Request, db_session: AsyncSession) -> User | None:
    user_id = request.session.get(SESSION_USER_ID)
    if not user_id:
        return None
    result = await db_session.execute(
        select(User).where(User.id == UUID(user_id))
    )
    return result.scalar_one_or_none()


async def _has_permission(user_id: UUID, db_session: AsyncSession) -> bool:
    perms = await get_user_permissions(db_session, str(user_id))
    if ADMINISTRATOR_PERMISSION in perms.permissions:
        return True
    return "use-ai-chat" in perms.permissions


async def _get_target_user_id(db_session: AsyncSession) -> str | None:
    """Get the user_id from the most recent user message for notification targeting."""
    result = await db_session.execute(
        select(AiChatMessage.user_id)
        .where(AiChatMessage.sender == "user")
        .where(AiChatMessage.user_id.is_not(None))
        .order_by(AiChatMessage.created_at.desc())
        .limit(1)
    )
    uid = result.scalar_one_or_none()
    return str(uid) if uid else None


# ---------------------------------------------------------------------------
# API guard — shared secret
# ---------------------------------------------------------------------------


async def aichat_secret_guard(
    connection: ASGIConnection, _handler: BaseRouteHandler
) -> None:
    secret = os.environ.get("AICHAT_SECRET", "")
    auth_header = connection.headers.get("authorization", "")
    token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    if not secret or not secrets.compare_digest(token, secret):
        raise NotAuthorizedException("Invalid secret")


# ---------------------------------------------------------------------------
# Web UI controller
# ---------------------------------------------------------------------------


class AiChatController(Controller):
    """User-facing web UI for AI chat."""

    path = "/"

    @get("/")
    async def index(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse | Redirect:
        user = await _get_user(request, db_session)
        if not user:
            return Redirect("https://zech.sh/auth/login?next=https://aichat.zech.sh/")

        if not await _has_permission(user.id, db_session):
            return TemplateResponse("unauthorized.html", context={"user": user})

        result = await db_session.execute(
            select(AiChatMessage)
            .order_by(AiChatMessage.created_at.asc())
        )
        messages = list(result.scalars().all())

        return TemplateResponse(
            "aichat.html",
            context={
                "user": user,
                "messages": messages,
                "hide_sidebar": True,
            },
        )

    @post("/send")
    async def send_message(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)

        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        body = await request.json()
        content = body.get("content", "").strip()
        if not content:
            return Response(content={"error": "empty message"}, status_code=400)

        msg = AiChatMessage(
            sender="user",
            content=content,
            user_id=user.id,
        )
        db_session.add(msg)
        await db_session.flush()

        await notify_user(
            str(user.id),
            "aichat:message",
            mode=NotificationMode.EPHEMERAL,
            sender="user",
            content=content,
            message_id=str(msg.id),
        )

        await db_session.commit()
        return Response(content={"ok": True})


# ---------------------------------------------------------------------------
# API controller (Claude-facing)
# ---------------------------------------------------------------------------


class AiChatApiController(Controller):
    """JSON API for Claude to read and send messages."""

    path = "/api"
    guards = [aichat_secret_guard]

    @get("/messages")
    async def get_messages(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        before = request.query_params.get("before")
        limit = min(int(request.query_params.get("limit", "10")), 50)

        query = select(AiChatMessage).order_by(AiChatMessage.created_at.desc())
        if before:
            before_msg = await db_session.get(AiChatMessage, UUID(before))
            if before_msg:
                query = query.where(AiChatMessage.created_at < before_msg.created_at)
        query = query.limit(limit)

        result = await db_session.execute(query)
        messages = list(result.scalars().all())

        # Mark user messages as read
        now = datetime.now(timezone.utc)
        read_ids = []
        target_user_id = None
        for msg in messages:
            if msg.sender == "user" and msg.read_by_claude_at is None:
                msg.read_by_claude_at = now
                read_ids.append(str(msg.id))
                if msg.user_id:
                    target_user_id = str(msg.user_id)

        if read_ids and target_user_id:
            await notify_user(
                target_user_id,
                "aichat:read",
                mode=NotificationMode.EPHEMERAL,
                message_ids=read_ids,
            )

        await db_session.commit()

        return Response(content=[
            {
                "id": str(m.id),
                "sender": m.sender,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "read_by_claude_at": m.read_by_claude_at.isoformat() if m.read_by_claude_at else None,
            }
            for m in reversed(messages)  # Return chronological order
        ])

    @get("/messages/unread")
    async def get_unread(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        result = await db_session.execute(
            select(AiChatMessage)
            .where(AiChatMessage.sender == "user")
            .where(AiChatMessage.read_by_claude_at.is_(None))
            .order_by(AiChatMessage.created_at.asc())
        )
        messages = list(result.scalars().all())

        now = datetime.now(timezone.utc)
        read_ids = []
        target_user_id = None
        for msg in messages:
            msg.read_by_claude_at = now
            read_ids.append(str(msg.id))
            if msg.user_id:
                target_user_id = str(msg.user_id)

        if read_ids and target_user_id:
            await notify_user(
                target_user_id,
                "aichat:read",
                mode=NotificationMode.EPHEMERAL,
                message_ids=read_ids,
            )

        await db_session.commit()

        return Response(content=[
            {
                "id": str(m.id),
                "sender": m.sender,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ])

    @post("/messages")
    async def create_message(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        body = await request.json()
        content = body.get("content", "").strip()
        if not content:
            return Response(content={"error": "empty message"}, status_code=400)

        msg = AiChatMessage(
            sender="claude",
            content=content,
        )
        db_session.add(msg)
        await db_session.flush()

        target_user_id = await _get_target_user_id(db_session)
        if target_user_id:
            await notify_user(
                target_user_id,
                "aichat:message",
                mode=NotificationMode.EPHEMERAL,
                sender="claude",
                content=content,
                message_id=str(msg.id),
            )

        await db_session.commit()
        return Response(
            content={"ok": True, "id": str(msg.id)},
            status_code=201,
        )
