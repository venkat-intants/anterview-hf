"""Document corpus RAG — PH5-E2.

A governed library of a COMPANY's own documents (policies, handbooks, process
notes) that the staff copilot can search. Not a candidate record: nothing here
is keyed on an applicant, and nothing here can make a hiring decision — the
ONLY path from this corpus to a model is ``search_company_documents``
(``app/agents/tools.py``), declared ``data_class="company_scoped"`` and a
``read`` effect, so it can retrieve a passage and cite it, never act on one.
Nothing else in this codebase calls ``search_corpus`` with model-facing
content in mind.

A DOCUMENT IS DATA, NEVER AN INSTRUCTION
Every retrieved passage is untrusted content the model reasons ABOUT, exactly
like a resume or a JD (``shared/agents/guardrails.py``). The defensible claim
is structural: there is no write tool a document could steer
(``ToolEffect`` has no ``"write"`` member), no parameter through which a
document could name another company or widen its own audience (tenancy and
``:is_hr`` come from ``ToolContext``, never from row content), and a
``draft_*`` handler can never even see a corpus passage (enforced by an AST
test, not by convention). "A retrieved document cannot change system
behaviour" is true and is the claim this module makes. "A retrieved document
cannot influence the model's prose" is NOT true and must never be claimed —
see ``detect_injection`` below, which reports rather than silently strips.

INGESTION IS SPLIT DELIBERATELY
Parsing (this module, in the request) is fast, deterministic, and its
failures — encrypted, a scan with no text layer, too large — are the ones a
person must see on the spot. Embedding (``app/reconciliation.py::
_corpus_embed_pass``) is the network-flaky part, so it happens in the
background reconciler, which already owns retry, backoff and parking. A
version is searchable once its last NULL embedding is filled; the reconciler
drains fast, so this is usually well under a minute, never more than one
interval.

TENANCY AND AUDIENCE ARE IN THE QUERY, NEVER A POST-FILTER
``search_corpus`` binds ``company_id`` and ``:is_hr`` as SQL parameters inside
the WHERE clause of the ONE statement that reaches the database. A row a
caller may not see is never fetched, so it cannot be logged, counted, or
accidentally cited — the same discipline as
``app/routers/hr_applicants.py::_semantic_search``, this module's template.

``CORPUS_AUDIENCE_ROLES`` lives HERE, not ``shared/agents/schema.py``
(PH5 Wave 3 lead decision, design doc Q5). Reusing
``DATA_CLASS_ROLES["candidate_pii"]`` for ``hr_only`` would read as a claim
that a company policy document is a NAMED CANDIDATE's personal data, which is
the opposite of what the tag means — it means "not even this company's own
super_admin should read this", not "this identifies a person". The frozensets
below are literals for that reason, with a test pinning that both are subsets
of ``DATA_CLASS_ROLES["company_scoped"]`` so the two vocabularies cannot
silently diverge on WHICH ROLES exist.

Callers commit — every function here does at most ``db.flush()`` (the
``app/job_tasks.py`` convention), except ``app/reconciliation.py``'s embed
pass, which commits incrementally like every other reconciliation pass, so a
failure part-way through a batch does not lose the work already done.
"""

from __future__ import annotations

import asyncio
import bisect
import hashlib
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import structlog
from defusedxml import ElementTree as defused_ET  # type: ignore[import-untyped]
from defusedxml.common import DefusedXmlException
from pypdf import PdfReader
from shared.agents.guardrails import detect_injection, strip_invisible
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app import document_storage as store
from app.config import settings
from app.document_storage import DocumentRejectedError
from app.embedding_client import EmbeddingError, embed_one_remote, to_pgvector_literal
from app.interviewer_scorecards import RequestMeta
from app.models import AuditLog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Closed vocabularies — mirrored by CHECK constraints in the migration, so a
# value neither side recognises fails loudly in dev rather than sitting in the
# database as a string nothing has a sentence for.
# ---------------------------------------------------------------------------
CORPUS_AUDIENCES: tuple[str, ...] = ("all_staff", "hr_only")
CORPUS_DOC_KINDS: tuple[str, ...] = ("policy", "handbook", "process", "jd_library", "other")
CORPUS_VERSION_STATUSES: tuple[str, ...] = ("parsed", "indexing", "indexed", "failed")
CORPUS_CONTENT_TYPES: tuple[str, ...] = (
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "text/plain",
    "text/markdown",
)
CORPUS_EVENTS: tuple[str, ...] = (
    "uploaded", "parsed", "indexed", "failed", "superseded", "audience_changed",
    "expired", "deleted", "reindex_requested",
)

# design §4.6 — the closed failure_code vocabulary. The first nine (well, ten:
# "too_long"/"too_many_chunks" share one row in the design table) are 422s at
# upload with no row ever created; only `embedding_unavailable` is written
# onto an already-created version, when the reconciler parks it.
FAILURE_CODES: tuple[str, ...] = (
    "unsupported_type", "too_large", "active_content", "encrypted", "no_text",
    "parse_timeout", "parse_error", "too_long", "too_many_chunks",
    "quota_exceeded", "embedding_unavailable",
)
FAILURE_SENTENCES: dict[str, str] = {
    "unsupported_type": "We take PDF, Word (.docx), plain text and Markdown files.",
    "too_large": "The file is larger than 10 MB.",
    "active_content": (
        "This PDF contains scripts or embedded files. Save or print it as a plain "
        "PDF and upload that."
    ),
    "encrypted": "This PDF is password-protected. Remove the password and upload it again.",
    "no_text": "This looks like a scan. We read text, not images — upload a text PDF.",
    "parse_timeout": "We could not read this file in time. Try a smaller or simpler document.",
    "parse_error": "We could not read this file.",
    "too_long": "This document is longer than we index (about 400,000 characters). Split it.",
    "too_many_chunks": "This document is longer than we index (about 400,000 characters). Split it.",
    "quota_exceeded": "Your library is full (200 documents). Delete one first.",
    "embedding_unavailable": (
        "Search indexing is unavailable. The document is stored and will be indexed "
        "automatically."
    ),
}
# A validation failure that is NOT a parsing failure_code (design "MY DECISIONS"
# Q3) — kept out of FAILURE_CODES/the migration CHECK, which is closed to what
# the PARSER can produce, not every 422 this module can raise.
_HR_ONLY_REQUIRES_HR_MANAGER = "hr_only_requires_hr_manager"

# Q5 — see the module docstring. Literal frozensets, not a reuse of
# shared.agents.schema.DATA_CLASS_ROLES["candidate_pii"]; the subset test lives
# in tests/unit/test_corpus_audience.py.
CORPUS_AUDIENCE_ROLES: dict[str, frozenset[str]] = {
    "all_staff": frozenset({"hr_manager", "super_admin"}),
    "hr_only": frozenset({"hr_manager"}),
}


def _may_read(role: str, audience: str) -> bool:
    """Whether *role* may read a document tagged *audience*.

    The ONE predicate ``get_document``/``list_documents``/``download_url``/
    ``update_document``/``delete_document`` all use — so "can this caller see
    this document at all" is answered in exactly one place. HIGH-2 (security
    review): every write path on an existing document must call this before
    doing anything else, not only the read paths — a caller who cannot read a
    document must not be able to widen its audience (``update_document``) or
    learn it existed by successfully deleting it (``delete_document``).
    """
    return role in CORPUS_AUDIENCE_ROLES.get(audience, frozenset())

