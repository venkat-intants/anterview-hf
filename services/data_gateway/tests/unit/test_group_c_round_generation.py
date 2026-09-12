"""C3 — a round's questions come from the competencies that round assesses.

The criteria were already frozen onto each round and already drove the AI
interview. Exam authoring ignored them: an aptitude round and a technical round
on the same opening generated from the same whole-role model, which is the
thing per-round criteria exist to stop.

These cover the lookup (whose rubric, and whether it is this company's) and
that both authoring endpoints send it. What the generator does with it is
covered in feedback_billing's test_exam_generator.
"""

from __future__ import annotations

import inspect
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


def _db(row: dict | None, criteria: list[dict] | None = None) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.mappings.return_value.first.return_value = row
    # load_criteria's own query returns rows keyed by round.
    result.mappings.return_value.all.return_value = criteria or []
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_a_rounds_rubric_is_returned_with_its_provenance() -> None:
    from app.workflows import round_rubric_for_generation

    rid, wid = uuid.uuid4(), uuid.uuid4()
    db = _db(
        {"id": rid, "title": "Aptitude", "workflow_id": wid, "job_title": "Python Developer"},
        [{"round_id": rid, "competency_id": "logical_reasoning",
          "competency_name": "Logical Reasoning", "competency_kind": "cognitive",
          "weight": 0.6, "anchors": None, "probes": None}],
    )
    got = await round_rubric_for_generation(db, company_id=uuid.uuid4(), workflow_round_id=rid)
    assert got is not None
    assert got["round_title"] == "Aptitude"
    assert got["job_title"] == "Python Developer"
    assert got["workflow_id"] == str(wid)
    assert [c["competency_id"] for c in got["criteria"]] == ["logical_reasoning"]


@pytest.mark.asyncio
async def test_another_companys_round_is_not_readable() -> None:
    """Tenancy is in the lookup, not in the caller: a round id from another
    company must not shape this company's questions."""
    from app.workflows import round_rubric_for_generation

    db = _db(None)
    assert await round_rubric_for_generation(
        db, company_id=uuid.uuid4(), workflow_round_id=uuid.uuid4()
    ) is None
    sql = " ".join(str(db.execute.call_args.args[0]).split())
    assert "wr.company_id = :c" in sql


@pytest.mark.asyncio
async def test_a_round_with_no_criteria_expresses_no_preference() -> None:
    """None, not an empty rubric — the caller then generates from the role
    model, which is better than a quota over nothing."""
    from app.workflows import round_rubric_for_generation

    rid = uuid.uuid4()
    db = _db({"id": rid, "title": "Technical", "workflow_id": uuid.uuid4(), "job_title": "Dev"}, [])
    assert await round_rubric_for_generation(
        db, company_id=uuid.uuid4(), workflow_round_id=rid
    ) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("rounds", "resolved"), [([], False), ([1], True), ([1, 2], False)])
async def test_an_exam_used_by_exactly_one_round_borrows_its_rubric(
    rounds: list[int], resolved: bool
) -> None:
    """Authoring happens on the exam, which does not know its round. One round
    using it is unambiguous; two is a guess, and guessing would generate the
    technical round's questions for the aptitude one."""
    import app.workflows as wf

    db = AsyncMock()
    result = MagicMock()
    result.all.return_value = [(uuid.uuid4(),) for _ in rounds]
    db.execute = AsyncMock(return_value=result)
    called: list[uuid.UUID] = []

    async def _by_id(_db: object, **kw: object) -> dict:
        called.append(kw["workflow_round_id"])  # type: ignore[arg-type]
        return {"criteria": [{"competency_id": "x"}]}

    original = wf.round_rubric_for_generation
    wf.round_rubric_for_generation = _by_id  # type: ignore[assignment]
    try:
        got = await wf.rubric_for_exam(db, company_id=uuid.uuid4(), exam_id=uuid.uuid4())
    finally:
        wf.round_rubric_for_generation = original  # type: ignore[assignment]
    assert (got is not None) is resolved
    assert len(called) == (1 if resolved else 0)


@pytest.mark.parametrize(
    ("module", "func"),
    [("app.routers.hr_exams", "generate_questions"),
     ("app.routers.hr_coding", "generate_coding_questions")],
)
def test_both_authoring_endpoints_send_the_rounds_rubric(module: str, func: str) -> None:
    import importlib

    src = inspect.getsource(getattr(importlib.import_module(module), func))
    assert "round_rubric=" in src
    assert "round_rubric_for_generation" in src
    # Named round wins; otherwise the exam's own round, when it has exactly one.
    assert "if body.workflow_round_id" in src
    assert "rubric_for_exam" in src
