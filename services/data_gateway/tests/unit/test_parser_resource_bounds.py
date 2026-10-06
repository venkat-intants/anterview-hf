"""Uploaded documents must bound the WORK they cause, not just the response.

Three parsers each had a bound that described the request instead of limiting it,
and all three were reachable with no login through the public apply form:

* **PDF** — ``for page in reader.pages: page.extract_text()`` with no page cap and
  no character budget. A ``/Pages`` tree shaped as a DAG (one leaf ``/Page``
  reached ``fanout ** levels`` ways) is a kilobyte on the wire. Measured against
  the old code in this repo: **1,182 B → 6,561 pages → 111,537 chars in 1.71 s**,
  and at a wider fanout 1,370 B → 6.5M chars in **41.4 s**.
* **DOCX** — ``para.iter(w:t)`` per paragraph re-walks each paragraph's whole
  subtree, so a linear chain of nested ``<w:p>`` is quadratic: 1,151 B → **8.85 s**.
* **Uploads** — five ``await file.read()`` calls with no argument, each followed by
  ``if len(content) > CAP``. The cap was measured after the body was already in
  memory.

Each was wrapped in ``asyncio.wait_for(asyncio.to_thread(...), 30)``, and comments
in all three files credited that wrapper with stopping exactly this. It does not:
``wait_for`` cancels the COROUTINE and cannot cancel the thread, so the work
continued after the request had been answered — on the default executor, which has
``min(32, cpu_count + 4)`` threads (5-6 on a 1-2 vCPU container) and is SHARED with
the bcrypt hashing in ``routers/auth.py``. Enough of these posted to the anonymous
apply form occupies the pool and login stops working. That is why these are tests
about CPU time and not about correctness.
"""

from __future__ import annotations

import ast
import io
import pathlib
import time
import zipfile
import zlib
from xml.etree import ElementTree

import pytest

from app import corpus as svc
from app import pdf_text

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_ROUTERS = pathlib.Path(svc.__file__).resolve().parent / "routers"


# ---------------------------------------------------------------------------
# Fixtures: the actual attack payloads, built here rather than committed as
# binaries so a reader can see WHY they are small.
# ---------------------------------------------------------------------------
def _dag_pdf(*, levels: int, fanout: int, declared_count: int, text: str = "Resume text.") -> bytes:
    """A PDF whose ``/Pages`` tree is a DAG: every node at a level shares ONE child.

    ``fanout ** levels`` page slots out of ``levels + 4`` objects. ``/Count`` is
    written independently of the real shape, because it is attacker-controlled —
    the tests below use that to exercise the early refusal AND the in-loop cap.
    """
    packed = zlib.compress(f"BT /F1 12 Tf 10 20 Td ({text}) Tj ET".encode())
    leaf, content, font = 2 + levels, 3 + levels, 4 + levels

    objs: dict[int, bytes] = {1: b"<</Type/Catalog/Pages 2 0 R>>"}
    for i in range(levels):
        num = 2 + i
        kids = b" ".join(b"%d 0 R" % (num + 1) for _ in range(fanout))
        objs[num] = b"<</Type/Pages/Kids[" + kids + b"]/Count %d>>" % declared_count
    objs[leaf] = (
        b"<</Type/Page/Parent %d 0 R/MediaBox[0 0 200 200]"
        b"/Resources<</Font<</F1 %d 0 R>>>>/Contents %d 0 R>>" % (1 + levels, font, content)
    )
    objs[content] = b"<</Length %d/Filter/FlateDecode>>stream\n%s\nendstream" % (len(packed), packed)
    objs[font] = b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = out.tell()
        out.write(b"%d 0 obj" % num + objs[num] + b"endobj\n")
    xref_at = out.tell()
    top = max(objs) + 1
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % top)
    for num in range(1, top):
        out.write(b"%010d 00000 n \n" % offsets.get(num, 0))
    out.write(b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (top, xref_at))
    return out.getvalue()


def _docx_body(body_xml: str) -> ElementTree.Element:
    """A parsed ``w:document`` root, which is what the extractor is handed."""
    return ElementTree.fromstring(
        f'<w:document xmlns:w="{_W_NS}"><w:body>{body_xml}</w:body></w:document>'
    )


