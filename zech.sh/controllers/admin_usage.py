"""Admin controller for viewing token usage across chat sessions."""

from __future__ import annotations

import json
from collections import defaultdict

from litestar import Controller, Request, get
from litestar.response import Template as TemplateResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.auth.guards import auth_guard, Permission
from skrift.admin.helpers import get_admin_context
from skrift.admin.navigation import ADMIN_NAV_TAG
from skrift.db.models.user import User

from models.chat import ChatMessage, ChatSession


def _extract_usage(usage_json: str) -> dict:
    """Extract flat usage metrics from either chat or research format.

    Chat mode (flat): {"input_tokens": N, "output_tokens": N, "input_cost": "0.001", ...}
    Research mode (nested): {"total": {"input_tokens": N, ...}, "research": {...}, ...}
    """
    try:
        data = json.loads(usage_json)
    except (json.JSONDecodeError, TypeError):
        return {"input_tokens": 0, "output_tokens": 0, "input_cost": 0.0, "output_cost": 0.0}

    if not data:
        return {"input_tokens": 0, "output_tokens": 0, "input_cost": 0.0, "output_cost": 0.0}

    # Research mode: use the "total" key
    if "total" in data and isinstance(data["total"], dict):
        data = data["total"]

    return {
        "input_tokens": int(data.get("input_tokens", 0) or 0),
        "output_tokens": int(data.get("output_tokens", 0) or 0),
        "input_cost": float(data.get("input_cost", 0) or 0),
        "output_cost": float(data.get("output_cost", 0) or 0),
    }


class UsageAdminController(Controller):
    """Admin controller for viewing token usage data."""

    path = "/admin"
    guards = [auth_guard]

    @get(
        "/usage",
        tags=[ADMIN_NAV_TAG],
        guards=[auth_guard, Permission("modify-site")],
        opt={"label": "Usage", "icon": "bar-chart", "order": 80},
    )
    async def usage_dashboard(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        """Show aggregated token usage across all chat sessions."""
        ctx = await get_admin_context(request, db_session)

        # Fetch all sessions
        sessions_result = await db_session.execute(
            select(ChatSession).order_by(ChatSession.created_at.desc())
        )
        sessions = list(sessions_result.scalars().all())

        # Fetch assistant messages with usage data
        messages_result = await db_session.execute(
            select(ChatMessage).where(
                ChatMessage.role == "assistant",
                ChatMessage.usage_json != "{}",
                ChatMessage.usage_json != "",
            )
        )
        messages = list(messages_result.scalars().all())

        # Resolve user names
        user_ids = {s.user_id for s in sessions}
        users_result = await db_session.execute(
            select(User).where(User.id.in_(user_ids))
        )
        user_map = {u.id: u for u in users_result.scalars().all()}

        # Aggregate per-chat
        chat_usage: dict[str, dict] = defaultdict(lambda: {
            "input_tokens": 0, "output_tokens": 0,
            "input_cost": 0.0, "output_cost": 0.0,
            "response_count": 0,
        })

        for msg in messages:
            usage = _extract_usage(msg.usage_json)
            entry = chat_usage[msg.chat_id]
            entry["input_tokens"] += usage["input_tokens"]
            entry["output_tokens"] += usage["output_tokens"]
            entry["input_cost"] += usage["input_cost"]
            entry["output_cost"] += usage["output_cost"]
            entry["response_count"] += 1

        # Build per-chat data list
        chat_data = []
        for session in sessions:
            usage = chat_usage.get(session.id)
            if not usage:
                continue
            chat_data.append({
                "title": session.title[:80] if session.title else "Untitled",
                "mode": session.mode,
                "created_at": session.created_at,
                "response_count": usage["response_count"],
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "total_cost": usage["input_cost"] + usage["output_cost"],
            })

        # Aggregate per-user
        user_usage: dict[str, dict] = defaultdict(lambda: {
            "input_tokens": 0, "output_tokens": 0,
            "input_cost": 0.0, "output_cost": 0.0,
            "session_count": 0, "response_count": 0,
        })

        # Track which sessions each user owns
        user_session_ids: dict[str, set] = defaultdict(set)
        for session in sessions:
            if session.id in chat_usage:
                user_session_ids[session.user_id].add(session.id)

        for session in sessions:
            usage = chat_usage.get(session.id)
            if not usage:
                continue
            entry = user_usage[session.user_id]
            entry["input_tokens"] += usage["input_tokens"]
            entry["output_tokens"] += usage["output_tokens"]
            entry["input_cost"] += usage["input_cost"]
            entry["output_cost"] += usage["output_cost"]
            entry["response_count"] += usage["response_count"]

        # Set session counts
        for uid, sids in user_session_ids.items():
            user_usage[uid]["session_count"] = len(sids)

        # Build per-user data list
        user_data = []
        for uid, usage in sorted(user_usage.items(), key=lambda x: x[1]["input_cost"] + x[1]["output_cost"], reverse=True):
            user = user_map.get(uid)
            user_data.append({
                "name": (user.name or user.email or str(uid)) if user else str(uid),
                "session_count": usage["session_count"],
                "response_count": usage["response_count"],
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "input_cost": usage["input_cost"],
                "output_cost": usage["output_cost"],
                "total_cost": usage["input_cost"] + usage["output_cost"],
            })

        # Grand totals
        grand_totals = {
            "input_tokens": sum(u["input_tokens"] for u in user_usage.values()),
            "output_tokens": sum(u["output_tokens"] for u in user_usage.values()),
            "input_cost": sum(u["input_cost"] for u in user_usage.values()),
            "output_cost": sum(u["output_cost"] for u in user_usage.values()),
            "total_cost": sum(u["input_cost"] + u["output_cost"] for u in user_usage.values()),
            "session_count": len([s for s in sessions if s.id in chat_usage]),
            "response_count": sum(u["response_count"] for u in user_usage.values()),
        }

        return TemplateResponse(
            "admin/usage.html",
            context={
                "chat_data": chat_data,
                "user_data": user_data,
                "grand_totals": grand_totals,
                **ctx,
            },
        )
