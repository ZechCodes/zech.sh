import base64
import hashlib
import hmac as hmac_mod
import json
import logging
import os
import time
from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as redis
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from litestar import Controller, Request, delete, get, post
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from skrift.auth.guards import ADMINISTRATOR_PERMISSION
from skrift.auth.roles import register_role
from skrift.auth.services import get_user_permissions
from skrift.auth.session_keys import SESSION_USER_ID
from skrift.config import get_settings
from skrift.db.models.user import User
from skrift.lib.notifications import NotificationMode, notify_user

from models.ai_chat import AiChatMessage
from models.ai_chat_channel import AiChatChannel

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
            settings.db.url, pool_size=2, execution_options=opts
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
# API guard — Ed25519 signature + rate limiting (channel-aware)
# ---------------------------------------------------------------------------


async def aichat_api_guard(
    connection: ASGIConnection, _handler: BaseRouteHandler
) -> None:
    channel_id_str = connection.headers.get("x-channel", "")
    if not channel_id_str:
        raise NotAuthorizedException("X-Channel header required")

    try:
        pub_key = await _lookup_channel_key(channel_id_str)
        if not pub_key or not _verify_signature_with_key(connection, pub_key):
            raise NotAuthorizedException("Invalid signature")
        connection.state["channel_id"] = channel_id_str
    except NotAuthorizedException:
        raise
    except Exception:
        logger.exception("Channel auth lookup failed")
        raise NotAuthorizedException("Auth failed")

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

        result = await db_session.execute(
            select(AiChatChannel).order_by(AiChatChannel.created_at.desc())
        )
        channels = list(result.scalars().all())

        csrf_token = _get_or_create_csrf_token(request)

        return TemplateResponse(
            "aichat_dashboard.html",
            context={
                "user": user,
                "channels": channels,
                "hide_sidebar": True,
                "csrf_token": csrf_token,
            },
        )

    @post("/channels")
    async def create_channel(
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
        name = body.get("name", "").strip()
        if not name:
            return Response(content={"error": "name required"}, status_code=400)
        if len(name) > 100:
            return Response(content={"error": "name too long"}, status_code=400)

        # Generate Ed25519 keypair
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()
        private_key_b64 = base64.b64encode(private_key.private_bytes_raw()).decode()
        public_key_b64 = base64.b64encode(public_key.public_bytes_raw()).decode()

        channel = AiChatChannel(
            name=name,
            public_key=public_key_b64,
            created_by_user_id=user.id,
        )
        db_session.add(channel)
        await db_session.flush()

        # Create compound token (shown once, never stored)
        token = _make_compound_token(private_key_b64, str(channel.id))

        await db_session.commit()

        return Response(
            content={
                "ok": True,
                "channel": {"id": str(channel.id), "name": channel.name},
                "token": token,
            },
            status_code=201,
        )

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

        csrf_token = _get_or_create_csrf_token(request)

        return TemplateResponse(
            "aichat.html",
            context={
                "user": user,
                "channel": channel,
                "messages": messages,
                "has_more": has_more,
                "hide_sidebar": True,
                "csrf_token": csrf_token,
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
        if not content:
            return Response(content={"error": "empty message"}, status_code=400)

        msg = AiChatMessage(
            sender="user",
            content=content,
            user_id=user.id,
            channel_id=channel.id,
        )
        db_session.add(msg)
        await db_session.flush()

        await notify_user(
            str(user.id),
            "aichat:message",
            mode=NotificationMode.TIMESERIES,
            sender="user",
            content=content,
            message_id=str(msg.id),
            channel_id=str(channel.id),
        )

        await db_session.commit()
        return Response(content={"ok": True})

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
        if not before:
            return Response(content={"error": "before param required"}, status_code=400)

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
                }
                for m in messages
            ],
        })


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

    @get("/messages")
    async def get_messages(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        channel_id = self._get_channel_id(request)
        before = request.query_params.get("before")
        limit = min(int(request.query_params.get("limit", "10")), 50)

        query = (
            select(AiChatMessage)
            .where(AiChatMessage.channel_id == channel_id)
            .order_by(AiChatMessage.created_at.desc())
        )
        if before:
            before_msg = await db_session.get(AiChatMessage, UUID(before))
            if before_msg:
                query = query.where(AiChatMessage.created_at < before_msg.created_at)
        query = query.limit(limit)

        result = await db_session.execute(query)
        messages = list(result.scalars().all())

        now = datetime.now(timezone.utc)
        read_ids = []
        target_user_id = None
        for msg in messages:
            if msg.sender == "user" and msg.read_by_claude_at is None:
                msg.read_by_claude_at = now
                read_ids.append(str(msg.id))
                if msg.user_id:
                    target_user_id = str(msg.user_id)

        if read_ids and target_user_id:
            await notify_user(
                target_user_id,
                "aichat:read",
                mode=NotificationMode.TIMESERIES,
                message_ids=read_ids,
            )

        await db_session.commit()

        return Response(content=[
            {
                "id": str(m.id),
                "sender": m.sender,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
                "read_by_claude_at": m.read_by_claude_at.isoformat() if m.read_by_claude_at else None,
            }
            for m in reversed(messages)
        ])

    @get("/messages/unread")
    async def get_unread(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        channel_id = self._get_channel_id(request)

        query = (
            select(AiChatMessage)
            .where(AiChatMessage.sender == "user")
            .where(AiChatMessage.read_by_claude_at.is_(None))
            .where(AiChatMessage.channel_id == channel_id)
            .order_by(AiChatMessage.created_at.asc())
        )

        result = await db_session.execute(query)
        messages = list(result.scalars().all())

        now = datetime.now(timezone.utc)
        read_ids = []
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
                message_ids=read_ids,
            )

        await db_session.commit()

        return Response(content=[
            {
                "id": str(m.id),
                "sender": m.sender,
                "content": m.content,
                "created_at": m.created_at.isoformat(),
            }
            for m in messages
        ])

    @post("/messages")
    async def create_message(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        channel_id = self._get_channel_id(request)

        body = await request.json()
        content = body.get("content", "").strip()
        if not content:
            return Response(content={"error": "empty message"}, status_code=400)

        msg = AiChatMessage(
            sender="claude",
            content=content,
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
                sender="claude",
                content=content,
                message_id=str(msg.id),
                channel_id=str(channel_id),
            )

        await db_session.commit()
        return Response(
            content={"ok": True, "id": str(msg.id)},
            status_code=201,
        )

    @post("/tool-status")
    async def tool_status(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        channel_id = self._get_channel_id(request)

        body = await request.json()
        status = body.get("status", "")
        if status not in ("active", "done", "idle"):
            return Response(content={"error": "invalid status"}, status_code=400)

        target_user_id = await _get_target_user_id(db_session, channel_id)
        if target_user_id:
            await notify_user(
                target_user_id,
                "aichat:tool",
                mode=NotificationMode.TIMESERIES,
                group=f"aichat:tool:{channel_id}",
                status=status,
                tool=body.get("tool", ""),
                description=body.get("description", ""),
                channel_id=str(channel_id),
            )

        return Response(content={"ok": True})
