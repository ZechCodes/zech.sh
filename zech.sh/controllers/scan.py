import asyncio
import json
import logging
import os
import re
import time
from collections import OrderedDict
from datetime import datetime, timezone
from urllib.parse import quote_plus
from uuid import UUID

from litestar import Controller, Request, get, post
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse
from litestar.response.sse import ServerSentEvent, ServerSentEventMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from skrift.auth.guards import ADMINISTRATOR_PERMISSION
from skrift.auth.services import get_user_permissions
from skrift.auth.session_keys import SESSION_USER_ID
from skrift.config import get_settings
from skrift.db.models.user import User
from skrift.lib.notifications import NotificationMode, notify_user

from controllers.brave_search import brave_search
from controllers.deep_research_agent import (
    DetailEvent as DeepDetailEvent,
    DoneEvent as DeepDoneEvent,
    ErrorEvent as DeepErrorEvent,
    StageEvent as DeepStageEvent,
    TextEvent as DeepTextEvent,
)
from controllers.research_agent import run_agent_research_pipeline
from google.genai.types import GenerateContentConfig, ThinkingConfig, ThinkingLevel

from controllers.llm import genai_client
from controllers.scan_agent import (
    classify_query,
    generate_chat_title,
    generate_suggestions,
)
from models.chat import ChatMessage, ChatSession

logger = logging.getLogger(__name__)

_RECENT_CHATS_LIMIT = 10
_HISTORY_PAGE_SIZE = 20

# ---------------------------------------------------------------------------
# Background task infrastructure
# ---------------------------------------------------------------------------

_session_factory: async_sessionmaker | None = None


def _get_session_factory() -> async_sessionmaker:
    global _session_factory
    if _session_factory is None:
        engine = create_async_engine(get_settings().db.url, pool_pre_ping=True, pool_recycle=300)
        _session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory


_active_research: dict[UUID, asyncio.Task] = {}

# ---------------------------------------------------------------------------
# Pre-buffered AI overview generation
# ---------------------------------------------------------------------------


class _OverviewBuffer:
    __slots__ = ("chunks", "done", "error", "_event", "created_at")

    def __init__(self) -> None:
        self.chunks: list[str] = []
        self.done: bool = False
        self.error: str | None = None
        self._event: asyncio.Event = asyncio.Event()
        self.created_at: float = time.monotonic()


_overview_buffers: dict[str, _OverviewBuffer] = {}
_OVERVIEW_BUFFER_TTL = 30  # seconds


async def _fill_overview_buffer(query: str, results: list[dict], buf: _OverviewBuffer) -> None:
    """Background task: stream LLM chunks into the buffer."""
    try:
        async for text in _stream_search_overview(query, results):
            buf.chunks.append(text)
            buf._event.set()
            buf._event.clear()
    except Exception as exc:
        logger.exception("Overview buffer error for %r", query)
        buf.error = str(exc)
    finally:
        buf.done = True
        buf._event.set()
    # Auto-cleanup after TTL
    await asyncio.sleep(_OVERVIEW_BUFFER_TTL)
    _overview_buffers.pop(query, None)


def _start_overview_task(query: str, results: list[dict]) -> None:
    key = query.strip()
    if key in _overview_buffers:
        return
    buf = _OverviewBuffer()
    _overview_buffers[key] = buf
    asyncio.create_task(_fill_overview_buffer(key, results, buf))


_NM = NotificationMode.TIMESERIES


