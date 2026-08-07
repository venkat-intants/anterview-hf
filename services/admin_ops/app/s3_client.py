"""Minimal async S3 / R2 helper for the DPDP erasure executor.

The erasure executor must physically delete object-storage artefacts (scorecard
PDFs, transcript JSON, resume PDFs) before it can honestly claim that PII has
been erased under DPDP Act 2023 §12.  This module provides the single
``delete_objects`` coroutine that does that work.

Design
------
- The client itself comes from ``shared/s3.py``, the one factory all four
  services share.  This module used to build its own aioboto3 session, and it
  was the copy that had drifted furthest: no path-style addressing and no
  ``use_ssl``, so it was one R2 endpoint change away from failing at DNS.
- aioboto3 (same library as feedback_billing and interview_core) keeps the boto
  session fully async so it never blocks the event loop.
- A missing key (NoSuchKey / 404) is treated as a success: the object is
  already gone, so the erasure goal is achieved.
- Any other error is re-raised so the caller can choose not to stamp the
  erasure request as 'completed', avoiding a false-erasure claim.

PII safety
----------
- Object keys are logged at DEBUG level only (keys contain scorecard/session
  UUIDs, not candidate names or email addresses).
- Credentials are never logged.

S3 configuration
----------------
The caller passes a ``Settings`` instance.  When object storage is NOT
configured this function raises ``StorageNotConfiguredError`` rather than
returning quietly.  It used to log a warning and return ``None``, which the
caller could not tell apart from a successful delete: the erasure executor
then wrote a non-zero ``s3_objects_deleted`` and stamped ``status='completed'``
on a request whose objects were all still sitting in the bucket.  Under DPDP
§12 a false completion claim is worse than an incomplete erasure that reports
itself, so "unconfigured" must be a loud, retryable failure.

Deleting nothing is still free: a call with no keys at all returns 0 without
touching the configuration, so local dev / CI can run erasures for users who
have no stored objects.

IMPORTANT: admin_ops shares the same AWS/R2 credentials as feedback_billing
(both access the scorecard bucket) and data_gateway (resume bucket).  Set
``S3_ENDPOINT_URL``, ``S3_ACCESS_KEY_ID``, ``S3_SECRET_ACCESS_KEY``,
``S3_SCORECARD_BUCKET``, and ``S3_BUCKET_NAME`` in the admin_ops ``.env``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from botocore.exceptions import ClientError
from shared.s3 import s3_client as open_s3_client

if TYPE_CHECKING:
    from app.config import Settings

log = structlog.get_logger(__name__)

# S3 error codes that mean "object already absent" — treated as success.
_ABSENT_CODES: frozenset[str] = frozenset({"NoSuchKey", "404"})


class StorageNotConfiguredError(RuntimeError):
    """Object storage was asked to delete keys but has no endpoint/credentials.

    Typed rather than a bare ``RuntimeError`` so the erasure executor — and any
    future caller — can distinguish "this deployment has no S3 wired up" from
    "S3 rejected the delete".  Both must leave the erasure request retryable;
    only this one is fixed by setting env vars rather than by retrying.
    """


async def delete_objects(
    keys_by_bucket: dict[str, list[str]],
    *,
    settings: Settings,
) -> int:
    """Delete a set of S3 object keys grouped by bucket.

    Parameters
    ----------
    keys_by_bucket:
        Mapping of ``{bucket_name: [key, ...]}`` to delete.  Buckets with
        an empty key list are skipped.
    settings:
        Admin-ops ``Settings`` instance — supplies S3 credentials and
        endpoint URL.

    Returns
    -------
    int
        The number of object keys actually deleted.  A key that was already
        absent counts as deleted — the erasure goal is met either way.  The
        caller compares this against the number of keys it collected and
        refuses to claim completion on a shortfall, which is only possible
        because this returns a count instead of ``None``.

    Raises
    ------
    StorageNotConfiguredError
        When there are keys to delete but no endpoint or no access key is
        configured.  The caller MUST NOT stamp the erasure request as
        'completed'.
    ClientError
        When a delete call fails for any reason other than the key being
        already absent.  The caller MUST catch this and NOT stamp the
        erasure request as 'completed'.
    Exception
        Any other unexpected boto / network error propagates identically.
    """
    # Nothing to delete needs no configuration — check this before the
    # credential guard so a user with no stored objects can still be erased on
    # a machine without S3 env vars.
    if not any(keys for keys in keys_by_bucket.values()):
        return 0

    # OR, not AND. An endpoint-less but credentialed config (or the reverse) is
    # not a valid state: with AND the guard silently does not fire and every
    # single key then fails its own ClientError against the wrong endpoint — a
    # storm of per-key errors instead of one clear reason.
    if not settings.s3_endpoint_url or not settings.s3_access_key_id:
        log.error(
            "s3_client.delete_objects.not_configured",
            has_endpoint=bool(settings.s3_endpoint_url),
            has_access_key=bool(settings.s3_access_key_id),
            key_count=sum(len(keys) for keys in keys_by_bucket.values()),
            reason="S3_ENDPOINT_URL / S3_ACCESS_KEY_ID must both be set before "
                   "object-storage deletion can fulfil DPDP §12.",
        )
        raise StorageNotConfiguredError(
            "S3 is not configured (S3_ENDPOINT_URL and S3_ACCESS_KEY_ID must "
            "both be set); refusing to report objects as deleted."
        )

    deleted = 0
    async with open_s3_client(
        endpoint=settings.s3_endpoint_url,
        region=settings.s3_region,
        access_key=settings.s3_access_key_id,
        secret_key=settings.s3_secret_access_key,
    ) as s3:
        for bucket, keys in keys_by_bucket.items():
            if not keys:
                continue
            for key in keys:
                await _delete_one(s3, bucket=bucket, key=key)
                deleted += 1
    return deleted


async def _delete_one(
    s3: object,
    *,
    bucket: str,
    key: str,
) -> None:
    """Delete a single S3 object key, treating 'already absent' as success.

    Parameters
    ----------
    s3:
        An active aioboto3 S3 client (context-manager body).
    bucket:
        Bucket name.
    key:
        Object key to delete.

    Raises
    ------
    ClientError
        When the delete fails for any reason other than the key being absent.
    """
    try:
        await s3.delete_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
        log.debug(
            "s3_client.deleted",
            bucket=bucket,
            # key contains only UUIDs — not direct PII
            key=key,
        )
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code in _ABSENT_CODES:
            # Object already gone — erasure goal is met.
            log.debug(
                "s3_client.already_absent",
                bucket=bucket,
                key=key,
                error_code=error_code,
            )
            return
        # Any other error (permission denied, bucket not found, network) must
        # propagate so the executor knows the delete failed and does NOT stamp
        # the erasure request as 'completed'.
        log.error(
            "s3_client.delete_failed",
            bucket=bucket,
            key=key,
            error_code=error_code,
        )
        raise
