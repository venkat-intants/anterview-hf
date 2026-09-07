"""The development-only disk fallback for resume storage.

What is being written here is a stranger's CV, so the tests that matter most
are the refusals. A deployment that quietly fell back to disk would accumulate
personal data with no encryption, no backup, and — the part that actually bites
— no path for the DPDP erasure executor to reach, since that purge talks to a
bucket. Nothing in the logs would look wrong.

So: it is off unless switched on, off wherever object storage IS configured,
and refused outright in production and staging.
"""

from __future__ import annotations

import pathlib

import pytest

from app import local_storage


# ---------------------------------------------------------------------------
# When it may be used at all
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("env", ["production", "staging", "PRODUCTION", " Staging "])
def test_it_refuses_to_serve_production_or_staging(env: str) -> None:
    """An operator who lost their S3 credentials must see uploads fail loudly,
    not succeed into a directory nobody backs up."""
    assert (
        local_storage.enabled(app_env=env, directory="/tmp/x", has_s3_credentials=False)
        is False
    )


@pytest.mark.parametrize("env", ["development", "dev", "test", "local"])
def test_it_serves_a_development_deployment(env: str) -> None:
    assert (
        local_storage.enabled(app_env=env, directory="/tmp/x", has_s3_credentials=False)
        is True
    )


def test_configured_object_storage_always_wins() -> None:
    """The fallback exists for a laptop with no bucket. A deployment that HAS
    one must never drift onto disk, whatever else is set."""
    assert (
        local_storage.enabled(
            app_env="development", directory="/tmp/x", has_s3_credentials=True
        )
        is False
    )


@pytest.mark.parametrize("directory", ["", "   "])
def test_it_is_off_until_someone_turns_it_on(directory: str) -> None:
    """There is deliberately no convenient default: writing personal data to
    disk should be something a person chose."""
    assert (
        local_storage.enabled(
            app_env="development", directory=directory, has_s3_credentials=False
        )
        is False
    )


# ---------------------------------------------------------------------------
# Key safety
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "key",
    [
        "../../etc/passwd",
        "applicants/../../../secrets.pdf",
        "/absolute/path.pdf",
        "C:/windows/system32/x.pdf",
        "applicants\\..\\escape.pdf",
        "",
    ],
)
@pytest.mark.asyncio
async def test_a_key_that_escapes_the_root_is_refused(
    tmp_path: pathlib.Path, key: str
) -> None:
    """Keys are built by this codebase and never by a request, so this is not
    reachable today. It is checked anyway because the containment guarantee
    should not depend on that staying true."""
    with pytest.raises(local_storage.LocalStorageError):
        await local_storage.put(str(tmp_path), key, b"%PDF-1.4")


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_it_writes_the_bytes_under_the_s3_style_key(
    tmp_path: pathlib.Path,
) -> None:
    """The same key shape as the bucket, so nothing downstream has to care
    which store served a file and a deployment can move between them."""
    key = "applicants/11111111-1111-1111-1111-111111111111/cv.pdf"
    returned = await local_storage.put(str(tmp_path), key, b"%PDF-1.4 hello")

    assert returned == key
    assert (tmp_path / key).read_bytes() == b"%PDF-1.4 hello"


@pytest.mark.asyncio
async def test_nothing_partial_is_left_behind(tmp_path: pathlib.Path) -> None:
    """Written to a temporary name and moved into place, so a crash mid-write
    cannot leave a truncated PDF that later reads as a corrupt resume rather
    than a missing one."""
    key = "applicants/c/cv.pdf"
    await local_storage.put(str(tmp_path), key, b"%PDF-1.4")
    leftovers = list(tmp_path.rglob("*.partial"))
    assert leftovers == []


@pytest.mark.asyncio
async def test_a_rewrite_replaces_rather_than_appends(tmp_path: pathlib.Path) -> None:
    key = "applicants/c/cv.pdf"
    await local_storage.put(str(tmp_path), key, b"first")
    await local_storage.put(str(tmp_path), key, b"second")
    assert (tmp_path / key).read_bytes() == b"second"


@pytest.mark.asyncio
async def test_delete_removes_the_object(tmp_path: pathlib.Path) -> None:
    key = "applicants/c/cv.pdf"
    await local_storage.put(str(tmp_path), key, b"%PDF-1.4")
    await local_storage.delete(str(tmp_path), key)
    assert not (tmp_path / key).exists()


@pytest.mark.asyncio
async def test_delete_never_raises(tmp_path: pathlib.Path) -> None:
    """Mirrors the S3 cleanup contract: it runs while a real error is being
    re-raised, and must not mask it."""
    await local_storage.delete(str(tmp_path), "applicants/c/never-existed.pdf")
    await local_storage.delete(str(tmp_path), "../escape.pdf")