def _nested_docx_zip(depth: int) -> bytes:
    """``depth`` nested ``<w:p>`` with one run at the bottom, as a real .docx."""
    body = "<w:p>" * depth + "<w:r><w:t>x</w:t></w:r>" + "</w:p>" * depth
    xml = f'<w:document xmlns:w="{_W_NS}"><w:body>{body}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
class TestPdfPageBound:
    def test_a_declared_page_count_over_the_cap_is_refused_before_any_page_is_read(
        self,
    ) -> None:
        """The cheap early exit. Reading ``/Root/Pages/Count`` off the catalog costs
        one object lookup; iterating ``reader.pages`` calls ``len()``, which
        flattens the entire tree first — about 1.9 s and 30 MB for a 1.3 KB DAG
        declaring 46,656 pages, i.e. the page cap alone would arrive too late."""
        bomb = _dag_pdf(levels=8, fanout=3, declared_count=6561)
        assert len(bomb) < 2_000, "the point of this payload is that it is tiny"

        out = pdf_text.extract(bomb)

        assert out.truncated is True
        assert out.text == ""
        assert out.page_count == 6561, "the caller still learns the document's real shape"

    def test_the_early_exit_costs_milliseconds_where_the_old_loop_cost_seconds(self) -> None:
        """Measured on the old code: 111,537 characters in 1.71 s from this exact
        payload, and 41.4 s from a wider one. A wall-clock assertion is a blunt
        instrument, so the threshold is loose enough to survive a slow CI runner
        and still fail by three orders of magnitude if the bound goes away."""
        bomb = _dag_pdf(levels=8, fanout=3, declared_count=6561)

        started = time.perf_counter()
        pdf_text.extract(bomb)
        elapsed = time.perf_counter() - started

        assert elapsed < 0.5, f"the early refusal took {elapsed:.2f}s"

    def test_a_count_that_lies_low_is_still_stopped_by_the_in_loop_cap(self) -> None:
        """``/Count`` is attacker-controlled, so the early exit is an optimisation
        and NOT the boundary. Declaring 1 page while the tree really holds 256
        skips that check entirely; the cap inside the loop is what holds."""
        liar = _dag_pdf(levels=4, fanout=4, declared_count=1)

        out = pdf_text.extract(liar, max_pages=3)

        assert out.truncated is True
        assert out.text.count("\n") + 1 == 3, "exactly max_pages pages were read"

    def test_a_lying_count_with_a_tree_past_pypdfs_own_limit_raises_rather_than_grinds(
        self,
    ) -> None:
        """The residual risk, stated honestly: a ``/Count`` that lies low still
        gets the tree flattened, and THAT is bounded by pypdf's own 100,000-entry
        page-tree limit rather than by anything here. A 1,393-byte PDF whose tree
        holds 1,048,576 slots raises ``LimitReachedError`` in ~0.5 s — which every
        caller already maps to a 400, because they catch ``Exception`` around this
        call. Worth a test because the bound is someone else's and could move.

        THE RAISE is the assertion that matters; the time bound is a loose safety
        net and is deliberately nowhere near the measurement. This took 0.47 s on
        a developer machine and 2.22 s on a GitHub runner — the first version of
        this test asserted ``< 2.0`` from the local figure and went red in CI on a
        correct implementation. The defect it guards against produced 41.4 s, so
        15 s separates "bounded" from "grinds" with room for any runner, and
        tightening it back buys nothing a reader of the number can use."""
        liar = _dag_pdf(levels=10, fanout=4, declared_count=1)

        started = time.perf_counter()
        with pytest.raises(Exception) as exc:  # noqa: PT011 — pypdf's own type
            pdf_text.extract(liar)
        elapsed = time.perf_counter() - started

        assert "limit" in str(exc.value).lower()
        assert elapsed < 15.0, f"flattening took {elapsed:.2f}s"

    def test_an_ordinary_document_is_not_truncated_and_keeps_its_text(self) -> None:
        """The bound must not be visible on the documents people actually upload."""
        plain = _dag_pdf(levels=1, fanout=1, declared_count=1, text="Senior Fitter, 6 years.")

        out = pdf_text.extract(plain)

        assert out.truncated is False
        assert "Senior Fitter" in out.text
        assert out.page_count == 1
        assert out.page_offsets == [0]


