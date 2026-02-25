"""Pluggable file storage with local and S3 backends.

Quick start::

    from lib.storage import create_storage_backend, StorageManager

    backend = create_storage_backend(app_config.get("storage", {}))
    manager = StorageManager(backend)

    stored = await manager.store("avatars/user123.png", raw_bytes)
"""

from __future__ import annotations

import mimetypes
import os
from typing import Any

from lib.storage.base import StorageBackend, StoredFile

__all__ = [
    "StorageBackend",
    "StorageManager",
    "StoredFile",
    "create_storage_backend",
]

# Maximum upload size enforced by StorageManager (10 MiB default).
_DEFAULT_MAX_SIZE = 10 * 1024 * 1024


def _env(name: str, default: str = "") -> str:
    """Read an environment variable, returning *default* when unset or empty."""
    return os.environ.get(name, "") or default


def create_storage_backend(config: dict[str, Any] | None = None) -> StorageBackend:
    """Instantiate a storage backend from an ``app.yaml`` config dict.

    The *config* dict is the ``storage`` section of the application
    configuration.  When *config* is ``None`` or empty, environment
    variables are consulted before falling back to sensible defaults so
    the app works out of the box with zero configuration::

        UPLOAD_BACKEND   – "local" (default) or "s3"
        UPLOAD_DIR       – local directory  (default ``./uploads``)
        UPLOAD_URL       – URL prefix       (default ``/uploads``)

    For S3 the standard ``S3_BUCKET``, ``S3_PREFIX``, ``AWS_REGION``,
    ``S3_ENDPOINT_URL``, ``AWS_ACCESS_KEY_ID``, and
    ``AWS_SECRET_ACCESS_KEY`` variables are read.

    All env-var defaults can be overridden by defining the ``storage``
    section in ``app.yaml``.
    """
    if not config:
        config = {}

    backend_name = config.get("backend") or _env("UPLOAD_BACKEND", "local")

    if backend_name == "s3":
        # Lazy import so aioboto3 is not required for local-only setups.
        from lib.storage.s3 import S3StorageBackend

        s3_cfg = config.get("s3", {})
        return S3StorageBackend(
            bucket=s3_cfg.get("bucket") or _env("S3_BUCKET"),
            prefix=s3_cfg.get("prefix") or _env("S3_PREFIX", "uploads/"),
            region=s3_cfg.get("region") or _env("AWS_REGION", "us-east-1"),
            endpoint_url=s3_cfg.get("endpoint_url") or _env("S3_ENDPOINT_URL") or None,
            access_key_id=s3_cfg.get("access_key_id") or _env("AWS_ACCESS_KEY_ID") or None,
            secret_access_key=s3_cfg.get("secret_access_key") or _env("AWS_SECRET_ACCESS_KEY") or None,
        )

    # Default: local filesystem backend.
    from lib.storage.local import LocalStorageBackend

    local_cfg = config.get("local", {})
    return LocalStorageBackend(
        directory=local_cfg.get("directory") or _env("UPLOAD_DIR", "./uploads"),
        base_url=local_cfg.get("base_url") or _env("UPLOAD_URL", "/uploads"),
    )


class StorageManager:
    """High-level API wrapping a :class:`StorageBackend`.

    Adds path/key normalisation, automatic content-type detection, and
    upload size validation on top of the raw backend.

    Parameters
    ----------
    backend:
        The underlying storage backend to delegate to.
    max_size:
        Maximum upload size in bytes.  ``0`` disables the check.
    """

    def __init__(
        self,
        backend: StorageBackend,
        max_size: int = _DEFAULT_MAX_SIZE,
    ) -> None:
        self._backend = backend
        self._max_size = max_size

    @property
    def backend(self) -> StorageBackend:
        return self._backend

    async def store(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> StoredFile:
        """Normalise *key*, detect content type if needed, validate size,
        then delegate to the backend.
        """
        key = self._normalise_key(key)

        if self._max_size and len(data) > self._max_size:
            raise ValueError(
                f"Upload size ({len(data)} bytes) exceeds the "
                f"maximum allowed ({self._max_size} bytes)"
            )

        if content_type is None:
            content_type = self._detect_content_type(key)

        return await self._backend.store(key, data, content_type)

    async def retrieve(self, key: str) -> bytes:
        return await self._backend.retrieve(self._normalise_key(key))

    async def delete(self, key: str) -> None:
        await self._backend.delete(self._normalise_key(key))

    async def exists(self, key: str) -> bool:
        return await self._backend.exists(self._normalise_key(key))

    async def get_url(self, key: str) -> str:
        return await self._backend.get_url(self._normalise_key(key))

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_key(key: str) -> str:
        """Collapse repeated slashes and strip leading slash."""
        parts = [p for p in key.split("/") if p]
        return "/".join(parts)

    @staticmethod
    def _detect_content_type(key: str) -> str:
        """Guess MIME type from the file extension in *key*."""
        mime, _ = mimetypes.guess_type(key)
        return mime or "application/octet-stream"
