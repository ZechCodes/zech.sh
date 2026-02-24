"""S3-compatible object storage backend (aioboto3)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from lib.storage.base import StoredFile

# aioboto3 is imported lazily so that the dependency is only required
# when this backend is actually instantiated.

# Threshold above which we switch to multipart upload (8 MiB).
_MULTIPART_THRESHOLD = 8 * 1024 * 1024
# Part size for multipart uploads (8 MiB).
_MULTIPART_PART_SIZE = 8 * 1024 * 1024
# Default presigned URL expiration in seconds (1 hour).
_PRESIGN_EXPIRY = 3600


class S3StorageBackend:
    """Store files in an S3-compatible bucket.

    Uses ``aioboto3`` for fully async operations.  Supports any
    S3-compatible service (AWS S3, MinIO, Cloudflare R2, etc.) via the
    *endpoint_url* parameter.

    Parameters
    ----------
    bucket:
        Target S3 bucket name.
    prefix:
        Key prefix prepended to every object key (e.g. ``"uploads/"``).
    region:
        AWS region (e.g. ``"us-east-1"``).
    endpoint_url:
        Custom endpoint for S3-compatible services.  ``None`` for AWS.
    access_key_id:
        AWS access key ID.
    secret_access_key:
        AWS secret access key.
    presign_expiry:
        Lifetime of presigned GET URLs in seconds.
    """

    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        region: str = "us-east-1",
        endpoint_url: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        presign_expiry: int = _PRESIGN_EXPIRY,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._region = region
        self._endpoint_url = endpoint_url or None
        self._access_key_id = access_key_id
        self._secret_access_key = secret_access_key
        self._presign_expiry = presign_expiry

        # Lazily resolved on first use.
        self._session: Any = None

    # ------------------------------------------------------------------
    # StorageBackend interface
    # ------------------------------------------------------------------

    async def store(self, key: str, data: bytes, content_type: str) -> StoredFile:
        s3_key = self._full_key(key)
        async with self._client() as client:
            if len(data) >= _MULTIPART_THRESHOLD:
                await self._multipart_upload(client, s3_key, data, content_type)
            else:
                await client.put_object(
                    Bucket=self._bucket,
                    Key=s3_key,
                    Body=data,
                    ContentType=content_type,
                )

            url = await self._generate_presigned_url(client, s3_key)
        return StoredFile(
            key=key,
            content_type=content_type,
            size=len(data),
            url=url,
            created_at=datetime.now(timezone.utc),
        )

    async def retrieve(self, key: str) -> bytes:
        s3_key = self._full_key(key)
        async with self._client() as client:
            try:
                response = await client.get_object(
                    Bucket=self._bucket,
                    Key=s3_key,
                )
                return await response["Body"].read()
            except client.exceptions.NoSuchKey as exc:
                raise FileNotFoundError(
                    f"No file stored at key '{key}'"
                ) from exc

    async def delete(self, key: str) -> None:
        s3_key = self._full_key(key)
        async with self._client() as client:
            # delete_object is a no-op for missing keys in S3.
            await client.delete_object(Bucket=self._bucket, Key=s3_key)

    async def exists(self, key: str) -> bool:
        s3_key = self._full_key(key)
        async with self._client() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=s3_key)
                return True
            except client.exceptions.ClientError:
                return False

    async def get_url(self, key: str) -> str:
        s3_key = self._full_key(key)
        async with self._client() as client:
            return await self._generate_presigned_url(client, s3_key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session(self) -> Any:
        if self._session is None:
            try:
                import aioboto3
            except ImportError:
                raise ImportError(
                    "aioboto3 is required for S3StorageBackend. "
                    "Install it with: pip install aioboto3"
                )
            self._session = aioboto3.Session()
        return self._session

    def _client(self) -> Any:
        """Return an async context-manager that yields an S3 client."""
        session = self._get_session()
        kwargs: dict[str, Any] = {
            "service_name": "s3",
            "region_name": self._region,
        }
        if self._endpoint_url:
            kwargs["endpoint_url"] = self._endpoint_url
        if self._access_key_id:
            kwargs["aws_access_key_id"] = self._access_key_id
        if self._secret_access_key:
            kwargs["aws_secret_access_key"] = self._secret_access_key
        return session.client(**kwargs)

    def _full_key(self, key: str) -> str:
        """Prepend the configured prefix to *key*."""
        normalised = key.lstrip("/")
        if self._prefix:
            return f"{self._prefix}/{normalised}"
        return normalised

    async def _generate_presigned_url(self, client: Any, s3_key: str) -> str:
        return await client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": s3_key},
            ExpiresIn=self._presign_expiry,
        )

    async def _multipart_upload(
        self,
        client: Any,
        s3_key: str,
        data: bytes,
        content_type: str,
    ) -> None:
        """Upload *data* using S3 multipart upload."""
        mpu = await client.create_multipart_upload(
            Bucket=self._bucket,
            Key=s3_key,
            ContentType=content_type,
        )
        upload_id = mpu["UploadId"]
        parts: list[dict[str, Any]] = []

        try:
            part_number = 1
            offset = 0
            while offset < len(data):
                chunk = data[offset : offset + _MULTIPART_PART_SIZE]
                resp = await client.upload_part(
                    Bucket=self._bucket,
                    Key=s3_key,
                    UploadId=upload_id,
                    PartNumber=part_number,
                    Body=chunk,
                )
                parts.append({"PartNumber": part_number, "ETag": resp["ETag"]})
                part_number += 1
                offset += _MULTIPART_PART_SIZE

            await client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=s3_key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
        except Exception:
            # Abort on any failure so we don't leave incomplete uploads.
            await client.abort_multipart_upload(
                Bucket=self._bucket,
                Key=s3_key,
                UploadId=upload_id,
            )
            raise
