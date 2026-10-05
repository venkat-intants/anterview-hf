"""Bulk submit and bulk approve must not become a second way to approve.

A 500-row import is the reason these exist: a bank question cannot go into an
exam until it is ``approved``, approval must come from someone other than the
author, and there was no bulk path — so importing 500 questions meant about a
thousand clicks, and the import would have been shipped unusable.

THE WHOLE RISK OF THIS FEATURE IS THE OBVIOUS IMPLEMENTATION. One
``UPDATE bank_questions SET status = 'approved' WHERE bank_id = ...`` is three
lines, is fast, and silently defeats all three guards in ``review()``:

  1. not the author,
  2. not the submitter,
  3. not anyone the event log shows CHANGED the question's content — a guard a
     security review added after finding a reviewer could edit someone else's
     draft, have it resubmitted, and approve their own wording.

No test of ``review()`` would notice, because ``review()`` would still be
correct and simply not called. So these tests assert the SHAPE as well as the
behaviour: the bulk path loops the single-item path, and the one-person case
approves nothing.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app import question_banks as svc


# ===========================================================================
# Structure — the bulk path must go through the single-item path
# ===========================================================================
def _code(fn: Any) -> str:
    """``fn``'s source with comments and its docstring removed.

    Scanning raw source would match this file's own prose — the first version
    of the SQL test below failed on the word "UPDATE" inside approve_all's
    docstring, which explains why a bulk UPDATE is forbidden. ast.unparse drops
    comments entirely and the docstring is removed explicitly, so what is left
    is only what runs.
    """
    tree = ast.parse(inspect.cleandoc(inspect.getsource(fn)))
    body = tree.body[0]
    assert isinstance(body, ast.FunctionDef | ast.AsyncFunctionDef)
    if (body.body and isinstance(body.body[0], ast.Expr)
            and isinstance(body.body[0].value, ast.Constant)
            and isinstance(body.body[0].value.value, str)):
        body.body = body.body[1:]
    return ast.unparse(tree)


def _calls(fn: Any) -> set[str]:
    """Every function name called in ``fn``'s body."""
    tree = ast.parse(_code(fn))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            names.add(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
    return names


def test_approve_all_calls_review_rather_than_writing_status_itself() -> None:
    """The guards live in review(). A bulk path that sets status directly is a
    second implementation of the lifecycle with none of them."""
    assert "review" in _calls(svc.approve_all)
    # The only mention of status may be the SELECT's filter — never an
    # assignment, which would be the transition review() owns.
    code = _code(svc.approve_all).replace("BankQuestion.status == 'in_review'", "")
    assert "status" not in code, "approve_all must not touch status itself"


def test_submit_all_calls_submit_rather_than_writing_status_itself() -> None:
    assert "submit" in _calls(svc.submit_all)
    code = _code(svc.submit_all).replace("BankQuestion.status == 'draft'", "")
    assert "status" not in code


@pytest.mark.parametrize("fn", [svc.approve_all, svc.submit_all])
def test_no_bulk_path_issues_raw_sql(fn: Any) -> None:
    """A hand-written UPDATE is the implementation that silently drops every
    guard. The selects these functions do need are ORM selects of ids only."""
    code = _code(fn)
    assert "text" not in _calls(fn), f"{fn.__name__} builds raw SQL"
    for forbidden in ("UPDATE ", "update "):
        assert forbidden.lower() not in code.lower(), (
            f"{fn.__name__} contains {forbidden!r}"
        )


def test_the_only_writers_of_a_non_authored_origin_are_the_two_bulk_creators() -> None:
    """``origin`` records provenance for a library that feeds real assessments.
    Keeping the list of writers to two named functions is what makes it
    auditable; an ``origin=`` argument on create_question's public callers
    would let any route claim any provenance."""
    source = inspect.getsource(svc)
    assert source.count('origin="ai_draft"') == 1
    assert source.count('origin="imported"') == 1


# ===========================================================================
# Behaviour — against a fake that enforces exactly the real rules
# ===========================================================================
class _Question:
    def __init__(self, qid: uuid.UUID, *, status: str, author: uuid.UUID,
                 submitter: uuid.UUID | None = None) -> None:
        self.id = qid
        self.status = status
        self.created_by_user_id = author
        self.submitted_by_user_id = submitter
        self.root_id = qid


class _Bank:
    """Stands in for the database, enforcing the three real guards.

    Deliberately NOT a mock of ``review``: if these tests stubbed the guards
    out, they would prove only that a loop loops. The point is that the loop
    inherits refusals it never implements.
    """

    def __init__(self, questions: list[_Question], *, editors: dict[uuid.UUID, set[uuid.UUID]] | None = None) -> None:
        self.questions = {q.id: q for q in questions}
        # question id -> users who created/edited its content
        self.editors = editors or {q.id: {q.created_by_user_id} for q in questions}
        self.approved: list[uuid.UUID] = []
        self.submitted: list[uuid.UUID] = []

    def ids(self, status: str) -> list[uuid.UUID]:
        return [q.id for q in self.questions.values() if q.status == status]

    async def submit(self, qid: uuid.UUID, actor: uuid.UUID) -> None:
        q = self.questions[qid]
        if q.status != "draft":
            raise svc.QuestionBankError(409, "Only a draft can be submitted for review.")
        q.status = "in_review"
        q.submitted_by_user_id = actor
        self.submitted.append(qid)

    async def review(self, qid: uuid.UUID, actor: uuid.UUID) -> None:
        q = self.questions[qid]
        if q.status != "in_review":
            raise svc.QuestionBankError(409, "Only a submitted question can be reviewed.")
        if actor in (q.created_by_user_id, q.submitted_by_user_id):
            raise svc.QuestionBankError(
                403, "You wrote or submitted this question — another reviewer must approve it."
            )
        if actor in self.editors.get(qid, set()):
            raise svc.QuestionBankError(
                403, "You changed this question's content — another reviewer must approve it."
            )
        q.status = "approved"
        self.approved.append(qid)


@pytest.fixture
def wire(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201 - pytest fixture
    """Point submit_all/approve_all's dependencies at a _Bank."""

    def _wire(bank: _Bank) -> None:
        async def _get_bank(*_a: object, **_k: object) -> object:
            return object()

        async def _submit(_db: object, *, company_id: uuid.UUID, qid: uuid.UUID,
                          actor: uuid.UUID) -> None:
            await bank.submit(qid, actor)

        async def _review(_db: object, *, company_id: uuid.UUID, qid: uuid.UUID,
                          actor: uuid.UUID, role: str, action: str,
                          note: str | None = None) -> None:
            assert action == "approve", "approve_all must only ever approve"
            await bank.review(qid, actor)

        class _Result:
            def __init__(self, ids: list[uuid.UUID]) -> None:
                self._ids = ids

            def scalars(self) -> _Result:
                return self

            def all(self) -> list[uuid.UUID]:
                return self._ids

        class _Db:
            def __init__(self, status: str) -> None:
                self.status = status

            async def execute(self, *_a: object, **_k: object) -> _Result:
                return _Result(bank.ids(self.status))

        monkeypatch.setattr(svc, "_get_bank", _get_bank)
        monkeypatch.setattr(svc, "submit", _submit)
        monkeypatch.setattr(svc, "review", _review)
        return _Db  # type: ignore[return-value]

    return _wire


@pytest.mark.asyncio
async def test_a_second_reviewer_approves_the_whole_import(wire: Any) -> None:
    """The case the feature is for: HR-A imported 3 questions and submitted
    them; HR-B approves all three in one request."""
    hr_a, hr_b = uuid.uuid4(), uuid.uuid4()
    qs = [_Question(uuid.uuid4(), status="in_review", author=hr_a, submitter=hr_a)
          for _ in range(3)]
    bank = _Bank(qs)
    db_cls = wire(bank)

    out = await svc.approve_all(db_cls("in_review"), company_id=uuid.uuid4(),
                                bank_id=uuid.uuid4(), actor=hr_b, role="hr_manager")

    assert out["approved"] == 3
    assert out["skipped"] == []
    assert len(bank.approved) == 3


@pytest.mark.asyncio
async def test_the_author_approves_nothing_through_the_bulk_path(wire: Any) -> None:
    """The guarantee. A one-person company gets every question skipped, with
    the reason — the same answer the per-question button gives, not a shortcut
    around it."""
    hr_a = uuid.uuid4()
    qs = [_Question(uuid.uuid4(), status="in_review", author=hr_a, submitter=hr_a)
          for _ in range(3)]
    bank = _Bank(qs)
    db_cls = wire(bank)

    out = await svc.approve_all(db_cls("in_review"), company_id=uuid.uuid4(),
                                bank_id=uuid.uuid4(), actor=hr_a, role="hr_manager")

    assert out["approved"] == 0
    assert bank.approved == []
    assert len(out["skipped"]) == 3
    assert all("another reviewer" in s["reason"] for s in out["skipped"])


@pytest.mark.asyncio
async def test_someone_who_edited_a_question_is_refused_it_but_approves_the_rest(
    wire: Any,
) -> None:
    """The third guard, the one a security review added — and the reason the
    bulk path must not be a single UPDATE. A reviewer who edited question 2
    approves 1 and 3 and is refused 2, per question."""
    hr_a, hr_b = uuid.uuid4(), uuid.uuid4()
    q1, q2, q3 = (_Question(uuid.uuid4(), status="in_review", author=hr_a, submitter=hr_a)
                  for _ in range(3))
    bank = _Bank([q1, q2, q3], editors={
        q1.id: {hr_a}, q2.id: {hr_a, hr_b}, q3.id: {hr_a},
    })
    db_cls = wire(bank)

    out = await svc.approve_all(db_cls("in_review"), company_id=uuid.uuid4(),
                                bank_id=uuid.uuid4(), actor=hr_b, role="hr_manager")

    assert out["approved"] == 2
    assert set(bank.approved) == {q1.id, q3.id}
    assert [s["question_id"] for s in out["skipped"]] == [str(q2.id)]
    assert "changed this question's content" in out["skipped"][0]["reason"]


@pytest.mark.asyncio
async def test_one_refusal_does_not_abandon_the_rest_of_the_batch(wire: Any) -> None:
    """A bank two people are working on holds questions this actor cannot act
    on. Failing the whole request for one of them would make the button useless
    exactly when it is needed."""
    hr_a, hr_b = uuid.uuid4(), uuid.uuid4()
    mine = _Question(uuid.uuid4(), status="in_review", author=hr_b, submitter=hr_b)
    theirs = [_Question(uuid.uuid4(), status="in_review", author=hr_a, submitter=hr_a)
              for _ in range(2)]
    bank = _Bank([mine, *theirs])
    db_cls = wire(bank)

    out = await svc.approve_all(db_cls("in_review"), company_id=uuid.uuid4(),
                                bank_id=uuid.uuid4(), actor=hr_b, role="hr_manager")

    assert out["approved"] == 2
    assert len(out["skipped"]) == 1


@pytest.mark.asyncio
async def test_submit_all_submits_every_draft(wire: Any) -> None:
    hr_a = uuid.uuid4()
    qs = [_Question(uuid.uuid4(), status="draft", author=hr_a) for _ in range(4)]
    bank = _Bank(qs)
    db_cls = wire(bank)

    out = await svc.submit_all(db_cls("draft"), company_id=uuid.uuid4(),
                               bank_id=uuid.uuid4(), actor=hr_a)

    assert out["submitted"] == 4
    assert out["skipped"] == []
    assert all(q.status == "in_review" for q in qs)


@pytest.mark.asyncio
async def test_submitting_is_not_approving(wire: Any) -> None:
    """Submitting one's own work is allowed and approving it is not. If
    submit_all ever advanced past in_review it would be an approval with one
    pair of eyes."""
    hr_a = uuid.uuid4()
    qs = [_Question(uuid.uuid4(), status="draft", author=hr_a) for _ in range(2)]
    bank = _Bank(qs)
    db_cls = wire(bank)

    await svc.submit_all(db_cls("draft"), company_id=uuid.uuid4(), bank_id=uuid.uuid4(),
                         actor=hr_a)

    assert bank.approved == []
    assert {q.status for q in qs} == {"in_review"}


@pytest.mark.asyncio
async def test_an_empty_bank_is_not_an_error(wire: Any) -> None:
    bank = _Bank([])
    db_cls = wire(bank)
    for fn, key in ((svc.submit_all, "submitted"), (svc.approve_all, "approved")):
        kwargs: dict[str, Any] = {"company_id": uuid.uuid4(), "bank_id": uuid.uuid4(),
                                  "actor": uuid.uuid4()}
        if fn is svc.approve_all:
            kwargs["role"] = "hr_manager"
        out = await fn(db_cls("draft"), **kwargs)
        assert out[key] == 0
        assert out["skipped"] == []


# ===========================================================================
# Review 2026-10-05 — re-importing a file must not duplicate the bank
# ===========================================================================
@pytest.mark.asyncio
async def test_re_importing_the_same_rows_skips_them(monkeypatch: pytest.MonkeyPatch) -> None:
    """The panel tells HR to fix the rejected rows and import the file again —
    the right instruction, and the natural thing to do after a partial import.
    Without this it added a second copy of every row that HAD worked, and the
    copy I wrote even claimed it would not.
    """
    rows = [
        {"row": 2, "kind": "mcq", "prompt": "Already here", "options": ["a", "b"],
         "correct_index": 0, "points": 1, "difficulty": "easy", "language": "en"},
        {"row": 3, "kind": "mcq", "prompt": "Brand new", "options": ["a", "b"],
         "correct_index": 1, "points": 1, "difficulty": "easy", "language": "en"},
    ]
    existing = svc.content_hash(
        kind="mcq", prompt="Already here", options=["a", "b"], correct_index=0
    )

    async def _hashes(*_a: object, **_k: object) -> set[str]:
        return {existing}

    inserted: list[str] = []

    async def _create(_db: object, **kw: Any) -> object:
        inserted.append(kw["prompt"])
        return object()

    monkeypatch.setattr(svc, "bank_content_hashes", _hashes)
    monkeypatch.setattr(svc, "create_question", _create)

    created, duplicates = await svc.create_imported_bulk(
        AsyncMock(), company_id=uuid.uuid4(), bank_id=uuid.uuid4(), actor=uuid.uuid4(), items=rows
    )

    assert inserted == ["Brand new"], "the question already in the bank was re-inserted"
    assert len(created) == 1
    assert [d["row"] for d in duplicates] == [2]
    assert "already in this bank" in duplicates[0]["message"]


@pytest.mark.asyncio
async def test_a_file_containing_the_same_question_twice_imports_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The second occurrence is a duplicate of the first even though the bank was
    empty when the request started — so the hash set has to grow as we go, not
    be read once and left alone."""
    same = {"kind": "mcq", "prompt": "Dup", "options": ["a", "b"], "correct_index": 0,
            "points": 1, "difficulty": "easy", "language": "en"}
    items = [{**same, "row": 2}, {**same, "row": 3}]

    async def _hashes(*_a: object, **_k: object) -> set[str]:
        return set()

    inserted: list[str] = []

    async def _create(_db: object, **kw: Any) -> object:
        inserted.append(kw["prompt"])
        return object()

    monkeypatch.setattr(svc, "bank_content_hashes", _hashes)
    monkeypatch.setattr(svc, "create_question", _create)

    created, duplicates = await svc.create_imported_bulk(
        AsyncMock(), company_id=uuid.uuid4(), bank_id=uuid.uuid4(), actor=uuid.uuid4(), items=items
    )

    assert len(inserted) == 1
    assert len(created) == 1
    assert [d["row"] for d in duplicates] == [3]


@pytest.mark.asyncio
async def test_dedupe_is_by_content_not_by_row_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """content_hash normalises case and whitespace, so a re-export of the same
    sheet with different capitalisation is still the same question — otherwise
    the dedupe would be trivially defeated by the thing most likely to change."""
    async def _hashes(*_a: object, **_k: object) -> set[str]:
        return {svc.content_hash(kind="mcq", prompt="What is 2 + 2?",
                                options=["Three", "Four"], correct_index=1)}

    inserted: list[str] = []

    async def _create(_db: object, **kw: Any) -> object:
        inserted.append(kw["prompt"])
        return object()

    monkeypatch.setattr(svc, "bank_content_hashes", _hashes)
    monkeypatch.setattr(svc, "create_question", _create)

    created, duplicates = await svc.create_imported_bulk(
        AsyncMock(), company_id=uuid.uuid4(), bank_id=uuid.uuid4(), actor=uuid.uuid4(),
        items=[{"row": 2, "kind": "mcq", "prompt": "what is  2 + 2?  ",
                "options": ["three", "FOUR "], "correct_index": 1, "points": 1,
                "difficulty": "easy", "language": "en"}],
    )

    assert inserted == [], "a case/whitespace variant was treated as a new question"
    assert len(duplicates) == 1
    assert created == []


def test_the_row_key_never_reaches_create_question() -> None:
    """`row` is the parser's line number. Passed through it would be a TypeError
    on every import, so this is asserted structurally as well as exercised."""
    import inspect

    source = inspect.getsource(svc.create_imported_bulk)
    assert 'k != "row"' in source
