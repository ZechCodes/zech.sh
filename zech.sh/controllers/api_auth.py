"""API key authentication utilities and Litestar guard."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from uuid import UUID

from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException
from litestar.handlers import BaseRouteHandler
from sqlalchemy import select, update

from models.api_key import ApiKey


def hash_api_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw API key."""
    return hashlib.sha256(raw_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """Generate a new API key.

    Returns (raw_key, key_hash, key_prefix).
    The raw key is shown once to the user, then only the hash is stored.
    """
    raw_key = "sk_" + secrets.token_urlsafe(32)
    key_hash = hash_api_key(raw_key)
    key_prefix = raw_key[:8]
    return raw_key, key_hash, key_prefix


async def api_key_guard(
    connection: ASGIConnection, _handler: BaseRouteHandler
) -> None:
    """Litestar guard that authenticates requests via Bearer API key."""
    auth_header = connection.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise NotAuthorizedException("Missing or invalid Authorization header")

    raw_key = auth_header[7:].strip()
    if not raw_key:
        raise NotAuthorizedException("Empty API key")

    key_hash = hash_api_key(raw_key)

    session_maker = connection.app.state.session_maker_class
    async with session_maker() as db_session:
        result = await db_session.execute(
            select(ApiKey).where(ApiKey.key_hash == key_hash)
        )
        api_key = result.scalar_one_or_none()

        if api_key is None or api_key.is_revoked:
            raise NotAuthorizedException("Invalid or revoked API key")

        connection.state["_api_key_user_id"] = api_key.user_id

        await db_session.execute(
            update(ApiKey)
            .where(ApiKey.id == api_key.id)
            .values(last_used_at=datetime.now(timezone.utc))
        )
        await db_session.commit()


def get_api_user_id(request: object) -> UUID:
    """Retrieve the user_id set by api_key_guard."""
    return request.state["_api_key_user_id"]  # type: ignore[union-attr]
