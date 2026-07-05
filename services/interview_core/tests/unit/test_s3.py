"""Unit tests for S5-005 — async S3 audio upload helper (app/s3.py).

All tests are fully offline — aioboto3 is mocked so no network calls are made.

Coverage:
  test_upload_audio_returns_correct_key       happy path — key format verified
  test_upload_audio_returns_none_on_failure   ClientError → None (no exception raised)
  test_upload_audio_returns_none_when_pcm_empty  empty bytes → None, S3 not called
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.config import Settings

# ---------------------------------------------------------------------------
# Minimal settings fixture
# ---------------------------------------------------------------------------


def _make_settings(**overrides: Any) -> Settings:
    """Return a Settings instance suitable for unit tests.

    Overrides are applied after building the base set so individual tests
    can tweak specific fields without constructing the full .env.
    """
    base: dict[str, Any] = {
        "database_url": "postgresql+asyncpg://test:test@localhost/testdb",
        "redis_url": "redis://localhost:6379/0",
        "jwt_secret": "test-secret-for-unit-tests",
        "s3_endpoint": "http://localhost:9000",
        "s3_region": "auto",
        "s3_bucket_name": "test-bucket",
        "s3_access_key_id": "minioadmin",
        "s3_secret_access_key": "minioadmin",
        "s3_use_ssl": False,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client_error(code: str = "NoSuchBucket") -> ClientError:
    """Construct a botocore ClientError for testing."""
    return ClientError(
        error_response={"Error": {"Code": code, "Message": "simulated error"}},
        operation_name="PutObject",
    )


def _mock_aioboto3_session(put_object_side_effect: Any = None) -> MagicMock:
    """Return a patched aioboto3.Session whose s3.put_object behaves as specified.

    ``put_object_side_effect``:
        - None (default)  → successful call, returns empty dict.
        - an Exception    → raised when put_object is awaited.
    """
    mock_s3_client = AsyncMock()
    if put_object_side_effect is not None:
        mock_s3_client.put_object = AsyncMock(side_effect=put_object_side_effect)
    else:
        mock_s3_client.put_object = AsyncMock(return_value={})

    # The s3 client is used as an async context manager:
    #   async with session.client("s3", ...) as s3_client:
    mock_client_ctx = AsyncMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_s3_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.client = MagicMock(return_value=mock_client_ctx)

    return mock_session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_audio_returns_correct_key() -> None:
    """Happy path: upload_audio returns the expected S3 key string.

    Key format contract (S5-005):
        interviews/{session_id}/turn_{turn_seq:04d}.pcm

    The aioboto3 layer is mocked — no real S3 call is made.  We assert:
      1. The returned key matches the format exactly.
      2. put_object was called exactly once (one upload per turn).
      3. put_object received the correct Bucket, Key, and Body.
    """
    from app.s3 import upload_audio

    session_id = "abc123"
    turn_seq = 1
    pcm_bytes = b"\x00\xff" * 160  # 320 bytes of dummy PCM

    mock_session = _mock_aioboto3_session()

    with patch("app.s3.aioboto3.Session", return_value=mock_session):
        s3_settings = _make_settings()
        result = await upload_audio(session_id, turn_seq, pcm_bytes, settings=s3_settings)

    expected_key = f"interviews/{session_id}/turn_{turn_seq:04d}.pcm"
    assert result == expected_key, (
        f"Expected key {expected_key!r}, got {result!r}"
    )

    # Verify put_object was called with the correct parameters.
    put_obj_mock = mock_session.client.return_value.__aenter__.return_value.put_object
    put_obj_mock.assert_awaited_once()
    call_kwargs = put_obj_mock.call_args.kwargs
    assert call_kwargs["Bucket"] == "test-bucket"
    assert call_kwargs["Key"] == expected_key
    assert call_kwargs["Body"] == pcm_bytes


@pytest.mark.asyncio
async def test_upload_audio_key_zero_padded_to_four_digits() -> None:
    """turn_seq is zero-padded to 4 digits in the key.

    turn_seq=7  → turn_0007.pcm
    turn_seq=42 → turn_0042.pcm
    """
    from app.s3 import upload_audio

    pcm_bytes = b"\x00" * 64
    mock_session = _mock_aioboto3_session()

    with patch("app.s3.aioboto3.Session", return_value=mock_session):
        s3_settings = _make_settings()
        key7 = await upload_audio("sess-1", 7, pcm_bytes, settings=s3_settings)
        key42 = await upload_audio("sess-2", 42, pcm_bytes, settings=s3_settings)

    assert key7 == "interviews/sess-1/turn_0007.pcm"
    assert key42 == "interviews/sess-2/turn_0042.pcm"


@pytest.mark.asyncio
async def test_upload_audio_returns_none_on_failure() -> None:
    """ClientError from put_object → upload_audio returns None (no exception raised).

    The non-blocking contract: NEVER raises so the turn completes even when
    S3 is unavailable (bucket not found, credentials invalid, etc.).
    """
    from app.s3 import upload_audio

    session_id = "sess-fail"
    turn_seq = 3
    pcm_bytes = b"\x00" * 64

    mock_session = _mock_aioboto3_session(
        put_object_side_effect=_make_client_error("NoSuchBucket")
    )

    with patch("app.s3.aioboto3.Session", return_value=mock_session):
        s3_settings = _make_settings()
        result = await upload_audio(session_id, turn_seq, pcm_bytes, settings=s3_settings)

    assert result is None, (
        f"Expected None on ClientError, got {result!r}"
    )


@pytest.mark.asyncio
async def test_upload_audio_returns_none_on_boto_core_error() -> None:
    """BotoCoreError (e.g. connection timeout) → upload_audio returns None."""
    from botocore.exceptions import BotoCoreError

    from app.s3 import upload_audio

    class _ConnTimeout(BotoCoreError):  # noqa: N818 — test-local stub mirroring a botocore error
        msg = "Connection timed out"

    mock_session = _mock_aioboto3_session(put_object_side_effect=_ConnTimeout())

    with patch("app.s3.aioboto3.Session", return_value=mock_session):
        s3_settings = _make_settings()
        result = await upload_audio("sess-x", 2, b"\x00" * 32, settings=s3_settings)

    assert result is None


@pytest.mark.asyncio
async def test_upload_audio_returns_none_when_pcm_empty() -> None:
    """Empty bytes → returns None immediately without calling S3 at all.

    There is no point uploading a zero-byte object.  The function must
    short-circuit before constructing an aioboto3 session.
    """
    from app.s3 import upload_audio

    mock_session = _mock_aioboto3_session()

    with patch("app.s3.aioboto3.Session", return_value=mock_session) as mock_session_cls:
        s3_settings = _make_settings()
        result = await upload_audio("sess-empty", 1, b"", settings=s3_settings)

    assert result is None, (
        f"Expected None for empty PCM, got {result!r}"
    )
    # aioboto3.Session must NOT have been constructed — no network attempt.
    mock_session_cls.assert_not_called()


@pytest.mark.asyncio
async def test_upload_audio_uses_custom_endpoint_for_minio() -> None:
    """S3_ENDPOINT is forwarded as endpoint_url when set (MinIO / R2 dev path)."""
    from app.s3 import upload_audio

    mock_session = _mock_aioboto3_session()

    with patch("app.s3.aioboto3.Session", return_value=mock_session):
        s3_settings = _make_settings(s3_endpoint="http://minio:9000")
        await upload_audio("sess-minio", 1, b"\x00" * 64, settings=s3_settings)

    # Verify client() was called with the custom endpoint_url.
    mock_session.client.assert_called_once()
    call_kwargs = mock_session.client.call_args.kwargs
    assert call_kwargs.get("endpoint_url") == "http://minio:9000"


@pytest.mark.asyncio
async def test_upload_audio_no_endpoint_for_real_aws() -> None:
    """S3_ENDPOINT='' → endpoint_url=None (real AWS S3 endpoint resolution)."""
    from app.s3 import upload_audio

    mock_session = _mock_aioboto3_session()

    with patch("app.s3.aioboto3.Session", return_value=mock_session):
        s3_settings = _make_settings(s3_endpoint="")
        await upload_audio("sess-aws", 1, b"\x00" * 64, settings=s3_settings)

    call_kwargs = mock_session.client.call_args.kwargs
    assert call_kwargs.get("endpoint_url") is None
