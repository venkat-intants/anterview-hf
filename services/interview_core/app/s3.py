"""Async S3 audio upload helper — S5-005.

Wraps aioboto3 so ws.py can store candidate PCM audio without any network
failure propagating into the turn path. The function is non-blocking by
contract: it NEVER raises — on any error it logs the problem and returns
None so the caller can proceed normally.

Key format: interviews/{session_id}/turn_{turn_seq:04d}.pcm
Bucket:     settings.s3_bucket_name

MinIO (local dev / CI):
    Set S3_ENDPOINT=http://localhost:9000.  The endpoint_url is passed to
    aioboto3 as-is.  SSL is disabled by default for MinIO (S3_USE_SSL=false).

Real AWS S3 (production):
    Leave S3_ENDPOINT empty (or unset).  endpoint_url becomes None which
    tells boto3 to use the standard AWS endpoint for the configured region.

PII note:
    The S3 key contains session_id and turn_seq — both are pseudonymous
    UUIDs / integers, not direct PII.  The audio bytes ARE voice biometric
    data and must be handled under DPDP Act 2023 §3(k).  They are uploaded
    encrypted-at-rest via the bucket policy (SSE-S3 in dev / SSE-KMS in
    production); we do not apply extra encryption here because the boto3
    session handles the HTTPS transport layer.
"""

from __future__ import annotations

import aioboto3
import structlog
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.config import Settings

log = structlog.get_logger(__name__)


async def upload_audio(
    session_id: str,
    turn_seq: int,
    pcm_bytes: bytes,
    *,
    settings: Settings,
) -> str | None:
    """Upload raw PCM audio to S3. Returns the S3 object key on success, None on failure.

    Key format: interviews/{session_id}/turn_{turn_seq:04d}.pcm

    Non-blocking contract: NEVER raises — logs error and returns None so the
    turn still completes even if S3 is unavailable.

    Args:
        session_id: Interview session UUID string (used in the S3 key).
        turn_seq:   Candidate turn sequence number, zero-based (zero-padded
                    to 4 digits in the key so lexicographic sort == numeric
                    sort up to turn 9999 — enough for any interview session).
        pcm_bytes:  Raw PCM audio bytes to upload.  Empty bytes are rejected
                    immediately (no network call) and None is returned.
        settings:   Service settings instance providing S3 credentials.

    Returns:
        The S3 object key string on success, or None when the upload was
        skipped (empty bytes) or failed (any exception).
    """
    if not pcm_bytes:
        log.info(
            "s3.upload_audio.skipped_empty",
            session_id=session_id,
            turn_seq=turn_seq,
        )
        return None

    key = f"interviews/{session_id}/turn_{turn_seq:04d}.pcm"

    # For local MinIO / Cloudflare R2, pass the custom endpoint.
    # For real AWS S3, None tells boto3 to resolve the standard regional endpoint.
    endpoint_url: str | None = settings.s3_endpoint if settings.s3_endpoint else None

    # MinIO/R2 (custom endpoint) require path-style addressing; AWS keeps default.
    boto_config: BotoConfig | None = (
        BotoConfig(s3={"addressing_style": "path"}) if endpoint_url else None
    )

    try:
        boto_session = aioboto3.Session(
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
            region_name=settings.s3_region,
        )
        async with boto_session.client(
            "s3",
            endpoint_url=endpoint_url,
            use_ssl=settings.s3_use_ssl,
            config=boto_config,
        ) as s3_client:
            await s3_client.put_object(
                Bucket=settings.s3_bucket_name,
                Key=key,
                Body=pcm_bytes,
                ContentType="audio/pcm",
            )

        log.info(
            "s3.upload_audio.done",
            session_id=session_id,
            turn_seq=turn_seq,
            key=key,
            bytes_uploaded=len(pcm_bytes),
        )
        return key

    except ClientError as exc:
        log.error(
            "s3.upload_audio.client_error",
            session_id=session_id,
            turn_seq=turn_seq,
            key=key,
            error_code=exc.response.get("Error", {}).get("Code", "UNKNOWN"),
            error=str(exc),
        )
        return None

    except BotoCoreError as exc:
        log.error(
            "s3.upload_audio.boto_error",
            session_id=session_id,
            turn_seq=turn_seq,
            key=key,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None

    except Exception as exc:
        # Guard against any unexpected library-level exception so the
        # non-blocking contract is upheld unconditionally.
        log.error(
            "s3.upload_audio.unexpected_error",
            session_id=session_id,
            turn_seq=turn_seq,
            key=key,
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return None
