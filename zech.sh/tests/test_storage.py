"""Tests for the pluggable storage backends."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.storage import StorageManager, create_storage_backend
from lib.storage.base import StorageBackend, StoredFile
from lib.storage.local import LocalStorageBackend


# ---------------------------------------------------------------
# StoredFile
# ---------------------------------------------------------------


class TestStoredFile:
    def test_fields(self):
        sf = StoredFile(
            key="images/photo.jpg",
            content_type="image/jpeg",
            size=1024,
            url="/uploads/images/photo.jpg",
        )
        assert sf.key == "images/photo.jpg"
        assert sf.content_type == "image/jpeg"
        assert sf.size == 1024
        assert sf.url == "/uploads/images/photo.jpg"
        assert sf.created_at is not None

    def test_frozen(self):
        sf = StoredFile(key="a", content_type="b", size=0, url="c")
        with pytest.raises(AttributeError):
            sf.key = "x"


# ---------------------------------------------------------------
# LocalStorageBackend
# ---------------------------------------------------------------


class TestLocalStorageBackend:
    @pytest.fixture()
    def storage(self, tmp_path: Path) -> LocalStorageBackend:
        return LocalStorageBackend(
            directory=str(tmp_path), base_url="/files"
        )

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, storage, tmp_path):
        data = b"hello world"
        stored = await storage.store("docs/readme.txt", data, "text/plain")
        assert stored.key == "docs/readme.txt"
        assert stored.content_type == "text/plain"
        assert stored.size == len(data)
        assert stored.url == "/files/docs/readme.txt"

        # File should exist on disk.
        assert (tmp_path / "docs" / "readme.txt").exists()

        # Retrieve must return exact bytes.
        assert await storage.retrieve("docs/readme.txt") == data

    @pytest.mark.asyncio
    async def test_exists(self, storage):
        assert await storage.exists("nope.txt") is False
        await storage.store("yep.txt", b"x", "text/plain")
        assert await storage.exists("yep.txt") is True

    @pytest.mark.asyncio
    async def test_delete(self, storage):
        await storage.store("tmp.txt", b"x", "text/plain")
        assert await storage.exists("tmp.txt") is True

        await storage.delete("tmp.txt")
        assert await storage.exists("tmp.txt") is False

    @pytest.mark.asyncio
    async def test_delete_missing_is_noop(self, storage):
        # Should not raise.
        await storage.delete("nonexistent.txt")

    @pytest.mark.asyncio
    async def test_retrieve_missing_raises(self, storage):
        with pytest.raises(FileNotFoundError):
            await storage.retrieve("nonexistent.txt")

    @pytest.mark.asyncio
    async def test_get_url(self, storage):
        url = await storage.get_url("img/cat.png")
        assert url == "/files/img/cat.png"

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self, storage):
        with pytest.raises(ValueError, match="outside the storage root"):
            await storage.store("../../etc/passwd", b"x", "text/plain")

    def test_protocol_conformance(self):
        assert isinstance(
            LocalStorageBackend(directory="/tmp/test_storage_proto"),
            StorageBackend,
        )


# ---------------------------------------------------------------
# create_storage_backend factory
# ---------------------------------------------------------------


class TestCreateStorageBackend:
    def test_default_is_local(self):
        backend = create_storage_backend()
        assert isinstance(backend, LocalStorageBackend)

    def test_empty_config_is_local(self):
        backend = create_storage_backend({})
        assert isinstance(backend, LocalStorageBackend)

    def test_explicit_local(self, tmp_path):
        backend = create_storage_backend(
            {"backend": "local", "local": {"directory": str(tmp_path)}}
        )
        assert isinstance(backend, LocalStorageBackend)

    def test_s3_backend(self):
        with patch("lib.storage.s3.aioboto3", create=True):
            backend = create_storage_backend(
                {
                    "backend": "s3",
                    "s3": {
                        "bucket": "test-bucket",
                        "region": "us-west-2",
                    },
                }
            )
            from lib.storage.s3 import S3StorageBackend

            assert isinstance(backend, S3StorageBackend)


# ---------------------------------------------------------------
# StorageManager
# ---------------------------------------------------------------


class TestStorageManager:
    @pytest.fixture()
    def mock_backend(self) -> MagicMock:
        backend = MagicMock(spec=StorageBackend)
        backend.store = AsyncMock(
            return_value=StoredFile(
                key="normalised",
                content_type="text/plain",
                size=5,
                url="/u/normalised",
            )
        )
        backend.retrieve = AsyncMock(return_value=b"hello")
        backend.delete = AsyncMock()
        backend.exists = AsyncMock(return_value=True)
        backend.get_url = AsyncMock(return_value="/u/normalised")
        return backend

    @pytest.fixture()
    def manager(self, mock_backend) -> StorageManager:
        return StorageManager(mock_backend, max_size=100)

    @pytest.mark.asyncio
    async def test_key_normalisation(self, manager, mock_backend):
        await manager.store("//a///b//c.txt", b"x", "text/plain")
        call_key = mock_backend.store.call_args[0][0]
        assert call_key == "a/b/c.txt"

    @pytest.mark.asyncio
    async def test_content_type_detection(self, manager, mock_backend):
        await manager.store("photo.jpg", b"x")
        call_ct = mock_backend.store.call_args[0][2]
        assert call_ct == "image/jpeg"

    @pytest.mark.asyncio
    async def test_explicit_content_type(self, manager, mock_backend):
        await manager.store("file.bin", b"x", "application/custom")
        call_ct = mock_backend.store.call_args[0][2]
        assert call_ct == "application/custom"

    @pytest.mark.asyncio
    async def test_unknown_extension_fallback(self, manager, mock_backend):
        await manager.store("file.zzz", b"x")
        call_ct = mock_backend.store.call_args[0][2]
        assert call_ct == "application/octet-stream"

    @pytest.mark.asyncio
    async def test_size_validation_rejects(self, manager):
        with pytest.raises(ValueError, match="exceeds the maximum"):
            await manager.store("big.bin", b"x" * 200, "application/octet-stream")

    @pytest.mark.asyncio
    async def test_size_validation_allows(self, manager, mock_backend):
        await manager.store("ok.bin", b"x" * 50, "application/octet-stream")
        assert mock_backend.store.called

    @pytest.mark.asyncio
    async def test_delegates_retrieve(self, manager, mock_backend):
        result = await manager.retrieve("file.txt")
        assert result == b"hello"
        mock_backend.retrieve.assert_awaited_once_with("file.txt")

    @pytest.mark.asyncio
    async def test_delegates_delete(self, manager, mock_backend):
        await manager.delete("file.txt")
        mock_backend.delete.assert_awaited_once_with("file.txt")

    @pytest.mark.asyncio
    async def test_delegates_exists(self, manager, mock_backend):
        assert await manager.exists("file.txt") is True
        mock_backend.exists.assert_awaited_once_with("file.txt")

    @pytest.mark.asyncio
    async def test_delegates_get_url(self, manager, mock_backend):
        url = await manager.get_url("file.txt")
        assert url == "/u/normalised"
        mock_backend.get_url.assert_awaited_once_with("file.txt")
