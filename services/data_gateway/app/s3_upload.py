"""Async S3 / R2 object helpers for data_gateway.

Provides ``upload_file`` (B-031 resume upload, B-032 JD document upload) and
``delete_objects`` (DPDP §8(7) retention purge — app/retention.py).

Supports:
  - Cloudflare R2 (via custom endpoint URL)
  - MinIO (local dev, via custom endpoint URL)
  - Real AWS S3 (endpoint left empty → aioboto3 uses the standard regional URL)

Why ``delete_objects`` lives here and not in ``shared/``
--------------------------------------------------------
``admin_ops/app/s3_client.py`` has a sibling with the same contract, and the
temptation is to import it. It cannot be imported: ``shared/`` is the only tree
COPY'd into every service image, and one service importing another's ``app.*``
would not resolve at runtime and would invert the dependency direction. The
CLIENT construction — the part that actually drifted seven ways — is already
shared (``shared/s3.py``); what is restated here is the ~20 lines of
absent-key/unconfigured policy, and it is restated because the two callers hold
different Settings shapes (``s3_endpoint`` here, ``s3_endpoint_url`` there —
DEP-ROOT). Promoting it to ``shared/`` is the right eventual move and is
blocked on that field name converging.
"""

from __future__ import annotations

from typing import Any

import structlog
from botocore.exceptions import ClientError
from shared.s3 import s3_client

from app.config import Settings

log = structlog.get_logger(__name__)

# S3 error codes that mean "object already absent" — treated as success, because
# the goal (the object is not in the bucket) is met either way.
_ABSENT_CODES: frozenset[str] = frozenset({"NoSuchKey", "404"})


class StorageNotConfiguredError(RuntimeError):
    """Object storage was asked to delete keys but has no endpoint/credentials.

    Typed rather than a bare ``RuntimeError`` so the retention purge can tell
    "this deployment has no S3 wired up" — an operator fix — from "S3 rejected
    the delete". Both must abort the purge before any row is deleted; only this
    one is fixed by setting env vars rather than by waiting for the next run.
    """


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


async def _delete_one(s3: Any, *, bucket: str, key: str) -> None:
    """Delete one object, tolerating a key that is already gone.

    ``s3`` is ``Any`` because aioboto3 ships no stubs — the same typing that
    ``shared/s3.py`` yields and that every other call site already uses.
    """
    try:
        await s3.delete_object(Bucket=bucket, Key=key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in _ABSENT_CODES:
            log.debug("s3.delete.absent", bucket=bucket, key=key, code=code)
            return
        raise
    log.debug("s3.delete.ok", bucket=bucket, key=key)


async def delete_objects(
    keys_by_bucket: dict[str, list[str]],
    *,
    settings: Settings,
) -> int:
    """Delete object keys grouped by bucket; return how many were removed.

    The return value is a COUNT rather than ``None`` for the same reason
    ``admin_ops`` learned the hard way (M-1a): a caller that cannot distinguish
    "deleted everything" from "silently did nothing because storage was not
    configured" will report a DPDP obligation as fulfilled when it is not. Here
    the caller is the retention purge, which compares this against the number of
    keys it collected and refuses to delete the owning rows on a shortfall.

    Args:
        keys_by_bucket: ``{bucket_name: [key, ...]}``. Empty lists are skipped.
        settings: data_gateway ``Settings`` — supplies endpoint + credentials.

    Returns:
        Number of keys deleted. A key that was already absent counts as deleted.

    Raises:
        StorageNotConfiguredError: keys were supplied but no endpoint or no
            access key is configured.
        ClientError: a delete failed for any reason other than the key being
            already absent.
    """
    # Deleting nothing needs no configuration. Checked before the credential
    # guard so a purge run on a machine with no S3 env vars still works for the
    # overwhelmingly common case: scorecards whose PDF has not been generated
    # yet carry NULL keys, so most purges collect none at all.
    if not any(keys for keys in keys_by_bucket.values()):
        return 0

    # OR, not AND. An endpoint-less but credentialed config (or the reverse) is
    # not a valid state: with AND the guard does not fire and every key then
    # fails its own ClientError against the wrong endpoint — a storm of per-key
    # errors instead of one clear reason.
    if not settings.s3_endpoint or not settings.s3_access_key_id:
        log.error(
            "s3.delete.not_configured",
            has_endpoint=bool(settings.s3_endpoint),
            has_access_key=bool(settings.s3_access_key_id),
            key_count=sum(len(keys) for keys in keys_by_bucket.values()),
            reason="S3_ENDPOINT / S3_ACCESS_KEY_ID must both be set before "
                   "object-storage deletion can fulfil DPDP §8(7).",
        )
        raise StorageNotConfiguredError(
            "S3 is not configured (S3_ENDPOINT and S3_ACCESS_KEY_ID must both "
            "be set); refusing to report objects as deleted."
        )

    deleted = 0
    async with s3_client(
        endpoint=settings.s3_endpoint,
        region=settings.s3_region,
        access_key=settings.s3_access_key_id,
        secret_key=settings.s3_secret_access_key,
        use_ssl=settings.s3_use_ssl,
    ) as s3:
        for bucket, keys in keys_by_bucket.items():
            for key in keys:
                await _delete_one(s3, bucket=bucket, key=key)
                deleted += 1
    return deleted
