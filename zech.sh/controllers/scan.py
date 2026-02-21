import json
import os
import re
from collections.abc import AsyncGenerator
from urllib.parse import quote_plus

from litestar import Controller, Request, get
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse
from litestar.response.sse import ServerSentEvent, ServerSentEventMessage

from skrift.auth.session_keys import SESSION_USER_ID
from skrift.lib.notifications import _ensure_nid

from controllers.scan_agent import ResearchDeps, classify_query, gemini_flash, research_agent


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

    @get("/")
    async def index(self, request: Request) -> TemplateResponse:
        user_id = request.session.get(SESSION_USER_ID)
        if not user_id:
            return TemplateResponse("unauthorized.html")
        return TemplateResponse("index.html")

    @get("/search")
    async def search(self, request: Request, q: str = "") -> Redirect | TemplateResponse:
        user_id = request.session.get(SESSION_USER_ID)
        if not user_id:
            return TemplateResponse("unauthorized.html")
        if not q.strip():
            return Redirect(path="/")

        classification = await classify_query(q)

        if classification == "RESEARCH":
            return TemplateResponse("research.html", context={"query": q})

        url = build_redirect_url(classification, q)
        return Redirect(path=url)

    @get("/research/stream")
    async def research_stream(self, request: Request, q: str = "") -> ServerSentEvent | TemplateResponse:
        user_id = request.session.get(SESSION_USER_ID)
        if not user_id:
            return TemplateResponse("unauthorized.html")
        if not q.strip():
            return ServerSentEvent(iter([]))

        nid = _ensure_nid(request)
        brave_api_key = os.environ.get("BRAVE_API_KEY", "")
        deps = ResearchDeps(nid=nid, brave_api_key=brave_api_key)

        async def generate() -> AsyncGenerator[ServerSentEventMessage, None]:
            try:
                async with research_agent.run_stream(q, deps=deps, model=gemini_flash()) as stream:
                    async for text in stream.stream_text(delta=True):
                        yield ServerSentEventMessage(
                            data=json.dumps({"text": text}),
                            event="text",
                        )
                yield ServerSentEventMessage(data="", event="done")
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
