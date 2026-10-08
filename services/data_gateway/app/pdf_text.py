"""One bounded PDF text extractor for every path that reads an uploaded PDF.

THE BOUND IS ON THE WORK, NOT ON THE RESPONSE, and that distinction is the whole
reason this module exists.

Three call sites — ``routers/resume.py`` (reachable with NO LOGIN through the
public apply form), ``routers/jd.py`` and ``corpus.py`` — each had their own copy
of ``for page in reader.pages: page.extract_text()`` with no page cap and no
character budget, wrapped in ``asyncio.wait_for(asyncio.to_thread(...), 30)``.
Their comments claimed that wrapper meant "a pathologically large or malformed
PDF cannot DoS the service". It does not, for two measured reasons:

1. ``asyncio.wait_for`` cancels the COROUTINE. It cannot cancel the thread, so
   the extraction keeps running and keeping allocating after the caller has
   already answered. Verified directly: the timeout fired at 0.52 s and the
   worker still ran to completion.

2. pypdf caps the page TREE at 100,000 entries, but nothing caps the TEXT. A
   ``/Pages`` tree built as a DAG — one leaf ``/Page`` object referenced
   ``fanout ** levels`` times, with a Flate-compressed content stream — is tiny
   on the wire and enormous in memory. Measured against the old code:

       1,026 B ->  1,000 pages ->  1.0M chars in  6.8 s
       1,370 B ->  6,561 pages ->  6.5M chars in 41.4 s   (~4,800x amplification)

   41 s is past the 30 s deadline, so the request 400s while the thread grinds
   on. The default executor is ``min(32, cpu_count + 4)`` — 5-6 threads on a
   1-2 vCPU container — and it is SHARED with the bcrypt calls in
   ``routers/auth.py``. Enough of these posted to the anonymous apply form
   occupies the pool and LOGIN STOPS WORKING.

A bounded extractor fixes that at the root: the thread returns in milliseconds
instead of minutes, so there is nothing to isolate and no pool to starve. An
upload cap cannot substitute for it — the payload here is a kilobyte.

TRUNCATION IS SILENT, AND THAT IS THE RIGHT TRADE. This text feeds resume
scoring, embedding and search; it is never shown back to the candidate as their
document. A CV that genuinely needs more than :data:`MAX_CHARS` of text does not
exist, and refusing the upload would turn an attack control into a reason a real
applicant cannot apply. The caller is told via :attr:`PdfText.truncated` so a
surface that cares can say so.

IT ALSO SANITISES, for the same "one path" reason the bound lives here. pypdf
passes NUL and lone surrogates through verbatim, Postgres ``text`` cannot hold
them, and on the anonymous apply doors a crafted CV used that to separate "this
address has never applied here" from the other four states (PH3-B4b, AR-10). The
page loop says the rest.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field

from pypdf import PdfReader
from shared.text import strip_unstorable

#: Pages read. A CV is 1-3 and a job description 1-10; the corpus takes real
#: documents, so this is sized for a long handbook rather than a resume. The
#: bomb above declares 6,561 pages from 1.4 KB, and every page past this one
#: carries no information a reader of this text needs.
MAX_PAGES: int = 300

#: Characters accumulated across all pages. ~150 pages of dense prose. The
#: scorer truncates to 8,000 before it reaches a model, and the embedder caps
#: its own input, so nothing downstream wants more than this.
MAX_CHARS: int = 400_000


@dataclass
class PdfText:
    """Extracted text plus what was left out, so a caller can report it."""

    text: str
    page_count: int
    #: Char offset into ``text`` where each extracted page begins. ``[0]`` is 0
    #: for a document with at least one page. Only as long as the pages actually
    #: read, which is what ``corpus`` needs for its passage offsets.
    page_offsets: list[int] = field(default_factory=list)
    #: True when MAX_PAGES or MAX_CHARS stopped the read before the end.
    truncated: bool = False


def extract(data: bytes, *, max_pages: int = MAX_PAGES, max_chars: int = MAX_CHARS) -> PdfText:
    """Read text from ``data``, stopping at the first bound reached.

    Raises whatever pypdf raises on a malformed or encrypted file — each caller
    already maps that to its own error shape, and swallowing it here would hide
    a parse failure behind an empty string.

    Both bounds are checked INSIDE the page loop. Checked after it, they would
    describe the result without limiting the work, which is the mistake the
    module docstring is about.
    """
    reader = PdfReader(io.BytesIO(data))
    pages: list[str] = []
    offsets: list[int] = []
    pos = 0
    truncated = False
    read = 0

    # Read the DECLARED page count off the catalog first. Iterating
    # `reader.pages` calls len(), which flattens the whole /Pages tree before the
    # page cap below can bite — about 1.9 s and 30 MB for a 1.3 KB DAG declaring
    # 46,656 pages. The declared /Count is attacker-controlled, so this is not a
    # security boundary on its own; it is a cheap early exit for the honest shape
    # of the attack. A /Count that LIES low still gets flattened, and that is
    # bounded by pypdf's own 100,000-entry page-tree limit.
    try:
        declared_count = int(reader.trailer["/Root"]["/Pages"]["/Count"])  # type: ignore[index]
    except Exception:  # noqa: BLE001 — a missing or odd /Count just means "read it"
        declared_count = -1
    if declared_count > max_pages:
        # Nothing read, but the caller still learns the real shape.
        return PdfText(text="", page_count=declared_count, page_offsets=[], truncated=True)

    for page in reader.pages:
        if read >= max_pages:
            truncated = True
            break
        # SANITISED HERE, PER PAGE, so every call site inherits it.
        #
        # pypdf returns whatever the PDF's encoding maps its codes to, NUL and
        # lone surrogates included, and a PDF carrying one is pure ASCII to look
        # at — an octal escape in a content stream, or a ToUnicode CMap entry, is
        # enough. That text is then written to `text` columns that cannot hold
        # it.
        #
        # On the anonymous apply doors that was a state oracle: `resume_text` is
        # written only on the branch that creates an applicant, so a crafted CV
        # answered 503 for "this address has never applied here" and 201 for the
        # other four states — with no form field involved and nothing a request
        # model could refuse (PH3-B4b, AR-10). On the authenticated callers the
        # same PDF was an unhandled 500.
        #
        # It belongs at the extractor and not at the call sites for the reason
        # this module exists: a list of guarded call sites is the thing that goes
        # stale. `public_apply.py` also wraps its own call, which an AST guard in
        # `test_group_e_public_apply.py` requires; the function is idempotent, so
        # the two compose. Stripped rather than refused because nobody typed it.
        #
        # PER PAGE, BEFORE `offsets` IS APPENDED TO, not once over the join:
        # `page_offsets` are char offsets into the text this function returns and
        # `corpus` builds passage offsets on them, so a strip applied after the
        # join would shift every offset past the first code point it removed.
        extracted = strip_unstorable(page.extract_text() or "")
        remaining = max_chars - pos
        if remaining <= 0:
            truncated = True
            break
        if len(extracted) > remaining:
            extracted = extracted[:remaining]
            truncated = True
        offsets.append(pos)
        pages.append(extracted)
        pos += len(extracted) + 1  # +1 for the "\n" the join below inserts
        read += 1
        if truncated:
            break

    # len(reader.pages) is the DECLARED count and is what a caller should report
    # as "this document has N pages"; `read` is how many we looked at.
    try:
        declared = len(reader.pages)
    except Exception:  # noqa: BLE001 — a broken page tree must not lose the text
        declared = read
    if read < declared:
        truncated = True
    return PdfText(
        text="\n".join(pages),
        page_count=declared,
        page_offsets=offsets,
        truncated=truncated,
    )


def extract_text(data: bytes) -> str:
    """Just the text, for the two callers that want nothing else."""
    return extract(data).text
