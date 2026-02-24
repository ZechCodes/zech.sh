"""StorageBackend protocol and StoredFile data type."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class StoredFile:
    """Metadata for a file managed by a storage backend."""

    key: str
    content_type: str
    size: int
    url: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@runtime_checkable
class StorageBackend(Protocol):
    """Interface that all storage backends must implement.

    Every method is async.  Keys use forward slashes as path separators
    regardless of the underlying operating system.
    """

    async def store(self, key: str, data: bytes, content_type: str) -> StoredFile:
        """Persist *data* under *key* and return its metadata."""
        ...

    async def retrieve(self, key: str) -> bytes:
        """Return the raw bytes stored under *key*.

        Raises ``FileNotFoundError`` when the key does not exist.
        """
        ...

    async def delete(self, key: str) -> None:
        """Remove the object at *key*.

        A no-op if the key does not exist.
        """
        ...

    async def exists(self, key: str) -> bool:
        """Return ``True`` when an object is stored under *key*."""
        ...

    async def get_url(self, key: str) -> str:
        """Return a URL through which the stored object can be accessed."""
        ...
