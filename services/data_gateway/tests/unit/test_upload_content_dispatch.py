"""The uploader must not choose which parser runs, and .xlsx must stay defused.

Two gaps on the question-import path:

1. ``read_spreadsheet(filename, content)`` branched on
   ``filename.endswith(".csv")`` with openpyxl as the fall-through. ``filename``
   comes straight off the multipart part, so a zip named ``.csv`` was decoded as
   text and a CSV named ``.xlsx`` was handed to openpyxl's zip reader. The repo
   already held the right idea one surface over —
   ``document_storage.sniff_corpus`` refuses a PNG named ``.pdf`` on its bytes,
   pinned by ``test_ph5_e2_corpus_ingest.py`` — and the spreadsheet reader never
   got it.

2. openpyxl defuses its XML parser only when ``defusedxml`` imports AND
   ``OPENPYXL_DEFUSEDXML`` is unset or exactly ``"True"``, computed once at import
   into ``openpyxl.xml.DEFUSEDXML``. With it False the ``read_only=True`` row
   reader this module uses runs on the stdlib expat parser, which expands internal
   entities. ``requirements.txt`` pinning ``defusedxml`` was the entire control, and
   nothing asserted it. There was no xlsx entity test at all — the DOCX path has
   ``test_xxe_laced_document_yields_no_expansion`` and the spreadsheet path had
   only amplification tests.

WHY THE PREDICATE IS "IS THIS A WORKBOOK" AND NOT "IS THIS TEXT". The CSV branch
decodes with ``errors="replace"``, so no encoding fails there: a Windows-1252
export from a non-English Excel imports with one replacement character. A strict
utf-8 sniff — the shape ``sniff_corpus`` uses — would start REFUSING those files,
trading an attack control for an HR manager who can no longer upload.
``test_a_windows_1252_csv_still_imports`` is that promise, written down.

A NOTE ON THE ENTITY FIXTURE, because the first version of it was worthless. It put
the payload in ``xl/sharedStrings.xml``, so the cell referenced a table that failed
to parse and the file was unreadable WHETHER OR NOT the entity would have expanded
— both tests passed with the hardening switched off. Measured with the payload
inline in the SHEET instead:

    hardening ON   billion-laughs -> refused
    hardening OFF  billion-laughs -> 10,000 characters returned

Those are two different observable outcomes, which is what the test needs.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app import question_import as qi

_SM = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

_CONTENT_TYPES = (
    "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/"
    "package/2006/content-types'><Default Extension='xml' ContentType="
    "'application/xml'/><Override PartName='/xl/workbook.xml' ContentType="
    "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'/>"
    "<Override PartName='/xl/worksheets/sheet1.xml' ContentType='application/"
    "vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/></Types>"
)


def _workbook(sheet_xml: str) -> bytes:
    """A structurally valid .xlsx wrapping *sheet_xml*.

    Same package shape as ``_dimension_bomb`` in test_question_import.py. The sheet
    is the only part that varies, because that is where every fixture here puts its
    payload — see the module docstring on why sharedStrings was wrong.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr(
            "_rels/.rels",
            f"<?xml version='1.0'?><Relationships xmlns='{_PKG}'><Relationship "
            f"Id='rId1' Type='{_REL}/officeDocument' Target='xl/workbook.xml'/>"
            "</Relationships>",
        )
        z.writestr(
            "xl/workbook.xml",
            f"<?xml version='1.0'?><workbook xmlns='{_SM}' xmlns:r='{_REL}'><sheets>"
            "<sheet name='S' sheetId='1' r:id='rId1'/></sheets></workbook>",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            f"<?xml version='1.0'?><Relationships xmlns='{_PKG}'><Relationship "
            f"Id='rId1' Type='{_REL}/worksheet' Target='worksheets/sheet1.xml'/>"
            "</Relationships>",
        )
        z.writestr("xl/worksheets/sheet1.xml", sheet_xml)
    return buf.getvalue()


def _sheet(*cells: str, doctype: str = "") -> str:
    inline = "".join(f"<c t='inlineStr'><is><t>{c}</t></is></c>" for c in cells)
    return (
        f"<?xml version='1.0'?>{doctype}"
        f"<worksheet xmlns='{_SM}'><sheetData><row>{inline}</row></sheetData></worksheet>"
    )


def _one_row_workbook(*cells: str) -> bytes:
    return _workbook(_sheet(*cells))


