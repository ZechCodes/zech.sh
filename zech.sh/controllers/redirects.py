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
