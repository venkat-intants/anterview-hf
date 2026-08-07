"""Tests for the object-storage delete helper used by the DPDP erasure executor.

The contract these pin is narrow but load-bearing: ``delete_objects`` must
report how many objects it deleted, and must refuse — loudly and typed — to
return a number it did not earn. Returning ``None`` on an unconfigured
deployment is what let the executor stamp status='completed' over objects that
were still in the bucket (M-1a, 2026-08-07).

No network call is made by any test here: the shared client factory is patched.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.s3_client import StorageNotConfiguredError, delete_objects


def _settings(*, endpoint: str = "https://acct.r2.cloudflarestorage.com",
              access_key: str = "key-id") -> MagicMock:
    settings = MagicMock()
    settings.s3_endpoint_url = endpoint
    settings.s3_access_key_id = access_key
    settings.s3_secret_access_key = "secret"
    settings.s3_region = "auto"
    return settings


def _patched_client(s3: Any) -> Any:
    """Patch the shared factory so no client is ever constructed for real."""
    opened: list[dict[str, Any]] = []

    @asynccontextmanager
    async def _factory(**kwargs: Any) -> Any:
        opened.append(kwargs)
        yield s3

    return patch("app.s3_client.open_s3_client", new=_factory), opened


@pytest.mark.asyncio
async def test_returns_count_of_keys_deleted() -> None:
    s3 = MagicMock()
    s3.delete_object = AsyncMock()
    patcher, opened = _patched_client(s3)

    with patcher:
        deleted = await delete_objects(
            {"scorecards": ["a.pdf", "b.json"], "uploads": ["cv.pdf"]},
            settings=_settings(),
        )

    assert deleted == 3, "the caller checks this number before claiming erasure"
    assert s3.delete_object.await_count == 3
    # Credentials and endpoint are handed to the shared factory verbatim; the
    # factory owns addressing style and use_ssl (see shared/s3.py).
    assert opened[0]["endpoint"] == "https://acct.r2.cloudflarestorage.com"
    assert opened[0]["access_key"] == "key-id"


@pytest.mark.asyncio
async def test_empty_bucket_is_skipped_without_stopping_its_siblings() -> None:
    """A bucket with no keys must be skipped, not short-circuit the whole call.

    The executor collects keys per bucket and routinely produces an empty list
    for one of them (a candidate with a scorecard but no uploaded resume, say).
    Skipping must not cost the sibling bucket its deletes, and the count must
    reflect only real keys — the caller compares that number against the keys it
    collected and refuses to claim completion on a shortfall.
    """
    s3 = MagicMock()
    s3.delete_object = AsyncMock()
    patcher, _ = _patched_client(s3)

    with patcher:
        deleted = await delete_objects(
            {"uploads": [], "scorecards": ["a.pdf"]},
            settings=_settings(),
        )

    assert deleted == 1
    buckets_touched = {c.kwargs["Bucket"] for c in s3.delete_object.await_args_list}
    assert buckets_touched == {"scorecards"}, "the empty bucket must never be contacted"


@pytest.mark.asyncio
async def test_absent_key_counts_as_deleted() -> None:
    """The erasure goal is met whether we removed the object or it was gone."""
    s3 = MagicMock()
    s3.delete_object = AsyncMock(
        side_effect=ClientError(
            {"Error": {"Code": "NoSuchKey", "Message": "gone"}}, "DeleteObject"
        )
    )
    patcher, _ = _patched_client(s3)

    with patcher:
        deleted = await delete_objects({"uploads": ["cv.pdf"]}, settings=_settings())

    assert deleted == 1


@pytest.mark.asyncio
async def test_non_absent_client_error_propagates() -> None:
    s3 = MagicMock()
    s3.delete_object = AsyncMock(
        side_effect=ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "no"}}, "DeleteObject"
        )
    )
    patcher, _ = _patched_client(s3)

    with patcher, pytest.raises(ClientError):
        await delete_objects({"uploads": ["cv.pdf"]}, settings=_settings())


@pytest.mark.asyncio
async def test_no_keys_needs_no_configuration() -> None:
    """A user with nothing in storage can still be erased on an unconfigured host."""
    deleted = await delete_objects(
        {"uploads": []}, settings=_settings(endpoint="", access_key="")
    )
    assert deleted == 0


@pytest.mark.asyncio
async def test_missing_access_key_alone_raises() -> None:
    """An endpoint without credentials must fail the guard.

    This is the AND-vs-OR case: the guard used to require BOTH fields empty, so
    a half-configured deployment sailed past it and produced one ClientError per
    key instead of one clear reason.
    """
    with pytest.raises(StorageNotConfiguredError):
        await delete_objects({"uploads": ["cv.pdf"]}, settings=_settings(access_key=""))


@pytest.mark.asyncio
async def test_missing_endpoint_alone_raises() -> None:
    with pytest.raises(StorageNotConfiguredError):
        await delete_objects({"uploads": ["cv.pdf"]}, settings=_settings(endpoint=""))


@pytest.mark.asyncio
async def test_unconfigured_never_reports_a_delete() -> None:
    """The M-1a regression: silence here reads as success to the caller."""
    with pytest.raises(StorageNotConfiguredError):
        await delete_objects(
            {"uploads": ["cv.pdf"]},
            settings=_settings(endpoint="", access_key=""),
        )
