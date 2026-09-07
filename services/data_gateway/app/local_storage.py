"""A local-disk object store, for development only.

Why this exists
---------------
Every resume path — the HR bulk upload and the public apply form — writes the
PDF to object storage *before* it writes the database row. With no S3
credentials that step raises, the public apply endpoint answers 503, and the
whole intake half of the product is untestable on a laptop without standing up
MinIO first. That is a real barrier to trying the thing, and the fix is twenty
lines.

Why it is fenced off from production
------------------------------------
What gets written here is a stranger's CV. On a container filesystem that is
PII with no lifecycle, no encryption at rest, no replication, and — the part
that actually matters — no path for the DPDP erasure executor to reach, since
that purge talks to a bucket. A deployment that quietly fell back to disk would
be accumulating personal data it cannot delete on request, and nothing in the
logs would look wrong.

So the fence is structural rather than advisory:

* it is used only when object storage has NO credentials, so a configured
  deployment can never silently drift onto it;
* it refuses outright in production or staging (``ENFORCED_ENVS``), raising
  rather than degrading — an operator who lost their S3 credentials should see
  uploads fail loudly, not succeed into a directory nobody backs up;
* it is off unless ``STORAGE_LOCAL_DIR`` is set. There is deliberately no
  convenient default: writing personal data to disk should be something someone
  chose, and the 503 that happens otherwise now names the variable that turns
  it on.

Keys are the same S3-style paths (``applicants/<company>/<uuid>.pdf``) so
nothing downstream has to care which store served a file, and so a deployment
can move between them without rewriting stored keys.
"""

from __future__ import annotations

import asyncio
import pathlib
import re

import structlog
from shared.security import ENFORCED_ENVS, normalise_app_env

log = structlog.get_logger(__name__)

# Object keys are built by this codebase, never by a request, but they are
# joined onto a filesystem path — so they are validated anyway. A key is
# slash-separated segments of safe characters and nothing else: no "..", no
# absolute paths, no drive letters, no backslashes.
_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*(?:/[A-Za-z0-9][A-Za-z0-9._\-]*)*$")


class LocalStorageError(RuntimeError):
    """Local storage was asked to act but may not, or could not."""


def enabled(*, app_env: str, directory: str, has_s3_credentials: bool) -> bool:
    """Whether the disk fallback may serve this deployment.

    Three conditions, all required. Split into a predicate rather than inlined
    at the call sites so "when does PII land on disk?" has one answer that can
    be read and tested on its own.
    """
    if has_s3_credentials:
        return False
    if not directory.strip():
        return False
    return normalise_app_env(app_env) not in ENFORCED_ENVS


def _resolve(directory: str, key: str) -> pathlib.Path:
    """The absolute path for *key*, or raise.

    Belt and braces: the key is pattern-checked, and the resolved path is then
    confirmed to be inside the root. The pattern alone would be enough today;
    the containment check is what keeps that true if the key format ever
    changes.
    """
    if not _SAFE_KEY.match(key):
        raise LocalStorageError(f"unsafe object key: {key!r}")
    root = pathlib.Path(directory).expanduser().resolve()
    target = (root / key).resolve()
    if not target.is_relative_to(root):
        raise LocalStorageError(f"object key escapes the storage root: {key!r}")
    return target


async def put(directory: str, key: str, data: bytes) -> str:
    """Write *data* at *key*. Returns the key, matching ``upload_file``."""
    target = _resolve(directory, key)

    def _write() -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Written to a temporary name and moved into place, so a crash
        # mid-write cannot leave a truncated PDF that later reads as a corrupt
        # resume rather than a missing one.
        tmp = target.with_suffix(target.suffix + ".partial")
        tmp.write_bytes(data)
        tmp.replace(target)

    # Off the event loop: this is blocking file I/O in an async request path.
    await asyncio.to_thread(_write)
    log.info("storage.local.put", key=key, bytes=len(data))
    return key


async def delete(directory: str, key: str) -> None:
    """Best-effort delete. Never raises — mirrors the S3 cleanup contract."""
    try:
        target = _resolve(directory, key)
        await asyncio.to_thread(target.unlink, True)  # missing_ok=True
        log.info("storage.local.delete", key=key)
    except Exception as exc:  # noqa: BLE001 — cleanup must not mask the real error
        log.warning("storage.local.delete_failed", key=key, error=type(exc).__name__)
