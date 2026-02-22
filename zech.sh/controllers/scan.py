import json
import os
import re
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from urllib.parse import quote_plus
from uuid import UUID

from litestar import Controller, Request, get, post
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse
from litestar.response.sse import ServerSentEvent, ServerSentEventMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.auth.session_keys import SESSION_USER_ID
from skrift.config import get_settings
from skrift.db.models.user import User

from controllers.scan_agent import (
    ClarificationEvent,
    DetailEvent,
    DoneEvent,
    ErrorEvent,
    StageEvent,
    TextEvent,
    classify_query,
    run_research_pipeline,
)
from models.chat import ChatMessage, ChatSession

_RECENT_CHATS_LIMIT = 10
_HISTORY_PAGE_SIZE = 20


def build_redirect_url(classification: str, query: str) -> str:
    """Build the redirect URL based on classification."""
    if classification == "URL":
        cleaned = re.sub(r"^https?://", "", query.strip())
        return f"https://{cleaned}"
    else:
        return f"https://www.google.com/search?q={quote_plus(query)}"


OPENSEARCH_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">
  <ShortName>Scan</ShortName>
  <Description>Smart search relay by zech.sh</Description>
  <Url type="text/html" method="get" template="https://scan.zech.sh/search?q={searchTerms}"/>
