"""The spreadsheet reader shared by the exam and question-bank importers.

An HR manager builds these files in Excel, which means every test here is about
a file that is *nearly* right: the answer named as "b" instead of "B", a Points
column Excel stored as 2.0, a trailing blank row, a difficulty typed as "Hrad".
The parser's job is to take what it can, and to tell the importer which LINE to
go and fix for everything else — "import failed" sends them back to a
forty-row file with no idea where to look.

ONE reader, not two. These helpers were private to ``routers/hr_exams.py``
until 2026-10-03; the bank importer would otherwise have been a second copy,
and the way two copies drift is silent — a file that imports into an exam but
is refused by a bank, or accepted with a *different* correct answer, with
nothing to say which reader was right.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import pytest

from app import question_import as qi

HEADER = list(qi.TEMPLATE_HEADER)
BANK_HEADER = list(qi.BANK_TEMPLATE_HEADER)


def _rows(*body: list[str], header: list[str] | None = None) -> list[list[str]]:
    return [header if header is not None else HEADER, *body]


def _q(prompt: str, a: str = "One", b: str = "Two", c: str = "", d: str = "",
       correct: str = "B", points: str = "1", extra: list[str] | None = None) -> list[str]:
    """One spreadsheet row. `extra` is the bank's Difficulty/Language/Competency."""
    return [prompt, a, b, c, d, correct, points, *(extra or [])]


# ===========================================================================
# "Correct" — three spellings, because all three are what people type
# ===========================================================================
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("A", 0), ("a", 0), ("B", 1), ("D", 3), ("1", 0), ("2", 1), ("4", 3)],
)
def test_correct_accepts_a_letter_or_a_number(raw: str, expected: int) -> None:
    assert qi.correct_to_index(raw, ["w", "x", "y", "z"]) == expected


def test_correct_accepts_the_option_text_itself() -> None:
    """What someone does when they are not sure the letters line up."""
    assert qi.correct_to_index("Voltage", ["Pressure", "Voltage", "Mass"]) == 1


def test_correct_matches_option_text_ignoring_case_and_padding() -> None:
    assert qi.correct_to_index("  voltage ", ["Pressure", "Voltage"]) == 1


@pytest.mark.parametrize("raw", ["", "   ", "E", "5", "0", "Reactance"])
def test_an_unusable_correct_cell_raises_with_a_reason(raw: str) -> None:
    """The message reaches the HR manager, so it names the value they typed."""
    with pytest.raises(ValueError) as exc:
        qi.correct_to_index(raw, ["Pressure", "Voltage"])
    assert raw.strip() in str(exc.value) or "missing" in str(exc.value)


# ===========================================================================
# Reading the file
# ===========================================================================
def test_a_csv_is_read_into_string_rows() -> None:
    buf = io.StringIO()
    csv.writer(buf).writerows([HEADER, _q("What is 2 + 2?", "3", "4", correct="B")])
    rows = qi.read_spreadsheet("q.csv", buf.getvalue().encode("utf-8"))
    assert rows[0][0] == "Question"
    assert rows[1][0] == "What is 2 + 2?"


def test_a_csv_with_a_utf8_bom_does_not_corrupt_the_first_header() -> None:
    """Excel's own "CSV UTF-8" export writes a BOM. Left in, it becomes part of
    the first cell, the header is not recognised, and row 1 imports as a
    question titled with an invisible character."""
    buf = io.StringIO()
    csv.writer(buf).writerows([HEADER, _q("Real question")])
    raw = buf.getvalue().encode("utf-8-sig")
    rows = qi.read_spreadsheet("q.csv", raw)
    assert rows[0][0] == "Question"
    items, errors = qi.parse_mcq_rows(rows)
    assert [i["prompt"] for i in items] == ["Real question"]
    assert errors == []


def test_an_unreadable_file_raises_with_a_sentence_for_a_person() -> None:
    with pytest.raises(qi.SpreadsheetError) as exc:
        qi.read_spreadsheet("q.xlsx", b"this is not a workbook")
    assert "valid .xlsx or .csv" in str(exc.value)


