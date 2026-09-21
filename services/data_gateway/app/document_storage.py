"""Candidate preboarding documents in object storage — PH4-A4, decision D4-3.

WHAT GETS IN
Only PDF, JPEG and PNG, recognised by their first bytes — never by the file
name or the Content-Type a browser claims. A PDF is also refused when it
carries active content (JavaScript, launch actions, embedded files): a
document HR opens should not be able to run anything. There is no virus
scanner yet (decision D4-3); this allow-list is the control that exists, and
docs/ACCEPTED-RISKS.md says so.

WHERE IT LIVES
``preboarding/{company_id}/{offer_id}/{document_id}`` — a key that names no
person, in the uploads bucket, stored with the DETECTED content type.

HOW IT COMES OUT
Only through a signed link that lives five minutes and makes the browser
download rather than render it (``Content-Disposition: attachment``). Nothing
serves a document's bytes through the API.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from shared.s3 import s3_client

from app.config import Settings
from app.s3_upload import delete_objects, upload_file

log = structlog.get_logger()

PRESIGN_SECONDS = 300
# Markers of content that can RUN or CARRY another file. Not /OpenAction or /AA:
# ordinary PDFs from Word and browsers use them to set the first view, and on
# their own they execute nothing. A best-effort scan of the raw bytes — a
# marker inside a compressed object stream is not seen, which is why the
# absence of a virus scanner is an accepted risk rather than a solved one.
_PDF_ACTIVE = re.compile(rb"/(JavaScript|JS|Launch|EmbeddedFiles?|RichMedia|XFA)\b")
# PDF names may spell any byte as #xx (``/J#61vaScript`` is ``/JavaScript``), and
# viewers decode it; so is the check (security review L2).
_NAME_ESCAPE = re.compile(rb"#([0-9A-Fa-f]{2})")


def _decoded_names(data: bytes) -> bytes:
    return _NAME_ESCAPE.sub(lambda m: bytes([int(m.group(1), 16)]), data)
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


class DocumentRejectedError(ValueError):
    """The file is not one we accept, in words a candidate can act on."""


class StorageUnavailableError(RuntimeError):
    """The document could not be stored — nothing the candidate did.

    Callers turn this into a 503 with "try again" wording, the way the CV path
    already does, instead of letting it reach the 500 handler. Before this, an
    environment with no object storage configured sent documents to boto3's
    default endpoint (``s3.auto.amazonaws.com``), which failed on the network
    and surfaced as a bare 500 — while a CV in the same environment got a 503
    naming the setting to fix.

    Deliberately NOT a local-disk fallback, unlike CVs: these are identity
    documents, downloads go out as signed URLs a disk cannot issue, and DPDP
    erasure reaches a bucket, not a directory. See ``local_storage`` for why
    even CVs are fenced off from that path in production.
    """


@dataclass(frozen=True)
class CheckedFile:
    content_type: str
    extension: str
    size_bytes: int
    sha256: str
    safe_name: str


def sniff(data: bytes) -> tuple[str, str] | None:
    """(content type, extension) from the magic bytes, or None."""
    if data.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    return None


def safe_filename(name: str | None, extension: str) -> str:
    """A name for display and the download header: no path, no control or odd
    characters, at most 120 characters, and the extension the CONTENT has."""
    base = (name or "").replace("\\", "/").split("/")[-1]
    base = base.rsplit(".", 1)[0] if "." in base else base
    base = _UNSAFE_NAME.sub("_", base).strip(" ._") or "document"
    return f"{base[:110]}.{extension}"


def check(data: bytes, filename: str | None, *, max_bytes: int) -> CheckedFile:
    if not data:
        raise DocumentRejectedError("The file is empty.")
    if len(data) > max_bytes:
        raise DocumentRejectedError(f"The file is larger than {max_bytes // (1024 * 1024)} MB.")
    kind = sniff(data)
    if kind is None:
        raise DocumentRejectedError("Upload a PDF, JPEG or PNG file.")
    content_type, extension = kind
    if content_type == "application/pdf" and _PDF_ACTIVE.search(_decoded_names(data)):
        raise DocumentRejectedError(
            "This PDF contains scripts or embedded files, which we cannot accept. "
            "Save or print it as a plain PDF and upload that."
        )
    return CheckedFile(
        content_type=content_type, extension=extension, size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(), safe_name=safe_filename(filename, extension),
    )


def storage_key(company_id: uuid.UUID, offer_id: uuid.UUID, document_id: uuid.UUID) -> str:
    return f"preboarding/{company_id}/{offer_id}/{document_id}"


async def store(settings: Settings, key: str, data: bytes, content_type: str) -> None:
    """Put a checked document in the uploads bucket, or raise StorageUnavailableError.

    Checked for configuration FIRST: with nothing configured there is nothing
    to try, and trying means a network call to Amazon that fails slowly and
    says nothing useful. The operator gets the fix in the log; the caller gets
    one exception type to map, whatever went wrong underneath.

    The ENDPOINT is required as well as the keys. With keys but no endpoint,
    boto3 falls back to Amazon's default region, so a half-configured
    deployment would quietly send identity documents somewhere nobody chose —
    a residency question, not just a bug. Every store this product uses (R2,
    Backblaze, MinIO) needs an endpoint anyway, and an AWS bucket should name
    its regional one (``https://s3.ap-south-1.amazonaws.com``) for the same
    reason.
    """
    missing = [
        name for name, value in (
            ("S3_ENDPOINT", settings.s3_endpoint),
            ("S3_ACCESS_KEY_ID", settings.s3_access_key_id),
            ("S3_SECRET_ACCESS_KEY", settings.s3_secret_access_key),
            ("S3_BUCKET_NAME", settings.s3_bucket_name),
        )
        if not (value or "").strip()
    ]
    if missing:
        # Names only, never values.
        log.warning("document_storage.not_configured", missing=missing)
        raise StorageUnavailableError("object storage is not configured")
    try:
        await upload_file(settings.s3_bucket_name, key, data, content_type, settings=settings)
    except (BotoCoreError, ClientError) as exc:
        # The key names no person, so it is safe to log; the bytes never are.
        log.warning("document_storage.upload_failed", key=key, error_type=type(exc).__name__)
        # A timeout does not mean the object is absent — the bucket may have
        # stored it and only the reply was lost. The caller rolls the row back,
        # so an object left here would belong to nothing, reachable only by an
        # erasure that happens to list its prefix. Delete it on the way out:
        # deleting a key that was never written is a no-op, and a failure here
        # must not mask the original one.
        try:
            await remove(settings, [key])
        except Exception as cleanup_exc:  # noqa: BLE001 — best effort, logged
            log.warning(
                "document_storage.cleanup_failed", key=key,
                error_type=type(cleanup_exc).__name__,
            )
        raise StorageUnavailableError(type(exc).__name__) from exc


async def signed_download(settings: Settings, key: str, filename: str) -> str:
    """A five-minute link that downloads (never renders) the document."""
    disposition = f'attachment; filename="{safe_filename(filename, filename.rsplit(".", 1)[-1])}"'
    async with s3_client(
        endpoint=settings.s3_endpoint, region=settings.s3_region,
        access_key=settings.s3_access_key_id, secret_key=settings.s3_secret_access_key,
        use_ssl=settings.s3_use_ssl,
    ) as s3:
        url: str = await s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.s3_bucket_name, "Key": key,
                    "ResponseContentDisposition": disposition,
                    "ResponseCacheControl": "no-store"},
            ExpiresIn=PRESIGN_SECONDS,
        )
    return url


async def keys_under(settings: Settings, prefix: str) -> list[str]:
    """Every object under a prefix — including any a failed commit orphaned
    (security review L4), which no row points at."""
    keys: list[str] = []
    async with s3_client(
        endpoint=settings.s3_endpoint, region=settings.s3_region,
        access_key=settings.s3_access_key_id, secret_key=settings.s3_secret_access_key,
        use_ssl=settings.s3_use_ssl,
    ) as s3:
        token: str | None = None
        while True:
            kwargs = {"Bucket": settings.s3_bucket_name, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            page = await s3.list_objects_v2(**kwargs)
            keys += [o["Key"] for o in page.get("Contents", [])]
            if not page.get("IsTruncated"):
                return keys
            token = page.get("NextContinuationToken")


def offer_prefix(company_id: uuid.UUID, offer_id: uuid.UUID) -> str:
    return f"preboarding/{company_id}/{offer_id}/"


async def remove(settings: Settings, keys: list[str]) -> int:
    if not keys:
        return 0
    return await delete_objects({settings.s3_bucket_name: keys}, settings=settings)