class TestPdfCharBound:
    def test_the_character_budget_stops_the_read_mid_document(self) -> None:
        """The page cap alone is not enough: one page can carry unbounded text, so
        a 300-page allowance with no character budget is still unbounded."""
        doc = _dag_pdf(levels=1, fanout=20, declared_count=20, text="A" * 40)

        out = pdf_text.extract(doc, max_chars=100)

        assert out.truncated is True
        assert len(out.text) <= 100 + 2, "+2 for the newlines the join inserts"

    def test_a_page_is_cut_rather_than_dropped_when_the_budget_runs_out(self) -> None:
        """Truncation is silent and partial on purpose. This text feeds scoring,
        embedding and search; it is never shown back to the candidate as their
        document, and refusing the upload would turn an attack control into a
        reason a real applicant cannot apply."""
        doc = _dag_pdf(levels=1, fanout=4, declared_count=4, text="B" * 30)

        out = pdf_text.extract(doc, max_chars=45)

        assert out.truncated is True
        assert out.text.startswith("B" * 30), "the first page survives whole"
        assert "B" * 31 in out.text.replace("\n", ""), "the second is cut, not discarded"

    def test_page_offsets_point_at_each_page_start_in_the_joined_text(self) -> None:
        """``corpus`` attributes every chunk to a page range from these offsets, so
        an off-by-one here mislabels a citation in an HR answer."""
        out = pdf_text.extract(_dag_pdf(levels=1, fanout=3, declared_count=3, text="Clause"))

        assert len(out.page_offsets) == 3
        for offset in out.page_offsets:
            assert out.text[offset : offset + len("Clause")] == "Clause"

    def test_offsets_only_cover_the_pages_actually_read(self) -> None:
        """Reporting offsets for pages that were never extracted would point past
        the end of ``text``."""
        out = pdf_text.extract(_dag_pdf(levels=2, fanout=3, declared_count=1), max_pages=2)

        assert len(out.page_offsets) == 2
        assert all(o < len(out.text) for o in out.page_offsets)


# ---------------------------------------------------------------------------
# DOCX
# ---------------------------------------------------------------------------
class TestDocxExtractionIsLinear:
    def test_twenty_thousand_nested_paragraphs_parse_in_well_under_a_second(self) -> None:
        """8.85 s on the old code from 1,151 bytes on the wire, and the four
        existing ZIP guards are all correct and all blind to it: the file is small
        compressed AND uncompressed, so entry count, declared uncompressed sum and
        the streaming budget every pass. The amplification is the TREE SHAPE.
        ``_DOCX_MAX_UNCOMPRESSED_BYTES`` (8 MB) permits ~700,000 levels."""
        root = _docx_body("<w:p>" * 20_000 + "<w:r><w:t>x</w:t></w:r>" + "</w:p>" * 20_000)

        started = time.perf_counter()
        svc._docx_paragraph_lines(root)
        elapsed = time.perf_counter() - started

        assert elapsed < 1.0, f"nested-paragraph extraction took {elapsed:.2f}s"

    def test_cost_grows_linearly_rather_than_quadratically_with_depth(self) -> None:
        """A wall-clock ceiling alone would pass on a quadratic implementation that
        happens to be fast. Quadrupling the depth quadrupled the work before
        (0.48 s at 8,000 → 8.85 s at 20,000, ~18x for 2.5x the depth) and roughly
        quadruples it now. The allowance is wide because this measures a few
        milliseconds on a shared runner — it is the SHAPE that is under test."""
        small = _docx_body("<w:p>" * 2_000 + "<w:r><w:t>x</w:t></w:r>" + "</w:p>" * 2_000)
        large = _docx_body("<w:p>" * 16_000 + "<w:r><w:t>x</w:t></w:r>" + "</w:p>" * 16_000)

        def cost(root: ElementTree.Element) -> float:
            started = time.perf_counter()
            for _ in range(3):
                svc._docx_paragraph_lines(root)
            return (time.perf_counter() - started) / 3

        ratio = cost(large) / max(cost(small), 1e-6)

        # 8x the depth: ~8x linear, ~64x quadratic. 25x separates them with room
        # to spare in both directions.
        assert ratio < 25, f"8x the depth cost {ratio:.0f}x the time — looks quadratic"

    def test_a_pathological_docx_parses_fast_through_the_real_entry_point(self) -> None:
        """End to end through the ZIP reader, not just the tree walk, so the test
        exercises what the route actually calls."""
        data = _nested_docx_zip(20_000)
        assert len(data) < 2_000

        started = time.perf_counter()
        out = svc._extract_docx_sync(data)
        elapsed = time.perf_counter() - started

        assert elapsed < 1.0, f"_extract_docx_sync took {elapsed:.2f}s"
        assert out.page_count is None


