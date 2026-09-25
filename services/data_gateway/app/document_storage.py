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
import io
import re
import uuid
import zipfile
from dataclasses import dataclass

import structlog
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
    """The file is not one we accept, in words a candidate can act on.

    ``code`` is optional and unused by the original preboarding ``check()`` —
    it exists for ``check_corpus()``, whose caller (``app/corpus.py``) needs a
    machine-readable reason from the closed ``failure_code`` vocabulary, not a
    string it would otherwise have to parse.
    """

    def __init__(self, message: str, *, code: str = "unsupported_type") -> None:
        super().__init__(message)
        self.code = code


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


# ---------------------------------------------------------------------------
# PH5-E2: the corpus sniffer — a SEPARATE allow-list from ``sniff()`` above.
#
# The company document library takes PDF, DOCX, TXT and MD; it deliberately
# does NOT take JPEG/PNG (a scanned photo of a policy has no extractable
# text — ``no_text`` refuses it cleanly rather than indexing nothing). Written
# as its own function, inlining the PDF magic-byte check rather than calling
# ``sniff()``, so a future change to the candidate-document allow-list cannot
# silently widen what the corpus accepts, and vice versa. A test
# (``test_corpus_sniff_does_not_widen_candidate_documents``) pins that
# ``sniff()``'s accepted set is unchanged by this module gaining a sibling.
# ---------------------------------------------------------------------------
_DOCX_ZIP_MAGIC = b"PK\x03\x04"
_DOCX_REQUIRED_ENTRY = "word/document.xml"
# Bytes that can never appear in genuine UTF-8 text content HR would upload —
# a NUL means the file is binary, whatever else decodes.
_NUL = b"\x00"


def sniff_corpus(data: bytes, filename: str | None) -> tuple[str, str] | None:
    """(content type, extension) for the CORPUS allow-list, or None.

    PDF and DOCX are recognised by content, exactly like ``sniff()``. Plain
    text and Markdown have no magic bytes to key on, so they are recognised by
    successfully decoding as UTF-8 with no NUL byte, and disambiguated from
    each other by the filename's extension only (never used to override what
    the CONTENT proves for PDF/DOCX).
    """
    if data.startswith(b"%PDF-"):
        return "application/pdf", "pdf"
    if data.startswith(_DOCX_ZIP_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = archive.namelist()
        except zipfile.BadZipFile:
            return None
        if _DOCX_REQUIRED_ENTRY in names:
            return (
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "docx",
            )
        return None
    if _NUL in data:
        return None
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
    name = (filename or "").strip().lower()
    if name.endswith(".md"):
        return "text/markdown", "md"
    return "text/plain", "txt"


def check_corpus(data: bytes, filename: str | None, *, max_bytes: int) -> CheckedFile:
    """``check()``'s corpus counterpart — PDF/DOCX/TXT/MD only, via ``sniff_corpus``.

    Raises the same ``DocumentRejectedError`` shape so callers can share one
    422 handler; ``app/corpus.py`` maps the message onto the closed
    ``failure_code`` vocabulary rather than parsing this string.
    """
    if not data:
        raise DocumentRejectedError("The file is empty.", code="unsupported_type")
    if len(data) > max_bytes:
        raise DocumentRejectedError(
            f"The file is larger than {max_bytes // (1024 * 1024)} MB.", code="too_large"
        )
    kind = sniff_corpus(data, filename)
    if kind is None:
        raise DocumentRejectedError(
            "We take PDF, Word (.docx), plain text and Markdown files.", code="unsupported_type"
        )
    content_type, extension = kind
    if content_type == "application/pdf" and _PDF_ACTIVE.search(_decoded_names(data)):
        raise DocumentRejectedError(
            "This PDF contains scripts or embedded files, which we cannot accept. "
            "Save or print it as a plain PDF and upload that.",
            code="active_content",
        )
    return CheckedFile(
        content_type=content_type, extension=extension, size_bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(), safe_name=safe_filename(filename, extension),
    )


def corpus_storage_key(company_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID) -> str:
    """A key that names no candidate — company documents are staff-authored,
    but the key shape still follows the platform's naming-nobody convention."""
    return f"corpus/{company_id}/{document_id}/{version_id}"


async def store(settings: Settings, key: str, data: bytes, content_type: str) -> None:
    await upload_file(settings.s3_bucket_name, key, data, content_type, settings=settings)


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
