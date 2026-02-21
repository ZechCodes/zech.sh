import os
import re
from urllib.parse import quote_plus

import httpx
from litestar import Controller, Request, get
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse

from skrift.auth.session_keys import SESSION_USER_ID

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent"

SYSTEM_PROMPT = """\
You are a query classifier. Given a user input, classify it as exactly one of:

URL — The input looks like a domain name, IP address, or URL (with or without a protocol). \
Examples: "github.com", "docs.python.org/3/library/asyncio", "192.168.1.1", "example.com/path?query=1"

SEARCH — The input is a simple web search query looking for results/links. \
Examples: "python list comprehension", "best pizza near me", "litestar framework", "weather today"

RESEARCH — The input is a question or request that needs a comprehensive, direct answer or \
in-depth analysis rather than a list of links. \
Examples: "how does TCP congestion control work?", "compare React vs Svelte for SPAs", \
"explain the difference between threads and processes"

Respond with exactly one word: URL, SEARCH, or RESEARCH. Nothing else."""


async def classify_query(query: str) -> str:
    """Classify a query using Gemini Flash."""
    api_key = os.environ["GOOGLE_API_KEY"]
    payload = {
        "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"parts": [{"text": query}]}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 256,
            "thinkingConfig": {
                "thinkingLevel": "minimal",
            },
        },
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{GEMINI_API_URL}?key={api_key}",
            json=payload,
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

    parts = data["candidates"][0]["content"]["parts"]
    # Thinking models may include thought parts; find the text part
    text = next(p["text"] for p in parts if "text" in p).strip().upper()
    if text not in ("URL", "SEARCH", "RESEARCH"):
        return "SEARCH"
    return text


def build_redirect_url(classification: str, query: str) -> str:
    """Build the redirect URL based on classification."""
    if classification == "URL":
        cleaned = re.sub(r"^https?://", "", query.strip())
        return f"https://{cleaned}"
    elif classification == "RESEARCH":
        return f"https://www.perplexity.ai/search?q={quote_plus(query)}"
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
        url = build_redirect_url(classification, q)
        return Redirect(path=url)

    @get("/opensearch.xml")
    async def opensearch(self) -> Response:
        return Response(
            content=OPENSEARCH_XML,
            media_type="application/opensearchdescription+xml",
        )