class TestDocxExtractionStaysCorrect:
    def test_runs_in_one_paragraph_keep_document_order(self) -> None:
        """The linear walk uses an explicit stack, and a stack reverses order —
        this assertion is here because the first version of it produced
        "boots.safety Wear". Word splits a sentence across runs at every
        formatting change, so reversal is not a corner case: it would scramble any
        paragraph with a bold word in it, and the resume text feeds scoring."""
        root = _docx_body(
            "<w:p><w:r><w:t>Wear </w:t></w:r>"
            "<w:r><w:t>safety </w:t></w:r>"
            "<w:r><w:t>boots.</w:t></w:r></w:p>"
        )

        assert svc._docx_paragraph_lines(root) == ["Wear safety boots."]

    def test_runs_wrapped_in_a_container_keep_document_order_too(self) -> None:
        """The stack is reversed in TWO places — pushing the paragraph's children
        and pushing each node's children — and a mutation test found the second one
        uncovered: with three sibling ``<w:r>`` directly under ``<w:p>``, only the
        first reversal is exercised.

        Word wraps runs in a container constantly: ``<w:hyperlink>``,
        ``<w:ins>``/``<w:del>`` for tracked changes, ``<w:sdt>`` for content
        controls, ``<w:smartTag>``. A handbook with a tracked edit or a link in a
        sentence would have come out backwards."""
        root = _docx_body(
            "<w:p><w:r><w:t>See </w:t></w:r>"
            "<w:hyperlink><w:r><w:t>the leave </w:t></w:r>"
            "<w:r><w:t>policy</w:t></w:r></w:hyperlink>"
            "<w:r><w:t> for details.</w:t></w:r></w:p>"
        )

        assert svc._docx_paragraph_lines(root) == ["See the leave policy for details."]

    def test_several_text_nodes_inside_one_run_keep_order(self) -> None:
        """One more level down: a single ``<w:r>`` holding two ``<w:t>`` either side
        of a ``<w:br/>``, which is what Word writes for a soft line break."""
        root = _docx_body(
            "<w:p><w:r><w:t>Line one </w:t><w:br/><w:t>line two.</w:t></w:r></w:p>"
        )

        assert svc._docx_paragraph_lines(root) == ["Line one line two."]

    def test_a_nested_paragraph_gets_its_own_line_and_is_not_duplicated(self) -> None:
        """Descending past a nested ``<w:p>`` was wrong on its own terms, not only
        slow: the outer loop already visits that paragraph, so its text was
        emitted twice — once inside the outer paragraph's line and once as its
        own. Word means the same thing we do here: a paragraph in a table cell is
        its own paragraph."""
        root = _docx_body(
            "<w:p><w:r><w:t>Outer.</w:t></w:r>"
            "<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Inner.</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
            "<w:r><w:t>Tail.</w:t></w:r></w:p>"
        )

        lines = svc._docx_paragraph_lines(root)

        assert lines == ["Outer.Tail.", "Inner."]
        assert sum(line.count("Inner.") for line in lines) == 1

    def test_ordinary_nesting_depth_is_well_inside_the_cap(self) -> None:
        """``_DOCX_MAX_PARA_DEPTH`` is 64. A real handbook nests a few levels for
        tables and text boxes; a cap that clipped those would lose policy text
        from the HR corpus, which is worse than slow."""
        inner = "<w:p><w:r><w:t>Cell text.</w:t></w:r></w:p>"
        for _ in range(6):
            inner = f"<w:tbl><w:tr><w:tc><w:p>{inner}</w:p></w:tc></w:tr></w:tbl>"
        root = _docx_body(inner)

        assert "Cell text." in svc._docx_paragraph_lines(root)

    def test_heading_styles_still_carry_through_the_new_walk(self) -> None:
        """``w:pStyle`` lives in ``w:pPr``, which the own-text walk descends into.
        The corpus chunker reads these headings for citations."""
        root = _docx_body(
            '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>'
            "<w:r><w:t>Leave Policy</w:t></w:r></w:p>"
            "<w:p><w:r><w:t>Twenty days a year.</w:t></w:r></w:p>"
        )

        assert svc._docx_paragraph_lines(root) == ["# Leave Policy", "Twenty days a year."]

    def test_the_depth_cap_is_a_number_a_reader_can_check(self) -> None:
        """Defence in depth only — the walk above is already linear — so if this
        ever has to change, it is not load-bearing."""
        assert svc._DOCX_MAX_PARA_DEPTH == 64


