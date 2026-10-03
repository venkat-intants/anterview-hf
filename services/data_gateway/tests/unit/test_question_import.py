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
        (3, "correct 'Z' is out of range for 2 options"),
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
