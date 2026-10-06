"""Every PDF upload path accepts the same content types, and the parser is the gate.

THREE PATHS REQUIRED EXACTLY ``application/pdf``; THREE ACCEPTED
``application/octet-stream`` TOO — and the split ran through one file, where
``hr_applicants.py``'s single-applicant upload was strict while its bulk upload was
lenient. Nobody chose that. The visible effect: curl with no ``--header``, some Android
file pickers and some corporate proxies all send octet-stream for a perfectly ordinary
PDF, so the same file uploaded on the public apply form and was refused on the
signed-in path.

WHY A CONTENT SNIFF IS NOT THE FIX HERE, which is the part worth writing down because
it is the obvious thing to reach for. ``file.content_type`` is a client-supplied
header and cannot be a control — that much is true. But measured against the installed
pypdf:

    prefix before the header     pypdf
    none                         READS
    leading whitespace           READS
    utf-8 BOM                    READS
    1 KB of junk                 READS
    64 KB of junk                READS

pypdf finds ``%PDF-`` at ANY offset. So an offset-0 check would refuse PDFs this
product accepts today — mail gateways, Java PDF libraries and scan-to-email appliances
all emit leading whitespace or a BOM — and a "within N bytes" check would be an
invented number with nothing to match it to.

Meanwhile every non-PDF is already refused cleanly (``PdfStreamError`` for a JPEG, PNG,
zip or plain text; ``EmptyFileError`` for an empty body), each route turns that into a
400, and since the page and character caps went in a non-PDF costs milliseconds. A
sniff would duplicate a refusal that already happens and pay for it by rejecting honest
uploads.

So these tests pin the allow-list as ONE list across every path, and pin that the real
refusal lives in the parser.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

from app import pdf_text

_ROUTERS = pathlib.Path(pdf_text.__file__).resolve().parent / "routers"

#: Every module with a PDF upload gate, and how many gates it holds. Named so a new
#: upload path is a deliberate addition to this list rather than a silent exemption.
_PDF_UPLOAD_MODULES: dict[str, int] = {
    "resume.py": 1,
    "jd.py": 1,
    "hr_applicants.py": 2,  # single applicant + bulk
    "public_apply.py": 2,  # one-shot submit + draft upload
}

_ACCEPTED = {"application/pdf", "application/octet-stream"}


def _gates(module: str) -> list[set[str]]:
    """The content-type allow-list of every gate in *module*, read by AST.

    Matches both shapes the repo uses — ``!= "application/pdf"`` and
    ``not in ("application/pdf", ...)`` — so a path that tightens back to the strict
    form is caught rather than skipped.
    """
    tree = ast.parse((_ROUTERS / module).read_text(encoding="utf-8"))
    found: list[set[str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        left = node.left
        if not (isinstance(left, ast.Attribute) and left.attr == "content_type"):
            continue
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            if isinstance(op, ast.NotEq) and isinstance(comparator, ast.Constant):
                found.append({comparator.value})
            elif isinstance(op, ast.NotIn) and isinstance(comparator, ast.Tuple):
                found.append(
                    {e.value for e in comparator.elts if isinstance(e, ast.Constant)}
                )
    return found


@pytest.mark.parametrize(("module", "expected_gates"), sorted(_PDF_UPLOAD_MODULES.items()))
def test_every_gate_in_the_module_is_found(module: str, expected_gates: int) -> None:
    """Guards the guard. If the AST walk stopped matching, every assertion below would
    pass against an empty list."""
    gates = _gates(module)

    assert len(gates) == expected_gates, (
        f"{module}: found {len(gates)} content-type gate(s), expected "
        f"{expected_gates}. If an upload path was added or removed, update "
        "_PDF_UPLOAD_MODULES — do not loosen this number to make it pass."
    )


@pytest.mark.parametrize("module", sorted(_PDF_UPLOAD_MODULES))
def test_every_pdf_upload_accepts_the_same_content_types(module: str) -> None:
    """ONE allow-list. Three paths used to require exactly ``application/pdf`` while
    three accepted octet-stream, and the split ran through ``hr_applicants.py``
    itself — its single upload strict, its bulk upload lenient."""
    for gate in _gates(module):
        assert gate == _ACCEPTED, (
            f"{module} has a gate accepting {sorted(gate)} rather than "
            f"{sorted(_ACCEPTED)}. octet-stream is what curl with no --header, some "
            "Android pickers and some corporate proxies send for an ordinary PDF — "
            "and the parser, not this header, is what refuses a non-PDF."
        )


def test_no_pdf_upload_path_is_stricter_than_the_public_one() -> None:
    """The asymmetry stated as its own assertion, because it is the user-visible bug:
    a signed-in person must not be refused a file an anonymous applicant can upload."""
    public = _gates("public_apply.py")
    assert public, "no gate found in public_apply.py"
    widest = max(public, key=len)

    for module in _PDF_UPLOAD_MODULES:
        for gate in _gates(module):
            assert gate >= widest, (
                f"{module} accepts {sorted(gate)}, narrower than the public apply "
                f"form's {sorted(widest)} — the same PDF would upload anonymously "
                "and be refused when signed in"
            )


class TestTheParserIsWhatRefusesANonPdf:
    """The claim the comments make, measured rather than asserted in prose. If this
    stopped holding, the content-type gate would be the only thing standing between a
    JPEG and the parser — and it is a client-supplied header."""

    @pytest.mark.parametrize(
        ("label", "body"),
        [
            ("jpeg", b"\xff\xd8\xff\xe0" + bytes(300)),
            ("png", b"\x89PNG\r\n\x1a\n" + bytes(300)),
            ("zip", b"PK\x03\x04" + bytes(300)),
            ("plain text", b"Dear sir, please find my CV attached.\n" * 10),
            ("empty", b""),
        ],
    )
    def test_a_non_pdf_is_refused_by_the_extractor(self, label: str, body: bytes) -> None:
        with pytest.raises(Exception) as exc:  # noqa: PT011 — pypdf's own types
            pdf_text.extract(body)

        assert not isinstance(exc.value, AssertionError), f"{label} was not refused"

    def test_a_pdf_with_a_bom_still_reads(self) -> None:
        """And the reason an offset-0 sniff would be a regression rather than a
        control. Excel, mail gateways and scan-to-email appliances emit these."""
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

        writer = PdfWriter()
        font = writer._add_object(
            DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
        )
        page = writer.add_blank_page(width=200, height=200)
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 10 100 Td (Senior Fitter) Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        import io

        buf = io.BytesIO()
        writer.write(buf)
        pdf = buf.getvalue()

        assert "Senior Fitter" in pdf_text.extract(b"\xef\xbb\xbf" + pdf).text
        assert "Senior Fitter" in pdf_text.extract(b"\n\n  " + pdf).text


def test_the_comment_explaining_why_there_is_no_sniff_survives() -> None:
    """A reader who finds a client-supplied header used as a gate will reach for a
    sniff. The comment is what stops that being a regression, so it is load-bearing
    and worth a test — the same reasoning the citation-contract tests use."""
    for module in ("resume.py", "jd.py", "hr_applicants.py"):
        source = (_ROUTERS / module).read_text(encoding="utf-8")

        assert re.search(r"THE PARSER IS THE GATE", source), (
            f"{module} lost the comment explaining why the content-type check is a "
            "hint and not a control, and why a sniff would refuse real PDFs"
        )