# Hybrid weighting — identical to hr_applicants._semantic_search, the template
# this module's search follows. Not re-tuned per design §7 (cut: "revisit with
# real queries").
_SEMANTIC_WEIGHT = 0.7
_LEXICAL_WEIGHT = 0.3
_MIN_SEARCH_LIMIT = 1
_MAX_SEARCH_LIMIT = 6
_DEFAULT_SEARCH_LIMIT = 4
# A passage handed to the model is capped well inside
# shared.agents.registry.MAX_TOOL_CONTENT_CHARS (12_000): six passages at 900
# chars each is 5.4k, leaving headroom for the JSON wrapper and citations.
_PASSAGE_CHARS = 900

_PARSE_TIMEOUT_SECONDS = 30.0
_DOCX_MAX_ENTRIES = 200
# 8 MB (MEDIUM-5, security review), down from an initial 40 MB: the char cap
# above (400k) means a legitimate document's extracted text is a fraction of
# that anyway, and 40 MB was a "generous" number nobody had weighed against
# the parse pool — several uploads at once, each briefly holding 40 MB of
# decompressed XML in a worker thread, is a memory story on its own.
_DOCX_MAX_UNCOMPRESSED_BYTES = 8 * 1024 * 1024
_NO_TEXT_MIN_CHARS = 50  # PDF only (design Q7) — a 30-char TXT file is legitimate.

