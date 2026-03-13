import asyncio
import base64
import hashlib
import hmac as hmac_mod
import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

import redis.asyncio as redis
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from litestar import Controller, Request, delete, get, post, put, websocket
from litestar.connection import ASGIConnection, WebSocket
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from skrift.auth.guards import ADMINISTRATOR_PERMISSION
from skrift.auth.roles import register_role
from skrift.auth.services import get_user_permissions
from skrift.auth.session_keys import SESSION_USER_ID
from skrift.config import get_settings
from skrift.db.models.user import User
from skrift.db.services.asset_service import get_asset_url, upload_asset
from skrift.lib.notifications import NotificationMode, dismiss_user_group, notifications, notify_user
from skrift.lib.push import send_push

from models.ai_chat import AiChatMessage
from models.ai_chat_channel import AiChatChannel
from models.ai_chat_device import AiChatDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Role registration
# ---------------------------------------------------------------------------

register_role(
    "ai-chatter", "use-ai-chat",
    display_name="AI Chatter",
    description="Access to AI Chat",
)

# ---------------------------------------------------------------------------
# Device WebSocket connection registry (for direct push to devices)
# ---------------------------------------------------------------------------

_device_ws_connections: dict[str, WebSocket] = {}


async def _push_to_device_ws(device_id: str, payload: dict) -> bool:
    """Push a message directly to a device's WebSocket connection (bypasses notifications)."""
    ws = _device_ws_connections.get(device_id)
    if not ws:
        return False
    try:
        await ws.send_text(json.dumps({
            "type": "event",
            "event_type": payload.get("event_type", ""),
            **{k: v for k, v in payload.items() if k != "event_type"},
        }))
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _get_user(request: Request, db_session: AsyncSession) -> User | None:
    user_id = request.session.get(SESSION_USER_ID)
    if not user_id:
        return None
    result = await db_session.execute(
        select(User).where(User.id == UUID(user_id))
    )
    return result.scalar_one_or_none()


async def _has_permission(user_id: UUID, db_session: AsyncSession) -> bool:
    perms = await get_user_permissions(db_session, str(user_id))
    if ADMINISTRATOR_PERMISSION in perms.permissions:
        return True
    return "use-ai-chat" in perms.permissions


async def _get_target_user_id(
    db_session: AsyncSession, channel_id: UUID
) -> str | None:
    """Get the user_id from the most recent user message for notification targeting."""
    query = (
        select(AiChatMessage.user_id)
        .where(AiChatMessage.sender == "user")
        .where(AiChatMessage.user_id.is_not(None))
        .where(AiChatMessage.channel_id == channel_id)
        .order_by(AiChatMessage.created_at.desc())
        .limit(1)
    )
    result = await db_session.execute(query)
    uid = result.scalar_one_or_none()
    return str(uid) if uid else None


# ---------------------------------------------------------------------------
# Compound token helpers
# ---------------------------------------------------------------------------

_TOKEN_SECRET: str | None = None


def _get_token_secret() -> str:
    """Get the secret used for HMAC signing of compound tokens."""
    global _TOKEN_SECRET
    if _TOKEN_SECRET is not None:
        return _TOKEN_SECRET
    _TOKEN_SECRET = os.environ.get("AICHAT_TOKEN_SECRET", "")
    if not _TOKEN_SECRET:
        _TOKEN_SECRET = hashlib.sha256(
            os.environ.get("SECRET_KEY", "aichat-fallback").encode()
        ).hexdigest()
    return _TOKEN_SECRET


def _make_compound_token(private_key_b64: str, channel_id: str) -> str:
    """Create a compound token: base64(json({k, c, s}))."""
    secret = _get_token_secret()
    sig = hmac_mod.new(
        secret.encode(),
        f"{private_key_b64}.{channel_id}".encode(),
        hashlib.sha256,
    ).digest()
    payload = json.dumps({
        "k": private_key_b64,
        "c": channel_id,
        "s": base64.b64encode(sig).decode(),
    }, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def _parse_compound_token(token: str) -> tuple[str, str] | None:
    """Parse and verify a compound token. Returns (private_key_b64, channel_id) or None."""
    try:
        padding = 4 - len(token) % 4
        if padding != 4:
            token += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(token))
        key_b64 = payload["k"]
        channel_id = payload["c"]
        sig = base64.b64decode(payload["s"])

        secret = _get_token_secret()
        expected = hmac_mod.new(
            secret.encode(),
            f"{key_b64}.{channel_id}".encode(),
            hashlib.sha256,
        ).digest()
        if not hmac_mod.compare_digest(sig, expected):
            return None
        return key_b64, channel_id
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Ed25519 signature verification
# ---------------------------------------------------------------------------

_MAX_TIMESTAMP_DRIFT = 60  # seconds


def _verify_signature_with_key(
    connection: ASGIConnection, public_key: Ed25519PublicKey
) -> bool:
    """Verify Ed25519 signature on a request using the given public key."""
    timestamp_str = connection.headers.get("x-timestamp", "")
    signature_b64 = connection.headers.get("x-signature", "")
    if not timestamp_str or not signature_b64:
        return False

    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return False

    if abs(time.time() - timestamp) > _MAX_TIMESTAMP_DRIFT:
        return False

    method = connection.scope["method"]
    path = connection.scope["path"]
    query = connection.scope.get("query_string", b"").decode()
    if query:
        path = f"{path}?{query}"

    message = f"{timestamp_str}.{method}.{path}".encode()

    try:
        signature = base64.b64decode(signature_b64)
        public_key.verify(signature, message)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Channel key cache (avoids DB lookup in guard on every request)
# ---------------------------------------------------------------------------

_channel_key_cache: dict[str, Ed25519PublicKey] = {}
_guard_engine = None


async def _get_guard_engine():
    """Get a dedicated engine for use in the API guard (no DI available)."""
    global _guard_engine
    if _guard_engine is None:
        settings = get_settings()
        opts = {}
        if settings.db.db_schema:
            opts["schema_translate_map"] = {None: settings.db.db_schema}
        _guard_engine = create_async_engine(
            settings.db.url, pool_size=5, execution_options=opts
        )
    return _guard_engine


async def _lookup_channel_key(channel_id: str) -> Ed25519PublicKey | None:
    """Look up a channel's public key, using cache when available."""
    if channel_id in _channel_key_cache:
        return _channel_key_cache[channel_id]

    try:
        engine = await _get_guard_engine()
        async with AsyncSession(engine) as session:
            result = await session.execute(
                select(AiChatChannel.public_key)
                .where(AiChatChannel.id == UUID(channel_id))
            )
            key_b64 = result.scalar_one_or_none()
    except Exception:
        logger.exception("Failed to look up channel key")
        return None

    if not key_b64:
        return None

    pub_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64))
    _channel_key_cache[channel_id] = pub_key
    return pub_key


def _invalidate_channel_cache(channel_id: str) -> None:
    """Remove a channel from the key cache."""
    _channel_key_cache.pop(channel_id, None)


# ---------------------------------------------------------------------------
# Redis rate limiting for API
# ---------------------------------------------------------------------------

_redis_client: redis.Redis | None = None
_API_RATE_LIMIT_READS = 60
_API_RATE_LIMIT_WRITES = 60


async def _get_redis() -> redis.Redis | None:
    global _redis_client
    redis_url = get_settings().redis.url
    if not redis_url:
        return None
    if _redis_client is None:
        _redis_client = redis.from_url(redis_url, decode_responses=True)
    return _redis_client


async def _check_rate_limit(key: str, limit: int, window: int = 60) -> bool:
    """Check rate limit using Redis sliding window. Returns True if allowed."""
    r = await _get_redis()
    if r is None:
        return True

    try:
        now = time.time()
        pipe = r.pipeline()
        pipe.zremrangebyscore(key, 0, now - window)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.expire(key, window)
        results = await pipe.execute()
        count = results[2]
        return count <= limit
    except redis.RedisError:
        logger.warning("Redis rate limit check failed, allowing request")
        return True


# ---------------------------------------------------------------------------
# Device auth code management (Redis-backed, 10 min TTL)
# ---------------------------------------------------------------------------

_DEVICE_CODE_TTL = 600  # 10 minutes


async def _store_device_code(code: str, data: dict) -> None:
    """Store a pending device auth code in Redis."""
    r = await _get_redis()
    if r:
        await r.setex(f"aichat:device-code:{code}", _DEVICE_CODE_TTL, json.dumps(data))


async def _get_device_code(code: str) -> dict | None:
    """Retrieve a pending device auth code from Redis."""
    r = await _get_redis()
    if not r:
        return None
    raw = await r.get(f"aichat:device-code:{code}")
    if not raw:
        return None
    return json.loads(raw)


async def _update_device_code(code: str, data: dict) -> None:
    """Update a device code (preserves remaining TTL)."""
    r = await _get_redis()
    if r:
        ttl = await r.ttl(f"aichat:device-code:{code}")
        if ttl > 0:
            await r.setex(f"aichat:device-code:{code}", ttl, json.dumps(data))


async def _delete_device_code(code: str) -> None:
    """Delete a device auth code."""
    r = await _get_redis()
    if r:
        await r.delete(f"aichat:device-code:{code}")