def _docx(text: str = "Not a workbook") -> bytes:
    """A zip that is a DOCX, i.e. has no ``xl/workbook.xml``."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr(
            "word/document.xml",
            f'<w:document xmlns:w="{_W}"><w:body><w:p><w:r><w:t>{text}'
            "</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


#: A one-kilobyte entity chain that expands to 10,000 characters. Four levels of
#: ten: unmistakable in an assertion, and small enough that a run with the
#: hardening off still finishes.
_BILLION_LAUGHS_DOCTYPE = (
    "<!DOCTYPE worksheet ["
    "<!ENTITY a 'aaaaaaaaaa'>"
    "<!ENTITY b '&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;'>"
    "<!ENTITY c '&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;'>"
    "<!ENTITY d '&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;'>"
    "]>"
)


# ---------------------------------------------------------------------------
# looks_like_xlsx — the predicate itself
# ---------------------------------------------------------------------------
class TestLooksLikeXlsx:
    def test_a_real_workbook_is_recognised(self) -> None:
        assert qi.looks_like_xlsx(_one_row_workbook("Question")) is True

    def test_a_csv_is_not(self) -> None:
        assert qi.looks_like_xlsx(b"Question,Option A\nWhat is 2+2?,4\n") is False

    def test_a_docx_is_not_although_it_is_a_zip(self) -> None:
        """The structural half of the check. Both are ZIPs; only one has
        ``xl/workbook.xml``. Without it, handing a .docx to ``load_workbook`` is no
        better than handing it a CSV — the same reasoning
        ``document_storage.sniff_corpus`` applies to ``word/document.xml``."""
        assert qi.looks_like_xlsx(_docx()) is False

    def test_a_truncated_zip_is_not_a_workbook_rather_than_an_exception(self) -> None:
        """Fail closed and quietly: a half-uploaded file must not raise out of a
        PREDICATE, which is called before anything has decided what the file is —
        an exception there escapes as a 500."""
        good = _one_row_workbook("Question")

        assert qi.looks_like_xlsx(good[: len(good) // 2]) is False

    def test_an_ole2_xls_is_not_a_workbook_to_this_predicate(self) -> None:
        """openpyxl has never read .xls, so this was already refused. Recorded so
        "we now dispatch by content" is not mistaken for "we now support .xls"."""
        assert qi.looks_like_xlsx(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64) is False

    def test_an_empty_body_is_not_a_workbook(self) -> None:
        """Lost in a rewrite and restored: the predicate is called on whatever was
        uploaded, including nothing."""
        assert qi.looks_like_xlsx(b"") is False

    def test_a_zip_of_something_else_entirely_is_not(self) -> None:
        """A zip with neither a workbook nor a document part. The magic matches and
        the structural check is what refuses it."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("readme.txt", "hello")

        assert qi.looks_like_xlsx(buf.getvalue()) is False

    def test_an_archive_appended_to_another_file_is_not_a_workbook(self) -> None:
        """Why the magic byte check is at OFFSET 0 and is not decorative.

        ``zipfile`` finds the end-of-central-directory record by scanning from the
        END of the file, so an archive appended to anything is still a readable
        archive — measured: a body beginning ``%PDF-`` with an xlsx appended has
        ``ZipFile`` reading ``['xl/workbook.xml']`` quite happily. Deleting
        ``startswith(_ZIP_MAGIC)`` therefore left every other test in this file
        green, which is how this case was found.

        Without the offset-0 requirement the same bytes answer True to "is this a
        PDF?" and True to "is this a workbook?", and which parser runs depends on
        which sniffer is asked first.
        """
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/workbook.xml", "<workbook/>")
        polyglot = b"%PDF-1.4\n% an ordinary looking header\n" + buf.getvalue()

        # The premise: it really is a readable archive holding the workbook part.
        with zipfile.ZipFile(io.BytesIO(polyglot)) as z:
            assert "xl/workbook.xml" in z.namelist()

        assert qi.looks_like_xlsx(polyglot) is False


