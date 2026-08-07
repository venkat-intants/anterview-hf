"""Unit tests for the multi-round exam structure router — DG-7.

``app/routers/hr_rounds.py`` is 775 lines of authoring API that carried its own
two tenant-isolation helpers (``_get_owned_round``, ``_get_owned_section``) and
had never been executed by a test. A regression dropping the ``company_id``
predicate from either would have been a silent cross-tenant read, so the two
cross-tenant 404 tests below come first and are the highest-value ones here.

The DB is mocked (no Postgres): every handler resolves its rows through
``db.scalar`` / ``db.execute``, so a scripted side-effect list is enough to
drive a handler end to end and assert on what it wrote.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models import ExamRound, ExamSection

_COMPANY = uuid.uuid4()
_OTHER_COMPANY = uuid.uuid4()
_EXAM = uuid.uuid4()
_NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixtures / builders
# ---------------------------------------------------------------------------
def _ctx() -> tuple[uuid.UUID, uuid.UUID]:
    """The (hr_user_id, company_id) pair HrCtxDep resolves from the session."""
    return uuid.uuid4(), _COMPANY


def _exam(status: str = "draft") -> SimpleNamespace:
    return SimpleNamespace(id=_EXAM, company_id=_COMPANY, status=status, updated_at=_NOW)


def _round(**over: Any) -> ExamRound:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "exam_id": _EXAM,
        "company_id": _COMPANY,
        "round_number": 1,
        "title": "Round 1",
        "pass_threshold": 60,
        "time_limit_seconds": None,
        "advances_to_interview": False,
        "status": "draft",
        "position": 1,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    return ExamRound(**{**defaults, **over})


def _section(kind: str = "mcq", **over: Any) -> ExamSection:
    defaults: dict[str, Any] = {
        "id": uuid.uuid4(),
        "round_id": uuid.uuid4(),
        "exam_id": _EXAM,
        "company_id": _COMPANY,
        "title": "Section 1",
        "kind": kind,
        "time_limit_seconds": None,
        "position": 1,
        "created_at": _NOW,
        "updated_at": _NOW,
        "deleted_at": None,
    }
    return ExamSection(**{**defaults, **over})


def _rows(*items: Any) -> MagicMock:
    """A db.execute() result whose .scalars().all() yields *items*."""
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(items)
    return result


def _db(*, scalars: list[Any] | None = None, executes: list[Any] | None = None) -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(side_effect=list(scalars or []))
    db.execute = AsyncMock(side_effect=list(executes or []))
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Tenant isolation — the two helpers that had no test at all
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_owned_round_cross_tenant_is_404_not_403() -> None:
    """Another company's round id must be indistinguishable from a missing one.

    404 rather than 403 on purpose: a 403 would confirm the id EXISTS, and an
    HR manager who can probe ids can enumerate a competitor's exam structure.
    """
    from app.routers.hr_rounds import _get_owned_round

    db = _db(scalars=[None])  # the company_id predicate matched nothing
    with pytest.raises(HTTPException) as exc:
        await _get_owned_round(db, _OTHER_COMPANY, _EXAM, uuid.uuid4())
    assert exc.value.status_code == 404
    assert exc.value.detail == "Round not found."


@pytest.mark.asyncio
async def test_get_owned_round_filters_on_company_exam_and_soft_delete() -> None:
    """Pin the three predicates. Losing any one is a silent correctness/tenancy bug:
    company_id -> cross-tenant read, exam_id -> a round from another exam,
    deleted_at -> a soft-deleted round stays editable."""
    from app.routers.hr_rounds import _get_owned_round

    rnd = _round()
    db = _db(scalars=[rnd])
    assert await _get_owned_round(db, _COMPANY, _EXAM, rnd.id) is rnd

    compiled = str(db.scalar.await_args.args[0])
    assert "exam_rounds.company_id" in compiled
    assert "exam_rounds.exam_id" in compiled
    assert "exam_rounds.deleted_at IS NULL" in compiled


@pytest.mark.asyncio
async def test_get_owned_section_cross_tenant_is_404_not_403() -> None:
    from app.routers.hr_rounds import _get_owned_section

    db = _db(scalars=[None])
    with pytest.raises(HTTPException) as exc:
        await _get_owned_section(db, _OTHER_COMPANY, _EXAM, uuid.uuid4())
    assert exc.value.status_code == 404
    assert exc.value.detail == "Section not found."


@pytest.mark.asyncio
async def test_get_owned_section_filters_on_company_exam_and_soft_delete() -> None:
    from app.routers.hr_rounds import _get_owned_section

    sec = _section()
    db = _db(scalars=[sec])
    assert await _get_owned_section(db, _COMPANY, _EXAM, sec.id) is sec

    compiled = str(db.scalar.await_args.args[0])
    assert "exam_sections.company_id" in compiled
    assert "exam_sections.exam_id" in compiled
    assert "exam_sections.deleted_at IS NULL" in compiled


# ---------------------------------------------------------------------------
# Structure read
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_structure_returns_rounds_with_their_sections() -> None:
    from app.routers.hr_rounds import get_structure

    rnd = _round()
    sec = _section(round_id=rnd.id)
    db = _db(
        scalars=[_exam(), 3],  # owned exam, then the section's question count
        executes=[_rows(rnd), _rows(sec)],  # rounds of the exam, sections of the round
    )

    out = await get_structure(_EXAM, _ctx(), db)

    assert out.exam_id == str(_EXAM)
    assert [r.id for r in out.rounds] == [str(rnd.id)]
    assert [s.id for s in out.rounds[0].sections] == [str(sec.id)]
    assert out.rounds[0].sections[0].question_count == 3


@pytest.mark.asyncio
async def test_get_structure_on_another_companys_exam_is_404() -> None:
    """The exam guard runs first, so a cross-tenant exam id never reaches the
    round query at all."""
    from app.routers.hr_rounds import get_structure

    db = _db(scalars=[None])
    with pytest.raises(HTTPException) as exc:
        await get_structure(_EXAM, _ctx(), db)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Round CRUD
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_round_numbers_the_first_round_one() -> None:
    from app.routers.hr_rounds import RoundCreateIn, create_round

    db = _db(scalars=[_exam(), None], executes=[_rows()])  # no existing max round_number

    out = await create_round(_EXAM, RoundCreateIn(title="  Aptitude  "), _ctx(), db)

    assert out.round_number == 1
    assert out.position == 1
    assert out.title == "Aptitude"  # whitespace trimmed at the boundary
    assert out.status == "draft"  # a new round is never born published
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_round_continues_the_existing_numbering() -> None:
    from app.routers.hr_rounds import RoundCreateIn, create_round

    db = _db(scalars=[_exam(), 2], executes=[_rows()])  # highest existing round_number

    out = await create_round(_EXAM, RoundCreateIn(title="Coding"), _ctx(), db)

    assert out.round_number == 3


@pytest.mark.asyncio
async def test_publishing_a_round_without_sections_is_400() -> None:
    from app.routers.hr_rounds import RoundUpdateIn, update_round

    rnd = _round()
    db = _db(scalars=[_exam(), rnd, 0])  # exam, round, section count = 0

    with pytest.raises(HTTPException) as exc:
        await update_round(_EXAM, rnd.id, RoundUpdateIn(status="published"), _ctx(), db)

    assert exc.value.status_code == 400
    assert "section" in exc.value.detail
    assert rnd.status == "draft"  # nothing was mutated before the guard fired


@pytest.mark.asyncio
async def test_publishing_a_round_without_questions_is_400() -> None:
    from app.routers.hr_rounds import RoundUpdateIn, update_round

    rnd = _round()
    sec = _section(round_id=rnd.id)
    db = _db(
        # exam, round, section count = 1, then mcq count = 0, coding count = 0
        scalars=[_exam(), rnd, 1, 0, 0],
        executes=[_rows(sec.id)],  # the round's live section ids
    )

    with pytest.raises(HTTPException) as exc:
        await update_round(_EXAM, rnd.id, RoundUpdateIn(status="published"), _ctx(), db)

    assert exc.value.status_code == 400
    assert "question" in exc.value.detail


@pytest.mark.asyncio
async def test_publishing_a_round_publishes_the_parent_exam() -> None:
    """There is no exam-level publish in the round model, and the candidate take
    path gates on exam.status == 'published'. If this cascade regresses, HR
    publishes a round and every candidate still gets "exam not available"."""
    from app.routers.hr_rounds import RoundUpdateIn, update_round

    exam = _exam(status="draft")
    rnd = _round()
    sec = _section(round_id=rnd.id)
    db = _db(
        # section count 1, 4 mcq, 0 coding, then the response builder's own count
        scalars=[exam, rnd, 1, 4, 0, 4],
        executes=[_rows(sec.id), _rows(sec)],
    )

    out = await update_round(_EXAM, rnd.id, RoundUpdateIn(status="published"), _ctx(), db)

    assert out.status == "published"
    assert exam.status == "published"
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_round_applies_only_the_supplied_fields() -> None:
    from app.routers.hr_rounds import RoundUpdateIn, update_round

    rnd = _round(title="Old", pass_threshold=60, advances_to_interview=False)
    db = _db(scalars=[_exam(), rnd], executes=[_rows()])

    out = await update_round(
        _EXAM, rnd.id, RoundUpdateIn(pass_threshold=75), _ctx(), db
    )

    assert out.pass_threshold == 75
    assert out.title == "Old"  # untouched — a None field means "not supplied"
    assert out.advances_to_interview is False


@pytest.mark.asyncio
async def test_delete_round_refuses_to_remove_the_last_one() -> None:
    from app.routers.hr_rounds import delete_round

    rnd = _round()
    db = _db(scalars=[_exam(), rnd, 0, 1])  # no attempts, and only 1 live round

    with pytest.raises(HTTPException) as exc:
        await delete_round(_EXAM, rnd.id, _ctx(), db)

    assert exc.value.status_code == 400
    assert rnd.deleted_at is None


@pytest.mark.asyncio
async def test_delete_round_is_blocked_once_an_attempt_exists() -> None:
    """Graded-exam integrity: a round a candidate has already sat cannot vanish."""
    from app.routers.hr_rounds import delete_round

    rnd = _round()
    db = _db(scalars=[_exam(), rnd, 1])  # one attempt on the round

    with pytest.raises(HTTPException) as exc:
        await delete_round(_EXAM, rnd.id, _ctx(), db)

    assert exc.value.status_code == 409
    assert rnd.deleted_at is None


@pytest.mark.asyncio
async def test_delete_round_cascades_the_soft_delete_to_its_sections() -> None:
    """The FK cascade only fires on a HARD delete, so the sections must be
    soft-deleted explicitly or they stay live under a deleted parent."""
    from app.routers.hr_rounds import delete_round

    rnd = _round()
    sec = _section(round_id=rnd.id)
    db = _db(scalars=[_exam(), rnd, 0, 2], executes=[_rows(sec)])

    resp = await delete_round(_EXAM, rnd.id, _ctx(), db)

    assert resp.status_code == 204
    assert rnd.deleted_at is not None
    assert sec.deleted_at == rnd.deleted_at
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reorder_rounds_rejects_a_set_that_is_not_the_live_rounds() -> None:
    """The ids must be exactly the exam's live rounds — otherwise a caller could
    renumber a partial set and leave duplicate round_numbers behind."""
    from app.routers.hr_rounds import ReorderIdsIn, reorder_rounds

    live = _round()
    db = _db(scalars=[_exam()], executes=[_rows(live)])

    with pytest.raises(HTTPException) as exc:
        await reorder_rounds(_EXAM, ReorderIdsIn(ids=[uuid.uuid4()]), _ctx(), db)

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_reorder_rounds_renumbers_from_one_in_the_given_order() -> None:
    from app.routers.hr_rounds import ReorderIdsIn, reorder_rounds

    first, second = _round(round_number=1, position=1), _round(round_number=2, position=2)
    db = _db(
        scalars=[_exam()],
        executes=[_rows(first, second), _rows(), _rows()],
    )

    out = await reorder_rounds(
        _EXAM, ReorderIdsIn(ids=[second.id, first.id]), _ctx(), db
    )

    assert [r.id for r in out] == [str(second.id), str(first.id)]
    assert (second.round_number, second.position) == (1, 1)
    assert (first.round_number, first.position) == (2, 2)
    # Two-phase write: the temporary 10_000+ numbers must be flushed before the
    # final ones, or the (exam_id, round_number) partial-unique index rejects it.
    db.flush.assert_awaited_once()


# ---------------------------------------------------------------------------
# Section CRUD
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_section_appends_after_the_last_position() -> None:
    from app.routers.hr_rounds import SectionCreateIn, create_section

    rnd = _round()
    db = _db(scalars=[_exam(), rnd, 0, 2, 0], executes=[])

    out = await create_section(
        _EXAM, rnd.id, SectionCreateIn(title="Coding", kind="coding"), _ctx(), db
    )

    assert out.position == 3
    assert out.kind == "coding"
    assert out.question_count == 0


@pytest.mark.asyncio
async def test_create_section_is_blocked_once_the_round_has_attempts() -> None:
    from app.routers.hr_rounds import SectionCreateIn, create_section

    rnd = _round()
    db = _db(scalars=[_exam(), rnd, 1])  # one attempt on the round

    with pytest.raises(HTTPException) as exc:
        await create_section(_EXAM, rnd.id, SectionCreateIn(title="Late"), _ctx(), db)

    assert exc.value.status_code == 409
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_update_section_cannot_change_kind() -> None:
    """kind is immutable after creation — flipping it would orphan every question
    the section already holds, since each kind reads a different table."""
    from app.routers.hr_rounds import SectionUpdateIn, update_section

    rnd = _round()
    sec = _section(kind="mcq", round_id=rnd.id)
    db = _db(scalars=[_exam(), rnd, sec, 0])

    out = await update_section(
        _EXAM, rnd.id, sec.id, SectionUpdateIn(title="Renamed"), _ctx(), db
    )

    assert out.title == "Renamed"
    assert out.kind == "mcq"
    assert not hasattr(SectionUpdateIn, "kind") or "kind" not in SectionUpdateIn.model_fields


@pytest.mark.asyncio
async def test_delete_section_cascades_to_both_question_tables() -> None:
    from app.routers.hr_rounds import delete_section

    rnd = _round()
    sec = _section(round_id=rnd.id)
    mcq = SimpleNamespace(deleted_at=None)
    coding = SimpleNamespace(deleted_at=None)
    db = _db(
        scalars=[_exam(), rnd, 0, sec],
        executes=[_rows(mcq), _rows(coding)],
    )

    resp = await delete_section(_EXAM, rnd.id, sec.id, _ctx(), db)

    assert resp.status_code == 204
    assert sec.deleted_at is not None
    assert mcq.deleted_at == sec.deleted_at
    assert coding.deleted_at == sec.deleted_at


# ---------------------------------------------------------------------------
# Section-scoped questions
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_listing_mcq_questions_of_a_coding_section_is_400() -> None:
    """The kind guard keeps the two question tables from being addressed through
    the wrong endpoint, which would silently return an empty list instead."""
    from app.routers.hr_rounds import list_section_questions

    sec = _section(kind="coding")
    db = _db(scalars=[_exam(), sec])

    with pytest.raises(HTTPException) as exc:
        await list_section_questions(_EXAM, sec.id, _ctx(), db)

    assert exc.value.status_code == 400
    assert "coding" in exc.value.detail


@pytest.mark.asyncio
async def test_deleting_a_section_question_soft_deletes_it() -> None:
    from app.routers.hr_rounds import delete_section_question

    sec = _section(kind="mcq")
    q = SimpleNamespace(deleted_at=None)
    db = _db(scalars=[_exam(), sec, 0, q])

    resp = await delete_section_question(_EXAM, sec.id, uuid.uuid4(), _ctx(), db)

    assert resp.status_code == 204
    assert q.deleted_at is not None
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_deleting_a_question_that_is_not_in_the_section_is_404() -> None:
    from app.routers.hr_rounds import delete_section_question

    sec = _section(kind="mcq")
    db = _db(scalars=[_exam(), sec, 0, None])

    with pytest.raises(HTTPException) as exc:
        await delete_section_question(_EXAM, sec.id, uuid.uuid4(), _ctx(), db)

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_adding_a_coding_question_to_an_mcq_section_is_400() -> None:
    from app.routers.hr_coding import CodingQuestionIn, TestCaseIn
    from app.routers.hr_rounds import add_section_coding_question

    sec = _section(kind="mcq")
    db = _db(scalars=[_exam(), sec])
    body = CodingQuestionIn(
        prompt="Reverse a string",
        allowed_languages=["python"],
        test_cases=[TestCaseIn(stdin="ab", expected_output="ba", is_sample=True)],
    )

    with pytest.raises(HTTPException) as exc:
        await add_section_coding_question(_EXAM, sec.id, body, _ctx(), db)

    assert exc.value.status_code == 400
    db.add.assert_not_called()