_CHUNK_CHARS = 1200
_CHUNK_OVERLAP = 150

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _qn(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


class CorpusError(Exception):
    """Refused. Carries the HTTP status, a machine-readable ``code`` (from
    ``FAILURE_CODES`` for a parsing failure, or a small set of other 422
    reasons this module raises directly) and a sentence for a person."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _corpus_error(code: str, *, status_code: int = 422) -> CorpusError:
    return CorpusError(status_code, code, FAILURE_SENTENCES.get(code, "This document was refused."))


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractedDocument:
    text: str
    page_count: int | None
    # Char offset into `text` where each page begins (PDF only, empty
    # otherwise). page_offsets[0] == 0 for a document with at least one page.
    page_offsets: list[int] = field(default_factory=list)


def _extract_pdf_sync(data: bytes) -> ExtractedDocument:
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — any malformed PDF is `parse_error`
        raise _corpus_error("parse_error") from exc
    if reader.is_encrypted:
        raise _corpus_error("encrypted")
    pages: list[str] = []
    offsets: list[int] = []
    pos = 0
    try:
        for page in reader.pages:
            extracted = page.extract_text() or ""
            offsets.append(pos)
            pages.append(extracted)
            pos += len(extracted) + 1  # +1 for the "\n" joiner below
    except Exception as exc:  # noqa: BLE001 — pypdf raises assorted types on bad content
        raise _corpus_error("parse_error") from exc
    return ExtractedDocument(text="\n".join(pages), page_count=len(reader.pages), page_offsets=offsets)


def _docx_paragraph_lines(root: Any) -> list[str]:
    """Every non-empty paragraph's text, Word heading styles normalised to a
    leading ``"# "`` so the SAME heading detector used for Markdown also
    carries a DOCX heading — read from ``w:pStyle`` in the raw XML, which is
    available without a DOCX library."""
    lines: list[str] = []
    for para in root.iter(_qn("p")):
        runs = [node.text or "" for node in para.iter(_qn("t"))]
        line = "".join(runs).strip()
        if not line:
            continue
        ppr = para.find(_qn("pPr"))
        style = ppr.find(_qn("pStyle")) if ppr is not None else None
        style_val = style.get(_qn("val")) if style is not None else None
        if style_val and re.match(r"(?i)^(heading|title)", style_val):
            lines.append(f"# {line}")
        else:
            lines.append(line)
    return lines


def _extract_docx_sync(data: bytes) -> ExtractedDocument:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise _corpus_error("parse_error") from exc
    infos = archive.infolist()
    if len(infos) > _DOCX_MAX_ENTRIES:
        raise _corpus_error("parse_error")
    if sum(i.file_size for i in infos) > _DOCX_MAX_UNCOMPRESSED_BYTES:
        raise _corpus_error("parse_error")

    names = archive.namelist()
    targets = sorted(
        n for n in names
        if n == "word/document.xml" or re.fullmatch(r"word/(header|footer)\d*\.xml", n)
    )
    if "word/document.xml" not in targets:
        raise _corpus_error("parse_error")

    lines: list[str] = []
    budget = _DOCX_MAX_UNCOMPRESSED_BYTES
    for name in targets:
        with archive.open(name) as fh:
            chunks: list[bytes] = []
            total = 0
            while True:
                block = fh.read(65536)
                if not block:
                    break
                total += len(block)
                budget -= len(block)
                if budget < 0:
                    # The declared size lied (or a maliciously crafted local
                    # header understated it) — abort rather than trust it.
                    raise _corpus_error("parse_error")
                chunks.append(block)
        raw = b"".join(chunks)
        try:
            root = defused_ET.fromstring(raw)
        except (defused_ET.ParseError, DefusedXmlException) as exc:
            # Includes an XXE/entity-expansion attempt: defusedxml refuses the
            # DOCTYPE outright rather than expanding or fetching anything, so
            # this is the exact "no external fetch, no expansion" outcome the
            # design's XXE test asserts — surfaced as a clean refusal.
            raise _corpus_error("parse_error") from exc
        lines.extend(_docx_paragraph_lines(root))

    return ExtractedDocument(text="\n\n".join(lines), page_count=None, page_offsets=[])


def _extract_text_sync(data: bytes) -> ExtractedDocument:
    return ExtractedDocument(text=data.decode("utf-8-sig", errors="replace"), page_count=None, page_offsets=[])


async def extract_text(data: bytes, content_type: str) -> ExtractedDocument:
    """Parse *data* off the event loop, with a hard wall-clock timeout.

    Raises ``CorpusError`` for every failure this module recognises —
    ``parse_timeout`` here, everything else from the per-format extractor.
    """
    if content_type == "application/pdf":
        fn = _extract_pdf_sync
    elif content_type == CORPUS_CONTENT_TYPES[1]:
        fn = _extract_docx_sync
    elif content_type in ("text/plain", "text/markdown"):
        fn = _extract_text_sync
    else:
        raise _corpus_error("unsupported_type")
    try:
        return await asyncio.wait_for(asyncio.to_thread(fn, data), timeout=_PARSE_TIMEOUT_SECONDS)
    except TimeoutError as exc:
        raise _corpus_error("parse_timeout") from exc


# ---------------------------------------------------------------------------
# Chunking — pure, unit-tested independently of the DB (design §4.8).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    content: str
    char_count: int
    content_sha256: str
    page_from: int | None
    page_to: int | None
    heading: str | None


_HEADING_RE = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.MULTILINE)
_BLANK_LINE_RE = re.compile(r"\n[ \t]*\n+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _heading_offsets(text_: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(1).strip()) for m in _HEADING_RE.finditer(text_)]


def _split_into_blocks(text_: str) -> list[tuple[int, str]]:
    """(start_offset, block_text) for each blank-line-separated block."""
    blocks: list[tuple[int, str]] = []
    pos = 0
    for m in _BLANK_LINE_RE.finditer(text_):
        block = text_[pos:m.start()]
        if block.strip():
            blocks.append((pos, block))
        pos = m.end()
    tail = text_[pos:]
    if tail.strip():
        blocks.append((pos, tail))
    return blocks


def _split_on_whitespace(start: int, text_: str) -> list[tuple[int, str]]:
    """Last resort: hard-cap length, but always breaking at a space — a chunk
    boundary must never land mid-word (design §4.8)."""
    pieces: list[tuple[int, str]] = []
    pos = 0
    n = len(text_)
    while pos < n:
        end = min(pos + _CHUNK_CHARS, n)
        if end < n:
            sp = text_.rfind(" ", pos, end)
            if sp > pos:
                end = sp
        pieces.append((start + pos, text_[pos:end]))
        pos = end
        while pos < n and text_[pos] == " ":
            pos += 1
    return pieces


def _split_oversized(start: int, block: str) -> list[tuple[int, str]]:
    """A block bigger than one chunk, split on sentence boundaries first, then
    (only for a "sentence" that is itself too long) on whitespace."""
    if len(block) <= _CHUNK_CHARS:
        return [(start, block)]
    bounds = sorted({0, len(block)} | {m.end() for m in _SENTENCE_SPLIT_RE.finditer(block)})
    pieces: list[tuple[int, str]] = []
    cur = ""
    cur_start = 0
    for i in range(len(bounds) - 1):
        seg = block[bounds[i]:bounds[i + 1]]
        if cur and len(cur) + len(seg) > _CHUNK_CHARS:
            pieces.append((start + cur_start, cur))
            cur, cur_start = seg, bounds[i]
        else:
            if not cur:
                cur_start = bounds[i]
            cur += seg
    if cur.strip():
        pieces.append((start + cur_start, cur))

    final: list[tuple[int, str]] = []
    for piece_start, piece in pieces:
        if len(piece) <= _CHUNK_CHARS:
            final.append((piece_start, piece))
        else:
            final.extend(_split_on_whitespace(piece_start, piece))
    return final


def _tail_overlap_parts(parts: list[tuple[int, str]], n: int) -> list[tuple[int, str]]:
    """The trailing ~*n* characters of a chunk's ordered ``(offset, text)``
    fragments, returned as fragments of their OWN rather than a single
    string (code review SHOULD finding).

    A chunk that carries overlap from the previous one used to be finalised
    with ``cur_start`` pointing at the NEW piece's offset — not the overlap
    text's true origin, which can sit on an earlier page — and with
    ``end_offset`` inflated by the overlap's own length on top of that wrong
    start. Both ``page_from`` and ``page_to`` could then name the wrong page.
    Returning fragments (not a re-joined, whitespace-normalised string) means
    the caller always finalises a chunk from its ACTUAL origin offsets,
    overlap included, rather than reconstructing them after the fact.

    Backs off to the next space in the fragment it truncates, so the overlap
    never starts mid-word — the same rule the single-string version applied.
    """
    if n <= 0 or not parts:
        return []
    if sum(len(t) for _, t in parts) <= n:
        return list(parts)
    out: list[tuple[int, str]] = []
    remaining = n
    for offset, txt in reversed(parts):
        if remaining <= 0:
            break
        if len(txt) <= remaining:
            out.append((offset, txt))
            remaining -= len(txt)
        else:
            tail = txt[-remaining:]
            sp = tail.find(" ")
            if 0 <= sp < len(tail) - 1:
                tail = tail[sp + 1:]
            out.append((offset + len(txt) - len(tail), tail))
            remaining = 0
    out.reverse()
    return out


def _page_for_offset(page_offsets: list[int], offset: int) -> int | None:
    if not page_offsets:
        return None
    idx = max(0, bisect.bisect_right(page_offsets, offset) - 1)
    return idx + 1  # 1-indexed


def chunk_document(doc: ExtractedDocument, *, max_chunks: int) -> list[Chunk]:
    """~1,200-char chunks with ~150-char overlap, split on blank lines then
    sentence boundaries, never mid-word (design §4.2/§4.8). Carries the
    nearest preceding heading and, for a PDF, the page range — computed from
    each chunk's own ordered fragments (offset, text), including its overlap
    fragment where one was carried over, never from a reconstructed string."""
    text_ = doc.text
    if not text_.strip():
        return []

    headings = _heading_offsets(text_)
    heading_starts = [h[0] for h in headings]

    def heading_for(offset: int) -> str | None:
        if not heading_starts:
            return None
        idx = bisect.bisect_right(heading_starts, offset) - 1
        return headings[idx][1] if idx >= 0 else None

    pieces: list[tuple[int, str]] = []
    for start, block in _split_into_blocks(text_):
        pieces.extend(_split_oversized(start, block))

    def finalise(ordinal: int, parts: list[tuple[int, str]]) -> Chunk:
        body = "\n\n".join(t for _, t in parts)
        stripped = body.strip()
        first_offset = parts[0][0]
        last_offset, last_text = parts[-1]
        last_end = last_offset + len(last_text) - 1
        return Chunk(
            ordinal=ordinal, content=stripped, char_count=len(stripped),
            content_sha256=hashlib.sha256(stripped.encode("utf-8")).hexdigest(),
            page_from=_page_for_offset(doc.page_offsets, first_offset),
            page_to=_page_for_offset(doc.page_offsets, max(first_offset, last_end)),
            heading=heading_for(first_offset),
        )

    chunks: list[Chunk] = []
    cur_parts: list[tuple[int, str]] = []
    cur_len = 0
    for start, piece in pieces:
        joined_len = cur_len + (2 if cur_parts else 0) + len(piece)
        if cur_parts and joined_len > _CHUNK_CHARS:
            if len(chunks) >= max_chunks:
                raise _corpus_error("too_many_chunks")
            chunks.append(finalise(len(chunks), cur_parts))
            overlap_parts = _tail_overlap_parts(cur_parts, _CHUNK_OVERLAP)
            cur_parts = [*overlap_parts, (start, piece)] if overlap_parts else [(start, piece)]
            cur_len = sum(len(t) for _, t in cur_parts) + 2 * (len(cur_parts) - 1)
        else:
            cur_parts.append((start, piece))
            cur_len = joined_len
    if cur_parts and "\n\n".join(t for _, t in cur_parts).strip():
        if len(chunks) >= max_chunks:
            raise _corpus_error("too_many_chunks")
        chunks.append(finalise(len(chunks), cur_parts))
    return chunks


# ---------------------------------------------------------------------------
# Injection defence for retrieved passages (design §4.7)
# ---------------------------------------------------------------------------

# Control characters other than \n / \t. The zero-width/bidi evasions are
# ``shared.agents.guardrails.strip_invisible`` (LOW-2, security review: that
# function did not exist when this module was first written, so a local copy
# was added; it exists now, and this is the one definition).
_CONTROL_CHARS = {c: None for c in range(0x00, 0x20) if c not in (0x09, 0x0A)}


def _strip_control_chars(text_: str) -> str:
    """The remaining cheap evasion ``strip_invisible`` does not cover: raw
    control bytes other than newline/tab."""
    return text_.translate(_CONTROL_CHARS)


def _neutralise_fence_markers(text_: str) -> str:
    """A passage cannot forge the ``<<<PASSAGE ...>>>`` fence the wire-format
    layer wraps around it (design §4.7.4): literal fence sequences inside the
    passage are neutralised here, at the source, so this holds regardless of
    which adapter builds the outer fence."""
    return text_.replace("<<<", "‹‹‹").replace(">>>", "›››")


def _prepare_passage_text(content: str) -> str:
    cleaned = _strip_control_chars(strip_invisible(content))
    return _neutralise_fence_markers(cleaned)[:_PASSAGE_CHARS]


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------


def _audit(
    db: AsyncSession, *, actor: uuid.UUID | None, action: str, resource_id: uuid.UUID,
    details: dict[str, Any], meta: RequestMeta, actor_type: str = "user",
) -> None:
    """Facts only — never a title, a file name, or extracted text (a title is
    HR's own free-text label and a corpus passage is company prose, either of
    which is worth keeping out of a log line as a matter of course)."""
    db.add(AuditLog(
        actor_id=actor, actor_type=actor_type, action=action, resource_type="corpus_document",
        resource_id=resource_id, details=details, ip_address=meta.ip_address,
        user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC),
    ))


async def _event(
    db: AsyncSession, *, company_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID | None,
    action: str, actor_type: str, actor: uuid.UUID | None, details: dict[str, Any] | None = None,
) -> None:
    await db.execute(
        text(
            "INSERT INTO corpus_events (id, company_id, document_id, version_id, action,"
            " actor_type, actor_user_id, details) VALUES (gen_random_uuid(), :c, :d, :v, :a,"
            " :t, :u, CAST(:j AS jsonb))"
        ),
        {"c": company_id, "d": document_id, "v": version_id, "a": action, "t": actor_type,
         "u": actor, "j": json.dumps(details or {}, default=str)},
    )


async def _document_row(db: AsyncSession, *, company_id: uuid.UUID, document_id: uuid.UUID) -> Any:
    return (
        await db.execute(
            text(
                "SELECT * FROM corpus_documents WHERE id = :i AND company_id = :c"
                "   AND deleted_at IS NULL"
            ),
            {"i": document_id, "c": company_id},
        )
    ).mappings().first()


def _document_out(row: Any, *, version_row: Any = None) -> dict[str, Any]:
    out = {
        "id": str(row["id"]), "title": row["title"], "audience": row["audience"],
        "doc_kind": row["doc_kind"],
        "expires_on": row["expires_on"].isoformat() if row["expires_on"] else None,
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }
    if version_row is not None:
        out.update({
            "version": version_row["version"], "status": version_row["status"],
            "failure_code": version_row["failure_code"],
            "original_name": version_row["original_name"],
            "content_type": version_row["content_type"], "size_bytes": version_row["size_bytes"],
            "page_count": version_row["page_count"], "chunk_count": version_row["chunk_count"],
            "injection_markers": version_row["injection_markers"],
            "uploaded_at": version_row["uploaded_at"],
            # Only present when the caller's query joined `users` (list_documents
            # / get_document) -- absent (via .get, not []) for the plain
            # `SELECT * FROM corpus_document_versions` version_row that
            # ingest_document/add_version pass, which have no name to give.
            # Null when the uploader's account no longer exists (LEFT JOIN).
            "uploaded_by_name": version_row.get("uploaded_by_name"),
        })
    return out


# ---------------------------------------------------------------------------
# Quotas
# ---------------------------------------------------------------------------


async def _check_document_quota(db: AsyncSession, company_id: uuid.UUID) -> None:
    count = await db.scalar(
        text("SELECT count(*) FROM corpus_documents WHERE company_id = :c AND deleted_at IS NULL"),
        {"c": company_id},
    )
    if int(count or 0) >= settings.corpus_max_documents_per_company:
        raise _corpus_error("quota_exceeded")


async def _check_chunk_quota(db: AsyncSession, company_id: uuid.UUID, incoming: int) -> None:
    """Code review CONSIDER: counts only the CURRENT, searchable generation of
    each document — ``superseded_at IS NULL`` excludes old versions kept
    around for the 180-day citation-resolution window (design §4.4), which
    are real rows with real embeddings but are not retrievable by anyone.
    Without this, a company that fixes typos by re-uploading accumulates
    quota against history nobody can search, on nothing but edit frequency.
    Storage/embedding spend for that history is bounded separately, by
    ``corpus_superseded_retention_days`` auto-purging it — this quota is
    about what a company can actively SEARCH, not every row it has ever
    written."""
    existing = await db.scalar(
        text(
            "SELECT COALESCE(SUM(chunk_count), 0) FROM corpus_document_versions"
            " WHERE company_id = :c AND redacted_at IS NULL AND superseded_at IS NULL"
        ),
        {"c": company_id},
    )
    if int(existing or 0) + incoming > settings.corpus_max_chunks_per_company:
        raise _corpus_error("quota_exceeded")


# ---------------------------------------------------------------------------
# Ingestion
# ---------------------------------------------------------------------------


async def _sniff_and_extract(data: bytes, filename: str | None) -> tuple[str, str, ExtractedDocument]:
    if len(data) > settings.corpus_document_max_bytes:
        raise _corpus_error("too_large")
    try:
        checked = store.check_corpus(data, filename, max_bytes=settings.corpus_document_max_bytes)
    except DocumentRejectedError as exc:
        raise CorpusError(422, exc.code, str(exc)) from exc
    extracted = await extract_text(data, checked.content_type)
    if checked.content_type == "application/pdf" and len(extracted.text.strip()) < _NO_TEXT_MIN_CHARS:
        raise _corpus_error("no_text")
    if len(extracted.text) > settings.corpus_max_chars_per_document:
        raise _corpus_error("too_long")
    return checked.content_type, checked.safe_name, extracted


async def ingest_document(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, actor_role: str,
    title: str, audience: str, doc_kind: str, expires_on: date | None, attested: bool,
    data: bytes, filename: str | None, meta: RequestMeta,
) -> dict[str, Any]:
    """Create a new corpus document with version 1. Caller commits.

    Raises ``CorpusError`` for every refusal; the first nine failure_codes
    (design §4.6) create NO row at all — every check below that can raise runs
    before the first INSERT.
    """
    title = (title or "").strip()
    if not 1 <= len(title) <= 200:
        raise CorpusError(422, "invalid_title", "Give the document a title of up to 200 characters.")
    if audience not in CORPUS_AUDIENCES:
        raise CorpusError(422, "invalid_audience", "Choose 'All staff' or 'HR only'.")
    if doc_kind not in CORPUS_DOC_KINDS:
        raise CorpusError(422, "invalid_kind", "Choose a valid document type.")
    if audience == "hr_only" and actor_role not in CORPUS_AUDIENCE_ROLES["hr_only"]:
        # design "MY DECISIONS" Q3 — a super_admin cannot create a document
        # they could not then read back.
        raise CorpusError(422, _HR_ONLY_REQUIRES_HR_MANAGER,
                          "HR-only documents are created by HR managers.")
    if not attested:
        raise CorpusError(
            422, "attestation_required",
            "Confirm this is a company document, not a record about a candidate.",
        )

    await _check_document_quota(db, company_id)
    content_type, safe_name, extracted = await _sniff_and_extract(data, filename)
    chunks = chunk_document(extracted, max_chunks=settings.corpus_max_chunks_per_document)
    await _check_chunk_quota(db, company_id, len(chunks))

    document_id = uuid.uuid4()
    version_id = uuid.uuid4()
    key = store.corpus_storage_key(company_id, document_id, version_id)
    sha256 = hashlib.sha256(data).hexdigest()
    injection_markers = sum(len(detect_injection(c.content)) for c in chunks)
    now = datetime.now(tz=UTC)

    await db.execute(
        text(
            "INSERT INTO corpus_documents (id, company_id, title, audience, doc_kind,"
            " expires_on, created_by_user_id, created_at, updated_at)"
            " VALUES (:i, :c, :t, :a, :k, :e, :u, :now, :now)"
        ),
        {"i": document_id, "c": company_id, "t": title, "a": audience, "k": doc_kind,
         "e": expires_on, "u": actor, "now": now},
    )
    await db.execute(
        text(
            "INSERT INTO corpus_document_versions (id, company_id, document_id, version, status,"
            " storage_key, original_name, content_type, size_bytes, sha256, page_count,"
            " char_count, chunk_count, injection_markers, uploaded_by_user_id, uploaded_at)"
            " VALUES (:i, :c, :d, 1, 'parsed', :k, :n, :ct, :sz, :sha, :pc, :cc, :chc, :im, :u, :now)"
        ),
        {"i": version_id, "c": company_id, "d": document_id, "k": key, "n": safe_name,
         "ct": content_type, "sz": len(data), "sha": sha256, "pc": extracted.page_count,
         "cc": len(extracted.text), "chc": len(chunks), "im": injection_markers, "u": actor,
         "now": now},
    )
    for chunk in chunks:
        await db.execute(
            text(
                "INSERT INTO corpus_chunks (id, company_id, document_id, version_id, ordinal,"
                " page_from, page_to, heading, content, char_count, content_sha256, created_at)"
                " VALUES (gen_random_uuid(), :c, :d, :v, :o, :pf, :pt, :h, :ct, :cc, :sha, :now)"
            ),
            {"c": company_id, "d": document_id, "v": version_id, "o": chunk.ordinal,
             "pf": chunk.page_from, "pt": chunk.page_to, "h": chunk.heading,
             "ct": chunk.content, "cc": chunk.char_count, "sha": chunk.content_sha256, "now": now},
        )
    await db.execute(
        text("UPDATE corpus_documents SET current_version_id = :v WHERE id = :i"),
        {"v": version_id, "i": document_id},
    )
    await store.store(settings, key, data, content_type)

    await _event(db, company_id=company_id, document_id=document_id, version_id=version_id,
                action="uploaded", actor_type="user", actor=actor)
    await _event(db, company_id=company_id, document_id=document_id, version_id=version_id,
                action="parsed", actor_type="user", actor=actor,
                details={"chunk_count": len(chunks), "injection_markers": injection_markers})
    _audit(
        db, actor=actor, action="corpus.document.uploaded", resource_id=document_id,
        details={"company_id": str(company_id), "version_id": str(version_id),
                 "audience": audience, "doc_kind": doc_kind, "content_type": content_type,
                 "size_bytes": len(data), "chunk_count": len(chunks), "attested": attested},
        meta=meta,
    )

    row = await _document_row(db, company_id=company_id, document_id=document_id)
    version_row = (
        await db.execute(text("SELECT * FROM corpus_document_versions WHERE id = :i"), {"i": version_id})
    ).mappings().one()
    return _document_out(row, version_row=version_row)


async def add_version(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, actor_role: str,
    document_id: uuid.UUID, data: bytes, filename: str | None, meta: RequestMeta,
) -> dict[str, Any]:
    """Replace-upload: a NEW version row, never an overwrite. Caller commits."""
    doc = await _document_row(db, company_id=company_id, document_id=document_id)
    if doc is None:
        raise CorpusError(404, "not_found", "Document not found.")
    if doc["audience"] == "hr_only" and actor_role not in CORPUS_AUDIENCE_ROLES["hr_only"]:
        raise CorpusError(422, _HR_ONLY_REQUIRES_HR_MANAGER,
                          "HR-only documents are created by HR managers.")

    content_type, safe_name, extracted = await _sniff_and_extract(data, filename)
    chunks = chunk_document(extracted, max_chunks=settings.corpus_max_chunks_per_document)
    await _check_chunk_quota(db, company_id, len(chunks))

    prev_version_id = doc["current_version_id"]
    next_version = await db.scalar(
        text("SELECT COALESCE(max(version), 0) + 1 FROM corpus_document_versions WHERE document_id = :d"),
        {"d": document_id},
    )
    version_id = uuid.uuid4()
    key = store.corpus_storage_key(company_id, document_id, version_id)
    sha256 = hashlib.sha256(data).hexdigest()
    injection_markers = sum(len(detect_injection(c.content)) for c in chunks)
    now = datetime.now(tz=UTC)

    await db.execute(
        text(
            "INSERT INTO corpus_document_versions (id, company_id, document_id, version, status,"
            " storage_key, original_name, content_type, size_bytes, sha256, page_count,"
            " char_count, chunk_count, injection_markers, uploaded_by_user_id, uploaded_at)"
            " VALUES (:i, :c, :d, :ver, 'parsed', :k, :n, :ct, :sz, :sha, :pc, :cc, :chc, :im, :u, :now)"
        ),
        {"i": version_id, "c": company_id, "d": document_id, "ver": next_version, "k": key,
         "n": safe_name, "ct": content_type, "sz": len(data), "sha": sha256,
         "pc": extracted.page_count, "cc": len(extracted.text), "chc": len(chunks),
         "im": injection_markers, "u": actor, "now": now},
    )
    for chunk in chunks:
        await db.execute(
            text(
                "INSERT INTO corpus_chunks (id, company_id, document_id, version_id, ordinal,"
                " page_from, page_to, heading, content, char_count, content_sha256, created_at)"
                " VALUES (gen_random_uuid(), :c, :d, :v, :o, :pf, :pt, :h, :ct, :cc, :sha, :now)"
            ),
            {"c": company_id, "d": document_id, "v": version_id, "o": chunk.ordinal,
             "pf": chunk.page_from, "pt": chunk.page_to, "h": chunk.heading,
             "ct": chunk.content, "cc": chunk.char_count, "sha": chunk.content_sha256, "now": now},
        )
    if prev_version_id is not None:
        await db.execute(
            text(
                "UPDATE corpus_document_versions SET superseded_at = :now, superseded_by_id = :v"
                " WHERE id = :p"
            ),
            {"now": now, "v": version_id, "p": prev_version_id},
        )
        await _event(db, company_id=company_id, document_id=document_id, version_id=prev_version_id,
                    action="superseded", actor_type="user", actor=actor,
                    details={"superseded_by_version": next_version})
    await db.execute(
        text("UPDATE corpus_documents SET current_version_id = :v, updated_at = :now WHERE id = :i"),
        {"v": version_id, "now": now, "i": document_id},
    )
    await store.store(settings, key, data, content_type)

    await _event(db, company_id=company_id, document_id=document_id, version_id=version_id,
                action="uploaded", actor_type="user", actor=actor,
                details={"version": next_version})
    await _event(db, company_id=company_id, document_id=document_id, version_id=version_id,
                action="parsed", actor_type="user", actor=actor,
                details={"chunk_count": len(chunks), "injection_markers": injection_markers})
    _audit(
        db, actor=actor, action="corpus.document.replaced", resource_id=document_id,
        details={"company_id": str(company_id), "version_id": str(version_id),
                 "version": next_version, "content_type": content_type, "size_bytes": len(data),
                 "chunk_count": len(chunks)},
        meta=meta,
    )

    row = await _document_row(db, company_id=company_id, document_id=document_id)
    version_row = (
        await db.execute(text("SELECT * FROM corpus_document_versions WHERE id = :i"), {"i": version_id})
    ).mappings().one()
    return _document_out(row, version_row=version_row)


async def update_document(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, actor_role: str,
    document_id: uuid.UUID, audience: str, expires_on: date | None, meta: RequestMeta,
) -> dict[str, Any]:
    """Edit audience and expiry. Both are the document's FULL new state (the
    ``PUT /rounds/{id}/task`` convention in this service) — ``expires_on=None``
    means "no expiry", not "leave unchanged"; the router reads the current
    values first if the caller means to change only one."""
    doc = await _document_row(db, company_id=company_id, document_id=document_id)
    if doc is None:
        raise CorpusError(404, "not_found", "Document not found.")
    if not _may_read(actor_role, doc["audience"]):
        # HIGH-2 (security review): a caller who cannot read the CURRENT
        # audience must not be able to widen it into one it can. 404, not
        # 403 — matching get_document's refusal, so the response does not
        # disclose that a document it cannot see exists at all.
        raise CorpusError(404, "not_found", "Document not found.")

    if audience not in CORPUS_AUDIENCES:
        raise CorpusError(422, "invalid_audience", "Choose 'All staff' or 'HR only'.")
    if audience == "hr_only" and actor_role not in CORPUS_AUDIENCE_ROLES["hr_only"]:
        raise CorpusError(422, _HR_ONLY_REQUIRES_HR_MANAGER,
                          "HR-only documents are created by HR managers.")

    new_audience = audience
    new_expires = expires_on
    changed_audience = new_audience != doc["audience"]
    await db.execute(
        text(
            "UPDATE corpus_documents SET audience = :a, expires_on = :e, updated_at = now()"
            " WHERE id = :i AND company_id = :c"
        ),
        {"a": new_audience, "e": new_expires, "i": document_id, "c": company_id},
    )
    if changed_audience:
        await _event(db, company_id=company_id, document_id=document_id, version_id=None,
                    action="audience_changed", actor_type="user", actor=actor,
                    details={"from": doc["audience"], "to": new_audience})
        _audit(
            db, actor=actor, action="corpus.document.edited", resource_id=document_id,
            details={"company_id": str(company_id), "audience": new_audience}, meta=meta,
        )
    row = await _document_row(db, company_id=company_id, document_id=document_id)
    return _document_out(row)


async def _purge_document_content(
    db: AsyncSession, *, company_id: uuid.UUID, document_id: uuid.UUID, action: str,
    actor: uuid.UUID | None, actor_type: str,
) -> None:
    """Delete every non-redacted version's chunks and stored object for one
    document, then blank and redact those version rows. Shared by
    ``delete_document`` (immediate, HR-initiated) and the expiry half of
    ``purge_corpus`` (nightly). Objects first, rows second — the
    ``app/retention.py`` ordering."""
    versions = (
        await db.execute(
            text(
                "SELECT id, storage_key, version FROM corpus_document_versions"
                " WHERE document_id = :d AND company_id = :c AND redacted_at IS NULL"
            ),
            {"d": document_id, "c": company_id},
        )
    ).mappings().all()
    keys = [v["storage_key"] for v in versions if v["storage_key"]]
    if keys:
        removed = await store.remove(settings, keys)
        if removed != len(keys):
            raise RuntimeError(
                f"corpus purge removed {removed} of {len(keys)} objects for document "
                f"{document_id}; refusing to redact the rows that reference them."
            )
    for v in versions:
        await db.execute(
            text("DELETE FROM corpus_chunks WHERE version_id = :v AND company_id = :c"),
            {"v": v["id"], "c": company_id},
        )
        await db.execute(
            text(
                "UPDATE corpus_document_versions SET storage_key = NULL, original_name = NULL,"
                " redacted_at = now() WHERE id = :i"
            ),
            {"i": v["id"]},
        )
        await _event(db, company_id=company_id, document_id=document_id, version_id=v["id"],
                    action=action, actor_type=actor_type, actor=actor,
                    details={"version": v["version"]})


async def delete_document(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, actor_role: str,
    document_id: uuid.UUID, meta: RequestMeta,
) -> None:
    """Immediate, complete purge — no grace window (design §4.5: "leave active
    retrieval" is strongest proven by the row being gone, not filtered)."""
    doc = await _document_row(db, company_id=company_id, document_id=document_id)
    if doc is None:
        raise CorpusError(404, "not_found", "Document not found.")
    if not _may_read(actor_role, doc["audience"]):
        # HIGH-2 (security review): a caller must not be able to delete a
        # document it cannot read, nor learn — via a 204 instead of a 404 —
        # that one exists. Same 404-not-403 shape as get_document/update_document.
        raise CorpusError(404, "not_found", "Document not found.")
    await _purge_document_content(
        db, company_id=company_id, document_id=document_id, action="deleted",
        actor=actor, actor_type="user",
    )
    await db.execute(
        text("UPDATE corpus_documents SET deleted_at = now(), updated_at = now() WHERE id = :i"),
        {"i": document_id},
    )
    _audit(db, actor=actor, action="corpus.document.deleted", resource_id=document_id,
          details={"company_id": str(company_id)}, meta=meta)


# Mirrors app/reconciliation.py::KIND_CORPUS_CHUNK exactly. Not imported —
# reconciliation.py already imports from this module (mark_version_failed,
# mark_versions_indexing, mark_version_indexed), so importing the constant
# back would make the two modules import each other. A test pins the two
# string literals against drift, on the MIN_ANSWERS_TO_SCORE precedent
# (reconciliation.py mirrors that one from the interview_core worker for the
# same reason: no shared code between the two, but the value must agree).
_RECONCILIATION_KIND_CORPUS_CHUNK = "corpus_chunk"


async def reindex_document(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, actor_role: str,
    document_id: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    """Retry a version parked at 'failed' after the reconciler's embed pass
    gave up on it (design gap, code review 2026-09-23): ``_corpus_embed_pass``
    only ever selects chunks whose version is ``'parsed'``/``'indexing'``, so
    once ``mark_version_failed`` writes ``status='failed'`` there was no path
    back to being indexed, ever — a transient embedder outage permanently
    bricked the document, and the version's own ``embedding_unavailable``
    sentence ("will be indexed automatically") was false for it. This resets
    the CURRENT version to ``'parsed'``, clears its ``failure_code``, and
    deletes the ``reconciliation_state`` row ``_record_failure``/
    ``mark_version_failed`` wrote (the same bookkeeping ``_clear_state`` would
    clear on a genuine success) — without that, the row's ``gave_up_at`` would
    still be set and the next pass's own "not backing off, not given up"
    predicate would skip it right past.

    Applies ONLY to the document's current version — an old, superseded
    version that happened to fail years ago is not this button's business,
    and superseded versions are never retrieved anyway.
    """
    doc = await _document_row(db, company_id=company_id, document_id=document_id)
    if doc is None:
        raise CorpusError(404, "not_found", "Document not found.")
    if not _may_read(actor_role, doc["audience"]):
        # Same 404-not-403 shape as get_document/update_document/delete_document.
        raise CorpusError(404, "not_found", "Document not found.")

    version_id = doc["current_version_id"]
    version = (
        (
            await db.execute(
                text("SELECT id, version, status FROM corpus_document_versions WHERE id = :v"),
                {"v": version_id},
            )
        ).mappings().first()
        if version_id is not None
        else None
    )
    if version is None or version["status"] != "failed":
        raise CorpusError(
            409, "not_failed", "This document is not stuck — there is nothing to reindex.",
        )

    await db.execute(
        text(
            "UPDATE corpus_document_versions SET status = 'parsed', failure_code = NULL"
            " WHERE id = :v"
        ),
        {"v": version_id},
    )
    await db.execute(
        text("DELETE FROM reconciliation_state WHERE kind = :kind AND ref_id = :ref"),
        {"kind": _RECONCILIATION_KIND_CORPUS_CHUNK, "ref": version_id},
    )
    await _event(db, company_id=company_id, document_id=document_id, version_id=version_id,
                action="reindex_requested", actor_type="user", actor=actor)
    _audit(
        db, actor=actor, action="corpus.document.reindex_requested", resource_id=document_id,
        details={"company_id": str(company_id), "version": version["version"]}, meta=meta,
    )

    row = await _document_row(db, company_id=company_id, document_id=document_id)
    version_row = (
        await db.execute(text("SELECT * FROM corpus_document_versions WHERE id = :i"), {"i": version_id})
    ).mappings().one()
    return _document_out(row, version_row=version_row)


async def purge_corpus(db: AsyncSession, *, superseded_days: int, dry_run: bool) -> int:
    """Nightly retention (design §4.5): documents whose ``expires_on`` has
    passed, and superseded versions older than *superseded_days*. Honours
    ``RETENTION_DRY_RUN`` like every other purge in this service. Caller
    commits."""
    now = datetime.now(tz=UTC)
    cutoff = now - timedelta(days=superseded_days)

    expired_ids = [
        r[0] for r in (
            await db.execute(
                text(
                    "SELECT id FROM corpus_documents WHERE deleted_at IS NULL"
                    " AND expires_on IS NOT NULL AND expires_on < CURRENT_DATE LIMIT 2000"
                )
            )
        ).all()
    ]
    superseded = (
        await db.execute(
            text(
                "SELECT id, company_id, document_id, version, storage_key"
                " FROM corpus_document_versions"
                " WHERE superseded_at IS NOT NULL AND superseded_at < :cutoff"
                "   AND redacted_at IS NULL LIMIT 2000"
            ),
            {"cutoff": cutoff},
        )
    ).mappings().all()

    if dry_run:
        log.info(
            "corpus.retention.candidates", expired_documents=len(expired_ids),
            superseded_versions=len(superseded), dry_run=True,
        )
        return len(expired_ids) + len(superseded)

    for document_id in expired_ids:
        row = (
            await db.execute(
                text("SELECT company_id FROM corpus_documents WHERE id = :i"), {"i": document_id}
            )
        ).mappings().first()
        if row is None:
            continue
        await _purge_document_content(
            db, company_id=row["company_id"], document_id=document_id, action="expired",
            actor=None, actor_type="system",
        )
        await db.execute(
            text("UPDATE corpus_documents SET deleted_at = now(), updated_at = now() WHERE id = :i"),
            {"i": document_id},
        )

    for v in superseded:
        if v["storage_key"]:
            removed = await store.remove(settings, [v["storage_key"]])
            if removed != 1:
                raise RuntimeError(
                    f"corpus retention could not remove object for version {v['id']}; "
                    "leaving it for the next run"
                )
        await db.execute(
            text("DELETE FROM corpus_chunks WHERE version_id = :v"), {"v": v["id"]},
        )
        await db.execute(
            text(
                "UPDATE corpus_document_versions SET storage_key = NULL, original_name = NULL,"
                " redacted_at = now() WHERE id = :i"
            ),
            {"i": v["id"]},
        )
        await _event(db, company_id=v["company_id"], document_id=v["document_id"], version_id=v["id"],
                    action="deleted", actor_type="system", actor=None,
                    details={"reason": "superseded_retention", "version": v["version"]})

    log.info(
        "corpus.retention.purged", expired_documents=len(expired_ids),
        superseded_versions=len(superseded), dry_run=False,
    )
    return len(expired_ids) + len(superseded)


# ---------------------------------------------------------------------------
# Listing and download
# ---------------------------------------------------------------------------


async def list_documents(db: AsyncSession, *, company_id: uuid.UUID, role: str) -> list[dict[str, Any]]:
    allowed = tuple(a for a in CORPUS_AUDIENCES if _may_read(role, a))
    if not allowed:
        return []
    rows = (
        await db.execute(
            text(
                "SELECT d.*, v.version, v.status, v.failure_code, v.original_name, v.content_type,"
                " v.size_bytes, v.page_count, v.chunk_count, v.injection_markers, v.uploaded_at,"
                " v.uploaded_by_user_id, COALESCE(u.full_name, u.email) AS uploaded_by_name"
                " FROM corpus_documents d"
                " LEFT JOIN corpus_document_versions v ON v.id = d.current_version_id"
                " LEFT JOIN users u ON u.id = v.uploaded_by_user_id AND u.company_id = d.company_id"
                " WHERE d.company_id = :c AND d.deleted_at IS NULL"
                "   AND d.audience = ANY(CAST(:aud AS text[]))"
                " ORDER BY d.updated_at DESC"
            ),
            {"c": company_id, "aud": list(allowed)},
        )
    ).mappings().all()
    return [_document_out(r, version_row=r) for r in rows]


async def get_document(db: AsyncSession, *, company_id: uuid.UUID, role: str,
                       document_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                "SELECT d.*, v.version, v.status, v.failure_code, v.original_name, v.content_type,"
                " v.size_bytes, v.page_count, v.chunk_count, v.injection_markers, v.uploaded_at,"
                " v.uploaded_by_user_id, COALESCE(u.full_name, u.email) AS uploaded_by_name"
                " FROM corpus_documents d"
                " LEFT JOIN corpus_document_versions v ON v.id = d.current_version_id"
                " LEFT JOIN users u ON u.id = v.uploaded_by_user_id AND u.company_id = d.company_id"
                " WHERE d.id = :i AND d.company_id = :c AND d.deleted_at IS NULL"
            ),
            {"i": document_id, "c": company_id},
        )
    ).mappings().first()
    if row is None or not _may_read(role, row["audience"]):
        return None
    return _document_out(row, version_row=row)


