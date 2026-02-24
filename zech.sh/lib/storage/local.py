"""Local filesystem storage backend."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from lib.storage.base import StoredFile


class LocalStorageBackend:
    """Store files on the local filesystem.

    This is the default backend — it works out of the box with zero
    configuration, writing to ``./uploads/`` relative to the working
    directory.

    Parameters
    ----------
    directory:
        Root directory for stored files.  Created automatically if it
        does not exist.
    base_url:
        URL prefix used when generating file URLs.  Defaults to
        ``/uploads``.
    """

    def __init__(
        self,
        directory: str = "./uploads",
        base_url: str = "/uploads",
    ) -> None:
        self._root = Path(directory).resolve()
        self._base_url = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    async def store(self, key: str, data: bytes, content_type: str) -> StoredFile:
        path = self._resolve(key)
        await asyncio.to_thread(self._write, path, data)
        url = self._make_url(key)
        return StoredFile(
            key=key,
            content_type=content_type,
            size=len(data),
            url=url,
            created_at=datetime.now(timezone.utc),
        )

    async def retrieve(self, key: str) -> bytes:
        path = self._resolve(key)
        try:
            return await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise FileNotFoundError(f"No file stored at key '{key}'") from exc

    async def delete(self, key: str) -> None:
        path = self._resolve(key)
        try:
            await asyncio.to_thread(path.unlink)
        except FileNotFoundError:
            pass

    async def exists(self, key: str) -> bool:
        path = self._resolve(key)
        return await asyncio.to_thread(path.exists)

    async def get_url(self, key: str) -> str:
        return self._make_url(key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve(self, key: str) -> Path:
        """Turn a forward-slash key into an absolute path under *_root*.

        Raises ``ValueError`` if the resolved path escapes the root
        directory (path traversal protection).
        """
        normalised = key.lstrip("/")
        target = (self._root / normalised).resolve()
        if not target.is_relative_to(self._root):
            raise ValueError(f"Key '{key}' resolves outside the storage root")
        return target

    def _make_url(self, key: str) -> str:
        normalised = key.lstrip("/")
        return f"{self._base_url}/{normalised}"

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
