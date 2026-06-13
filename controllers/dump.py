from uuid import UUID

from litestar import Controller, Request, get
from litestar.exceptions import NotFoundException
from litestar.response import Template as TemplateResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.auth.session_keys import SESSION_USER_ID
from skrift.db.models.user import User
from skrift.db.services import page_service
from skrift.db.services.setting_service import get_cached_site_name_for
from skrift.lib.seo import (
    OpenGraphMeta,
    SEOMeta,
    get_page_og_meta,
    get_page_seo_meta,
)


class DumpController(Controller):
    path = "/"

    async def _get_user_context(
        self, request: Request, db_session: AsyncSession
    ) -> dict:
        """Get user data for template context if logged in."""
        user_id = request.session.get(SESSION_USER_ID)
        if not user_id:
            return {"user": None}
        result = await db_session.execute(
            select(User).where(User.id == UUID(user_id))
        )
        user = result.scalar_one_or_none()
        return {"user": user}

    @get("/")
    async def index(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        """List published posts, newest first."""
        user_ctx = await self._get_user_context(request, db_session)
        flash = request.session.pop("flash", None)
        published_only = not request.session.get(SESSION_USER_ID)

        posts = await page_service.list_pages(
            db_session,
            published_only=published_only,
            page_type="post",
            order_by="published",
        )

        site_name = get_cached_site_name_for("dump") or "DUMP.ZECH.SH"
        base_url = str(request.base_url).rstrip("/").replace("http://", "https://", 1)
        seo_meta = SEOMeta(
            title="DUMP.ZECH.SH",
            description="Code-heavy posts from the trenches",
            canonical_url=base_url,
            robots=None,
        )
        og_meta = OpenGraphMeta(
            title="DUMP.ZECH.SH",
            description="Code-heavy posts from the trenches",
            url=base_url,
            site_name=site_name,
            image=None,
            type="website",
        )

        return TemplateResponse(
            "index.html",
            context={
                "posts": posts,
                "flash": flash,
                "seo_meta": seo_meta,
                "og_meta": og_meta,
                **user_ctx,
            },
        )

    @get("/{slug:str}")
    async def view_post(
        self, request: Request, db_session: AsyncSession, slug: str
    ) -> TemplateResponse:
        """View a single post by slug."""
        user_ctx = await self._get_user_context(request, db_session)
        flash = request.session.pop("flash", None)
        published_only = not request.session.get(SESSION_USER_ID)

        post = await page_service.get_page_by_slug(
            db_session,
            slug,
            published_only=published_only,
            page_type="post",
        )
        if not post:
            raise NotFoundException(f"Post '{slug}' not found")

        site_name = get_cached_site_name_for("dump") or "DUMP.ZECH.SH"
        base_url = str(request.base_url).rstrip("/").replace("http://", "https://", 1)
        seo_meta = await get_page_seo_meta(post, site_name, base_url)
        og_meta = await get_page_og_meta(post, site_name, base_url)

        return TemplateResponse(
            "post.html",
            context={
                "post": post,
                "flash": flash,
                "seo_meta": seo_meta,
                "og_meta": og_meta,
                **user_ctx,
            },
        )
