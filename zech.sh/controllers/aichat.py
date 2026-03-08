import base64
import logging
import os
import time
from datetime import datetime, timezone
from uuid import UUID

import redis.asyncio as redis
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from litestar import Controller, Request, get, post
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from litestar.response import Redirect, Response
from litestar.response import Template as TemplateResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.auth.guards import ADMINISTRATOR_PERMISSION
from skrift.auth.roles import register_role
from skrift.auth.services import get_user_permissions
from skrift.auth.session_keys import SESSION_USER_ID
from skrift.config import get_settings
from skrift.db.models.user import User
from skrift.lib.notifications import NotificationMode, notify_user

from models.ai_chat import AiChatMessage

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


async def _get_target_user_id(db_session: AsyncSession) -> str | None:
    """Get the user_id from the most recent user message for notification targeting."""
    result = await db_session.execute(
        select(AiChatMessage.user_id)
        .where(AiChatMessage.sender == "user")
        .where(AiChatMessage.user_id.is_not(None))
        .order_by(AiChatMessage.created_at.desc())
        .limit(1)
    )
    uid = result.scalar_one_or_none()
    return str(uid) if uid else None


# ---------------------------------------------------------------------------
# Ed25519 signature verification
# ---------------------------------------------------------------------------

_MAX_TIMESTAMP_DRIFT = 60  # seconds

_public_key: Ed25519PublicKey | None = None


def _get_public_key() -> Ed25519PublicKey | None:
    """Load the Ed25519 public key from AICHAT_PUBLIC_KEY env var (base64-encoded)."""
    global _public_key
    if _public_key is not None:
        return _public_key
    key_b64 = os.environ.get("AICHAT_PUBLIC_KEY", "")
    if not key_b64:
        return None
    _public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64))
    return _public_key


# ---------------------------------------------------------------------------
# Redis rate limiting for API
# ---------------------------------------------------------------------------

_redis_client: redis.Redis | None = None
_API_RATE_LIMIT_READS = 60  # requests per minute for reads
_API_RATE_LIMIT_WRITES = 20  # requests per minute for writes


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
        return True  # No Redis = no rate limiting (fail open)

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
# API guard — Ed25519 signature + rate limiting
# ---------------------------------------------------------------------------


async def _verify_signature(connection: ASGIConnection) -> bool:
    """Verify Ed25519 signature on the request."""
    public_key = _get_public_key()
    if public_key is None:
        return False

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

    body = ""
    if method == "POST":
        body_bytes = await connection.body()
        body = body_bytes.decode()

    message = f"{timestamp_str}.{method}.{path}.{body}".encode()

    try:
        signature = base64.b64decode(signature_b64)
        public_key.verify(signature, message)
        return True
    except Exception:
        return False


def _verify_shared_secret(connection: ASGIConnection) -> bool:
    """Legacy: verify shared secret auth (for transition period)."""
    import secrets as _secrets
    secret = os.environ.get("AICHAT_SECRET", "")
    if not secret:
        return False
    auth_header = connection.headers.get("authorization", "")
    token = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
    if not token:
        return False
    return _secrets.compare_digest(token, secret)


async def aichat_api_guard(
    connection: ASGIConnection, _handler: BaseRouteHandler
) -> None:
    # Try Ed25519 signature first, fall back to shared secret
    if not await _verify_signature(connection) and not _verify_shared_secret(connection):
        raise NotAuthorizedException("Invalid authentication")

    # Rate limiting
    client_ip = connection.headers.get("x-forwarded-for", "api-client")
    method = connection.scope["method"]
    is_write = method == "POST"
    limit = _API_RATE_LIMIT_WRITES if is_write else _API_RATE_LIMIT_READS
    rate_key = f"aichat:rate:{client_ip}:{'write' if is_write else 'read'}"

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
            select(AiChatMessage)
            .order_by(AiChatMessage.created_at.asc())
        )
        messages = list(result.scalars().all())

        csrf_token = _get_or_create_csrf_token(request)

        return TemplateResponse(
            "aichat.html",
            context={
                "user": user,
                "messages": messages,
                "hide_sidebar": True,
                "csrf_token": csrf_token,
            },
        )

    @post("/send")
    async def send_message(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        user = await _get_user(request, db_session)
        if not user:
            return Response(content={"error": "unauthorized"}, status_code=401)

        if not await _has_permission(user.id, db_session):
            return Response(content={"error": "forbidden"}, status_code=403)

        # CSRF verification
        import hmac
        submitted_token = request.headers.get("x-csrf-token", "")
        stored_token = request.session.get(CSRF_SESSION_KEY, "")
        if not stored_token or not hmac.compare_digest(submitted_token, stored_token):
            return Response(content={"error": "CSRF validation failed"}, status_code=403)

        body = await request.json()
        content = body.get("content", "").strip()
        if not content:
            return Response(content={"error": "empty message"}, status_code=400)

        msg = AiChatMessage(
            sender="user",
            content=content,
            user_id=user.id,
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
        )

        await db_session.commit()
        return Response(content={"ok": True})


# ---------------------------------------------------------------------------
# API controller (Claude-facing)
# ---------------------------------------------------------------------------


class AiChatApiController(Controller):
    """JSON API for Claude to read and send messages."""

    path = "/api"
    guards = [aichat_api_guard]

    @get("/messages")
    async def get_messages(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        before = request.query_params.get("before")
        limit = min(int(request.query_params.get("limit", "10")), 50)

        query = select(AiChatMessage).order_by(AiChatMessage.created_at.desc())
        if before:
            before_msg = await db_session.get(AiChatMessage, UUID(before))
            if before_msg:
                query = query.where(AiChatMessage.created_at < before_msg.created_at)
        query = query.limit(limit)

        result = await db_session.execute(query)
        messages = list(result.scalars().all())

        # Mark user messages as read
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
                mode=NotificationMode.EPHEMERAL,
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
            for m in reversed(messages)  # Return chronological order
        ])

    @get("/messages/unread")
    async def get_unread(
        self, request: Request, db_session: AsyncSession
    ) -> Response:
        result = await db_session.execute(
            select(AiChatMessage)
            .where(AiChatMessage.sender == "user")
            .where(AiChatMessage.read_by_claude_at.is_(None))
            .order_by(AiChatMessage.created_at.asc())
        )
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
                mode=NotificationMode.EPHEMERAL,
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
        body = await request.json()
        content = body.get("content", "").strip()
        if not content:
            return Response(content={"error": "empty message"}, status_code=400)

        msg = AiChatMessage(
            sender="claude",
            content=content,
        )
        db_session.add(msg)
        await db_session.flush()

        target_user_id = await _get_target_user_id(db_session)
        if target_user_id:
            await notify_user(
                target_user_id,
                "aichat:message",
                mode=NotificationMode.TIMESERIES,
                sender="claude",
                content=content,
                message_id=str(msg.id),
            )

        await db_session.commit()
        return Response(
            content={"ok": True, "id": str(msg.id)},
            status_code=201,
        )