async def download_url(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, role: str,
    document_id: uuid.UUID, version: int | None = None, meta: RequestMeta,
) -> dict[str, Any]:
    """A signed link. Writes ``corpus.document.downloaded`` (MEDIUM-4, security
    review) — unlike a read of the metadata rows, this is what actually lets
    someone see the document's CONTENT, so it gets the same audit trail every
    other content download in this service writes; the caller (the router)
    MUST commit for that row to persist, since this GET otherwise mutates
    nothing else."""
    # where_version is one of exactly two hardcoded literals, chosen by
    # whether the caller asked for a specific version -- never built from
    # request input, so this is not a B608 injection vector. Isolated on its
    # own line, joined into the query by name rather than as an inline
    # f-string fragment (LOW-6, security review): bandit's B608 check anchors
    # its finding to the FIRST line of the concatenated SQL expression below —
    # verified by removing the suppression and confirming bandit reports
    # exactly one issue at exactly that line — and this shape keeps that one
    # line the only thing a reader needs to check.
    where_version = "v.version = :ver" if version is not None else "v.id = d.current_version_id"
    join_clause = "JOIN corpus_document_versions v ON v.document_id = d.id AND " + where_version
    sql_text = (
        "SELECT d.audience, v.storage_key, v.original_name, v.version FROM corpus_documents d "  # nosec B608
        + join_clause +
        " WHERE d.id = :i AND d.company_id = :c AND d.deleted_at IS NULL"
    )
    row = (
        await db.execute(text(sql_text), {"i": document_id, "c": company_id, "ver": version})
    ).mappings().first()
    if row is None or not _may_read(role, row["audience"]):
        raise CorpusError(404, "not_found", "Document not found.")
    if not row["storage_key"]:
        raise CorpusError(410, "gone", "This version's file has been removed under retention.")
    url = await store.signed_download(settings, row["storage_key"], row["original_name"] or "document")
    _audit(
        db, actor=actor, action="corpus.document.downloaded", resource_id=document_id,
        details={"company_id": str(company_id), "version": row["version"]}, meta=meta,
    )
    return {"url": url, "expires_in": store.PRESIGN_SECONDS}