def test_a_real_xlsx_round_trips() -> None:
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    ws.append(["What does a multimeter measure?", "Pressure", "Voltage", "", "", "B", 2])
    buf = io.BytesIO()
    wb.save(buf)

    rows = qi.read_spreadsheet("q.xlsx", buf.getvalue())
    items, errors = qi.parse_mcq_rows(rows)
    assert errors == []
    assert items[0]["prompt"] == "What does a multimeter measure?"
    assert items[0]["correct_index"] == 1
    # Excel stores an integer cell as a float; "2.0" must still mean 2 points.
    assert items[0]["points"] == 2


# ===========================================================================
# Rows: what survives, what is reported
# ===========================================================================
def test_the_header_row_is_not_imported_as_a_question() -> None:
    items, _ = qi.parse_mcq_rows(_rows(_q("A real question")))
    assert len(items) == 1
    assert items[0]["prompt"] == "A real question"


def test_a_file_with_no_header_still_imports_its_first_row() -> None:
    """Someone who deleted the header should not silently lose question one.

    These prompts contain the word "question" on purpose: the detector used to
    return "this is a header" for any first cell merely CONTAINING it, so a
    sheet whose first question mentioned questions lost that row — not as an
    error, as a header.
    """
    items, errors = qi.parse_mcq_rows([_q("First question"), _q("Second question")])
    assert [i["prompt"] for i in items] == ["First question", "Second question"]
    assert errors == []


def test_blank_lines_are_skipped_silently() -> None:
    """Excel hands back trailing empties for any touched cell. Reporting them
    as errors would bury the real ones."""
    items, errors = qi.parse_mcq_rows(_rows(_q("Real"), ["", "", "", "", "", "", ""], []))
    assert len(items) == 1
    assert errors == []


def test_each_error_names_the_spreadsheet_line() -> None:
    """The whole point. Row 3 here is row 3 in Excel — 1-based, header included."""
    items, errors = qi.parse_mcq_rows(_rows(
        _q("Fine"),                                  # row 2
        _q("Bad answer", correct="Z"),               # row 3
        ["", "One", "Two", "", "", "A", "1"],        # row 4 — no prompt
        _q("Only one option", a="Solo", b=""),       # row 5
    ))
    assert [i["prompt"] for i in items] == ["Fine"]
    assert [(e.row, e.message) for e in errors] == [
        # "columns", not "options": a letter names a COLUMN now, so the bound
        # it was checked against is the four Option columns.
        (3, "correct 'Z' is out of range for 4 columns"),
        (4, "missing question text"),
        (5, "need at least 2 options"),
    ]


def test_a_partial_file_still_imports_its_good_rows() -> None:
    """Partial success is the design: re-keying one cell is a minute, re-keying
    the file is an afternoon."""
    items, errors = qi.parse_mcq_rows(_rows(
        _q("Good one"), _q("Broken", correct=""), _q("Good two"),
    ))
    assert [i["prompt"] for i in items] == ["Good one", "Good two"]
    assert len(errors) == 1


def test_the_row_number_travels_with_each_item() -> None:
    """Not cosmetic: a caller that validates further (the exam builds a
    Pydantic model per item) can still name the line it rejected. Deriving it
    afterwards means matching on prompt text, which a sheet with two identical
    prompts breaks."""
    items, _ = qi.parse_mcq_rows(_rows(_q("First"), _q("Second")))
    assert [i["row"] for i in items] == [2, 3]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", 1), ("3", 3), ("2.0", 2), ("not a number", 1), ("0", 1), ("500", 100), ("-4", 1)],
)
def test_points_are_clamped_and_never_reject_a_question(raw: str, expected: int) -> None:
    """A wrong weight is worth flagging; it is not worth throwing away the
    question text over, so an unreadable Points cell is 1 rather than an error."""
    items, errors = qi.parse_mcq_rows(_rows(_q("Q", points=raw)))
    assert items[0]["points"] == expected
    assert errors == []


def test_a_huge_file_stops_and_says_so_rather_than_truncating_quietly() -> None:
    body = [_q(f"Question {n}") for n in range(qi.MAX_ROWS + 25)]
    items, errors = qi.parse_mcq_rows(_rows(*body))
    assert len(items) == qi.MAX_ROWS
    assert any("split the file" in e.message for e in errors)


# ===========================================================================
# The bank layout: three more columns, all optional
# ===========================================================================
def test_a_plain_seven_column_exam_sheet_imports_into_a_bank() -> None:
    """The reason the extra columns are optional: an HR manager with existing
    exam sheets should not have to rebuild them to fill a bank."""
    items, errors = qi.parse_bank_rows(
        _rows(_q("Exam-era question")), default_difficulty="hard", default_language="te"
    )
    assert errors == []
    assert items[0]["kind"] == "mcq"
    assert items[0]["difficulty"] == "hard"
    assert items[0]["language"] == "te"