# ---------------------------------------------------------------------------
# Dispatch: the bytes decide, not the name
# ---------------------------------------------------------------------------
class TestDispatchFollowsContent:
    def test_a_workbook_named_csv_is_read_as_a_workbook(self) -> None:
        """The attack, inverted into a feature. Before this, naming a zip ``.csv``
        sent it through ``content.decode(...)`` and ``csv.reader``, which is a text
        parser being handed compressed binary."""
        rows = qi.read_spreadsheet("totally-a.csv", _one_row_workbook("Question", "A"))

        assert rows == [["Question", "A"]]

    def test_a_csv_is_still_read_as_a_csv(self) -> None:
        rows = qi.read_spreadsheet("q.csv", b"Question,Option A\nWhat is 2+2?,4\n")

        assert rows == [["Question", "Option A"], ["What is 2+2?", "4"]]

    def test_a_csv_with_no_extension_at_all_still_reads(self) -> None:
        """The old branch defaulted an extensionless upload to openpyxl, so a
        pasted CSV with no filename failed. Content dispatch fixes that."""
        rows = qi.read_spreadsheet("", b"Question,Option A\nWhat is 2+2?,4\n")

        assert rows == [["Question", "Option A"], ["What is 2+2?", "4"]]

    def test_a_docx_named_xlsx_never_reaches_openpyxl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ZIP with no ``xl/workbook.xml``, refused BY THE PREDICATE.

        The assertion that matters is ``load_workbook`` never being called. Checking
        only the message would pass on the PRE-CHANGE code too — openpyxl raised on
        a docx and the old wrapper produced the identical sentence — so the test
        could not tell "refused by the predicate" from "refused by openpyxl", which
        is precisely what its name claims (security review 2026-10-06).
        """
        openpyxl = pytest.importorskip("openpyxl")

        def fail_if_called(*_a: object, **_kw: object) -> object:
            pytest.fail("load_workbook was reached — a .docx got past the predicate")

        monkeypatch.setattr(openpyxl, "load_workbook", fail_if_called)

        with pytest.raises(qi.SpreadsheetError) as exc:
            qi.read_spreadsheet("questions.xlsx", _docx())

        assert "valid .xlsx or .csv" in str(exc.value)

    def test_the_name_only_words_the_error_and_never_picks_the_parser(self) -> None:
        """The invariant as a pair: identical bytes, two names, same result. The
        workbook is read as a workbook under BOTH names, so the name cannot have
        chosen the parser."""
        data = _one_row_workbook("Question", "A")

        assert qi.read_spreadsheet("x.csv", data) == qi.read_spreadsheet("x.xlsx", data)

    def test_garbage_named_xlsx_keeps_its_helpful_sentence(self) -> None:
        """Content decides the parser; the NAME decides the wording. Pure content
        dispatch would read this as a one-column CSV and the caller would report
        "no question rows found" — vaguer, for the commonest real mistake there is
        (the wrong file picked out of a folder)."""
        with pytest.raises(qi.SpreadsheetError) as exc:
            qi.read_spreadsheet("q.xlsx", b"this is not a workbook")

        assert "valid .xlsx or .csv" in str(exc.value)

    def test_an_xlsm_name_is_treated_as_a_workbook_name_too(self) -> None:
        """``_claims_to_be_a_workbook`` lists ``.xlsm``; macro-enabled workbooks are
        ordinary OOXML packages and HR sheets are often saved that way."""
        with pytest.raises(qi.SpreadsheetError):
            qi.read_spreadsheet("q.xlsm", b"not a workbook either")

    def test_a_real_workbook_named_xlsm_reads(self) -> None:
        rows = qi.read_spreadsheet("q.xlsm", _one_row_workbook("Question"))

        assert rows == [["Question"]]


class TestBinaryBodiesAreRefused:
    """THE REGRESSION CONTENT DISPATCH INTRODUCED, and the guard that closes it.

    Before the change, a file without a ``.csv`` name went to openpyxl and got a
    clean 400. After it, anything that is not a workbook went to the CSV branch —
    and Python 3.12's ``csv`` module no longer rejects NUL bytes — so a PDF posted
    to the importer came back as a hundred rows of mojibake and the route answered
    200 with a hundred row errors. ``sniff_corpus`` already guards precisely this
    (``document_storage.py``'s ``_NUL in data``); the CSV branch now does too.
    """

    def test_a_pdf_posted_to_the_importer_is_refused(self) -> None:
        body = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>\x00\x00\x00endobj\n%%EOF"

        with pytest.raises(qi.SpreadsheetError) as exc:
            qi.read_spreadsheet("resume.pdf", body)

        assert "valid .xlsx or .csv" in str(exc.value)

    def test_a_png_posted_to_the_importer_is_refused(self) -> None:
        with pytest.raises(qi.SpreadsheetError):
            qi.read_spreadsheet("chart.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

    def test_the_name_does_not_rescue_a_recognised_binary_either(self) -> None:
        """A recognised format header named ``.csv`` is still that format. The
        guard reads the bytes, so the extension cannot talk it out of refusing."""
        with pytest.raises(qi.SpreadsheetError):
            qi.read_spreadsheet("data.csv", b"\x1f\x8b\x08\x00gzipped")

    def test_an_archive_that_is_not_a_workbook_is_refused_whatever_it_is_called(
        self,
    ) -> None:
        """A .docx named ``q.csv`` reached ``csv.reader`` and came back as rows of
        compressed noise. An archive is not comma-separated text under any name."""
        with pytest.raises(qi.SpreadsheetError):
            qi.read_spreadsheet("q.csv", _docx())

    def test_an_unrecognised_binary_still_reaches_the_csv_branch(self) -> None:
        """THE LIMIT OF THIS GUARD, stated rather than hidden. A deny-list only
        knows the headers it lists, so a format that is not in it is read as text
        and reported per row — which is the pre-existing behaviour for any
        unparseable content, and is the price of not breaking the stray-NUL
        property below. Asserted so nobody reads the deny-list as a claim that
        every binary is caught."""
        rows = qi.read_spreadsheet("x.bin", b"\x00\x01\x02nothing known here")

        assert rows, "an unlisted header is read, not refused"

    def test_a_csv_with_one_stray_nul_keeps_its_per_row_message(self) -> None:
        """THE PROPERTY A BLANKET NUL GUARD WOULD HAVE DESTROYED, asserted here so
        the deny-list is never "simplified" into an any-NUL check.

        ``parse_mcq_rows`` names the row and imports the rest. A whole-file refusal
        would fail a 200-row import over one dirty cell, which is the
        500-at-flush-time behaviour the row-level message was written to replace.
        """
        body = (
            b"Question,Option A,Option B,Option C,Option D,Correct,Points\n"
            b"A\x00B,a,b,,,A,1\nGood one,a,b,,,A,1\n"
        )

        rows = qi.read_spreadsheet("q.csv", body)
        items, errors = qi.parse_mcq_rows(rows)

        assert [i["prompt"] for i in items] == ["Good one"], "the good row imports"
        assert errors[0].row == 2
        assert "NUL" in errors[0].message

    def test_a_utf16_csv_is_refused_once_rather_than_per_row(self) -> None:
        """UTF-16 text carries a BOM, then NULs between ASCII characters. Every
        row used to fail individually with "contains a NUL byte"; the BOM is in
        the deny-list, so now the FILE fails once with a sentence about itself.

        Note it is the BOM doing this and not the NULs — a BOM-less UTF-16 body
        falls through to the per-row path, which is the honest limit of a
        header-based guard."""
        with pytest.raises(qi.SpreadsheetError):
            qi.read_spreadsheet("q.csv", "Question,Option A\n".encode("utf-16"))


class TestAPrintableHeaderIsNotAMagicByte:
    """THE DEFECT THIS LIST SHIPPED WITH, found by the security review and pinned
    here because nothing else would have caught it.

    The first deny-list included ``BM`` (BMP), ``MZ`` (exe), ``GIF8``, ``BZh`` and
    ``Rar!`` — all printable ASCII, matched against the first bytes of a CSV.
    ``_skip_header`` supports header-less sheets on purpose, so a sheet whose first
    question begins with those letters was refused as "binary", with a message about
    .xlsx validity that said nothing about the content. "BMI" is a plausible first
    question for the healthcare family in ``shared/intelligence/``.

    The rule the list now follows: a magic qualifies only if it contains a byte that
    cannot appear in text. ``%PDF-`` is the one stated exception.
    """

    @pytest.mark.parametrize(
        ("label", "body"),
        [
            ("BMP magic vs a BMI question", b"BMI,Option A,Option B,Option C,Option D,Correct,Points\n"),
            ("BMP magic vs a BMW question", b"BMW,Option A\nWhich brand is German?,BMW\n"),
            ("exe magic vs a currency question", b"MZN is the currency of?,Mozambique,India,,,A,1\n"),
            ("bzip2 magic vs a question about bzip2", b"BZh is the magic of?,bzip2,gzip,,,A,1\n"),
            ("RAR magic vs a question about RAR", b"Rar! is a what?,archive,image,,,A,1\n"),
            ("GIF magic vs a question about GIF", b"GIF8 is the header of?,GIF,PNG,,,A,1\n"),
        ],
    )
    def test_a_question_sheet_is_not_mistaken_for_a_binary(
        self, label: str, body: bytes
    ) -> None:
        assert not qi._looks_binary(body), f"{label}: refused a legitimate CSV"

        rows = qi.read_spreadsheet("q.csv", body)

        assert rows, f"{label}: read no rows"

    def test_the_rule_is_enforced_on_the_list_itself(self) -> None:
        """Every entry must carry a byte that cannot appear in text — otherwise the
        next person adding "one more obvious header" reintroduces the defect. The
        single exception is named, not inferred, so adding a second one has to be a
        deliberate edit to this test."""
        printable = {
            magic for magic in qi._BINARY_MAGICS if all(0x20 <= b < 0x7F for b in magic)
        }

        assert printable == {b"%PDF-"}, (
            f"these magics are printable ASCII and will collide with real question "
            f"text: {sorted(printable - {b'%PDF-'})}"
        )

    def test_the_dropped_magics_stay_dropped(self) -> None:
        """A list of what was removed and why lives in the module. If one comes
        back, this says so by name rather than by a mysterious 400 in production."""
        for magic in qi._DELIBERATELY_NOT_MAGICS:
            assert magic not in qi._BINARY_MAGICS, (
                f"{magic!r} is printable ASCII and collided with a real question"
            )


class TestNoLegitimateUploadBreaks:
    def test_a_windows_1252_csv_still_imports(self) -> None:
        """THE PROMISE THIS CHANGE IS NOT ALLOWED TO BREAK. A CSV exported from a
        non-English Excel is Windows-1252, not UTF-8. The CSV branch decodes with
        ``errors="replace"`` so it imports today with a replacement character, and a
        strict utf-8 sniff would start refusing it outright. The NUL guard above
        cannot refuse it either, because cp1252 text carries no format header —
        which is why the header, and not "does it decode?", is the predicate."""
        body = "Question,Option A\nQuel est le coût ?,Dix\n".encode("cp1252")
        assert not qi._looks_binary(body), "the deny-list must not match a cp1252 CSV"

        rows = qi.read_spreadsheet("q.csv", body)

        assert rows[0] == ["Question", "Option A"]
        assert rows[1][1] == "Dix"
        # The subject of the docstring, asserted: the accented byte becomes U+FFFD
        # rather than failing the decode. Without this, a change away from
        # errors="replace" would not be caught here (security review 2026-10-06).
        assert "\ufffd" in rows[1][0], (
            f"expected a replacement character in {rows[1][0]!r} — if the decode "
            "now raises or succeeds exactly, this file's handling has changed"
        )

    def test_a_utf8_bom_csv_still_imports(self) -> None:
        """Excel on Windows writes a BOM. ``utf-8-sig`` eats it."""
        rows = qi.read_spreadsheet("q.csv", "﻿Question,Option A\nQ,A\n".encode())

        assert rows[0] == ["Question", "Option A"]

    def test_an_xls_gets_the_same_message_it_got_before(self) -> None:
        """No regression, and no new support either."""
        with pytest.raises(qi.SpreadsheetError) as exc:
            qi.read_spreadsheet("old.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)

        assert "valid .xlsx or .csv" in str(exc.value)

    def test_a_real_workbook_named_xlsx_is_unaffected(self) -> None:
        """The ordinary path, which is most uploads."""
        rows = qi.read_spreadsheet("q.xlsx", _one_row_workbook("Question", "Option A"))

        assert rows == [["Question", "Option A"]]

    def test_a_plain_text_file_is_still_read_as_a_csv(self) -> None:
        """A widening the change makes deliberately: a ``.txt`` of comma-separated
        rows used to go to openpyxl and 400. It is text, it parses, and refusing it
        served nobody."""
        rows = qi.read_spreadsheet("q.txt", b"Question,Option A\nQ,A\n")

        assert rows[0] == ["Question", "Option A"]


# ---------------------------------------------------------------------------
# XML entity hardening on the .xlsx path
# ---------------------------------------------------------------------------
class TestSpreadsheetXmlHardening:
    def test_openpyxl_reports_its_hardening_as_active(self) -> None:
        """The flag the boot check reads."""
        openpyxl_xml = pytest.importorskip("openpyxl.xml")

        assert openpyxl_xml.DEFUSEDXML is True, (
            "openpyxl's XML hardening is off in this environment — install "
            "defusedxml, and check OPENPYXL_DEFUSEDXML is unset or exactly 'True'"
        )

    def test_the_read_only_row_reader_is_the_defused_one(self) -> None:
        """``read_only=True`` streams rows through ``iterparse``, so that is the
        parser that must be defused. ``fromstring`` is hardened separately (by lxml
        when present, by defusedxml otherwise), which is why this asserts on
        ``iterparse`` — the part this module actually uses."""
        functions = pytest.importorskip("openpyxl.xml.functions")

        assert "defusedxml" in functions.iterparse.__module__

    def test_a_billion_laughs_sheet_yields_no_expansion(self) -> None:
        """The missing sibling of the DOCX test
        (``test_xxe_laced_document_yields_no_expansion``), and the one assertion
        here that genuinely discriminates.

        Measured both ways on this exact fixture: with the hardening ON the sheet is
        refused; with it OFF the cell comes back holding 10,000 characters from a
        one-kilobyte upload. An earlier version put the payload in
        ``xl/sharedStrings.xml`` and passed either way, because the cell then
        referenced a table that failed to parse and the file was unreadable for a
        reason unrelated to the entity.
        """
        data = _workbook(_sheet("&d;", doctype=_BILLION_LAUGHS_DOCTYPE))
        assert len(data) < 2_000, "the point of this payload is that it is tiny"

        try:
            rows = qi.read_spreadsheet("bomb.xlsx", data)
        except qi.SpreadsheetError:
            return  # refused outright: the intended outcome

        flat = "".join(c for row in rows for c in row)
        pytest.fail(
            f"the sheet parsed and returned {len(flat)} characters — entity "
            "expansion is enabled, so openpyxl is not running defused"
        )

    def test_an_external_entity_is_not_fetched(self) -> None:
        """XXE proper: no file read, no network call.

        Stated honestly — this one does NOT discriminate. expat declines to resolve
        an external SYSTEM entity on its own, so the fixture is refused with the
        hardening off as well. It is here as a guarantee about our behaviour, not as
        a test of defusedxml; ``test_a_billion_laughs_sheet_yields_no_expansion`` is
        the one that fails when the hardening goes away.
        """
        doctype = "<!DOCTYPE worksheet [<!ENTITY xxe SYSTEM 'file:///etc/passwd'>]>"
        data = _workbook(_sheet("&xxe;", doctype=doctype))

        try:
            rows = qi.read_spreadsheet("xxe.xlsx", data)
        except qi.SpreadsheetError:
            return

        flat = "".join(c for row in rows for c in row)
        assert "root:" not in flat, "an external entity was resolved"


class TestTheHardeningCheckItself:
    def test_it_passes_when_the_flag_is_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        openpyxl_xml = pytest.importorskip("openpyxl.xml")
        monkeypatch.setattr(openpyxl_xml, "DEFUSEDXML", True)

        qi.assert_xlsx_parser_hardened()

    def test_it_raises_when_the_flag_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A warning would not do: the process would go on serving uploads with
        entity expansion enabled, and the only evidence would be a log line nobody
        reads until a kilobyte of XML has become gigabytes of heap."""
        openpyxl_xml = pytest.importorskip("openpyxl.xml")
        monkeypatch.setattr(openpyxl_xml, "DEFUSEDXML", False)

        with pytest.raises(RuntimeError) as exc:
            qi.assert_xlsx_parser_hardened()

        assert "DEFUSEDXML" in str(exc.value)
        assert "OPENPYXL_DEFUSEDXML" in str(exc.value), (
            "the message must name the env var — that is the failure mode an "
            "`import defusedxml` check would have missed entirely, because "
            "OPENPYXL_DEFUSEDXML=true (lowercase) disables the hardening with the "
            "package installed and correct"
        )

    def test_openpyxl_absent_is_not_this_checks_business(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``_read_workbook`` already reports a missing openpyxl per request, as a
        500, because it is a server-side fault and not the uploader's. Raising here
        would turn it into a boot failure, which is a different change."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kw: object) -> object:
            if name.startswith("openpyxl"):
                raise ImportError("no openpyxl in this image")
            return real_import(name, *args, **kw)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", fake_import)

        qi.assert_xlsx_parser_hardened()

    def test_the_workbook_reader_calls_it(self) -> None:
        """The check is only a guarantee if the parse path runs it. Asserted from
        the source because the alternative — forcing the flag off and watching a
        parse fail — cannot tell this check's refusal from any other failure."""
        import inspect

        assert "assert_xlsx_parser_hardened()" in inspect.getsource(qi._read_workbook)

    def test_the_service_reports_it_at_boot_without_refusing_to_start(self) -> None:
        """REPORTED at boot, ENFORCED at the point of use — and the asymmetry is the
        point.

        data_gateway is the AUTH service: login, SSO, consent, the DPDP ledger. A
        boot refusal would let a wrong ``OPENPYXL_DEFUSEDXML``, or an image build
        that dropped ``defusedxml``, take down authentication for the whole platform
        — a total outage against a 99.5% uptime NFR, bought for visibility that
        ``_read_workbook``'s own check already provides by failing closed (security
        review 2026-10-06).

        It is also NOT on ``Settings``: importing openpyxl at config-import time
        cost ~180 ms to every importer of ``settings``, ``alembic/env.py`` included,
        which would have made a database migration refuse to run over a spreadsheet
        parser's environment variable.

        ASSERTED BY AST, NOT BY SUBSTRING. The first version of this read main.py as
        text, and a mutation that replaced ``try:`` with ``if True:`` — leaving a
        dangling ``except`` and a file that does not compile — kept every substring
        it looked for and sailed through. Parsing is what makes that impossible.
        """
        import ast
        import pathlib

        main_py = pathlib.Path(qi.__file__).resolve().parent / "main.py"
        tree = ast.parse(main_py.read_text(encoding="utf-8"))

        lifespan = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
                and node.name == "lifespan"
            ),
            None,
        )
        assert lifespan is not None, "main.py has no lifespan function"

        def calls(node: ast.AST, name: str) -> bool:
            return any(
                isinstance(c, ast.Call)
                and (
                    getattr(c.func, "id", None) == name
                    or getattr(c.func, "attr", None) == name
                )
                for c in ast.walk(node)
            )

        guarded = [
            stmt
            for stmt in ast.walk(lifespan)
            if isinstance(stmt, ast.Try) and calls(stmt, "assert_xlsx_parser_hardened")
        ]
        assert guarded, (
            "the boot check is not inside a try/except — an unhandled raise here "
            "takes authentication down for the platform over a spreadsheet env var"
        )

        handlers = [h for t in guarded for h in t.handlers]
        assert any(
            getattr(h.type, "id", None) == "RuntimeError" for h in handlers
        ), "the handler must name RuntimeError, which is what the check raises"
        assert any(calls(h, "critical") for h in handlers), (
            "the handler must log at CRITICAL: the condition it reports is one "
            "upload away from being exploitable, so it must cross an alert threshold"
        )

        # And it must still come before anything is initialised, so the report is
        # emitted before an engine, pool or scheduler exists.
        body_order = [
            i
            for i, stmt in enumerate(lifespan.body)
            if calls(stmt, "assert_xlsx_parser_hardened") or calls(stmt, "init_engine")
        ]
        first = lifespan.body[body_order[0]]
        assert calls(first, "assert_xlsx_parser_hardened"), (
            "the boot report must precede init_engine()"
        )