# ---------------------------------------------------------------------------
# Device key cache (similar to channel key cache)
# ---------------------------------------------------------------------------

_device_key_cache: dict[str, Ed25519PublicKey] = {}


async def _lookup_device_key(device_id: str) -> Ed25519PublicKey | None:
    """Look up a device's public key, using cache when available."""
    if device_id in _device_key_cache:
        return _device_key_cache[device_id]

    try:
        engine = await _get_guard_engine()
        async with AsyncSession(engine) as session:
            result = await session.execute(
                select(AiChatDevice.public_key)
                .where(AiChatDevice.id == UUID(device_id))
            )
            key_b64 = result.scalar_one_or_none()
    except Exception:
        logger.exception("Failed to look up device key")
        return None

    if not key_b64:
        return None

    pub_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64))
    _device_key_cache[device_id] = pub_key
    return pub_key


def _invalidate_device_cache(device_id: str) -> None:
    """Remove a device from the key cache."""
    _device_key_cache.pop(device_id, None)


# ---------------------------------------------------------------------------
# Device API guard — Ed25519 signature verification for device endpoints
# ---------------------------------------------------------------------------


async def aichat_device_api_guard(
    connection: ASGIConnection, _handler: BaseRouteHandler
) -> None:
    """Guard for device-authenticated API endpoints."""
    device_id_str = connection.headers.get("x-device-id", "")
    if not device_id_str:
        raise NotAuthorizedException("X-Device-Id header required")

    try:
        pub_key = await _lookup_device_key(device_id_str)
        if not pub_key or not _verify_signature_with_key(connection, pub_key):
            raise NotAuthorizedException("Invalid device signature")
        connection.state["device_id"] = device_id_str
    except NotAuthorizedException:
        raise
    except Exception:
        logger.exception("Device auth lookup failed")
        raise NotAuthorizedException("Auth failed")


# ---------------------------------------------------------------------------
# API guard — Ed25519 signature + rate limiting (channel-aware)
# ---------------------------------------------------------------------------


async def _verify_device_owns_channel(device_id: str, channel_id: str) -> bool:
    """Check that a channel belongs to the given device."""
    try:
        engine = await _get_guard_engine()
        async with AsyncSession(engine) as session:
            result = await session.execute(
                select(AiChatChannel.device_id)
                .where(AiChatChannel.id == UUID(channel_id))
            )
            ch_device_id = result.scalar_one_or_none()
            return ch_device_id is not None and str(ch_device_id) == device_id
    except Exception:
        logger.exception("Failed to verify device-channel ownership")
        return False


async def aichat_api_guard(
    connection: ASGIConnection, _handler: BaseRouteHandler
) -> None:
    channel_id_str = connection.headers.get("x-channel", "")
    if not channel_id_str:
        raise NotAuthorizedException("X-Channel header required")

    authenticated = False

    # Try channel key auth first
    try:
        pub_key = await _lookup_channel_key(channel_id_str)
        if pub_key and _verify_signature_with_key(connection, pub_key):
            authenticated = True
            connection.state["channel_id"] = channel_id_str
    except Exception:
        pass

    # Fall back to device key auth (device signs with its key + X-Device-Id header)
    if not authenticated:
        device_id_str = connection.headers.get("x-device-id", "")
        if device_id_str:
            try:
                dev_key = await _lookup_device_key(device_id_str)
                if dev_key and _verify_signature_with_key(connection, dev_key):
                    if await _verify_device_owns_channel(device_id_str, channel_id_str):
                        authenticated = True
                        connection.state["channel_id"] = channel_id_str
                        connection.state["device_id"] = device_id_str
            except Exception:
                logger.exception("Device auth lookup failed")

    if not authenticated:
        raise NotAuthorizedException("Invalid signature")

    # Rate limiting
    rate_id = channel_id_str or connection.headers.get("x-forwarded-for", "api-client")
    method = connection.scope["method"]
    is_write = method == "POST"
    limit = _API_RATE_LIMIT_WRITES if is_write else _API_RATE_LIMIT_READS
    rate_key = f"aichat:rate:{rate_id}:{'write' if is_write else 'read'}"

    if not await _check_rate_limit(rate_key, limit):
        raise PermissionDeniedException("Rate limit exceeded")


# ---------------------------------------------------------------------------
# CSRF guard for web UI
# ---------------------------------------------------------------------------

CSRF_SESSION_KEY = "_csrf_token"


def _get_or_create_csrf_token(request: Request) -> str:
    """Get existing CSRF token or create a new one."""
    import secrets as _secrets
    token = request.session.get(CSRF_SESSION_KEY)
    if not token:
        token = _secrets.token_urlsafe(32)
        request.session[CSRF_SESSION_KEY] = token
    return token


# ---------------------------------------------------------------------------
# Sidebar data helper
# ---------------------------------------------------------------------------


async def _get_sidebar_data(user_id: UUID, db_session: AsyncSession) -> dict:
    """Fetch devices, channels grouped by device, and unread counts for sidebar."""
    result = await db_session.execute(
        select(AiChatChannel).order_by(AiChatChannel.created_at.desc())
    )
    channels = list(result.scalars().all())

    # Unread counts per channel (claude messages not yet read by user)
    unread_result = await db_session.execute(
        select(
            AiChatMessage.channel_id,
            func.count(AiChatMessage.id),
        )
        .where(AiChatMessage.sender == "claude")
        .where(AiChatMessage.read_by_user_at.is_(None))
        .where(AiChatMessage.channel_id.is_not(None))
        .group_by(AiChatMessage.channel_id)
    )
    unread_counts = {str(row[0]): row[1] for row in unread_result.all()}

    # Devices owned by user
    device_result = await db_session.execute(
        select(AiChatDevice)
        .where(AiChatDevice.owner_user_id == user_id)
        .order_by(AiChatDevice.created_at.desc())
    )
    devices = list(device_result.scalars().all())

    # Group channels by device, separating active and archived
    device_channels: dict[str, list] = {str(d.id): [] for d in devices}
    archived_channels: dict[str, list] = {str(d.id): [] for d in devices}
    for channel in channels:
        key = str(channel.device_id) if channel.device_id else None
        if key and key in device_channels:
            if channel.archived:
                archived_channels[key].append(channel)
            else:
                device_channels[key].append(channel)

    return {
        "channels": channels,
        "devices": devices,
        "device_channels": device_channels,
        "archived_channels": archived_channels,
        "unread_counts": unread_counts,
    }


# ---------------------------------------------------------------------------
# Web UI controller
# ---------------------------------------------------------------------------