def test_the_file_wins_over_the_callers_defaults_per_row() -> None:
    items, errors = qi.parse_bank_rows(
        _rows(
            _q("Says easy", "a", "b", correct="A", points="1", extra=["easy", "hi", ""]),
            _q("Says nothing"),
        ),
        default_difficulty="medium", default_language="en",
    )
    assert errors == []
    assert (items[0]["difficulty"], items[0]["language"]) == ("easy", "hi")
    assert (items[1]["difficulty"], items[1]["language"]) == ("medium", "en")


@pytest.mark.parametrize(
    ("difficulty", "language", "needle"),
    [("Hrad", "en", "difficulty"), ("easy", "fr", "language")],
)
def test_a_misspelled_difficulty_or_language_is_an_error_not_a_silent_default(
    difficulty: str, language: str, needle: str
) -> None:
    """Both are GRADED facts: a "hard" question carries the weight of a hard
    question in a picker, and a Telugu question shown to an English candidate is
    unusable. Importing "Hrad" as medium would put a wrong fact into a reusable
    library, which is worse than a row the importer is told to fix."""
    items, errors = qi.parse_bank_rows(
        _rows(_q("Q", "a", "b", correct="A", points="1", extra=[difficulty, language, ""]),),
    )
    assert items == []
    assert len(errors) == 1
    assert needle in errors[0].message
    assert errors[0].row == 2


def test_difficulty_and_language_are_matched_case_insensitively() -> None:
    items, errors = qi.parse_bank_rows(
        _rows(_q("Q", "a", "b", correct="A", points="1", extra=["EASY", "HI", ""]),),
    )
    assert errors == []
    assert (items[0]["difficulty"], items[0]["language"]) == ("easy", "hi")


def test_a_competency_becomes_a_slug_and_keeps_its_human_name() -> None:
    items, _ = qi.parse_bank_rows(
        _rows(_q("Q", "a", "b", correct="A", points="1", extra=["easy", "en", "Electrical Safety"]),),
    )
    assert items[0]["competencies"] == [
        {"id": "electrical_safety", "name": "Electrical Safety"}
    ]


def test_no_competency_column_leaves_the_question_untagged() -> None:
    items, _ = qi.parse_bank_rows(_rows(_q("Q")))
    assert "competencies" not in items[0]


def test_bank_rows_never_claim_to_be_coding_questions() -> None:
    """A coding question needs test cases, a reference solution and allowed
    languages. No spreadsheet row can express those, so the importer must not
    produce one — an untested coding question in a bank would be gradeable
    against nothing."""
    items, _ = qi.parse_bank_rows(_rows(_q("Looks like code to me")))
    assert {i["kind"] for i in items} == {"mcq"}


def test_bank_errors_come_back_in_line_order() -> None:
    """Difficulty errors are found on a second pass over the rows, so without
    an explicit sort they arrive after every parse error regardless of line —
    which reads as a jumbled list to whoever is fixing the file."""
    items, errors = qi.parse_bank_rows(
        _rows(
            _q("A", "a", "b", correct="A", points="1", extra=["nope", "en", ""]),   # row 2
            _q("B", correct="Z"),                                              # row 3
            _q("C", "a", "b", correct="A", points="1", extra=["easy", "klingon", ""]),  # row 4
        ),
    )
    assert items == []
    assert [e.row for e in errors] == [2, 3, 4]


def test_the_two_headers_agree_on_their_shared_columns() -> None:
    """A bank sheet is an exam sheet plus three columns. If the shared prefix
    ever diverged, the same file would mean different things to the two
    importers — which is the failure one shared reader exists to prevent."""
    assert BANK_HEADER[: len(HEADER)] == HEADER
    assert BANK_HEADER[len(HEADER):] == list(qi.BANK_EXTRA_HEADER)


def test_an_empty_file_is_no_items_and_no_errors() -> None:
    """Distinct from a file full of bad rows: the caller answers 400 "no
    question rows found" for this, and partial success for that."""
    items, errors = qi.parse_bank_rows([])
    assert (items, errors) == ([], [])