# ---------------------------------------------------------------------------
# Hooks for app/reconciliation.py — the embed pass owns retries/backoff/
# parking; it calls back into this module for the schema and event-writing it
# should not have to know, on the applicant embed pass's own split of
# responsibility.
# ---------------------------------------------------------------------------


async def mark_versions_indexing(db: AsyncSession, version_ids: list[uuid.UUID]) -> None:
    """parsed -> indexing, for every version the embed pass is about to touch.

    ``superseded_at IS NULL`` (HIGH-1) is not redundant with the caller's own
    SELECT: a version can be superseded in the window between that SELECT and
    this UPDATE, and ``corpus_versions_immutable`` refuses ANY status change
    on a superseded row. Excluding it here means Postgres simply does not
    touch that one row — no trigger fires, no exception, and the other rows
    in the same batch UPDATE still succeed. The caller's try/except is the
    remaining defence for a reason this WHERE clause does not anticipate.
    """
    if not version_ids:
        return
    await db.execute(
        text(
            "UPDATE corpus_document_versions SET status = 'indexing'"
            " WHERE id = ANY(:ids) AND status = 'parsed'"
            "   AND superseded_at IS NULL AND redacted_at IS NULL"
        ),
        {"ids": version_ids},
    )


async def mark_version_indexed(
    db: AsyncSession, *, company_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID,
) -> bool:
    """Promote a version to 'indexed' once every one of its chunks has an
    embedding. A no-op (returns False) if it is not currently
    parsed/indexing, so the caller may call this unconditionally."""
    result = await db.execute(
        text(
            "UPDATE corpus_document_versions SET status = 'indexed', indexed_at = now()"
            " WHERE id = :v AND status IN ('parsed', 'indexing')"
            "   AND superseded_at IS NULL AND redacted_at IS NULL"
        ),
        {"v": version_id},
    )
    # AsyncSession.execute is typed as returning Result, which declares no
    # rowcount; a DML statement actually returns a CursorResult, which does
    # (app/retention.py's precedent).
    if not cast("CursorResult[Any]", result).rowcount:
        return False
    await _event(db, company_id=company_id, document_id=document_id, version_id=version_id,
                action="indexed", actor_type="system", actor=None)
    return True


