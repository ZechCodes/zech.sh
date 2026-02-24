# Storage Backends

## Overview

Add a pluggable file storage layer under `skrift/lib/storage/` that supports local filesystem storage (default) and S3-compatible object storage.

## Module Structure

```
skrift/lib/storage/
├── __init__.py          # StorageManager + create_storage_backend factory
├── base.py              # StorageBackend protocol + StoredFile
├── local.py             # LocalStorageBackend (default, ./uploads/)
└── s3.py                # S3StorageBackend (aioboto3)
```

## Components

### `base.py` — Protocol & Data Types

- `StoredFile` — dataclass holding file metadata (key, content type, size, URL, timestamps)
- `StorageBackend` — protocol defining the interface all backends must implement:
  - `store(key, data, content_type) -> StoredFile`
  - `retrieve(key) -> bytes`
  - `delete(key) -> None`
  - `exists(key) -> bool`
  - `get_url(key) -> str`

### `local.py` — Local Filesystem Backend

- Default backend, stores files in `./uploads/`
- Configurable base directory
- Generates URLs relative to the application root
- Suitable for development and single-node deployments

### `s3.py` — S3 Backend

- Uses `aioboto3` for async S3 operations
- Configurable bucket, prefix, region, and endpoint URL (for S3-compatible services like MinIO, R2)
- Supports presigned URLs for direct client access
- Handles multipart uploads for large files

### `__init__.py` — Factory & Manager

- `create_storage_backend(config) -> StorageBackend` — factory that reads app config and returns the appropriate backend instance
- `StorageManager` — high-level API wrapping a backend with:
  - Path/key normalization
  - Content type detection
  - Upload size validation

## Configuration

Add a `storage` section to `app.yaml`:

```yaml
storage:
  backend: local  # or "s3"
  local:
    directory: ./uploads
  s3:
    bucket: $S3_BUCKET
    prefix: uploads/
    region: $AWS_REGION
    endpoint_url: $S3_ENDPOINT_URL  # optional, for S3-compatible services
    access_key_id: $AWS_ACCESS_KEY_ID
    secret_access_key: $AWS_SECRET_ACCESS_KEY
```

## Dependencies

- `aioboto3` — async S3 client (only required when using S3 backend)
- `python-magic` or `mimetypes` — content type detection

## Implementation Notes

- All backend methods are async
- Local backend is the default so the app works out of the box with zero configuration
- S3 backend should be lazy-loaded to avoid requiring `aioboto3` when not in use
- File keys use forward slashes as separators regardless of OS