def test_a_header_only_file_is_also_empty() -> None:
    items, errors = qi.parse_bank_rows([BANK_HEADER])
    assert (items, errors) == ([], [])


def test_items_are_shaped_for_create_question() -> None:
    """Guards the seam: these dicts are splatted into
    question_banks.create_question, so an extra key is a TypeError at runtime
    and a missing one is a silent default."""
    import inspect

    from app import question_banks

    allowed = set(inspect.signature(question_banks.create_question).parameters)
    items, _ = qi.parse_bank_rows(
        _rows(_q("Q", "a", "b", correct="A", points="1", extra=["easy", "en", "Safety"]),),
    )
    extra: set[str] = set()
    for item in items:
        extra |= {k for k in item if k not in allowed}
    # `row` is the parser's own bookkeeping and the callers drop it by name.
    assert extra == {"row"}, f"unexpected keys for create_question: {extra}"


def test_the_bank_importer_sets_no_status_or_origin_of_its_own() -> None:
    """Provenance and the review lifecycle belong to the service layer. A
    parser that could set `status` would be a way to import questions straight
    past review."""
    items: list[dict[str, Any]] = []
    parsed, _ = qi.parse_bank_rows(_rows(_q("Q")))
    items.extend(parsed)
    for item in items:
        assert "status" not in item
        assert "origin" not in item


# ===========================================================================
# Review 2026-10-05 — four confirmed defects, each reproduced before the fix
# ===========================================================================
def test_a_letter_names_the_column_not_a_position_in_the_survivors() -> None:
    """THE WORST DEFECT THIS FILE HAS HELD, and it shipped in the exam importer
    from PH3 until 2026-10-05.

    Blank option cells are dropped before the answer is resolved, and a letter
    used to index the COMPACTED list. So a row with a gap marked the wrong
    answer, silently:

        Which wire is live? | red | <blank> | blue | green | C

    compacts to [red, blue, green], and 'C' -> index 2 -> "green". The sheet
    says column C, which is "blue". No error, no warning — a wrong graded fact
    imported into a library that decides who gets interviewed, which is exactly
    what this module's docstring says a misspelled Difficulty must never become.
    """
    items, errors = qi.parse_mcq_rows(
        _rows(_q("Which wire is live?", "red", "", "blue", "green", correct="C"))
    )
    assert errors == []
    chosen = items[0]["options"][items[0]["correct_index"]]
    assert chosen == "blue", f"column C is 'blue', got {chosen!r}"


def test_naming_an_empty_column_is_an_error_rather_than_a_shift() -> None:
    """The other half of the fix. If the letter points at a blank cell there is
    no answer to store, and guessing the next one along is how the bug above
    produced wrong data instead of a complaint."""
    items, errors = qi.parse_mcq_rows(_rows(_q("Q?", "red", "", "blue", "", correct="B")))
    assert items == []
    assert errors[0].row == 2
    assert "names option B, which is empty" in errors[0].message


@pytest.mark.parametrize(
    ("correct", "expected"),
    [("A", "red"), ("C", "blue"), ("D", "green"), ("1", "red"), ("3", "blue"), ("blue", "blue")],
)
def test_every_spelling_of_correct_agrees_across_a_gap(correct: str, expected: str) -> None:
    """Letter, number and text must all name the same answer on a row with a
    gap. Before the fix the three disagreed, which is the shape that makes a
    bug like this survive review: whichever one a tester tried looked fine."""
    items, errors = qi.parse_mcq_rows(
        _rows(_q("Q?", "red", "", "blue", "green", correct=correct))
    )
    assert errors == []
    assert items[0]["options"][items[0]["correct_index"]] == expected


def test_a_row_with_no_gaps_is_resolved_exactly_as_before() -> None:
    """The fix must not move the ordinary case."""
    items, _ = qi.parse_mcq_rows(_rows(_q("Q?", "w", "x", "y", "z", correct="C")))
    assert items[0]["options"] == ["w", "x", "y", "z"]
    assert items[0]["options"][items[0]["correct_index"]] == "y"