# ---------------------------------------------------------------------------
# Uploads: the cap must bound the read, not describe it
# ---------------------------------------------------------------------------
def _zero_arg_awaited_reads(path: pathlib.Path) -> list[tuple[int, str]]:
    """Every ``await <name>.read()`` with no argument, as (line, receiver)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Await):
            continue
        call = node.value
        if not isinstance(call, ast.Call) or call.args or call.keywords:
            continue
        func = call.func
        # A bare Name receiver is an UploadFile parameter or a loop variable over
        # a list of them. `obj["Body"].read()` in resume.py is a Subscript — an
        # object WE wrote to R2 under our own cap, and truncating a download would
        # corrupt the file the user asked for, so it is deliberately not here.
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "read"
            and isinstance(func.value, ast.Name)
        ):
            found.append((node.lineno, func.value.id))
    return found


def test_no_router_reads_an_upload_before_it_knows_the_size() -> None:
    """``read(limit + 1)`` is the pattern, and one byte over the cap is all that is
    needed to know the body is too big. ``read()`` with no argument pulls the whole
    spooled body into one bytes object and the ``len(...) > CAP`` check then runs on
    something already paid for — so the 2 MB cap on the exam importer did not stop
    a 2 GB upload from becoming 2 GB of heap.

    A static test rather than a behavioural one because the defect is a MISSING
    argument: there is no input that distinguishes the two implementations except
    one large enough to hurt the machine running the suite. The eleven call sites
    that already pass a limit are what makes this enforceable."""
    offenders = {
        f"{path.name}:{line} ({name}.read())"
        for path in sorted(_ROUTERS.glob("*.py"))
        for line, name in _zero_arg_awaited_reads(path)
    }

    assert not offenders, "these read an upload unbounded, then measure it: " + ", ".join(
        sorted(offenders)
    )


@pytest.mark.parametrize(
    ("module", "symbol"),
    [
        ("routers/hr_exams.py", "_MAX_IMPORT_BYTES + 1"),
        ("routers/question_banks.py", "question_import.MAX_IMPORT_BYTES + 1"),
        ("routers/resume.py", "_MAX_RESUME_BYTES + 1"),
        ("routers/jd.py", "_MAX_JD_BYTES + 1"),
        ("routers/hr_applicants.py", "_MAX_RESUME_BYTES + 1"),
    ],
)
def test_each_fixed_site_reads_its_own_cap_plus_one(module: str, symbol: str) -> None:
    """The five sites this change touched, named individually. A guard that only
    says "no bare read()" is satisfied by ``read(8)``; these say the limit passed
    is the SAME constant the length check compares against."""
    source = (pathlib.Path(svc.__file__).resolve().parent / module).read_text(encoding="utf-8")

    assert f"read({symbol})" in source, f"{module} should read({symbol})"
