"""Integrations admin controller."""

from __future__ import annotations

from typing import Annotated

from litestar import Controller, Request, get, post
from litestar.response import Template as TemplateResponse, Redirect
from litestar.params import Body
from litestar.enums import RequestEncodingType
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.auth.guards import auth_guard, Permission
from skrift.admin.helpers import get_admin_context
from skrift.admin.navigation import ADMIN_NAV_TAG
from skrift.db.services import setting_service
from skrift.lib.flash import flash_success, get_flash_messages


class IntegrationsAdminController(Controller):
    """Controller for integration settings in admin."""

    path = "/admin"
    guards = [auth_guard]

    @get(
        "/integrations",
        tags=[ADMIN_NAV_TAG],
        guards=[auth_guard, Permission("modify-site")],
        opt={"label": "Integrations", "icon": "link", "order": 90},
    )
    async def integrations(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        """Integrations settings page."""
        ctx = await get_admin_context(request, db_session)
        settings = await setting_service.get_settings(
            db_session, ["discord_invite_url"]
        )
        flash_messages = get_flash_messages(request)
        return TemplateResponse(
            "admin/integrations.html",
            context={
                "flash_messages": flash_messages,
                "settings": settings,
                **ctx,
            },
        )

    @post(
        "/integrations",
        guards=[auth_guard, Permission("modify-site")],
    )
    async def save_integrations(
        self,
        request: Request,
        db_session: AsyncSession,
        data: Annotated[dict, Body(media_type=RequestEncodingType.URL_ENCODED)],
    ) -> Redirect:
        """Save integration settings."""
        discord_invite_url = data.get("discord_invite_url", "").strip()
        await setting_service.set_setting(
            db_session, "discord_invite_url", discord_invite_url
        )
        flash_success(request, "Integration settings saved successfully")
        return Redirect(path="/admin/integrations")