@pytest.mark.parametrize(
    "name",
    [
        "विद्युत सुरक्षा",  # hi
        "విద్యుత్ భద్రత",        # te
    ],
)
def test_a_hindi_or_telugu_competency_does_not_abort_the_import(name: str) -> None:
    """It used to take the WHOLE FILE down, naming no row.

    The slug was built with ``ch.isalnum()``, which is True for Devanagari and
    Telugu letters, so the id came out non-ASCII;
    ``question_banks.validate_competencies`` enforces ``^[a-z0-9_]{1,80}$`` and
    raised INSIDE create_imported_bulk's loop, rolling every row back. EN/HI/TE
    are the Day-1 languages (CLAUDE.md hard constraint 5), so the languages the
    platform exists for were the ones that broke it.
    """
    from app import question_banks

    items, errors = qi.parse_bank_rows(
        _rows(_q("Q", "a", "b", correct="A", extra=["easy", "en", name]))
    )
    assert errors == []
    comps = items[0]["competencies"]
    # The real validator, not a copy of its regex — that is the whole point.
    question_banks.validate_competencies(comps)
    assert comps[0]["name"] == name, "the human name must survive intact"


def test_the_competency_id_is_stable_for_the_same_name() -> None:
    """Two rows tagged with the same name must land on the same competency, or
    a bank tagged in Hindi could not be filtered at all."""
    name = "विद्युत"
    assert qi.competency_slug(name) == qi.competency_slug(name)
    assert qi.competency_slug(name) != qi.competency_slug(name + "x")


@pytest.mark.parametrize("name", ["Electrical Safety", "SQL", "a-b-c", "  Safety  "])
def test_a_latin_competency_keeps_its_readable_slug(name: str) -> None:
    """The hash fallback is for names with no usable ASCII. An English name must
    still produce something a person can recognise in a filter."""
    slug = qi.competency_slug(name)
    assert not slug.startswith("comp_"), f"{name!r} should not need the hash"
    assert slug and all(c.islower() or c.isdigit() or c == "_" for c in slug)


@pytest.mark.parametrize("name", ["व", "___", "...", "中文"])
def test_a_name_with_no_usable_ascii_falls_back_to_a_valid_hashed_id(name: str) -> None:
    from app import question_banks

    slug = qi.competency_slug(name)
    question_banks.validate_competencies([{"id": slug, "name": name}])


def test_an_oversized_csv_field_is_a_readable_refusal_not_a_crash() -> None:
    """csv raises on a field over 131072 chars. Uncaught it was a 500 on a file
    the uploader could have fixed."""
    body = ("Question,Option A,Option B,Option C,Option D,Correct,Points\n"
            + "x" * 200_000 + ",a,b,,,A,1\n").encode()
    with pytest.raises(qi.SpreadsheetError) as exc:
        qi.read_spreadsheet("q.csv", body)
    assert "CSV" in str(exc.value)


def test_a_nul_byte_names_its_row_instead_of_failing_at_the_database() -> None:
    """Postgres refuses a NUL in a text column. Left in, it surfaced as a 500 at
    flush time — after every other row had been built — so the whole import
    failed on one unusable cell with nothing to point at."""
    rows = qi.read_spreadsheet(
        "q.csv",
        b"Question,Option A,Option B,Option C,Option D,Correct,Points\n"
        b"A\x00B,a,b,,,A,1\nGood one,a,b,,,A,1\n",
    )
    items, errors = qi.parse_mcq_rows(rows)
    assert [i["prompt"] for i in items] == ["Good one"], "the good row must still import"
    assert errors[0].row == 2
    assert "NUL" in errors[0].message


