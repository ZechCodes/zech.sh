"""Admin controller for uploading, listing, and serving page images."""

from __future__ import annotations

import mimetypes
import uuid as _uuid
from pathlib import PurePosixPath
from typing import Annotated
from uuid import UUID

from litestar import Controller, Request, Response, delete, get, post
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.params import Body
from litestar.response import Redirect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from skrift.auth.guards import auth_guard
from skrift.auth.session_keys import SESSION_USER_ID
from skrift.config import load_app_config

from lib.storage import StorageManager, create_storage_backend
from models.page_image import PageImage

# Allowed image MIME types.
_ALLOWED_TYPES = frozenset({
    "image/jpeg",
    "image/png",
    "image/gif",
    "image/webp",
    "image/svg+xml",
    "image/avif",
})

_MAX_IMAGE_SIZE = 10 * 1024 * 1024  # 10 MiB


def _get_storage_manager() -> StorageManager:
    """Build a StorageManager from the current app config."""
    try:
        config = load_app_config(strict=False)
        storage_config = config.get("storage")
    except Exception:
        storage_config = None
    backend = create_storage_backend(storage_config)
    return StorageManager(backend, max_size=_MAX_IMAGE_SIZE)


def _safe_extension(filename: str, content_type: str) -> str:
    """Derive a safe file extension from *filename* or *content_type*."""
    suffix = PurePosixPath(filename).suffix.lower() if filename else ""
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif"}:
        return suffix
    ext = mimetypes.guess_extension(content_type) or ""
    return ext if ext else ".bin"


class UploadsController(Controller):
    """Image upload, listing, and serving endpoints under ``/admin/uploads``."""

    path = "/admin/uploads"
    guards = [auth_guard]

    @post("/")
    async def upload_image(
        self,
        request: Request,
        db_session: AsyncSession,
        data: Annotated[UploadFile, Body(media_type=RequestEncodingType.MULTI_PART)],
    ) -> Response:
        """Accept an image upload and persist it via the storage backend.

        The uploaded file is stored under ``images/{page_id}/{uuid}.{ext}``
        when a ``page_id`` query parameter is provided, or under
        ``images/{uuid}.{ext}`` otherwise.
        """
        raw = await data.read()
        content_type = data.content_type or "application/octet-stream"
        filename = data.filename or ""

        if content_type not in _ALLOWED_TYPES:
            return Response(
                content={"error": f"Unsupported image type: {content_type}"},
                status_code=400,
            )

        if len(raw) > _MAX_IMAGE_SIZE:
            return Response(
                content={"error": "Image exceeds the 10 MiB size limit"},
                status_code=400,
            )

        page_id = request.query_params.get("page_id", "")
        role = request.query_params.get("role", "featured")

        ext = _safe_extension(filename, content_type)
        unique = _uuid.uuid4().hex[:12]
        if page_id:
            key = f"images/{page_id}/{unique}{ext}"
        else:
            key = f"images/{unique}{ext}"

        manager = _get_storage_manager()
        stored = await manager.store(key, raw, content_type)

        # Persist metadata in the database.
        image = PageImage(
            page_id=UUID(page_id) if page_id else _uuid.UUID(int=0),
            role=role,
            storage_key=stored.key,
            url=stored.url,
            alt_text="",
            content_type=stored.content_type,
            size=stored.size,
            original_filename=filename,
        )
        db_session.add(image)
        await db_session.commit()
        await db_session.refresh(image)

        return Response(
            content={
                "id": str(image.id),
                "url": stored.url,
                "key": stored.key,
                "content_type": stored.content_type,
                "size": stored.size,
                "original_filename": filename,
            },
            status_code=201,
        )

    @get("/page/{page_id:uuid}")
    async def list_page_images(
        self,
        db_session: AsyncSession,
        page_id: UUID,
        role: str | None = None,
    ) -> Response:
        """Return all images attached to a page, optionally filtered by role."""
        query = (
            select(PageImage)
            .where(PageImage.page_id == page_id)
            .order_by(PageImage.sort_order.asc(), PageImage.created_at.desc())
        )
        if role:
            query = query.where(PageImage.role == role)

        result = await db_session.execute(query)
        images = result.scalars().all()

        return Response(
            content=[
                {
                    "id": str(img.id),
                    "url": img.url,
                    "key": img.storage_key,
                    "role": img.role,
                    "alt_text": img.alt_text,
                    "content_type": img.content_type,
                    "size": img.size,
                    "original_filename": img.original_filename,
                    "sort_order": img.sort_order,
                }
                for img in images
            ],
            status_code=200,
        )

    @delete("/{image_id:uuid}")
    async def delete_image(
        self,
        db_session: AsyncSession,
        image_id: UUID,
    ) -> Response:
        """Remove an image from storage and the database."""
        result = await db_session.execute(
            select(PageImage).where(PageImage.id == image_id)
        )
        image = result.scalar_one_or_none()
        if not image:
            return Response(content={"error": "Image not found"}, status_code=404)

        manager = _get_storage_manager()
        await manager.delete(image.storage_key)

        await db_session.delete(image)
        await db_session.commit()

        return Response(content={"ok": True}, status_code=200)


class UploadServeController(Controller):
    """Serve uploaded files from the local storage backend.

    Mounted at ``/uploads`` so that URLs produced by
    :class:`~lib.storage.local.LocalStorageBackend` resolve correctly.
    """

    path = "/uploads"

    @get("/{file_path:path}")
    async def serve_upload(self, file_path: str) -> Response:
        """Stream an uploaded file back to the client."""
        manager = _get_storage_manager()
        try:
            data = await manager.retrieve(file_path)
        except FileNotFoundError:
            return Response(content="Not found", status_code=404)

        content_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"
        return Response(
            content=data,
            status_code=200,
            headers={
                "Content-Type": content_type,
                "Cache-Control": "public, max-age=31536000, immutable",
            },
        )