</OpenSearchDescription>"""


async def _get_user(request: Request, db_session: AsyncSession) -> User | None:
    user_id = request.session.get(SESSION_USER_ID)
    if not user_id:
        return None
    result = await db_session.execute(
        select(User).where(User.id == UUID(user_id))
    )
    return result.scalar_one_or_none()


async def _get_recent_chats(
    user_id: UUID, db_session: AsyncSession, limit: int = _RECENT_CHATS_LIMIT
) -> list[ChatSession]:
    result = await db_session.execute(
        select(ChatSession)
        .where(ChatSession.user_id == user_id)
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


class ScanController(Controller):
    path = "/"

    @get("/")
    async def index(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        user = await _get_user(request, db_session)
        if not user:
            return TemplateResponse("unauthorized.html")
        recent_chats = await _get_recent_chats(user.id, db_session)
        return TemplateResponse(
            "index.html", context={"user": user, "recent_chats": recent_chats}
        )

    @get("/search")
    async def search(
        self, request: Request, db_session: AsyncSession, q: str = ""
    ) -> Response | Redirect | TemplateResponse:
        user = await _get_user(request, db_session)
        if not user:
            return TemplateResponse("unauthorized.html")
        if not q.strip():
            return Redirect(path="/")

        classification = await classify_query(q)
        accept = request.headers.get("accept", "")
        is_json = "application/json" in accept

        if classification == "RESEARCH":
            chat = ChatSession(user_id=user.id, title=q.strip()[:500])
            db_session.add(chat)
            await db_session.flush()

            user_msg = ChatMessage(
                chat_id=chat.id, role="user", content=q.strip()
            )
            db_session.add(user_msg)
            await db_session.commit()

            chat_url = f"/chat/{chat.id}"
            if is_json:
                return Response(
                    content={"url": chat_url, "type": "research"},
                    status_code=200,
                )
            return Redirect(path=chat_url)

        url = build_redirect_url(classification, q)
        if is_json:
            return Response(content={"url": url}, status_code=200)
        return Redirect(path=url)

    @get("/chat/{chat_id:uuid}")
    async def chat_view(
        self, request: Request, db_session: AsyncSession, chat_id: UUID
    ) -> TemplateResponse | Redirect:
        user = await _get_user(request, db_session)
        if not user:
            return TemplateResponse("unauthorized.html")

        result = await db_session.execute(
            select(ChatSession).where(
                ChatSession.id == chat_id, ChatSession.user_id == user.id
            )
        )
        chat = result.scalar_one_or_none()
        if not chat:
            return Redirect(path="/")

        messages = await _get_chat_messages(chat_id, db_session)
        recent_chats = await _get_recent_chats(user.id, db_session)

        needs_stream = bool(messages) and messages[-1].role == "user"

        return TemplateResponse(
            "chat.html",
            context={
                "user": user,
                "chat": chat,
                "messages": messages,
                "needs_stream": needs_stream,
                "recent_chats": recent_chats,
            },
        )

    @post("/chat/{chat_id:uuid}/message")
    async def add_message(
        self, request: Request, db_session: AsyncSession, chat_id: UUID
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)

        result = await db_session.execute(
            select(ChatSession).where(
                ChatSession.id == chat_id, ChatSession.user_id == user.id
            )
        )
        chat = result.scalar_one_or_none()
        if not chat:
            return Response(content={"error": "not found"}, status_code=404)

        body = await request.json()
        content = body.get("content", "").strip()
        if not content:
            return Response(content={"error": "empty message"}, status_code=400)

        msg = ChatMessage(chat_id=chat_id, role="user", content=content)
        db_session.add(msg)
        chat.updated_at = datetime.now(timezone.utc)
        await db_session.commit()

        return Response(
            content={"id": str(msg.id), "content": content},
            status_code=201,
        )

    @get("/chat/{chat_id:uuid}/stream")
    async def chat_stream(
        self,
        request: Request,
        db_session: AsyncSession,
        chat_id: UUID,
        context: str = "",
    ) -> ServerSentEvent | TemplateResponse:
        user_id = request.session.get(SESSION_USER_ID)
        if not user_id:
            return TemplateResponse("unauthorized.html")

        result = await db_session.execute(
            select(ChatSession).where(
                ChatSession.id == chat_id,
                ChatSession.user_id == UUID(user_id),
            )
        )
        chat = result.scalar_one_or_none()
        if not chat:
            return ServerSentEvent(iter([]))

        messages = await _get_chat_messages(chat_id, db_session)
        if not messages or messages[-1].role != "user":
            return ServerSentEvent(iter([]))

        latest_user_msg = messages[-1]
        query = latest_user_msg.content

        history = []
        for msg in messages[:-1]:
            if msg.content:
                history.append({"role": msg.role, "content": msg.content})

        brave_api_key = os.environ.get("BRAVE_API_KEY", "")
        redis_url = get_settings().redis.url

        async def generate() -> AsyncGenerator[ServerSentEventMessage, None]:
            accumulated_text = ""
            accumulated_events: list[dict] = []
            usage_data: dict = {}

            try:
                async for event in run_research_pipeline(
                    query,
                    brave_api_key,
                    db_session=db_session,
                    redis_url=redis_url,
                    additional_context=context,
                    conversation_history=history if history else None,
                ):
                    if isinstance(event, StageEvent):
                        accumulated_events.append(
                            {"type": "stage", "stage": event.stage}
                        )
                        yield ServerSentEventMessage(
                            data=json.dumps({"stage": event.stage}),
                            event="stage",
                        )
                    elif isinstance(event, DetailEvent):
                        accumulated_events.append(
                            {"type": "detail", "detail_type": event.type, **event.payload}
                        )
                        yield ServerSentEventMessage(
                            data=json.dumps({"type": event.type, **event.payload}),
                            event="detail",
                        )
                        if event.type == "usage":
                            usage_data = event.payload
                    elif isinstance(event, TextEvent):
                        accumulated_text += event.text
                        yield ServerSentEventMessage(
                            data=json.dumps({"text": event.text}),
                            event="text",
                        )
                    elif isinstance(event, DoneEvent):
                        assistant_msg = ChatMessage(
                            chat_id=chat_id,
                            role="assistant",
                            content=accumulated_text,
                            events_json=json.dumps(accumulated_events),
                            usage_json=json.dumps(usage_data),
                        )
                        db_session.add(assistant_msg)
                        await db_session.commit()
                        yield ServerSentEventMessage(data="", event="done")
                    elif isinstance(event, ClarificationEvent):
                        accumulated_events.append(
                            {"type": "clarification", "questions": event.questions}
                        )
                        yield ServerSentEventMessage(
                            data=json.dumps({"questions": event.questions}),
                            event="clarification",
                        )
                    elif isinstance(event, ErrorEvent):
                        accumulated_events.append(
                            {"type": "error", "error": event.error}
                        )
                        yield ServerSentEventMessage(
                            data=json.dumps({"error": event.error}),
                            event="error",
                        )
            except Exception as exc:
                yield ServerSentEventMessage(
                    data=json.dumps({"error": str(exc)}),
                    event="error",
                )

        return ServerSentEvent(generate())

    @get("/history")
    async def history(
        self, request: Request, db_session: AsyncSession, page: int = 1
    ) -> TemplateResponse:
        user = await _get_user(request, db_session)
        if not user:
            return TemplateResponse("unauthorized.html")

        if page < 1:
            page = 1

        offset = (page - 1) * _HISTORY_PAGE_SIZE

        count_result = await db_session.execute(
            select(func.count(ChatSession.id)).where(
                ChatSession.user_id == user.id
            )
        )
        total = count_result.scalar() or 0
        total_pages = max(1, (total + _HISTORY_PAGE_SIZE - 1) // _HISTORY_PAGE_SIZE)

        result = await db_session.execute(
            select(ChatSession)
            .where(ChatSession.user_id == user.id)
            .order_by(ChatSession.updated_at.desc())
            .offset(offset)
            .limit(_HISTORY_PAGE_SIZE)
        )
        chats = list(result.scalars().all())

        recent_chats = await _get_recent_chats(user.id, db_session)

        return TemplateResponse(
            "history.html",
            context={
                "user": user,
                "chats": chats,
                "page": page,
                "total_pages": total_pages,
                "total": total,
                "recent_chats": recent_chats,
            },
        )

    @get("/opensearch.xml")
    async def opensearch(self) -> Response:
        return Response(
            content=OPENSEARCH_XML,
            media_type="application/opensearchdescription+xml",
        )
