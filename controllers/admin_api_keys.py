"""Admin controller for managing API keys."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from litestar import Controller, Request, get, post
from litestar.response import Redirect
from litestar.response import Template as TemplateResponse
from litestar.params import Body
from litestar.enums import RequestEncodingType
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.auth.guards import auth_guard, Permission
from skrift.auth.session_keys import SESSION_USER_ID
from skrift.admin.helpers import get_admin_context
from skrift.admin.navigation import ADMIN_NAV_TAG
from skrift.lib.flash import flash_success, flash_error, get_flash_messages

from controllers.api_auth import generate_api_key
from models.api_key import ApiKey


class ApiKeysAdminController(Controller):
    """Admin controller for API key CRUD operations."""

    path = "/admin"
    guards = [auth_guard]

    @get(
        "/api-keys",
        tags=[ADMIN_NAV_TAG],
        guards=[auth_guard, Permission("modify-site")],
        opt={"label": "API Keys", "icon": "key", "order": 85},
    )
    async def list_api_keys(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse:
        """List all active API keys."""
        ctx = await get_admin_context(request, db_session)
        user_id = request.session.get(SESSION_USER_ID)

        result = await db_session.execute(
            select(ApiKey)
            .where(ApiKey.user_id == UUID(user_id), ApiKey.is_revoked == False)  # noqa: E712
            .order_by(ApiKey.created_at.desc())
        )
        api_keys = list(result.scalars().all())

        flash_messages = get_flash_messages(request)
        return TemplateResponse(
            "admin/api_keys.html",
            context={
                "api_keys": api_keys,
                "flash_messages": flash_messages,
                "new_key": None,
                **ctx,
            },
        )

    @post(
        "/api-keys/create",
        guards=[auth_guard, Permission("modify-site")],
    )
    async def create_api_key(
        self,
        request: Request,
        db_session: AsyncSession,
        data: Annotated[dict, Body(media_type=RequestEncodingType.URL_ENCODED)],
    ) -> TemplateResponse:
        """Generate a new API key and display it once."""
        ctx = await get_admin_context(request, db_session)
        user_id = request.session.get(SESSION_USER_ID)
        name = data.get("name", "").strip()
        if not name:
            name = "Untitled Key"

        raw_key, key_hash, key_prefix = generate_api_key()

        api_key = ApiKey(
            user_id=UUID(user_id),
            name=name,
            key_hash=key_hash,
            key_prefix=key_prefix,
        )
        db_session.add(api_key)
        await db_session.commit()

        # Re-fetch list for display
        result = await db_session.execute(
            select(ApiKey)
            .where(ApiKey.user_id == UUID(user_id), ApiKey.is_revoked == False)  # noqa: E712
            .order_by(ApiKey.created_at.desc())
        )
        api_keys = list(result.scalars().all())

        return TemplateResponse(
            "admin/api_keys.html",
            context={
                "api_keys": api_keys,
                "flash_messages": [],
                "new_key": raw_key,
                **ctx,
            },
        )

    @post(
        "/api-keys/{key_id:uuid}/revoke",
        guards=[auth_guard, Permission("modify-site")],
    )
    async def revoke_api_key(
        self,
        request: Request,
        db_session: AsyncSession,
        key_id: UUID,
    ) -> Redirect:
        """Revoke an API key."""
        user_id = request.session.get(SESSION_USER_ID)

        result = await db_session.execute(
            select(ApiKey).where(
                ApiKey.id == key_id, ApiKey.user_id == UUID(user_id)
            )
        )
        api_key = result.scalar_one_or_none()
        if api_key is None:
            flash_error(request, "API key not found")
            return Redirect(path="/admin/api-keys")

        api_key.is_revoked = True
        await db_session.commit()

        flash_success(request, f"API key '{api_key.name}' has been revoked")
        return Redirect(path="/admin/api-keys")
