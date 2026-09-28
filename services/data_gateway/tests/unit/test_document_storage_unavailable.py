"""A document that cannot be stored is a 503 the candidate can act on, not a 500.

Found by the full pipeline test: with no object storage configured, a candidate's
PAN card went to boto3's default endpoint (``s3.auto.amazonaws.com``), failed on
the network, and came back as a bare 500 — while a CV in the very same
environment got a 503 naming the setting to fix. Same kind of upload, two
different failures, and the worse one on the more sensitive file.

The shared store now raises one error type whatever went wrong underneath, and
every caller maps it to a 503. Deliberately no disk fallback — see
``document_storage.StorageUnavailableError``.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError


def _settings(**over: object) -> SimpleNamespace:
    base = {
        "s3_endpoint": "http://127.0.0.1:9000",
        "s3_bucket_name": "intants-uploads",
        "s3_access_key_id": "key",
        "s3_secret_access_key": "secret",
    }
    return SimpleNamespace(**{**base, **over})


@pytest.mark.asyncio
async def test_no_storage_configured_refuses_without_calling_out() -> None:
    """Nothing to try, so nothing is tried — not a slow call to Amazon."""
    from app import document_storage as store

    with (
        patch.object(store, "upload_file", AsyncMock()) as upload,
        pytest.raises(store.StorageUnavailableError),
    ):
        await store.store(
            _settings(s3_access_key_id="", s3_secret_access_key=""),
            "preboarding/c/o/d", b"%PDF-1.4", "application/pdf",
        )
    upload.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        EndpointConnectionError(endpoint_url="https://intants-uploads.s3.auto.amazonaws.com/x"),
        ClientError({"Error": {"Code": "NoSuchBucket", "Message": "gone"}}, "PutObject"),
    ],
    ids=["unreachable", "refused"],
)
async def test_a_storage_failure_becomes_one_error_type(failure: Exception) -> None:
    from app import document_storage as store

    with (
        patch.object(store, "upload_file", AsyncMock(side_effect=failure)),
        pytest.raises(store.StorageUnavailableError),
    ):
        await store.store(_settings(), "preboarding/c/o/d", b"%PDF-1.4", "application/pdf")


@pytest.mark.asyncio
async def test_a_stored_document_still_stores() -> None:
    from app import document_storage as store

    with patch.object(store, "upload_file", AsyncMock()) as upload:
        await store.store(_settings(), "preboarding/c/o/d", b"%PDF-1.4", "application/pdf")
    upload.assert_awaited_once()


@pytest.mark.parametrize("module", ["app.preboarding", "app.job_tasks"])
def test_every_caller_maps_storage_failure_to_a_503(module: str) -> None:
    """Each store() call is wrapped, so the next caller added is a visible gap.

    Structural rather than behavioural: the behavioural path needs a database
    for each caller. What this pins is the thing that went wrong — a store()
    call nobody had taught what to do when storage is not there.
    """
    import importlib

    src = inspect.getsource(importlib.import_module(module))
    calls = src.count("await store.store(")
    handled = src.count("except store.StorageUnavailableError")
    assert calls, f"{module} no longer stores documents — update this test"
    assert handled == calls, f"{module}: {calls} store() calls, {handled} mapped to a 503"
    assert "503" in src


@pytest.mark.asyncio
@pytest.mark.parametrize("unset", ["s3_endpoint", "s3_bucket_name"])
async def test_keys_without_an_endpoint_or_bucket_refuse_rather_than_go_to_aws(unset: str) -> None:
    """Keys but no endpoint would send identity documents to Amazon's default region.

    Nobody chose that destination, so it is refused like missing keys are.
    delete_objects already refused endpoint-less configs; uploads now match.
    """
    from app import document_storage as store

    with (
        patch.object(store, "upload_file", AsyncMock()) as upload,
        pytest.raises(store.StorageUnavailableError),
    ):
        await store.store(_settings(**{unset: ""}), "preboarding/c/o/d", b"%PDF-1.4",
                          "application/pdf")
    upload.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_upload_deletes_whatever_may_have_landed() -> None:
    """A timeout can hide a successful write; the row is rolled back, so the object goes too."""
    from app import document_storage as store

    lost = EndpointConnectionError(endpoint_url="http://127.0.0.1:9000/x")
    with (
        patch.object(store, "upload_file", AsyncMock(side_effect=lost)),
        patch.object(store, "delete_objects", AsyncMock(return_value=1)) as delete,
        pytest.raises(store.StorageUnavailableError),
    ):
        await store.store(_settings(), "preboarding/c/o/d", b"%PDF-1.4", "application/pdf")
    delete.assert_awaited_once()
    assert delete.await_args.args[0] == {"intants-uploads": ["preboarding/c/o/d"]}


@pytest.mark.asyncio
async def test_a_failed_cleanup_does_not_hide_the_original_failure() -> None:
    from app import document_storage as store

    with (
        patch.object(store, "upload_file",
                     AsyncMock(side_effect=EndpointConnectionError(endpoint_url="http://x"))),
        patch.object(store, "delete_objects", AsyncMock(side_effect=RuntimeError("also down"))),
        pytest.raises(store.StorageUnavailableError, match="EndpointConnectionError"),
    ):
        await store.store(_settings(), "preboarding/c/o/d", b"%PDF-1.4", "application/pdf")