# ---------------------------------------------------------------------------
# A malformed workbook is a 400, not a 500
# ---------------------------------------------------------------------------
class TestResourceExhaustionIsNotTheUploadersFault:
    """``MemoryError`` subclasses ``Exception``, so the streaming handler would have
    filed a resource-exhaustion event as "is it a valid .xlsx?" and told nobody
    (security review 2026-10-06). It is re-raised now."""

    def test_a_memory_error_is_not_turned_into_a_400(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        openpyxl = pytest.importorskip("openpyxl")
        real_load = openpyxl.load_workbook

        class _Exhausted:
            def __init__(self, wb: object) -> None:
                self._wb = wb

            @property
            def active(self) -> object:
                raise MemoryError("out of memory while streaming")

            def close(self) -> None:
                self._wb.close()

        def loader(*a: object, **kw: object) -> object:
            return _Exhausted(real_load(*a, **kw))

        monkeypatch.setattr(openpyxl, "load_workbook", loader)

        with pytest.raises(MemoryError):
            qi.read_spreadsheet("q.xlsx", _one_row_workbook("Question"))

    def test_the_workbook_is_still_closed_when_it_does(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``finally: wb.close()`` must run on the re-raise path too, or a
        MemoryError leaks the open archive handle that caused it."""
        openpyxl = pytest.importorskip("openpyxl")
        real_load = openpyxl.load_workbook
        closed: list[bool] = []

        class _Exhausted:
            def __init__(self, wb: object) -> None:
                self._wb = wb

            @property
            def active(self) -> object:
                raise MemoryError("out of memory")

            def close(self) -> None:
                closed.append(True)
                self._wb.close()

        monkeypatch.setattr(openpyxl, "load_workbook", lambda *a, **kw: _Exhausted(real_load(*a, **kw)))

        with pytest.raises(MemoryError):
            qi.read_spreadsheet("q.xlsx", _one_row_workbook("Question"))

        assert closed == [True]


class TestTheCsvFailureMessageIsForAPerson:
    def test_csvs_own_words_do_not_reach_the_uploader(self) -> None:
        """The reason used to be interpolated verbatim, which handed an HR manager
        "do you need to open the file with newline=''?" — a Python API hint, not
        advice anybody can act on (security review 2026-10-06)."""
        giant = b"a," + b"x" * 200_000 + b"\n"

        with pytest.raises(qi.SpreadsheetError) as exc:
            qi.read_spreadsheet("q.csv", giant)

        message = str(exc.value)
        assert "CSV" in message, "the reader must still say which format failed"
        for leak in ("newline=", "_csv.", "quotechar", "Python"):
            assert leak not in message, f"library internals reached the user: {leak}"


class TestAMalformedWorkbookIsA400NotA500:
    """``load_workbook`` was wrapped in ``except Exception -> SpreadsheetError``.
    The loop that STREAMS the rows afterwards was wrapped only in
    ``try/finally: wb.close()``, so anything openpyxl raised mid-stream left
    ``read_spreadsheet`` as itself and the route answered 500 on a file the uploader
    could have fixed (review 2026-10-06).
    """

    def test_a_dangling_shared_string_reference_is_reported_not_raised(self) -> None:
        """Reached without malice by any workbook whose cells reference a
        shared-strings table that is absent or short: openpyxl loads the package and
        then raises IndexError out of ``_reader.py``'s
        ``self.shared_strings[int(value)]``."""
        data = _workbook(
            f"<?xml version='1.0'?><worksheet xmlns='{_SM}'><sheetData>"
            "<row><c t='s'><v>7</v></c></row></sheetData></worksheet>"
        )

        with pytest.raises(qi.SpreadsheetError) as exc:
            qi.read_spreadsheet("q.xlsx", data)

        assert "valid .xlsx or .csv" in str(exc.value)

    def test_our_own_error_is_not_rewritten_into_the_vaguer_one(self) -> None:
        """The handler re-raises ``SpreadsheetError`` untouched. A CSV whose field
        blows csv's own limit already gets a sentence naming the reason, and catching
        it would replace that with "is it a valid .xlsx or .csv?" — a worse message
        for a file that is not an xlsx at all."""
        giant = b"a," + b"x" * 200_000 + b"\n"

        with pytest.raises(qi.SpreadsheetError) as exc:
            qi.read_spreadsheet("q.csv", giant)

        assert "CSV" in str(exc.value), f"the CSV-specific reason was lost: {exc.value}"


# ---------------------------------------------------------------------------
# What the two routes do with each outcome
# ---------------------------------------------------------------------------
class TestTheRoutesMapTheOutcomesCorrectly:
    """Both importers key their status off the MESSAGE: a ``SpreadsheetError``
    containing "not installed" is a 500 (a server-side dependency is not the
    uploader's fault), anything else is a 400. That is a substring contract between
    three files, so it is worth a test of its own.
    """

    def test_a_missing_openpyxl_says_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kw: object) -> object:
            if name == "openpyxl":
                raise ImportError("not in this image")
            return real_import(name, *args, **kw)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", fake_import)

        with pytest.raises(qi.SpreadsheetError) as exc:
            qi.read_spreadsheet("q.xlsx", _one_row_workbook("Question"))

        assert "not installed" in str(exc.value), (
            "both routers map this substring to a 500; losing it turns a "
            "server-side fault into the uploader's 400"
        )

    def test_a_csv_is_unaffected_by_a_missing_openpyxl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The useful half of the degraded case: CSV import needs nothing but the
        stdlib, so it must keep working in an image without openpyxl."""
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args: object, **kw: object) -> object:
            if name.startswith("openpyxl"):
                raise ImportError("not in this image")
            return real_import(name, *args, **kw)  # type: ignore[arg-type]

        monkeypatch.setattr(builtins, "__import__", fake_import)

        rows = qi.read_spreadsheet("q.csv", b"Question,Option A\nQ,A\n")

        assert rows[0] == ["Question", "Option A"]

    @pytest.mark.parametrize("module", ["app.routers.hr_exams", "app.routers.question_banks"])
    def test_both_routes_still_split_400_from_500_on_that_substring(self, module: str) -> None:
        """Asserted from the source rather than over HTTP: the contract IS the
        substring, and an HTTP test here would need an exam, a bank, a requisition
        and an authenticated HR session to prove one ``in``."""
        import importlib
        import inspect

        source = inspect.getsource(importlib.import_module(module))

        assert "not installed" in source, f"{module} no longer keys off the substring"
        # Not `"500" in source`: that was near-tautological — "500" appears in both
        # modules for unrelated reasons, so the assertion passed whatever happened
        # to the mapping (security review 2026-10-06). Match the expression.
        assert '500 if "not installed" in str(exc)' in source.replace("'", '"'), (
            f"{module} no longer maps a missing dependency to a 500 — a server-side "
            "fault would be reported to the uploader as their 400"
        )
