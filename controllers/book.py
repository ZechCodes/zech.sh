from litestar import Controller, Request, get
from litestar.response import Template as TemplateResponse
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.controllers.helpers import get_user_context
from skrift.db.services.setting_service import get_cached_site_name
from skrift.seo import OpenGraphMeta, SEOMeta

BOOK_PAGE_TITLE = "Book a call · Zech Zimmerman"
BOOK_PAGE_DESCRIPTION = (
    "Book a scoping call with Zech Zimmerman. Engagements start with a "
    "fixed-scope audit of your agent architecture."
)
BOOK_PAGE_URL = "https://zech.sh/book"


class BookController(Controller):
    path = "/book"

    @get("/")
    async def book(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        """The booking page: engagement framing plus the Cal.com inline embed."""
        user_ctx = await get_user_context(request, db_session)
        flash = request.session.pop("flash", None)

        seo_meta = SEOMeta(
            title=BOOK_PAGE_TITLE,
            description=BOOK_PAGE_DESCRIPTION,
            canonical_url=BOOK_PAGE_URL,
            robots=None,
        )
        og_meta = OpenGraphMeta(
            title=BOOK_PAGE_TITLE,
            description=BOOK_PAGE_DESCRIPTION,
            image=None,
            url=BOOK_PAGE_URL,
            site_name=get_cached_site_name() or "zech.sh",
        )

        return TemplateResponse(
            "book.html",
            context={
                "flash": flash,
                "seo_meta": seo_meta,
                "og_meta": og_meta,
                **user_ctx,
            },
        )