async def _run_pipeline_bg(
    user_id: UUID,
    chat_id: UUID,
    chat_mode: str,
    tz: str = "",
) -> None:
    """Run the research pipeline as a background task, pushing notifications."""
    try:
        async with _get_session_factory()() as db_session:
            # Load messages for query + history
            result = await db_session.execute(
                select(ChatMessage)
                .where(ChatMessage.chat_id == chat_id)
                .order_by(ChatMessage.created_at.asc())
            )
            messages = list(result.scalars().all())
            if not messages or messages[-1].role != "user":
                return

            query = messages[-1].content
            history = []
            for msg in messages[:-1]:
                if msg.content:
                    history.append({"role": msg.role, "content": msg.content})

            brave_api_key = os.environ.get("BRAVE_API_KEY", "")
            redis_url = get_settings().redis.url
            uid = str(user_id)
            cid = str(chat_id)

            accumulated_text = ""
            accumulated_events: list[dict] = []
            usage_data: dict = {}

            async def _notify(ntype: str, **payload: object) -> None:
                payload["chat_id"] = cid
                await notify_user(uid, ntype, mode=_NM, **payload)

            try:
                pipeline_mode = "lite" if chat_mode == "research" else "deep"
                pipeline_gen = run_agent_research_pipeline(
                    query,
                    brave_api_key,
                    db_session=db_session,
                    redis_url=redis_url,
                    user_timezone=tz,
                    conversation_history=history if history else None,
                    mode=pipeline_mode,
                )
                async for event in pipeline_gen:
                    if isinstance(event, DeepStageEvent):
                        accumulated_events.append(
                            {"type": "stage", "stage": event.stage}
                        )
                        await _notify("scan:stage", stage=event.stage)
                    elif isinstance(event, DeepDetailEvent):
                        accumulated_events.append(
                            {"type": "detail", "detail_type": event.type, **event.payload}
                        )
                        await _notify("scan:detail", detail_type=event.type, **event.payload)
                        if event.type == "usage":
                            usage_data = event.payload
                    elif isinstance(event, DeepTextEvent):
                        accumulated_text += event.text
                        await _notify("scan:text", text=event.text)
                    elif isinstance(event, DeepDoneEvent):
                        assistant_msg = ChatMessage(
                            chat_id=chat_id,
                            role="assistant",
                            content=accumulated_text,
                            events_json=json.dumps(accumulated_events),
                            usage_json=json.dumps(usage_data),
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
                        await _notify("scan:done")
                    elif isinstance(event, DeepErrorEvent):
                        accumulated_events.append(
                            {"type": "error", "error": event.error}
                        )
                        await _notify("scan:error", error=event.error)
            except Exception as exc:
                logger.exception("Pipeline error for chat %s", chat_id)
                await _notify("scan:error", error=str(exc))
    except Exception:
        logger.exception("Background task setup error for chat %s", chat_id)
    finally:
        _active_research.pop(chat_id, None)


def _start_pipeline_task(
    user_id: UUID, chat_id: UUID, chat_mode: str, tz: str = ""
) -> None:
    """Create and register a background pipeline task."""
    if chat_id in _active_research:
        return
    task = asyncio.create_task(_run_pipeline_bg(user_id, chat_id, chat_mode, tz=tz))
    _active_research[chat_id] = task


def build_redirect_url(classification: str, query: str) -> str:
    """Build the redirect URL based on classification."""
    if classification == "URL":
        cleaned = re.sub(r"^https?://", "", query.strip())
        return f"https://{cleaned}"
    else:
        return f"https://www.google.com/search?q={quote_plus(query)}"


_OVERVIEW_SYSTEM = (
    "You are a search assistant. Given search result descriptions for a query, "
    "write a concise 2-3 sentence overview that directly addresses the query. "
    "Be factual and cite no sources — just summarize what the results indicate."
)


async def _stream_search_overview(query: str, results: list[dict]):
    """Async generator that streams AI overview chunks from Flash Lite."""
    snippets = "\n".join(
        f"- {r.get('title', '')}: {r.get('description', '')}"
        for r in results[:10]
    )
    client = genai_client()
    async for chunk in await client.aio.models.generate_content_stream(
        model="gemini-3.1-flash-lite-preview",
        contents=f"Query: {query}\n\nSearch results:\n{snippets}",
        config=GenerateContentConfig(
            system_instruction=_OVERVIEW_SYSTEM,
            temperature=0.3,
            thinking_config=ThinkingConfig(
                thinking_level=ThinkingLevel.LOW,
            ),
        ),
    ):
        if chunk.text:
            yield chunk.text


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


_SCAN_PERMISSION = "scan"


async def _has_scan_permission(user_id: UUID, db_session: AsyncSession) -> bool:
    """Check whether a user has the 'scan' permission."""
    perms = await get_user_permissions(db_session, str(user_id))
    if ADMINISTRATOR_PERMISSION in perms.permissions:
        return True
    return _SCAN_PERMISSION in perms.permissions


# ---------------------------------------------------------------------------
# Autocomplete suggestion cache (in-memory LRU)
# ---------------------------------------------------------------------------

_SUGGEST_CACHE_MAXSIZE = 256
_SUGGEST_CACHE_TTL = 300  # 5 minutes

# key -> (timestamp, suggestions)
_suggest_cache: OrderedDict[str, tuple[float, list[str]]] = OrderedDict()


def _suggest_cache_put(key: str, suggestions: list[str]) -> None:
    """Insert into the LRU cache, evicting oldest if over maxsize."""
    _suggest_cache[key] = (time.monotonic(), suggestions)
    _suggest_cache.move_to_end(key)
    while len(_suggest_cache) > _SUGGEST_CACHE_MAXSIZE:
        _suggest_cache.popitem(last=False)


async def _get_suggestions(query: str, mode: str) -> list[str]:
    """Get autocomplete suggestions, checking cache first."""
    key = f"{mode}:{query.lower().strip()}"
    entry = _suggest_cache.get(key)
    if entry is not None:
        ts, suggestions = entry
        if time.monotonic() - ts < _SUGGEST_CACHE_TTL:
            _suggest_cache.move_to_end(key)
            return suggestions
        del _suggest_cache[key]
    suggestions = await generate_suggestions(query, mode)
    _suggest_cache_put(key, suggestions)
    return suggestions


class ScanController(Controller):
    path = "/"

    @get("/")
    async def index(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        try:
            user = await _get_user(request, db_session)
            if not user:
                return TemplateResponse("unauthorized.html")
            if not await _has_scan_permission(user.id, db_session):
                return TemplateResponse("unauthorized.html")
            recent_chats = await _get_recent_chats(user.id, db_session)
            return TemplateResponse(
                "index.html", context={"user": user, "recent_chats": recent_chats, "hide_sidebar": True}
            )
        except Exception:
            logger.exception("Error in index")
            raise

    @get("/search")
    async def search(
        self,
        request: Request,
        db_session: AsyncSession,
        q: str = "",
        mode: str = "",
        page: int = 1,
    ) -> Response | Redirect | TemplateResponse:
        try:
            user = await _get_user(request, db_session)
            if not user:
                return TemplateResponse("unauthorized.html")
            if not await _has_scan_permission(user.id, db_session):
                return TemplateResponse("unauthorized.html")
            if not q.strip():
                return Redirect(path="/")

            if mode == "deep":
                classification = "DEEP_RESEARCH"
            elif mode == "discover":
                classification = "RESEARCH"
            elif mode == "search":
                classification = "SEARCH"
            else:
                classification = await classify_query(q)
            accept = request.headers.get("accept", "")
            is_json = "application/json" in accept

            if classification in ("RESEARCH", "DEEP_RESEARCH"):
                chat_mode = "deep_research" if classification == "DEEP_RESEARCH" else "research"
                title = await generate_chat_title(q.strip())
                chat = ChatSession(user_id=user.id, title=title, mode=chat_mode)
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
                        content={"url": chat_url, "type": chat_mode},
                        status_code=200,
                    )
                return Redirect(path=chat_url)

            if classification == "URL":
                url = build_redirect_url(classification, q)
                if is_json:
                    return Response(content={"url": url}, status_code=200)
                return Redirect(path=url)

            # SEARCH: in-app Brave results with AI overview
            if page < 1:
                page = 1
            brave_api_key = os.environ.get("BRAVE_API_KEY", "")
            count = 10
            offset = (page - 1) * count
            results = await brave_search(q.strip(), brave_api_key, count=count, offset=offset)

            if page == 1 and results:
                _start_overview_task(q.strip(), results)

            has_next = len(results) >= count
            recent_chats = await _get_recent_chats(user.id, db_session)

            if is_json:
                return Response(
                    content={
                        "results": results,
                        "overview": "",
                        "query": q.strip(),
                        "page": page,
                        "has_next": has_next,
                    },
                    status_code=200,
                )
            return TemplateResponse(
                "search_results.html",
                context={
                    "user": user,
                    "query": q.strip(),
                    "results": results,
                    "overview": "",
                    "page": page,
                    "has_next": has_next,
                    "recent_chats": recent_chats,
                },
            )
        except Exception:
            logger.exception("Error in search")
            raise

    @get("/search/overview")
    async def search_overview(
        self,
        request: Request,
        db_session: AsyncSession,
        q: str = "",
    ) -> ServerSentEvent:
        async def _stream():
            user = await _get_user(request, db_session)
            if not user or not await _has_scan_permission(user.id, db_session):
                yield ServerSentEventMessage(event="error", data="unauthorized")
                return
            query = q.strip()
            if not query:
                yield ServerSentEventMessage(event="done", data="")
                return

            buf = _overview_buffers.get(query)
            if buf is None:
                # Fallback: no pre-buffered data (direct hit or expired) — generate inline
                try:
                    brave_api_key = os.environ.get("BRAVE_API_KEY", "")
                    results = await brave_search(query, brave_api_key, count=10, offset=0)
                    if not results:
                        yield ServerSentEventMessage(event="done", data="")
                        return
                    async for text in _stream_search_overview(query, results):
                        yield ServerSentEventMessage(event="text", data=text)
                    yield ServerSentEventMessage(event="done", data="")
                except Exception:
                    logger.exception("SSE overview error (inline fallback)")
                    yield ServerSentEventMessage(event="error", data="generation failed")
                return

            # Consume pre-buffered chunks
            try:
                cursor = 0
                while True:
                    # Replay any chunks accumulated since last read
                    while cursor < len(buf.chunks):
                        yield ServerSentEventMessage(event="text", data=buf.chunks[cursor])
                        cursor += 1
                    if buf.done:
                        break
                    buf._event.clear()
                    await buf._event.wait()

                if buf.error:
                    yield ServerSentEventMessage(event="error", data=buf.error)
                else:
                    yield ServerSentEventMessage(event="done", data="")
            except Exception:
                logger.exception("SSE overview error (buffered)")
                yield ServerSentEventMessage(event="error", data="generation failed")
            finally:
                _overview_buffers.pop(query, None)

        return ServerSentEvent(_stream())

    @get("/chat/{chat_id:uuid}")
    async def chat_view(
        self, request: Request, db_session: AsyncSession, chat_id: UUID
    ) -> TemplateResponse | Redirect:
        try:
            user = await _get_user(request, db_session)
            if not user:
                return TemplateResponse("unauthorized.html")
            if not await _has_scan_permission(user.id, db_session):
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

            # Crash recovery: needs_stream but no active task → restart pipeline
            if needs_stream and chat_id not in _active_research:
                _start_pipeline_task(user.id, chat_id, chat.mode)

            return TemplateResponse(
                "chat.html",
                context={
                    "user": user,
                    "chat": chat,
                    "messages": messages,
                    "needs_stream": needs_stream,
                    "recent_chats": recent_chats,
                    "last_notification_at": chat.last_notification_at,
                },
            )
        except Exception:
            logger.exception("Error in chat_view")
            raise

    @post("/chat/{chat_id:uuid}/message")
    async def add_message(
        self, request: Request, db_session: AsyncSession, chat_id: UUID
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_scan_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

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

        tz = body.get("tz", "")
        _start_pipeline_task(user.id, chat_id, chat.mode, tz=tz)

        return Response(
            content={"id": str(msg.id), "content": content, "started": True},
            status_code=201,
        )

    @get("/history")
    async def history(
        self, request: Request, db_session: AsyncSession, page: int = 1
    ) -> TemplateResponse:
        try:
            user = await _get_user(request, db_session)
            if not user:
                return TemplateResponse("unauthorized.html")
            if not await _has_scan_permission(user.id, db_session):
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

            # Aggregate events/usage per chat for the history list
            chat_meta: dict[UUID, dict] = {}
            if chats:
                chat_ids = [c.id for c in chats]
                msg_result = await db_session.execute(
                    select(
                        ChatMessage.chat_id,
                        ChatMessage.events_json,
                        ChatMessage.usage_json,
                    )
                    .where(
                        ChatMessage.chat_id.in_(chat_ids),
                        ChatMessage.role == "assistant",
                    )
                )
                for chat_id, events_json, usage_json in msg_result:
                    meta = chat_meta.setdefault(chat_id, {"urls": [], "usage": None, "tool_calls": 0})
                    try:
                        events = json.loads(events_json) if events_json else []
                    except (json.JSONDecodeError, TypeError):
                        events = []
                    for ev in events:
                        if ev.get("type") != "detail":
                            continue
                        dt = ev.get("detail_type")
                        if dt in ("research", "search", "fetch"):
                            meta["tool_calls"] += 1
                        if dt == "fetch_done" and ev.get("url") and not ev.get("failed"):
                            meta["urls"].append(ev["url"])
                        if dt == "usage" and ev.get("total"):
                            meta["usage"] = ev
                    if not meta["usage"]:
                        try:
                            usage = json.loads(usage_json) if usage_json else {}
                        except (json.JSONDecodeError, TypeError):
                            usage = {}
                        if usage.get("total"):
                            meta["usage"] = usage

            recent_chats = await _get_recent_chats(user.id, db_session)

            return TemplateResponse(
                "history.html",
                context={
                    "user": user,
                    "chats": chats,
                    "chat_meta": chat_meta,
                    "page": page,
                    "total_pages": total_pages,
                    "total": total,
                    "recent_chats": recent_chats,
                },
            )
        except Exception:
            logger.exception("Error in history")
            raise

    @get("/suggest")
    async def suggest(
        self,
        request: Request,
        db_session: AsyncSession,
        q: str = "",
        mode: str = "launch",
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user or not await _has_scan_permission(user.id, db_session):
            return Response(content={"suggestions": []}, status_code=200)
        if len(q.strip()) < 2:
            return Response(content={"suggestions": []}, status_code=200)
        if mode not in ("launch", "discover", "deep", "search"):
            mode = "launch"
        suggestions = await _get_suggestions(q.strip(), mode)
        return Response(content={"suggestions": suggestions}, status_code=200)

    @get("/opensearch.xml")
    async def opensearch(self) -> Response:
        return Response(
            content=OPENSEARCH_XML,
            media_type="application/opensearchdescription+xml",
        )