# ===========================================================================
# Resource bounds — the reader, not the parser, is where the bound belongs
# ===========================================================================
def _dimension_bomb(declared_rows: int) -> bytes:
    """A valid .xlsx whose `<dimension>` lies about its width.

    `A1:XFD1048576` makes openpyxl's read-only reader pad EVERY row out to
    16,384 cells, and byte-identical rows deflate at roughly 229:1 — so a few
    kilobytes of upload used to become gigabytes of Python lists. The payload
    stays far under MAX_IMPORT_BYTES, which is the point: the upload cap and the
    Caddy body cap cannot see this attack at all.
    """
    import io
    import zipfile

    rows = "".join(
        "<row><c t='inlineStr'><is><t>x</t></is></c></row>" for _ in range(declared_rows)
    )
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg = "http://schemas.openxmlformats.org/package/2006/relationships"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr(
            "[Content_Types].xml",
            "<?xml version='1.0'?><Types xmlns='http://schemas.openxmlformats.org/"
            "package/2006/content-types'><Default Extension='xml' ContentType="
            "'application/xml'/><Override PartName='/xl/workbook.xml' ContentType="
            "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'/>"
            "<Override PartName='/xl/worksheets/sheet1.xml' ContentType='application/"
            "vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml'/></Types>",
        )
        z.writestr(
            "_rels/.rels",
            f"<?xml version='1.0'?><Relationships xmlns='{pkg}'><Relationship Id='rId1' "
            f"Type='{rel}/officeDocument' Target='xl/workbook.xml'/></Relationships>",
        )
        z.writestr(
            "xl/workbook.xml",
            f"<?xml version='1.0'?><workbook xmlns='{ns}' xmlns:r='{rel}'><sheets>"
            "<sheet name='S' sheetId='1' r:id='rId1'/></sheets></workbook>",
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            f"<?xml version='1.0'?><Relationships xmlns='{pkg}'><Relationship Id='rId1' "
            f"Type='{rel}/worksheet' Target='worksheets/sheet1.xml'/></Relationships>",
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            f"<?xml version='1.0'?><worksheet xmlns='{ns}'>"
            f"<dimension ref='A1:XFD1048576'/><sheetData>{rows}</sheetData></worksheet>",
        )
    return buf.getvalue()


def test_a_lied_about_dimension_cannot_amplify_a_tiny_upload() -> None:
    """Measured before the fix: a 25.7 KB upload produced 20,000 x 16,384 cells,
    2.7 GB of peak memory and 30 s of CPU — on the event loop, in a service that
    runs one uvicorn worker. The upload was well under the 2 MB cap, so neither
    MAX_IMPORT_BYTES nor the proxy's body cap could see it.
    """
    openpyxl = pytest.importorskip("openpyxl")
    assert openpyxl
    payload = _dimension_bomb(20_000)
    assert len(payload) < qi.MAX_IMPORT_BYTES, "the payload must be small — that is the attack"

    rows = qi.read_spreadsheet("bomb.xlsx", payload)

    # Every row sliced to the template width, so the padding costs nothing.
    assert {len(r) for r in rows} == {qi.MAX_COLS}
    # One past the cap, so the parser can still say "stopped after N rows".
    assert len(rows) <= qi.MAX_ROWS + 1


def test_the_reader_stops_rather_than_materialising_the_whole_sheet() -> None:
    """The bound has to live in the reader. MAX_ROWS used to be enforced in
    parse_mcq_rows — after every row had already been built — which is the
    "a cap applied after the read is not a cap" shape."""
    openpyxl = pytest.importorskip("openpyxl")
    assert openpyxl
    rows = qi.read_spreadsheet("bomb.xlsx", _dimension_bomb(50_000))
    assert len(rows) <= qi.MAX_ROWS + 1


def test_a_sheet_of_only_blank_rows_is_not_walked_forever() -> None:
    """The regression my own 2026-10-05 blank-row fix introduced. Removing the
    hard loop bound meant a padded sheet of blank rows was stripped cell by
    cell: 10,000 rows x 16,384 cells measured at 12.5 s. Blank rows must not
    consume the cap, AND must not be unbounded."""
    blank = [[""] * 40 for _ in range(qi.MAX_SCAN_ROWS * 3)]
    items, errors = qi.parse_mcq_rows([list(qi.TEMPLATE_HEADER), *blank])
    assert items == []
    # The point is that it returned at all, bounded by MAX_SCAN_ROWS.
    assert len(blank) > qi.MAX_SCAN_ROWS


def test_a_csv_is_bounded_the_same_way() -> None:
    """Both readers, or the bound is only on whichever format the attacker does
    not pick."""
    body = "Question,Option A,Option B,Option C,Option D,Correct,Points\n" + (
        "Q,a,b,,,A,1\n" * (qi.MAX_ROWS + 50)
    )
    rows = qi.read_spreadsheet("q.csv", body.encode())
    assert len(rows) <= qi.MAX_ROWS + 1


def test_rows_wider_than_the_template_are_sliced() -> None:
    body = "Question,Option A,Option B,Option C,Option D,Correct,Points\n" + (
        "Q," + ",".join(["x"] * 500) + "\n"
    )
    rows = qi.read_spreadsheet("q.csv", body.encode())
    assert all(len(r) <= qi.MAX_COLS for r in rows)
