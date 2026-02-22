import json
import os
import re
from collections.abc import AsyncGenerator
from urllib.parse import quote_plus
from uuid import UUID

from litestar import Controller, Request, get
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse
from litestar.response.sse import ServerSentEvent, ServerSentEventMessage
from sqlalchemy import select
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


class ScanController(Controller):
    path = "/"

    async def _get_user(
        self, request: Request, db_session: AsyncSession
    ) -> User | None:
        user_id = request.session.get(SESSION_USER_ID)
        if not user_id:
            return None
        result = await db_session.execute(
            select(User).where(User.id == UUID(user_id))
        )
        return result.scalar_one_or_none()

    @get("/")
    async def index(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        user = await self._get_user(request, db_session)
        if not user:
            return TemplateResponse("unauthorized.html")
        return TemplateResponse("index.html", context={"user": user})

    @get("/search")
    async def search(
        self, request: Request, db_session: AsyncSession, q: str = ""
    ) -> Response | Redirect | TemplateResponse:
        user = await self._get_user(request, db_session)
        if not user:
            return TemplateResponse("unauthorized.html")
        if not q.strip():
            return Redirect(path="/")

        classification = await classify_query(q)
        accept = request.headers.get("accept", "")
        is_json = "application/json" in accept

        if classification == "RESEARCH":
            research_url = f"/search?q={quote_plus(q)}"
            if is_json:
                return Response(content={"url": research_url, "type": "research"}, status_code=200)
            return TemplateResponse("research.html", context={"query": q})

        url = build_redirect_url(classification, q)
        # Return JSON for fetch requests (form JS), redirect for direct navigation (OpenSearch)
        if is_json:
            return Response(content={"url": url}, status_code=200)
        return Redirect(path=url)

    @get("/research/stream")
    async def research_stream(self, request: Request, db_session: AsyncSession, q: str = "", context: str = "") -> ServerSentEvent | TemplateResponse:
        user_id = request.session.get(SESSION_USER_ID)
        if not user_id:
            return TemplateResponse("unauthorized.html")
        if not q.strip():
            return ServerSentEvent(iter([]))

        brave_api_key = os.environ.get("BRAVE_API_KEY", "")
        redis_url = get_settings().redis.url

        async def generate() -> AsyncGenerator[ServerSentEventMessage, None]:
            try:
                async for event in run_research_pipeline(
                    q, brave_api_key,
                    db_session=db_session,
                    redis_url=redis_url,
                    additional_context=context,
                ):
                    if isinstance(event, StageEvent):
                        yield ServerSentEventMessage(
                            data=json.dumps({"stage": event.stage}),
                            event="stage",
                        )
                    elif isinstance(event, DetailEvent):
                        yield ServerSentEventMessage(
                            data=json.dumps({"type": event.type, **event.payload}),
                            event="detail",
                        )
                    elif isinstance(event, TextEvent):
                        yield ServerSentEventMessage(
                            data=json.dumps({"text": event.text}),
                            event="text",
                        )
                    elif isinstance(event, DoneEvent):
                        yield ServerSentEventMessage(data="", event="done")
                    elif isinstance(event, ClarificationEvent):
                        yield ServerSentEventMessage(
                            data=json.dumps({"questions": event.questions}),
                            event="clarification",
                        )
                    elif isinstance(event, ErrorEvent):
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

    @get("/opensearch.xml")
    async def opensearch(self) -> Response:
        return Response(
            content=OPENSEARCH_XML,
            media_type="application/opensearchdescription+xml",
        )
