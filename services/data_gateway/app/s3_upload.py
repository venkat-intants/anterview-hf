"""Async S3 / R2 upload helper for data_gateway.

Provides a single ``upload_file`` coroutine used by B-031 (resume upload)
and B-032 (JD document upload).

Supports:
  - Cloudflare R2 (via custom endpoint URL)
  - MinIO (local dev, via custom endpoint URL)
  - Real AWS S3 (endpoint left empty → aioboto3 uses the standard regional URL)
"""

from __future__ import annotations

import structlog
from shared.s3 import s3_client

from app.config import Settings

log = structlog.get_logger(__name__)


async def upload_file(
    bucket: str,
    key: str,
    data: bytes,
    content_type: str,
    *,
    settings: Settings,
) -> str:
    """Upload *data* to S3-compatible storage and return the object *key*.

    Parameters
    ----------
    bucket:
        Target bucket name.
    key:
        Object key within the bucket, e.g. ``resumes/uuid.pdf``.
    data:
        Raw bytes to upload.
    content_type:
        MIME type for the object, e.g. ``application/pdf``.
    settings:
        Application settings — supplies credentials and endpoint.

    Returns
    -------
    str
        The key that was uploaded (same as the *key* argument), useful for
        callers that want to store it immediately after upload.

    Raises
    ------
    Exception
        Any error from the underlying boto3 client is re-raised without
        wrapping so callers can handle ``ClientError`` specifically if needed.
    """
    # Client construction lives in shared.s3 (finding SVC-1): this was one of
    # five hand-rolled aioboto3 sessions across four services, and three of the
    # five had already missed the path-style and use_ssl fixes. Endpoint
    # resolution, the credential "or None" fallback and the path-style rule for
    # custom endpoints (MinIO/R2 — virtual-host style would resolve to
    # "bucket.localhost:9000" and fail) now have exactly one definition.
    async with s3_client(
        endpoint=settings.s3_endpoint,
        region=settings.s3_region,
        access_key=settings.s3_access_key_id,
        secret_key=settings.s3_secret_access_key,
        use_ssl=settings.s3_use_ssl,
    ) as s3:
        await s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    log.info(
        "s3.upload.ok",
        bucket=bucket,
        key=key,
        content_type=content_type,
        size_bytes=len(data),
    )
    return key