async def mark_version_failed(
    db: AsyncSession, *, company_id: uuid.UUID, document_id: uuid.UUID, version_id: uuid.UUID,
    failure_code: str,
) -> None:
    await db.execute(
        text(
            "UPDATE corpus_document_versions SET status = 'failed', failure_code = :fc"
            " WHERE id = :v AND redacted_at IS NULL"
        ),
        {"fc": failure_code, "v": version_id},
    )
    await _event(db, company_id=company_id, document_id=document_id, version_id=version_id,
                action="failed", actor_type="system", actor=None,
                details={"failure_code": failure_code})


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


async def embeddings_available() -> bool:
    """Whether the embedder is reachable right now — for ``GET /agent/status``'s
    ``corpus_semantic`` field (design §9 Q14: today's equivalent failure for
    applicant search is entirely silent, and this wave's checklist item is not
    to repeat that). A cheap, company-independent probe: a one-word query
    costs about the same as any other single embed call and never touches the
    database. Best-effort — this is a status indicator, not a gate, so any
    failure just means "no" rather than raising."""
    try:
        vec = await embed_one_remote(text="ping", task_type="query", acting_user_id="status-probe")
    except EmbeddingError:
        return False
    return bool(vec)


async def search_corpus(
    db: AsyncSession, *, company_id: uuid.UUID, role: str, query: str,
    limit: int = _DEFAULT_SEARCH_LIMIT,
) -> dict[str, Any]:
    """Hybrid (semantic + full-text) search over one company's indexed
    corpus, scoped by tenancy and audience IN the query.

    Returns:
        {"semantic": bool,
         "passages": [{"document_id": str, "title": str, "version": int,
                        "page": int | None, "heading": str | None,
                        "text": str, "contains_instructions": bool}, ...]}

    ``role`` decides the audience predicate here — the caller MUST pass the
    role from an authenticated ``ToolContext``/session, never a value taken
    from request input, since ``:is_hr`` is derived from it and bound as a
    query parameter, not composed into the SQL text.
    """
    limit = max(_MIN_SEARCH_LIMIT, min(int(limit), _MAX_SEARCH_LIMIT))
    is_hr = role == "hr_manager"
    # LOW-1 (security review): defence in depth, matching list_documents — a
    # role that cannot read ANY corpus audience (platform_owner, admin, a
    # candidate) gets zero passages without ever reaching the database. The
    # WHERE clause below is still the real control; this only means a
    # structurally-impossible caller (the tool's own allowed_roles already
    # exclude these) fails the same way here as everywhere else in this file.
    if not any(role in roles for roles in CORPUS_AUDIENCE_ROLES.values()):
        return {"semantic": True, "passages": []}
    query = (query or "").strip()
    if not query:
        return {"semantic": True, "passages": []}

    qvec: list[float] = []
    semantic = True
    try:
        # LOW-3 (security review): a distinct, recognisably-not-a-user
        # convention -- acting_user_id is attribution for feedback_billing's
        # OWN logs (never authorisation, see embedding_client.py), and a bare
        # company UUID there would read as a user id to anyone grepping those
        # logs later.
        qvec = await embed_one_remote(
            text=query, task_type="query", acting_user_id=f"system:corpus:{company_id}",
        )
    except EmbeddingError as exc:
        log.warning("corpus.search.embed_unavailable", error=str(exc))
        semantic = False

    params: dict[str, Any] = {"cid": company_id, "q": query, "limit": limit, "is_hr": is_hr}
    lexical_expr = "ts_rank_cd(to_tsvector('english', c.content), plainto_tsquery('english', :q))"
    if qvec:
        params["qvec"] = to_pgvector_literal(qvec)
        semantic_expr = (
            "CASE WHEN c.embedding IS NULL THEN 0"
            " ELSE 1 - (c.embedding <=> CAST(:qvec AS halfvec)) END"
        )
        signal_predicate = f"(c.embedding IS NOT NULL OR ({lexical_expr}) > 0)"
    else:
        semantic_expr = "0"
        signal_predicate = f"({lexical_expr}) > 0"
    score_expr = (
        f"({_SEMANTIC_WEIGHT} * ({semantic_expr}) + {_LEXICAL_WEIGHT} * LEAST(({lexical_expr}), 1.0))"
    )

    # Every fragment concatenated in below is a FIXED SQL expression built
    # only from this function's own module-level weight constants and the two
    # hand-written expressions above; the only values that vary per call
    # (:cid, :q, :qvec, :is_hr, :limit) are bound parameters, never
    # concatenated into the string. Tenancy (:cid) and audience (:is_hr) are
    # IN this one statement by construction, not a post-filter. Joined by name
    # (LOW-6, security review) rather than as inline f-string fragments
    # scattered across a long block: bandit's B608 check anchors its one real
    # finding to the FIRST line of the concatenation below — verified by
    # removing the suppression and confirming bandit reports exactly one
    # issue at exactly that line — so this shape keeps that one line the only
    # thing a reader needs to check.
    sql_text = (
        "SELECT c.document_id, c.page_from, c.heading, c.content, d.title, v.version, "  # nosec B608
        + score_expr
        + " AS score "
        "FROM corpus_chunks c "
        "JOIN corpus_document_versions v ON v.id = c.version_id AND v.company_id = c.company_id "
        "JOIN corpus_documents d ON d.id = c.document_id AND d.company_id = c.company_id "
        "WHERE d.company_id = :cid "
        "  AND d.deleted_at IS NULL "
        "  AND (d.expires_on IS NULL OR d.expires_on >= CURRENT_DATE) "
        "  AND v.superseded_at IS NULL "
        "  AND v.redacted_at IS NULL "
        "  AND v.status = 'indexed' "
        "  AND (d.audience = 'all_staff' OR :is_hr) "
        "  AND " + signal_predicate + " "
        "ORDER BY " + score_expr + " DESC "
        "LIMIT :limit"
    )
    rows = (await db.execute(text(sql_text), params)).mappings().all()

    passages = [
        {
            "document_id": str(row["document_id"]),
            "title": row["title"],
            "version": row["version"],
            "page": row["page_from"],
            "heading": row["heading"],
            "text": _prepare_passage_text(row["content"]),
            "contains_instructions": bool(detect_injection(row["content"])),
        }
        for row in rows
    ]
    return {"semantic": semantic, "passages": passages}
