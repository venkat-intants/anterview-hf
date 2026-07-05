"""Minimal async S3 / R2 helper for the DPDP erasure executor.

The erasure executor must physically delete object-storage artefacts (scorecard
PDFs, transcript JSON, resume PDFs) before it can honestly claim that PII has
been erased under DPDP Act 2023 §12.  This module provides the single
``delete_objects`` coroutine that does that work.

Design
------
- Uses aioboto3 (same library as feedback_billing and interview_core) so the
  boto session is fully async and never blocks the event loop.
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
The caller passes a ``Settings`` instance.  When ``settings.s3_endpoint_url``
is empty the function skips the delete (graceful no-op) and logs a warning —
this lets the service start without S3 credentials in local dev/CI while still
honouring the contract in production.

IMPORTANT: admin_ops shares the same AWS/R2 credentials as feedback_billing
(both access the scorecard bucket) and data_gateway (resume bucket).  Set
``S3_ENDPOINT_URL``, ``S3_ACCESS_KEY_ID``, ``S3_SECRET_ACCESS_KEY``,
``S3_SCORECARD_BUCKET``, and ``S3_BUCKET_NAME`` in the admin_ops ``.env``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aioboto3
import structlog
from botocore.exceptions import ClientError

if TYPE_CHECKING:
    from app.config import Settings

log = structlog.get_logger(__name__)

# S3 error codes that mean "object already absent" — treated as success.
_ABSENT_CODES: frozenset[str] = frozenset({"NoSuchKey", "404"})


async def delete_objects(
    keys_by_bucket: dict[str, list[str]],
    *,
    settings: Settings,
) -> None:
    """Delete a set of S3 object keys grouped by bucket.

    Parameters
    ----------
    keys_by_bucket:
        Mapping of ``{bucket_name: [key, ...]}`` to delete.  Buckets with
        an empty key list are skipped.
    settings:
        Admin-ops ``Settings`` instance — supplies S3 credentials and
        endpoint URL.

    Raises
    ------
    ClientError
        When a delete call fails for any reason other than the key being
        already absent.  The caller MUST catch this and NOT stamp the
        erasure request as 'completed'.
    Exception
        Any other unexpected boto / network error propagates identically.

    No-op behaviour
    ---------------
    When ``settings.s3_endpoint_url`` is empty AND
    ``settings.s3_access_key_id`` is empty the function logs a warning and
    returns without making any network call.  This lets local dev/CI run
    without object-storage credentials.  In production both settings MUST
    be set — the caller (erasure executor) logs this path so it is
    observable.
    """
    if not settings.s3_endpoint_url and not settings.s3_access_key_id:
        log.warning(
            "s3_client.delete_objects.no_credentials",
            reason="S3_ENDPOINT_URL and S3_ACCESS_KEY_ID are both empty — "
                   "skipping object-storage deletion.  Set S3_* env vars in "
                   "production to fulfil DPDP §12.",
        )
        return

    endpoint_url: str | None = settings.s3_endpoint_url or None

    boto_session = aioboto3.Session(
        aws_access_key_id=settings.s3_access_key_id or None,
        aws_secret_access_key=settings.s3_secret_access_key or None,
        region_name=settings.s3_region,
    )

    async with boto_session.client(
        "s3",
        endpoint_url=endpoint_url,
    ) as s3:
        for bucket, keys in keys_by_bucket.items():
            if not keys:
                continue
            for key in keys:
                await _delete_one(s3, bucket=bucket, key=key)


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
