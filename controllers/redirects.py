from litestar import Controller, get
from litestar.exceptions import NotFoundException
from litestar.response import Redirect
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.db.services.setting_service import get_setting


class RedirectsController(Controller):
    path = "/discord"

    @get("/")
    async def discord(self, db_session: AsyncSession) -> Redirect:
        url = await get_setting(db_session, "discord_invite_url")
        if not url:
            raise NotFoundException("Discord invite not configured")
        return Redirect(path=url)


class FaviconController(Controller):
    """Serve the legacy root /favicon.ico that browsers and crawlers auto-request
    (the HTML <link rel=icon> only covers clients that parse the page)."""

    path = "/"

    @get("/favicon.ico")
    async def favicon(self) -> Redirect:
        return Redirect(path="/static/town/favicon.ico", status_code=301)