class AiChatController(Controller):
    """User-facing web UI for AI chat."""

    path = "/"

    @get("/")
    async def index(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse | Redirect:
        user = await _get_user(request, db_session)
        if not user:
            return Redirect("https://zech.sh/auth/login?next=https://aichat.zech.sh/")

        if not await _has_permission(user.id, db_session):
            return TemplateResponse("unauthorized.html", context={"user": user})

        return TemplateResponse(
            "aichat.html",
            context={
                "user": user,
                "channel": None,
                "messages": [],
                "has_more": False,
                "csrf_token": _get_or_create_csrf_token(request),
            },
        )

    @get("/api/sidebar")
    async def sidebar_data(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        """JSON endpoint for sidebar device/channel data."""
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)

        sidebar = await _get_sidebar_data(user.id, db_session)

        return Response(content={
            "devices": [
                {"id": str(d.id), "name": d.name, "status": d.status or "offline"}
                for d in sidebar["devices"]
            ],
            "device_channels": {
                did: [{"id": str(c.id), "name": c.name} for c in chs]
                for did, chs in sidebar["device_channels"].items()
            },
            "archived_channels": {
                did: [{"id": str(c.id), "name": c.name} for c in chs]
                for did, chs in sidebar["archived_channels"].items()
            },
            "unread_counts": sidebar["unread_counts"],
        })

    @post("/channels/{channel_id:str}/update", status_code=200)
    async def update_channel(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        # CSRF verification
        submitted_token = request.headers.get("x-csrf-token", "")
        stored_token = request.session.get(CSRF_SESSION_KEY, "")
        if not stored_token or not hmac_mod.compare_digest(submitted_token, stored_token):
            return Response(content={"error": "CSRF validation failed"}, status_code=403)

        result = await db_session.execute(
            select(AiChatChannel).where(AiChatChannel.id == UUID(channel_id))
        )
        channel = result.scalar_one_or_none()
        if not channel:
            return Response(content={"error": "not found"}, status_code=404)

        body = await request.json()
        token = None

        # Rename
        name = body.get("name", "").strip()
        if name and name != channel.name:
            if len(name) > 100:
                return Response(content={"error": "name too long"}, status_code=400)
            channel.name = name

        # Regenerate key pair
        if body.get("regenerate_key"):
            private_key = Ed25519PrivateKey.generate()
            public_key = private_key.public_key()
            private_key_b64 = base64.b64encode(private_key.private_bytes_raw()).decode()
            public_key_b64 = base64.b64encode(public_key.public_bytes_raw()).decode()
            channel.public_key = public_key_b64
            _invalidate_channel_cache(channel_id)
            token = _make_compound_token(private_key_b64, str(channel.id))

        await db_session.commit()

        resp: dict = {"ok": True, "channel": {"id": str(channel.id), "name": channel.name}}
        if token:
            resp["token"] = token
        return Response(content=resp)

    @delete("/channels/{channel_id:str}", status_code=200)
    async def delete_channel(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        result = await db_session.execute(
            select(AiChatChannel).where(AiChatChannel.id == UUID(channel_id))
        )
        channel = result.scalar_one_or_none()
        if not channel:
            return Response(content={"error": "not found"}, status_code=404)

        _invalidate_channel_cache(channel_id)
        await db_session.delete(channel)
        await db_session.commit()
        return Response(content={"ok": True})

    @post("/channels/{channel_id:str}/archive", status_code=200)
    async def archive_channel(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        """Archive or unarchive a channel."""
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        result = await db_session.execute(
            select(AiChatChannel).where(AiChatChannel.id == UUID(channel_id))
        )
        channel = result.scalar_one_or_none()
        if not channel:
            return Response(content={"error": "not found"}, status_code=404)

        body = await request.json()
        archived = bool(body.get("archived", True))
        channel.archived = archived

        # Send worker:stop command when archiving
        if archived and channel.device_id:
            device_result = await db_session.execute(
                select(AiChatDevice).where(AiChatDevice.id == channel.device_id)
            )
            device = device_result.scalar_one_or_none()
            if device and device.owner_user_id:
                await notify_user(
                    str(device.owner_user_id),
                    "aichat:device-command",
                    mode=NotificationMode.TIMESERIES,
                    push_notify=False,
                    command="worker:stop",
                    payload={"channel_id": channel_id},
                    device_id=str(device.id),
                )

        await db_session.commit()
        return Response(content={"ok": True, "archived": archived})

    @get("/c/{channel_id:str}")
    async def channel_chat(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> TemplateResponse | Redirect:
        user = await _get_user(request, db_session)
        if not user:
            return Redirect("https://zech.sh/auth/login?next=https://aichat.zech.sh/")

        if not await _has_permission(user.id, db_session):
            return TemplateResponse("unauthorized.html", context={"user": user})

        result = await db_session.execute(
            select(AiChatChannel).where(AiChatChannel.id == UUID(channel_id))
        )
        channel = result.scalar_one_or_none()
        if not channel:
            return Redirect("/")

        # Fetch last 100 messages (grab 101 to detect if there are older ones)
        result = await db_session.execute(
            select(AiChatMessage)
            .where(AiChatMessage.channel_id == channel.id)
            .order_by(AiChatMessage.created_at.desc())
            .limit(101)
        )
        messages = list(result.scalars().all())
        has_more = len(messages) > 100
        messages = list(reversed(messages[:100]))

        # Find first unread Claude message (client marks as read via observer)
        first_unread_id = None
        for msg in messages:
            if msg.sender == "claude" and msg.read_by_user_at is None:
                first_unread_id = str(msg.id)
                break

        csrf_token = _get_or_create_csrf_token(request)

        # Look up device's X25519 public key for E2E auto-rekey
        device_x25519_public = ""
        if channel.device_id:
            dev_result = await db_session.execute(
                select(AiChatDevice.x25519_public).where(
                    AiChatDevice.id == channel.device_id
                )
            )
            device_x25519_public = dev_result.scalar_one_or_none() or ""

        return TemplateResponse(
            "aichat.html",
            context={
                "user": user,
                "channel": channel,
                "messages": messages,
                "has_more": has_more,
                "hide_sidebar": True,
                "csrf_token": csrf_token,
                "first_unread_id": first_unread_id,
                "encrypted_channel_key": channel.encrypted_channel_key or "",
                "key_nonce": channel.key_nonce or "",
                "device_x25519_public": device_x25519_public,
            },
        )

    @post("/c/{channel_id:str}/send")
    async def send_message(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)

        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        # CSRF verification
        submitted_token = request.headers.get("x-csrf-token", "")
        stored_token = request.session.get(CSRF_SESSION_KEY, "")
        if not stored_token or not hmac_mod.compare_digest(submitted_token, stored_token):
            return Response(content={"error": "CSRF validation failed"}, status_code=403)

        result = await db_session.execute(
            select(AiChatChannel).where(AiChatChannel.id == UUID(channel_id))
        )
        channel = result.scalar_one_or_none()
        if not channel:
            return Response(content={"error": "channel not found"}, status_code=404)

        body = await request.json()
        content = body.get("content", "").strip()
        attachments = body.get("attachments") or []
        encrypted_payload = body.get("encrypted_payload", "")
        msg_nonce = body.get("nonce", "")
        is_encrypted = bool(encrypted_payload and msg_nonce)

        if not content and not attachments and not is_encrypted:
            return Response(content={"error": "empty message"}, status_code=400)

        # Validate and sanitize attachments
        clean_attachments = []
        for att in attachments[:10]:
            if isinstance(att, dict) and att.get("asset_id") and att.get("url"):
                clean_attachments.append({
                    "asset_id": str(att["asset_id"]),
                    "filename": str(att.get("filename", "")),
                    "content_type": str(att.get("content_type", "")),
                    "url": str(att["url"]),
                })

        msg = AiChatMessage(
            sender="user",
            content=content if not is_encrypted else "[encrypted]",
            user_id=user.id,
            channel_id=channel.id,
            attachments=clean_attachments or None,
        )
        db_session.add(msg)
        await db_session.flush()

        notification_kwargs = {
            "sender": "user",
            "message_id": str(msg.id),
            "channel_id": str(channel.id),
        }
        if not is_encrypted:
            notification_kwargs["content"] = content
            notification_kwargs["attachments"] = clean_attachments

        await notify_user(
            str(user.id),
            "aichat:message",
            mode=NotificationMode.TIMESERIES,
            push_notify=False,
            **notification_kwargs,
        )

        # For encrypted messages, push content directly to device WebSocket
        # (bypasses notification persistence)
        if is_encrypted and channel.device_id:
            await _push_to_device_ws(str(channel.device_id), {
                "event_type": "aichat:user-content",
                "encrypted_payload": encrypted_payload,
                "nonce": msg_nonce,
                "message_id": str(msg.id),
                "channel_id": str(channel.id),
                "sender": "user",
            })

        await db_session.commit()
        return Response(content={"ok": True})

    @post("/c/{channel_id:str}/event")
    async def send_event(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)

        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        submitted_token = request.headers.get("x-csrf-token", "")
        stored_token = request.session.get(CSRF_SESSION_KEY, "")
        if not stored_token or not hmac_mod.compare_digest(submitted_token, stored_token):
            return Response(content={"error": "CSRF validation failed"}, status_code=403)

        result = await db_session.execute(
            select(AiChatChannel).where(AiChatChannel.id == UUID(channel_id))
        )
        channel = result.scalar_one_or_none()
        if not channel:
            return Response(content={"error": "channel not found"}, status_code=404)

        body = await request.json()
        event_type = body.get("event_type", "").strip()
        if not event_type:
            return Response(content={"error": "event_type required"}, status_code=400)

        msg = AiChatMessage(
            sender="event",
            content=event_type,
            channel_id=channel.id,
        )
        db_session.add(msg)
        await db_session.flush()

        await notify_user(
            str(user.id),
            "aichat:message",
            mode=NotificationMode.TIMESERIES,
            push_notify=False,
            sender="event",
            content=event_type,
            message_id=str(msg.id),
            channel_id=str(channel.id),
        )

        await db_session.commit()
        return Response(content={"ok": True, "id": str(msg.id)}, status_code=201)

    @post("/c/{channel_id:str}/mark-read")
    async def mark_read_by_user(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        """Mark Claude messages as read by the user (for real-time messages)."""
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)

        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        submitted_token = request.headers.get("x-csrf-token", "")
        stored_token = request.session.get(CSRF_SESSION_KEY, "")
        if not stored_token or not hmac_mod.compare_digest(submitted_token, stored_token):
            return Response(content={"error": "CSRF validation failed"}, status_code=403)

        body = await request.json()
        message_ids = body.get("message_ids", [])
        if not message_ids:
            return Response(content={"error": "message_ids required"}, status_code=400)

        now = datetime.now(timezone.utc)
        result = await db_session.execute(
            select(AiChatMessage)
            .where(AiChatMessage.id.in_([UUID(mid) for mid in message_ids]))
            .where(AiChatMessage.channel_id == UUID(channel_id))
            .where(AiChatMessage.sender == "claude")
            .where(AiChatMessage.read_by_user_at.is_(None))
        )
        for msg in result.scalars().all():
            msg.read_by_user_at = now

        await db_session.commit()
        return Response(content={"ok": True})

    @post("/c/{channel_id:str}/upload")
    async def upload_attachment(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        # CSRF verification
        submitted_token = request.headers.get("x-csrf-token", "")
        stored_token = request.session.get(CSRF_SESSION_KEY, "")
        if not stored_token or not hmac_mod.compare_digest(submitted_token, stored_token):
            return Response(content={"error": "CSRF validation failed"}, status_code=403)

        result = await db_session.execute(
            select(AiChatChannel).where(AiChatChannel.id == UUID(channel_id))
        )
        channel = result.scalar_one_or_none()
        if not channel:
            return Response(content={"error": "channel not found"}, status_code=404)

        form = await request.form()
        upload = form.get("file")
        if not upload or not hasattr(upload, "read"):
            return Response(content={"error": "no file provided"}, status_code=400)

        content_type = upload.content_type or "application/octet-stream"
        if not content_type.startswith("image/"):
            return Response(content={"error": "only images are supported"}, status_code=400)

        data = await upload.read()
        max_size = 10 * 1024 * 1024  # 10 MB
        if len(data) > max_size:
            return Response(content={"error": "file too large (max 10MB)"}, status_code=400)

        storage = request.app.state.storage_manager
        asset = await upload_asset(
            db_session,
            storage,
            filename=upload.filename or "image",
            data=data,
            content_type=content_type,
            folder="aichat",
            user_id=user.id,
        )
        url = await get_asset_url(storage, asset)

        await db_session.commit()

        return Response(content={
            "ok": True,
            "asset_id": str(asset.id),
            "filename": asset.filename,
            "content_type": asset.content_type,
            "url": url,
        })

    @get("/c/{channel_id:str}/messages")
    async def load_older_messages(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        result = await db_session.execute(
            select(AiChatChannel).where(AiChatChannel.id == UUID(channel_id))
        )
        channel = result.scalar_one_or_none()
        if not channel:
            return Response(content={"error": "not found"}, status_code=404)

        before = request.query_params.get("before")
        after = request.query_params.get("after")
        if not before and not after:
            return Response(content={"error": "before or after param required"}, status_code=400)

        if after:
            # Fetch messages newer than the given message ID
            after_msg = await db_session.get(AiChatMessage, UUID(after))
            if not after_msg:
                return Response(content={"error": "message not found"}, status_code=404)

            result = await db_session.execute(
                select(AiChatMessage)
                .where(AiChatMessage.channel_id == channel.id)
                .where(AiChatMessage.created_at > after_msg.created_at)
                .order_by(AiChatMessage.created_at.asc())
                .limit(100)
            )
            messages = list(result.scalars().all())

            return Response(content={
                "messages": [
                    {
                        "id": str(m.id),
                        "sender": m.sender,
                        "content": m.content,
                        "read_by_claude_at": m.read_by_claude_at.isoformat() if m.read_by_claude_at else None,
                        "attachments": m.attachments or [],
                    }
                    for m in messages
                ],
            })

        before_msg = await db_session.get(AiChatMessage, UUID(before))
        if not before_msg:
            return Response(content={"error": "message not found"}, status_code=404)

        # Fetch 101 to detect if there are still more
        result = await db_session.execute(
            select(AiChatMessage)
            .where(AiChatMessage.channel_id == channel.id)
            .where(AiChatMessage.created_at < before_msg.created_at)
            .order_by(AiChatMessage.created_at.desc())
            .limit(101)
        )
        messages = list(result.scalars().all())
        has_more = len(messages) > 100
        messages = list(reversed(messages[:100]))

        return Response(content={
            "has_more": has_more,
            "messages": [
                {
                    "id": str(m.id),
                    "sender": m.sender,
                    "content": m.content,
                    "read_by_claude_at": m.read_by_claude_at.isoformat() if m.read_by_claude_at else None,
                    "attachments": m.attachments or [],
                }
                for m in messages
            ],
        })

    @post("/c/{channel_id:str}/interaction/{interaction_id:str}/respond")
    async def interaction_respond(
        self, channel_id: str, interaction_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        """User responds to a plan or question from the agent."""
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)

        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        # CSRF verification
        submitted_token = request.headers.get("x-csrf-token", "")
        stored_token = request.session.get(CSRF_SESSION_KEY, "")
        if not stored_token or not hmac_mod.compare_digest(submitted_token, stored_token):
            return Response(content={"error": "CSRF validation failed"}, status_code=403)

        body = await request.json()
        action = body.get("action", "")  # "accept", "deny", or "answer"
        answer = body.get("answer", "")
        reason = body.get("reason", "")

        if action not in ("accept", "deny"):
            return Response(content={"error": "invalid action"}, status_code=400)

        # Dismiss the persistent interaction notification
        await dismiss_user_group(user.id, f"aichat:interaction:{channel_id}")

        # Send the response as a notification so the agent's SSE listener picks it up
        await notify_user(
            user.id,
            "aichat:interaction-response",
            mode=NotificationMode.QUEUED,
            group=f"aichat:interaction-response:{channel_id}",
            push_notify=False,
            interaction_id=interaction_id,
            action=action,
            answer=answer,
            reason=reason,
            channel_id=channel_id,
        )

        return Response(content={"ok": True})

    @post("/c/{channel_id:str}/request-history")
    async def request_history(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        """Request message history from device via server relay.

        Browser sends {before, limit}, server forwards to device via WS,
        device responds with encrypted messages. Response comes back via SSE.
        """
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)

        # CSRF verification
        submitted_token = request.headers.get("x-csrf-token", "")
        stored_token = request.session.get(CSRF_SESSION_KEY, "")
        if not stored_token or not hmac_mod.compare_digest(submitted_token, stored_token):
            return Response(content={"error": "CSRF validation failed"}, status_code=403)

        body = await request.json()
        request_id = body.get("request_id", secrets.token_urlsafe(8))

        # Forward to device via notification
        await notify_user(
            user.id,
            "aichat:history-request",
            mode=NotificationMode.TIMESERIES,
            push_notify=False,
            channel_id=channel_id,
            request_id=request_id,
            before=body.get("before"),
            limit=min(body.get("limit", 100), 200),
        )

        return Response(content={"ok": True, "request_id": request_id})

    @post("/c/{channel_id:str}/rekey")
    async def rekey(
        self, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        """Request channel key re-encryption from device for a new browser session.

        Browser sends its X25519 public key. Server forwards to device.
        Device computes new shared secret, re-encrypts channel keys, responds via WS.
        Response comes back to browser via SSE.
        """
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)

        # CSRF verification
        submitted_token = request.headers.get("x-csrf-token", "")
        stored_token = request.session.get(CSRF_SESSION_KEY, "")
        if not stored_token or not hmac_mod.compare_digest(submitted_token, stored_token):
            return Response(content={"error": "CSRF validation failed"}, status_code=403)

        body = await request.json()
        browser_x25519_public = body.get("browser_x25519_public", "")
        if not browser_x25519_public:
            return Response(content={"error": "browser_x25519_public required"}, status_code=400)

        # Use browser-provided request_id if present (for response matching),
        # otherwise generate one
        request_id = body.get("request_id") or secrets.token_urlsafe(8)

        # Forward to device via notification
        await notify_user(
            user.id,
            "aichat:rekey-request",
            mode=NotificationMode.TIMESERIES,
            push_notify=False,
            channel_id=channel_id,
            request_id=request_id,
            browser_x25519_public=browser_x25519_public,
        )

        return Response(content={"ok": True, "request_id": request_id})


# ---------------------------------------------------------------------------
# Shared business logic (used by both HTTP API and WebSocket handler)
# ---------------------------------------------------------------------------


async def _do_send_message(
    db_session: AsyncSession,
    channel_id: UUID,
    content: str,
    attachments: list[dict] | None = None,
    encrypted_payload: str = "",
    nonce: str = "",
) -> dict:
    """Create a claude message, notify the user, and send push."""
    content = (content or "").strip()
    is_encrypted = bool(encrypted_payload and nonce)

    clean_attachments: list[dict] = []
    if attachments:
        for att in attachments[:10]:
            if isinstance(att, dict) and att.get("asset_id") and att.get("url"):
                clean_attachments.append({
                    "asset_id": str(att["asset_id"]),
                    "filename": str(att.get("filename", "")),
                    "content_type": str(att.get("content_type", "")),
                    "url": str(att["url"]),
                })

    if not content and not clean_attachments and not is_encrypted:
        raise ValueError("empty message")

    # Store message — content may be empty if encrypted
    msg = AiChatMessage(
        sender="claude",
        content=content if not is_encrypted else "[encrypted]",
        channel_id=channel_id,
        attachments=clean_attachments or None,
    )
    db_session.add(msg)
    await db_session.flush()

    target_user_id = await _get_target_user_id(db_session, channel_id)
    if target_user_id:
        ch_result = await db_session.execute(
            select(AiChatChannel.name).where(AiChatChannel.id == channel_id)
        )
        channel_name = ch_result.scalar_one_or_none() or "Agent"

        notification_kwargs = {
            "sender": "claude",
            "message_id": str(msg.id),
            "channel_id": str(channel_id),
        }
        if not is_encrypted:
            notification_kwargs["content"] = content
            notification_kwargs["attachments"] = clean_attachments

        await notify_user(
            target_user_id,
            "aichat:message",
            mode=NotificationMode.TIMESERIES,
            push_notify=False,
            **notification_kwargs,
        )

        # Push notification — generic body when encrypted
        if is_encrypted:
            truncated = "New message"
        else:
            truncated = content[:120] + "..." if len(content) > 120 else content
        try:
            await send_push(
                db_session,
                user_id=target_user_id,
                title=f"AI.CHAT::{channel_name}",
                body=truncated,
                url=f"/c/{channel_id}#msg-{msg.id}",
                tag=f"aichat-msg-{channel_id}",
            )
        except Exception:
            logger.exception("Push send failed for user %s", target_user_id)

    await db_session.commit()
    return {"ok": True, "id": str(msg.id)}


async def _do_mark_read(
    db_session: AsyncSession,
    channel_id: UUID,
    message_ids: list[str],
) -> dict:
    """Mark user messages as read by claude."""
    if not message_ids:
        raise ValueError("message_ids required")

    query = (
        select(AiChatMessage)
        .where(AiChatMessage.id.in_([UUID(mid) for mid in message_ids]))
        .where(AiChatMessage.channel_id == channel_id)
        .where(AiChatMessage.sender == "user")
        .where(AiChatMessage.read_by_claude_at.is_(None))
    )
    result = await db_session.execute(query)
    messages = list(result.scalars().all())

    now = datetime.now(timezone.utc)
    read_ids: list[str] = []
    target_user_id = None
    for msg in messages:
        msg.read_by_claude_at = now
        read_ids.append(str(msg.id))
        if msg.user_id:
            target_user_id = str(msg.user_id)

    if read_ids and target_user_id:
        await notify_user(
            target_user_id,
            "aichat:read",
            mode=NotificationMode.TIMESERIES,
            push_notify=False,
            message_ids=read_ids,
        )

    await db_session.commit()
    return {"marked": read_ids}


async def _do_create_event(
    db_session: AsyncSession,
    channel_id: UUID,
    event_type: str,
) -> dict:
    """Store a channel event (e.g. plan:enter, plan:exit) as a message."""
    event_type = (event_type or "").strip()
    if not event_type:
        raise ValueError("event_type required")

    msg = AiChatMessage(
        sender="event",
        content=event_type,
        channel_id=channel_id,
    )
    db_session.add(msg)
    await db_session.flush()

    target_user_id = await _get_target_user_id(db_session, channel_id)
    if target_user_id:
        await notify_user(
            target_user_id,
            "aichat:message",
            mode=NotificationMode.TIMESERIES,
            push_notify=False,
            sender="event",
            content=event_type,
            message_id=str(msg.id),
            channel_id=str(channel_id),
        )

    await db_session.commit()
    return {"ok": True, "id": str(msg.id)}


async def _do_tool_status(
    db_session: AsyncSession,
    channel_id: UUID,
    status: str,
    tool: str = "",
    description: str = "",
    encrypted_description: str = "",
    description_nonce: str = "",
) -> dict:
    """Update tool status with DB persistence and notification."""
    if status not in ("active", "done", "idle"):
        raise ValueError("invalid status")
    is_encrypted = bool(encrypted_description and description_nonce)

    # Persist tool use in a "tools" message block
    # When encrypted, use placeholder — real data lives on device
    db_description = "[encrypted]" if is_encrypted else description
    if status == "active" and (description or is_encrypted):
        last_claude = await db_session.execute(
            select(AiChatMessage.created_at)
            .where(
                AiChatMessage.channel_id == channel_id,
                AiChatMessage.sender == "claude",
            )
            .order_by(AiChatMessage.created_at.desc())
            .limit(1)
        )
        last_claude_at = last_claude.scalar_one_or_none()

        tools_query = (
            select(AiChatMessage)
            .where(
                AiChatMessage.channel_id == channel_id,
                AiChatMessage.sender == "tools",
            )
            .order_by(AiChatMessage.created_at.desc())
            .limit(1)
        )
        if last_claude_at:
            tools_query = tools_query.where(
                AiChatMessage.created_at > last_claude_at
            )

        result = await db_session.execute(tools_query)
        tools_msg = result.scalar_one_or_none()

        if tools_msg:
            existing = tools_msg.content or ""
            lines = [l for l in existing.split("\n") if l]
            if not lines or lines[-1] != db_description:
                tools_msg.content = existing + "\n" + db_description if existing else db_description
        else:
            tools_msg = AiChatMessage(
                sender="tools",
                content=db_description,
                channel_id=channel_id,
            )
            db_session.add(tools_msg)

        await db_session.commit()

    target_user_id = await _get_target_user_id(db_session, channel_id)
    if target_user_id:
        if tool == "reasoning":
            group = f"aichat:reasoning:{channel_id}"
        else:
            group = f"aichat:tool:{channel_id}"

        notification_kwargs = {
            "status": status,
            "tool": tool,
            "channel_id": str(channel_id),
        }
        if not is_encrypted:
            notification_kwargs["description"] = description

        await notify_user(
            target_user_id,
            "aichat:tool",
            mode=NotificationMode.QUEUED,
            group=group,
            push_notify=False,
            **notification_kwargs,
        )

    return {"ok": True}


async def _do_create_interaction(
    db_session: AsyncSession,
    channel_id: UUID,
    interaction_type: str,
    content: str,
    options: list | None = None,
    multi_select: bool = False,
    encrypted_payload: str = "",
    nonce: str = "",
) -> dict:
    """Create an interaction request (question or plan) for the user."""
    if interaction_type not in ("question", "plan"):
        raise ValueError("invalid type")

    interaction_id = str(uuid4())

    target_user_id = await _get_target_user_id(db_session, channel_id)
    if target_user_id:
        is_encrypted = bool(encrypted_payload and nonce)
        notification_kwargs: dict = dict(
            interaction_id=interaction_id,
            interaction_type=interaction_type,
            channel_id=str(channel_id),
        )
        if not is_encrypted:
            notification_kwargs["content"] = content
            if options:
                notification_kwargs["options"] = options
                notification_kwargs["multi_select"] = multi_select
        await notify_user(
            target_user_id,
            "aichat:interaction",
            mode=NotificationMode.QUEUED,
            group=f"aichat:interaction:{channel_id}",
            push_notify=False,
            **notification_kwargs,
        )

    return {"ok": True, "interaction_id": interaction_id}


async def _do_report_directories(
    db_session: AsyncSession,
    channel_id: UUID,
    working_directory: str,
    additional_directories: list | None = None,
) -> dict:
    """Report agent's working directory and optional additional directories."""
    result = await db_session.execute(
        select(AiChatChannel).where(AiChatChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise ValueError("channel not found")

    if working_directory:
        channel.working_directory = working_directory[:500]

    if additional_directories is not None:
        channel.additional_directories = json.dumps(additional_directories)

    await db_session.commit()
    return {"ok": True}


async def _do_list_device_channels(
    db_session: AsyncSession,
    device_id: str,
) -> dict:
    """Return channels assigned to a device."""
    result = await db_session.execute(
        select(
            AiChatChannel.id,
            AiChatChannel.name,
            AiChatChannel.working_directory,
            AiChatChannel.additional_directories,
        )
        .where(AiChatChannel.device_id == UUID(device_id))
        .where(AiChatChannel.archived.is_(False))
    )
    channels = []
    for row in result.all():
        additional: list = []
        if row.additional_directories:
            try:
                additional = json.loads(row.additional_directories)
            except (ValueError, TypeError):
                pass
        channels.append({
            "id": str(row.id),
            "name": row.name,
            "working_directory": row.working_directory or "",
            "additional_directories": additional,
        })
    return {"channels": channels}


async def _do_create_channel(
    db_session: AsyncSession,
    device_id: str,
    name: str,
    working_directory: str = "",
) -> dict:
    """Create a new channel for a device (called via WebSocket)."""
    name = (name or "").strip() or "New Task"
    if len(name) > 100:
        name = name[:100]

    # Look up device to get owner
    result = await db_session.execute(
        select(AiChatDevice).where(AiChatDevice.id == UUID(device_id))
    )
    device = result.scalar_one_or_none()
    if not device:
        raise ValueError("Device not found")

    # Generate channel keypair
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()
    public_key_b64 = base64.b64encode(public_key.public_bytes_raw()).decode()

    channel = AiChatChannel(
        name=name,
        public_key=public_key_b64,
        created_by_user_id=device.owner_user_id,
        device_id=device.id,
        working_directory=working_directory or None,
    )
    db_session.add(channel)
    await db_session.flush()

    await db_session.commit()

    return {
        "channel": {
            "id": str(channel.id),
            "name": channel.name,
            "working_directory": working_directory,
        },
    }


async def _do_rename_device(
    db_session: AsyncSession,
    device_id: str,
    name: str,
) -> dict:
    """Rename a device."""
    name = (name or "").strip()
    if not name:
        raise ValueError("name is required")
    if len(name) > 100:
        raise ValueError("name too long")

    result = await db_session.execute(
        select(AiChatDevice).where(AiChatDevice.id == UUID(device_id))
    )
    device = result.scalar_one_or_none()
    if not device:
        raise ValueError("device not found")

    device.name = name
    await db_session.commit()
    return {"device": {"id": str(device.id), "name": device.name}}


async def _do_delete_device(
    db_session: AsyncSession,
    device_id: str,
) -> dict:
    """Delete a device and disassociate its channels."""
    result = await db_session.execute(
        select(AiChatDevice).where(AiChatDevice.id == UUID(device_id))
    )
    device = result.scalar_one_or_none()
    if not device:
        raise ValueError("device not found")

    # Disassociate channels from this device
    channel_result = await db_session.execute(
        select(AiChatChannel).where(AiChatChannel.device_id == device.id)
    )
    for channel in channel_result.scalars().all():
        channel.device_id = None

    _invalidate_device_cache(device_id)
    await db_session.delete(device)
    await db_session.commit()
    return {}


async def _do_report_device_status(
    db_session: AsyncSession,
    device_id: str,
    status_data: dict,
) -> dict:
    """Update device status and last_seen_at."""
    result = await db_session.execute(
        select(AiChatDevice).where(AiChatDevice.id == UUID(device_id))
    )
    device = result.scalar_one_or_none()
    if not device:
        raise ValueError("device not found")

    device.status = status_data.get("status", "online")
    device.last_seen_at = datetime.now(timezone.utc)
    await db_session.commit()
    return {"ok": True}


# ---------------------------------------------------------------------------
# API controller (agent-facing, channel-aware)
# ---------------------------------------------------------------------------


class AiChatApiController(Controller):
    """JSON API for agents to read and send messages."""

    path = "/api"
    guards = [aichat_api_guard]

    def _get_channel_id(self, request: Request) -> UUID:
        """Get channel_id from auth state (always present after guard)."""
        return UUID(request.state["channel_id"])

    @post("/messages/read")
    async def mark_read(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        channel_id = self._get_channel_id(request)
        body = await request.json()
        try:
            result = await _do_mark_read(db_session, channel_id, body.get("message_ids", []))
        except ValueError as e:
            return Response(content={"error": str(e)}, status_code=400)
        return Response(content=result)

    @post("/messages")
    async def create_message(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        channel_id = self._get_channel_id(request)
        body = await request.json()
        try:
            result = await _do_send_message(
                db_session, channel_id,
                body.get("content", ""),
                body.get("attachments"),
            )
        except ValueError as e:
            return Response(content={"error": str(e)}, status_code=400)
        return Response(content=result, status_code=201)

    @post("/event")
    async def create_event(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        """Store a channel event (e.g. plan:enter, plan:exit) as a message."""
        channel_id = self._get_channel_id(request)
        body = await request.json()
        try:
            result = await _do_create_event(db_session, channel_id, body.get("event_type", ""))
        except ValueError as e:
            return Response(content={"error": str(e)}, status_code=400)
        return Response(content=result, status_code=201)

    @post("/session")
    async def create_session(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        """Exchange Ed25519 auth for a session cookie (for SSE stream access)."""
        channel_id = self._get_channel_id(request)

        # Find the channel owner
        result = await db_session.execute(
            select(AiChatChannel.created_by_user_id)
            .where(AiChatChannel.id == channel_id)
        )
        owner_id = result.scalar_one_or_none()
        if not owner_id:
            return Response(content={"error": "channel not found"}, status_code=404)

        # Set session user_id so the SSE stream delivers this user's notifications
        request.session[SESSION_USER_ID] = str(owner_id)

        return Response(content={"ok": True, "channel_id": str(channel_id)})

    @post("/tool-status")
    async def tool_status(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        channel_id = self._get_channel_id(request)
        body = await request.json()
        try:
            result = await _do_tool_status(
                db_session, channel_id,
                body.get("status", ""), body.get("tool", ""), body.get("description", ""),
            )
        except ValueError as e:
            return Response(content={"error": str(e)}, status_code=400)
        return Response(content=result)

    @post("/interaction")
    async def create_interaction(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        """Agent creates an interaction request (question or plan) for the user."""
        channel_id = self._get_channel_id(request)
        body = await request.json()
        try:
            result = await _do_create_interaction(
                db_session, channel_id,
                body.get("type", ""), body.get("content", ""),
                options=body.get("options", []),
                multi_select=body.get("multi_select", False),
            )
        except ValueError as e:
            return Response(content={"error": str(e)}, status_code=400)
        return Response(content=result, status_code=201)

    @post("/directories")
    async def report_directories(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        """Agent reports its working directory and optional additional directories."""
        channel_id = self._get_channel_id(request)
        body = await request.json()
        try:
            result = await _do_report_directories(
                db_session, channel_id,
                body.get("working_directory", ""),
                body.get("additional_directories"),
            )
        except ValueError as e:
            return Response(content={"error": str(e)}, status_code=404)
        return Response(content=result)


# ---------------------------------------------------------------------------
# Device registration controller (public — no auth for registration)
# ---------------------------------------------------------------------------


class AiChatDeviceRegistrationController(Controller):
    """Public endpoints for device auth flow."""

    path = "/api/devices"

    @post("/register")
    async def register_device(self, request: Request) -> Response:
        """Start device auth flow. Returns device_code + auth_url."""
        body = await request.json()
        public_key = body.get("public_key", "").strip()
        name = body.get("name", "").strip()

        if not public_key:
            return Response(content={"error": "public_key required"}, status_code=400)
        if not name:
            name = "Unknown Device"
        if len(name) > 100:
            name = name[:100]

        # Validate public key is valid base64 Ed25519 (32 bytes)
        try:
            key_bytes = base64.b64decode(public_key)
            if len(key_bytes) != 32:
                raise ValueError("invalid key length")
            Ed25519PublicKey.from_public_bytes(key_bytes)
        except Exception:
            return Response(content={"error": "invalid public_key"}, status_code=400)

        # Optional X25519 public key for E2E encryption key exchange
        x25519_public = body.get("x25519_public", "").strip()
        if x25519_public:
            try:
                x_bytes = base64.b64decode(x25519_public)
                if len(x_bytes) != 32:
                    raise ValueError("invalid x25519 key length")
            except Exception:
                x25519_public = ""  # Ignore invalid key, proceed without E2E

        device_code = secrets.token_urlsafe(16)
        auth_url = f"https://aichat.zech.sh/devices/authorize?code={device_code}"

        code_data = {
            "status": "pending",
            "public_key": public_key,
            "name": name,
        }
        if x25519_public:
            code_data["x25519_public"] = x25519_public

        await _store_device_code(device_code, code_data)

        return Response(
            content={"device_code": device_code, "auth_url": auth_url},
            status_code=200,
        )

    @get("/status")
    async def device_auth_status(self, request: Request) -> Response:
        """Poll for device auth completion."""
        code = request.query_params.get("code", "")
        if not code:
            return Response(content={"error": "code required"}, status_code=400)

        data = await _get_device_code(code)
        if not data:
            return Response(content={"error": "device_code expired"}, status_code=410)

        if data["status"] == "approved":
            # Clean up code after retrieval
            await _delete_device_code(code)
            resp = {
                "status": "approved",
                "device_id": data["device_id"],
            }
            # Include browser's X25519 public key for ECDH completion
            if data.get("browser_x25519_public"):
                resp["browser_x25519_public"] = data["browser_x25519_public"]
            return Response(content=resp)
        elif data["status"] == "denied":
            await _delete_device_code(code)
            return Response(content={"status": "denied"})
        else:
            return Response(content={"status": "pending"})


# ---------------------------------------------------------------------------
# Device approval controller (web UI — session auth)
# ---------------------------------------------------------------------------


class AiChatDeviceApprovalController(Controller):
    """Web UI for approving device registrations."""

    path = "/devices"

    @get("/authorize")
    async def authorize_page(
        self, request: Request, db_session: AsyncSession
    ) -> TemplateResponse | Redirect:
        user = await _get_user(request, db_session)
        if not user:
            code = request.query_params.get("code", "")
            return Redirect(
                f"https://zech.sh/auth/login?next=https://aichat.zech.sh/devices/authorize?code={code}"
            )

        if not await _has_permission(user.id, db_session):
            return TemplateResponse("unauthorized.html", context={"user": user})

        code = request.query_params.get("code", "")
        if not code:
            return Redirect("/")

        data = await _get_device_code(code)
        if not data or data["status"] != "pending":
            return TemplateResponse(
                "aichat_device_authorize.html",
                context={
                    "user": user,
                    "error": "This device code has expired or already been used.",
                    "hide_sidebar": True,
                },
            )

        csrf_token = _get_or_create_csrf_token(request)
        return TemplateResponse(
            "aichat_device_authorize.html",
            context={
                "user": user,
                "device_name": data["name"],
                "device_code": code,
                "csrf_token": csrf_token,
                "hide_sidebar": True,
                "device_x25519_public": data.get("x25519_public", ""),
            },
        )

    @post("/authorize")
    async def authorize_device(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        # CSRF verification
        submitted_token = request.headers.get("x-csrf-token", "")
        stored_token = request.session.get(CSRF_SESSION_KEY, "")
        if not stored_token or not hmac_mod.compare_digest(submitted_token, stored_token):
            return Response(content={"error": "CSRF validation failed"}, status_code=403)

        body = await request.json()
        code = body.get("code", "")
        action = body.get("action", "")

        if not code:
            return Response(content={"error": "code required"}, status_code=400)

        data = await _get_device_code(code)
        if not data or data["status"] != "pending":
            return Response(content={"error": "device_code expired or used"}, status_code=410)

        if action == "deny":
            await _update_device_code(code, {**data, "status": "denied"})
            return Response(content={"ok": True, "status": "denied"})

        if action != "approve":
            return Response(content={"error": "invalid action"}, status_code=400)

        # Accept browser's X25519 public key for E2E key exchange
        browser_x25519_public = body.get("browser_x25519_public", "").strip()

        # Create the device
        device = AiChatDevice(
            name=data["name"],
            public_key=data["public_key"],
            owner_user_id=user.id,
            status="offline",
        )
        db_session.add(device)
        await db_session.flush()

        # Update the code with device_id and browser's X25519 key for the manager to retrieve
        code_update = {
            **data,
            "status": "approved",
            "device_id": str(device.id),
        }
        if browser_x25519_public:
            code_update["browser_x25519_public"] = browser_x25519_public

        await _update_device_code(code, code_update)

        await db_session.commit()

        return Response(content={
            "ok": True,
            "status": "approved",
            "device_id": str(device.id),
        })


# ---------------------------------------------------------------------------
# Device API controller (device-authenticated)
# ---------------------------------------------------------------------------


class AiChatDeviceApiController(Controller):
    """API endpoints authenticated by device Ed25519 keys."""

    path = "/api/device"
    guards = [aichat_device_api_guard]

    def _get_device_id(self, request: Request) -> str:
        return request.state["device_id"]

    @post("/session")
    async def create_device_session(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        """Exchange device auth for a session cookie (for SSE stream access)."""
        device_id = self._get_device_id(request)

        result = await db_session.execute(
            select(AiChatDevice.owner_user_id)
            .where(AiChatDevice.id == UUID(device_id))
        )
        owner_id = result.scalar_one_or_none()
        if not owner_id:
            return Response(content={"error": "device not found"}, status_code=404)

        request.session[SESSION_USER_ID] = str(owner_id)
        return Response(content={"ok": True, "device_id": device_id})

    @post("/status")
    async def report_device_status(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        """Manager reports device + worker status."""
        device_id = self._get_device_id(request)
        body = await request.json()
        try:
            result = await _do_report_device_status(db_session, device_id, body)
        except ValueError as e:
            return Response(content={"error": str(e)}, status_code=404)
        return Response(content=result)

    @get("/channels")
    async def list_device_channels(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        """Return channels assigned to this device."""
        device_id = self._get_device_id(request)
        result = await _do_list_device_channels(db_session, device_id)
        return Response(content=result)


# ---------------------------------------------------------------------------
# Device WebSocket controller
# ---------------------------------------------------------------------------


def _verify_ws_signature(socket: WebSocket, public_key: Ed25519PublicKey) -> bool:
    """Verify Ed25519 signature on a WebSocket upgrade request.

    WebSocket ASGI scopes don't have an HTTP method, so we hardcode GET
    and use the known path.
    """
    timestamp_str = socket.headers.get("x-timestamp", "")
    signature_b64 = socket.headers.get("x-signature", "")
    if not timestamp_str or not signature_b64:
        return False

    try:
        timestamp = int(timestamp_str)
    except ValueError:
        return False

    if abs(time.time() - timestamp) > _MAX_TIMESTAMP_DRIFT:
        return False

    path = "/api/device/ws"
    message = f"{timestamp_str}.GET.{path}".encode()

    try:
        signature = base64.b64decode(signature_b64)
        public_key.verify(signature, message)
        return True
    except Exception:
        return False


async def _dispatch_ws_message(
    db_session: AsyncSession,
    device_id: str,
    msg_type: str,
    msg: dict,
    owned_channels: set[str],
) -> dict:
    """Route a WebSocket message to the appropriate business logic handler."""
    channel_id_str = msg.get("channel_id")
    channel_id = UUID(channel_id_str) if channel_id_str else None

    match msg_type:
        case "send_message":
            return await _do_send_message(
                db_session, channel_id, msg.get("content", ""),
                msg.get("attachments"),
                encrypted_payload=msg.get("encrypted_payload", ""),
                nonce=msg.get("nonce", ""),
            )
        case "mark_read":
            return await _do_mark_read(
                db_session, channel_id, msg.get("message_ids", []),
            )
        case "send_event":
            return await _do_create_event(
                db_session, channel_id, msg.get("event_type", ""),
            )
        case "tool_status":
            return await _do_tool_status(
                db_session, channel_id,
                msg.get("status", ""), msg.get("tool", ""),
                msg.get("description", ""),
                encrypted_description=msg.get("encrypted_description", ""),
                description_nonce=msg.get("description_nonce", ""),
            )
        case "create_interaction":
            return await _do_create_interaction(
                db_session, channel_id,
                msg.get("interaction_type", ""), msg.get("content", ""),
                options=msg.get("options"),
                multi_select=msg.get("multi_select", False),
                encrypted_payload=msg.get("encrypted_payload", ""),
                nonce=msg.get("nonce", ""),
            )
        case "report_directories":
            return await _do_report_directories(
                db_session, channel_id,
                msg.get("working_directory", ""),
                msg.get("additional_directories"),
            )
        case "list_channels":
            return await _do_list_device_channels(db_session, device_id)
        case "create_channel":
            result = await _do_create_channel(
                db_session, device_id,
                msg.get("name", ""),
                msg.get("working_directory", ""),
            )
            # Add new channel to owned set so the device can use it immediately
            new_id = result["channel"]["id"]
            owned_channels.add(new_id)
            return result
        case "rename_device":
            return await _do_rename_device(
                db_session, device_id, msg.get("name", ""),
            )
        case "delete_device":
            return await _do_delete_device(db_session, device_id)
        case "report_status":
            return await _do_report_device_status(db_session, device_id, msg)
        case "update_device_x25519":
            return await _do_update_device_x25519(
                db_session, device_id,
                msg.get("x25519_public", ""),
            )
        case "register_channel_key":
            return await _do_register_channel_key(
                db_session, channel_id,
                msg.get("encrypted_channel_key", ""),
                msg.get("key_nonce", ""),
            )
        case "relay_content":
            return await _do_relay_to_browser(
                db_session, device_id,
                "aichat:content-relay",
                mode=NotificationMode.EPHEMERAL,
                **{k: v for k, v in msg.items()
                   if k not in ("type", "channel_id")},
            )
        case "rekey_response":
            rekey_kwargs = {"request_id": msg.get("request_id", "")}
            if msg.get("encrypted_key"):
                rekey_kwargs["encrypted_key"] = msg["encrypted_key"]
                rekey_kwargs["nonce"] = msg.get("nonce", "")
            return await _do_relay_to_browser(
                db_session, device_id,
                "aichat:rekey-response",
                **rekey_kwargs,
            )
        case "history_response":
            return await _do_relay_to_browser(
                db_session, device_id,
                "aichat:history-response",
                request_id=msg.get("request_id", ""),
                channel_id=msg.get("channel_id", ""),
                messages=msg.get("messages", []),
                has_more=msg.get("has_more", False),
            )
        case _:
            raise ValueError(f"Unknown message type: {msg_type}")


async def _do_relay_to_browser(
    db_session: AsyncSession,
    device_id: str,
    event_type: str,
    mode: NotificationMode = NotificationMode.TIMESERIES,
    **kwargs,
) -> dict:
    """Relay a message from the device to the browser via SSE notification."""
    # Look up device owner
    result = await db_session.execute(
        select(AiChatDevice.owner_user_id).where(AiChatDevice.id == UUID(device_id))
    )
    owner_id = result.scalar_one_or_none()
    if not owner_id:
        raise ValueError("Device not found")

    await notify_user(
        owner_id,
        event_type,
        mode=mode,
        push_notify=False,
        **kwargs,
    )
    return {}


async def _do_update_device_x25519(
    db_session: AsyncSession,
    device_id: str,
    x25519_public: str,
) -> dict:
    """Update the device's X25519 public key for E2E key exchange."""
    if not x25519_public:
        raise ValueError("x25519_public required")

    # Validate key length (32 bytes raw)
    import base64
    try:
        raw = base64.b64decode(x25519_public)
        if len(raw) != 32:
            raise ValueError("X25519 public key must be 32 bytes")
    except Exception:
        raise ValueError("Invalid X25519 public key")

    result = await db_session.execute(
        select(AiChatDevice).where(AiChatDevice.id == UUID(device_id))
    )
    device = result.scalar_one_or_none()
    if not device:
        raise ValueError("Device not found")

    device.x25519_public = x25519_public
    await db_session.commit()

    return {"ok": True}


async def _do_register_channel_key(
    db_session: AsyncSession,
    channel_id: UUID | None,
    encrypted_channel_key: str,
    key_nonce: str,
) -> dict:
    """Store an encrypted channel key on the channel record."""
    if not channel_id:
        raise ValueError("channel_id required")
    if not encrypted_channel_key or not key_nonce:
        raise ValueError("encrypted_channel_key and key_nonce required")

    result = await db_session.execute(
        select(AiChatChannel).where(AiChatChannel.id == channel_id)
    )
    channel = result.scalar_one_or_none()
    if not channel:
        raise ValueError("Channel not found")

    channel.encrypted_channel_key = encrypted_channel_key
    channel.key_nonce = key_nonce
    await db_session.commit()

    return {"ok": True}


class AiChatDeviceWebSocketController(Controller):
    """WebSocket endpoint for device manager connections.

    Replaces SSE + HTTP for device managers. A single WebSocket carries
    both request/response traffic and pushed notification events.
    """

    path = "/api/device"

    @websocket("/ws")
    async def device_ws(self, socket: WebSocket) -> None:
        await socket.accept()

        # --- Auth: verify device signature from upgrade headers ---
        device_id = socket.headers.get("x-device-id", "")
        if not device_id:
            await socket.close(code=4001, reason="X-Device-Id required")
            return

        pub_key = await _lookup_device_key(device_id)
        if not pub_key or not _verify_ws_signature(socket, pub_key):
            await socket.close(code=4001, reason="Invalid signature")
            return

        # --- Resolve device owner for notification subscription ---
        session_maker = socket.app.state.session_maker_class
        async with session_maker() as db_session:
            result = await db_session.execute(
                select(AiChatDevice.owner_user_id)
                .where(AiChatDevice.id == UUID(device_id))
            )
            owner_id = result.scalar_one_or_none()

        if not owner_id:
            await socket.close(code=4003, reason="Device not found")
            return

        owner_id_str = str(owner_id)

        # --- Load owned channel IDs for per-message authorization ---
        owned_channels: set[str] = set()
        async with session_maker() as db_session:
            result = await db_session.execute(
                select(AiChatChannel.id)
                .where(AiChatChannel.device_id == UUID(device_id))
            )
            owned_channels = {str(row[0]) for row in result.all()}

        # --- Register for notifications ---
        nid = f"device:{device_id}"
        q = await notifications.register_connection(nid, owner_id_str)

        # --- Background task: forward notification events to WebSocket ---
        async def forward_events() -> None:
            try:
                while True:
                    n = await q.get()
                    event_data = n.to_dict()
                    event_type = event_data.get("type")
                    logger.info(
                        "Device %s: forwarding event type=%s to WebSocket",
                        device_id, event_type,
                    )
                    await socket.send_text(json.dumps({
                        "type": "event",
                        "event_type": event_type,
                        **{k: v for k, v in event_data.items() if k != "type"},
                    }))
            except Exception as exc:
                logger.warning(
                    "Device %s: forward_events stopped: %s", device_id, exc,
                )

        forwarder = asyncio.create_task(forward_events())

        # --- Register for direct WS push ---
        _device_ws_connections[device_id] = socket

        logger.info("Device %s WebSocket connected (owner=%s)", device_id, owner_id_str)

        # --- Message receive loop ---
        try:
            while True:
                raw = await socket.receive_text()
                msg = json.loads(raw)
                rid = msg.get("rid")
                msg_type = msg.get("type")

                try:
                    # Channel-scoped requests: verify ownership
                    channel_id_str = msg.get("channel_id")
                    if channel_id_str and msg_type not in ("list_channels", "report_status"):
                        if channel_id_str not in owned_channels:
                            async with session_maker() as db_session:
                                ok = await _verify_device_owns_channel(device_id, channel_id_str)
                            if ok:
                                owned_channels.add(channel_id_str)
                            else:
                                raise PermissionError(
                                    f"Channel {channel_id_str} not owned by device"
                                )

                    # Dispatch to business logic
                    async with session_maker() as db_session:
                        result = await _dispatch_ws_message(
                            db_session, device_id, msg_type, msg, owned_channels,
                        )

                    if rid:
                        await socket.send_text(json.dumps({
                            "type": "response", "rid": rid,
                            "ok": True, "data": result,
                        }))

                except Exception as e:
                    logger.exception("WS request error [%s]: %s", msg_type, e)
                    if rid:
                        await socket.send_text(json.dumps({
                            "type": "response", "rid": rid,
                            "ok": False, "error": str(e),
                        }))

        except Exception:
            pass  # Disconnect
        finally:
            forwarder.cancel()
            _device_ws_connections.pop(device_id, None)
            notifications.unregister_connection(nid, q)
            logger.info("Device %s WebSocket disconnected", device_id)


# ---------------------------------------------------------------------------
# Device management controller (user session auth)
# ---------------------------------------------------------------------------


class AiChatDeviceManagementController(Controller):
    """User-facing device management (session-authenticated)."""

    path = "/api/user-devices"

    @post("/{device_id:str}/workers")
    async def request_worker(
        self, device_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        """Create a channel and send worker:start command to device."""
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        # Verify device exists and is owned by user
        result = await db_session.execute(
            select(AiChatDevice).where(AiChatDevice.id == UUID(device_id))
        )
        device = result.scalar_one_or_none()
        if not device or device.owner_user_id != user.id:
            return Response(content={"error": "device not found"}, status_code=404)

        body = await request.json()
        name = body.get("name", "").strip() or "New Task"
        if len(name) > 100:
            name = name[:100]

        # Generate channel keypair
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        private_key_b64 = base64.b64encode(private_key.private_bytes_raw()).decode()
        public_key_b64 = base64.b64encode(public_key.public_bytes_raw()).decode()

        channel = AiChatChannel(
            name=name,
            public_key=public_key_b64,
            created_by_user_id=user.id,
            device_id=device.id,
        )
        db_session.add(channel)
        await db_session.flush()

        channel_token = _make_compound_token(private_key_b64, str(channel.id))

        # Send worker:start command as timeseries notification
        await notify_user(
            str(user.id),
            "aichat:device-command",
            mode=NotificationMode.TIMESERIES,
            push_notify=False,
            command="worker:start",
            payload={
                "channel_id": str(channel.id),
                "channel_token": channel_token,
            },
            device_id=str(device.id),
        )

        await db_session.commit()

        return Response(
            content={
                "ok": True,
                "channel": {"id": str(channel.id), "name": channel.name},
            },
            status_code=201,
        )

    @delete("/{device_id:str}/workers/{channel_id:str}", status_code=200)
    async def stop_worker(
        self, device_id: str, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        """Send worker:stop command to device."""
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        result = await db_session.execute(
            select(AiChatDevice).where(AiChatDevice.id == UUID(device_id))
        )
        device = result.scalar_one_or_none()
        if not device or device.owner_user_id != user.id:
            return Response(content={"error": "device not found"}, status_code=404)

        # Send worker:stop command
        await notify_user(
            str(user.id),
            "aichat:device-command",
            mode=NotificationMode.TIMESERIES,
            push_notify=False,
            command="worker:stop",
            payload={"channel_id": channel_id},
            device_id=str(device.id),
        )

        return Response(content={"ok": True})

    @post("/{device_id:str}/workers/{channel_id:str}/restart", status_code=200)
    async def restart_worker(
        self, device_id: str, channel_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        """Send worker:restart command to device."""
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        result = await db_session.execute(
            select(AiChatDevice).where(AiChatDevice.id == UUID(device_id))
        )
        device = result.scalar_one_or_none()
        if not device or device.owner_user_id != user.id:
            return Response(content={"error": "device not found"}, status_code=404)

        # Fetch channel's working directory for restart
        ch_result = await db_session.execute(
            select(AiChatChannel.working_directory)
            .where(AiChatChannel.id == UUID(channel_id))
        )
        ch_row = ch_result.one_or_none()
        working_directory = ch_row.working_directory if ch_row else ""

        # Send worker:restart command
        payload: dict = {"channel_id": channel_id}
        if working_directory:
            payload["working_directory"] = working_directory

        await notify_user(
            str(user.id),
            "aichat:device-command",
            mode=NotificationMode.TIMESERIES,
            push_notify=False,
            command="worker:restart",
            payload=payload,
            device_id=str(device.id),
        )

        return Response(content={"ok": True})

    @put("/{device_id:str}")
    async def update_device(
        self, device_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        """Update device name."""
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        result = await db_session.execute(
            select(AiChatDevice).where(AiChatDevice.id == UUID(device_id))
        )
        device = result.scalar_one_or_none()
        if not device or device.owner_user_id != user.id:
            return Response(content={"error": "device not found"}, status_code=404)

        body = await request.json()
        name = body.get("name", "").strip()
        if name:
            if len(name) > 100:
                return Response(content={"error": "name too long"}, status_code=400)
            device.name = name

        await db_session.commit()
        return Response(content={"ok": True, "device": {"id": str(device.id), "name": device.name}})

    @delete("/{device_id:str}", status_code=200)
    async def delete_device(
        self, device_id: str, request: Request, db_session: AsyncSession
    ) -> Response:
        """Delete device and disassociate its channels."""
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)
        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        result = await db_session.execute(
            select(AiChatDevice).where(AiChatDevice.id == UUID(device_id))
        )
        device = result.scalar_one_or_none()
        if not device or device.owner_user_id != user.id:
            return Response(content={"error": "device not found"}, status_code=404)

        # Disassociate channels from this device
        channel_result = await db_session.execute(
            select(AiChatChannel).where(AiChatChannel.device_id == device.id)
        )
        for channel in channel_result.scalars().all():
            channel.device_id = None

        _invalidate_device_cache(device_id)
        await db_session.delete(device)
        await db_session.commit()

        return Response(content={"ok": True})
