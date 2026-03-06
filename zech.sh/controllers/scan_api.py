"""JSON API for programmatic access to the research pipeline."""

from __future__ import annotations

import json
from uuid import UUID

from litestar import Controller, Request, get, post
from litestar.response import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.auth.guards import ADMINISTRATOR_PERMISSION
from skrift.auth.services import get_user_permissions

from controllers.api_auth import api_key_guard, get_api_user_id
from controllers.scan import _active_research, _start_pipeline_task
from controllers.scan_agent import generate_chat_title
from models.chat import ChatMessage, ChatSession

_API_MODE_PERMISSION: dict[str, str] = {
    "lite": "use-research",
    "deep": "use-deep-research",
}

_CHAT_MODE_PERMISSION: dict[str, str] = {
    "research": "use-research",
    "deep_research": "use-deep-research",
}


async def _check_api_permission(user_id: UUID, db_session: AsyncSession, permission: str) -> bool:
    perms = await get_user_permissions(db_session, str(user_id))
    if ADMINISTRATOR_PERMISSION in perms.permissions:
        return True
    return permission in perms.permissions


class ResearchApiController(Controller):
    """API endpoints for triggering and retrieving research results."""

    path = "/api"
    guards = [api_key_guard]

    @post("/research")
    async def create_research(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        """Start a new research job."""
        user_id = get_api_user_id(request)
        body = await request.json()

        query = (body.get("query") or "").strip()
        if not query:
            return Response(
                content={"error": "query is required"}, status_code=400
            )

        mode = body.get("mode", "lite")
        if mode == "deep":
            chat_mode = "deep_research"
        elif mode == "lite":
            chat_mode = "research"
        else:
            return Response(
                content={"error": "mode must be 'lite' or 'deep'"},
                status_code=400,
            )

        required_perm = _API_MODE_PERMISSION.get(mode)
        if required_perm and not await _check_api_permission(user_id, db_session, required_perm):
            return Response(content={"error": "forbidden"}, status_code=403)

        title = await generate_chat_title(query)
        chat = ChatSession(user_id=user_id, title=title, mode=chat_mode)
        db_session.add(chat)
        await db_session.flush()

        user_msg = ChatMessage(chat_id=chat.id, role="user", content=query)
        db_session.add(user_msg)
        await db_session.commit()

        _start_pipeline_task(user_id, chat.id, chat_mode)

        return Response(
            content={
                "id": str(chat.id),
                "url": f"/chat/{chat.id}",
                "title": title,
            },
            status_code=201,
        )

    @get("/research/{research_id:uuid}")
    async def get_research(
        self, request: Request, db_session: AsyncSession, research_id: UUID
    ) -> Response:
        """Retrieve the status and result of a research job."""
        user_id = get_api_user_id(request)

        # Verify ownership
        result = await db_session.execute(
            select(ChatSession).where(
                ChatSession.id == research_id, ChatSession.user_id == user_id
            )
        )
        chat = result.scalar_one_or_none()
        if chat is None:
            return Response(
                content={"error": "not found"}, status_code=404
            )

        required_perm = _CHAT_MODE_PERMISSION.get(chat.mode)
        if required_perm and not await _check_api_permission(user_id, db_session, required_perm):
            return Response(content={"error": "forbidden"}, status_code=403)

        # Fetch messages
        msg_result = await db_session.execute(
            select(ChatMessage)
            .where(ChatMessage.chat_id == research_id)
            .order_by(ChatMessage.created_at.asc())
        )
        messages = list(msg_result.scalars().all())

        # Determine status
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        user_msgs = [m for m in messages if m.role == "user"]

        if research_id in _active_research:
            status = "running"
        elif assistant_msgs and assistant_msgs[-1].content:
            status = "completed"
        elif assistant_msgs:
            # Assistant message exists but empty content — check for errors
            try:
                events = json.loads(assistant_msgs[-1].events_json or "[]")
            except (json.JSONDecodeError, TypeError):
                events = []
            has_error = any(e.get("type") == "error" for e in events)
            status = "failed" if has_error else "completed"
        elif user_msgs:
            # User message exists but no assistant response and not active
            # Crash recovery: restart pipeline
            _start_pipeline_task(user_id, research_id, chat.mode)
            status = "pending"
        else:
            status = "pending"

        # Build response
        response_data: dict = {
            "id": str(research_id),
            "status": status,
            "title": chat.title,
            "mode": chat.mode,
            "created_at": chat.created_at.isoformat(),
            "result": None,
        }

        if status == "completed" and assistant_msgs:
            last_assistant = assistant_msgs[-1]

            # Extract sources from events
            try:
                events = json.loads(last_assistant.events_json or "[]")
            except (json.JSONDecodeError, TypeError):
                events = []
            sources = [
                e["url"]
                for e in events
                if e.get("type") == "detail"
                and e.get("detail_type") == "fetch_done"
                and e.get("url")
                and not e.get("failed")
            ]

            # Extract usage
            try:
                usage = json.loads(last_assistant.usage_json or "{}")
            except (json.JSONDecodeError, TypeError):
                usage = {}

            response_data["result"] = {
                "content": last_assistant.content,
                "sources": sources,
                "usage": usage,
            }

        return Response(content=response_data, status_code=200)
