"""Async S3 / R2 upload helper for data_gateway.

Provides a single ``upload_file`` coroutine used by B-031 (resume upload)
and B-032 (JD document upload).

Supports:
  - Cloudflare R2 (via custom endpoint URL)
  - MinIO (local dev, via custom endpoint URL)
  - Real AWS S3 (endpoint left empty → aioboto3 uses the standard regional URL)
"""

from __future__ import annotations

import aioboto3
import structlog
from botocore.config import Config as BotoConfig

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
    endpoint_url: str | None = settings.s3_endpoint if settings.s3_endpoint else None

    # MinIO and R2 are reached via a custom endpoint and require PATH-style
    # addressing (bucket in the path, not as a subdomain) — virtual-host style
    # would resolve to "bucket.localhost:9000" / "bucket.<acct>.r2..." and fail.
    # For real AWS S3 (no custom endpoint) we leave the default (virtual-host).
    boto_config: BotoConfig | None = (
        BotoConfig(s3={"addressing_style": "path"}) if endpoint_url else None
    )

    session = aioboto3.Session(
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        region_name=settings.s3_region,
    )

    async with session.client(
        "s3",
        endpoint_url=endpoint_url,
        use_ssl=settings.s3_use_ssl,
        config=boto_config,
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
