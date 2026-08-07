"""The two query helpers that used to be per-router copies — DG-4 and DG-5.

Both findings are the same shape: a rule that must hold everywhere, written out
separately in each place that needs it. These tests pin the single
implementation AND that every former call site now routes through it, because a
green helper with one router still holding a private copy fixes nothing.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from shared.agents import ToolContext

from app.models import Applicant, CodingQuestion, Exam, ExamQuestion, ExamRound, ExamSection
from app.utils.ownership import get_owned
from app.utils.sql_like import LIKE_ESCAPE, like_literal

_COMPANY = uuid.uuid4()


# ---------------------------------------------------------------------------
# DG-4 — LIKE wildcard escaping
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Nurse", "Nurse"),
        ("%", r"\%"),
        ("_", r"\_"),
        ("100%_sure", r"100\%\_sure"),
        # The escape char itself must be doubled FIRST, or escaping % would
        # re-escape the backslash that escaping just introduced.
        ("\\", "\\\\"),
        ("\\%", r"\\\%"),
    ],
)
def test_like_literal_neutralises_wildcards(raw: str, expected: str) -> None:
    assert like_literal(raw) == expected


def test_like_literal_is_idempotent_under_the_declared_escape_char() -> None:
    """Applying it twice must stay escaped rather than corrupt — the guard
    against a call site that wraps an already-escaped value."""
    once = like_literal("50%")
    assert like_literal(once) == "50" + LIKE_ESCAPE * 2 + LIKE_ESCAPE + "%"


def test_hr_applicants_uses_the_shared_helper_not_a_private_copy() -> None:
    """The alias must be the same object. A copy is exactly how this rule came
    to be enforced on one path and not the other."""
    from app.routers import hr_applicants

    assert hr_applicants._like_literal is like_literal
    assert hr_applicants._LIKE_ESCAPE == LIKE_ESCAPE


def test_copilot_pipeline_sql_declares_the_escape_clause() -> None:
    """Escaping the value is only half of it: without ESCAPE '\\' Postgres has
    no reason to read the backslash as an escape at all."""
    from app.agents.tools import _PIPELINE_SQL

    rendered = str(_PIPELINE_SQL)
    assert f"ESCAPE '{LIKE_ESCAPE}'" in rendered
    # The old form built the pattern in SQL around a raw bound value, which is
    # what left the wildcards live.
    assert "'%' || CAST(:job AS text) || '%'" not in rendered


@pytest.mark.parametrize(
    ("supplied", "expected"),
    [
        ("Nurse", "%Nurse%"),
        # The finding: a model steered into passing a bare wildcard used to get
        # the entire company roster back from a filter meant to narrow it.
        ("%", r"%\%%"),
        ("_", r"%\_%"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_copilot_job_filter_binds_an_escaped_pattern(
    supplied: Any, expected: str | None
) -> None:
    from app.agents.tools import _job_filter

    assert _job_filter(supplied) == expected


@pytest.mark.asyncio
async def test_list_applicants_tool_passes_the_escaped_pattern_to_the_query() -> None:
    """End to end through the registered tool, so the escaping cannot be
    bypassed by a call path that skips the helper."""
    from app.agents.tools import registry

    captured: dict[str, Any] = {}

    async def _execute(sql: Any, params: Any = None) -> MagicMock:
        captured.update(params or {})
        result = MagicMock()
        result.all.return_value = []
        return result

    db = MagicMock()
    db.execute = _execute
    ctx = ToolContext(
        actor_id="u-1", role="hr_manager", company_id="co-1", resources={"db": db}
    )

    result = await registry.invoke(
        "list_applicants", {"job_title": "%"}, ctx, call_id="c1"
    )

    assert result.ok
    assert captured["job"] == r"%\%%"
    # Tenancy never came from the model's arguments in the first place; assert it
    # is still the context's company after the change.
    assert captured["cid"] == "co-1"


# ---------------------------------------------------------------------------
# DG-5 — one ownership query behind seven named helpers
# ---------------------------------------------------------------------------
def _db(row: Any) -> AsyncMock:
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=row)
    return db


@pytest.mark.asyncio
async def test_get_owned_applies_all_three_predicates() -> None:
    db = _db(object())
    await get_owned(db, Exam, _COMPANY, uuid.uuid4(), noun="Exam")

    compiled = str(db.scalar.await_args.args[0])
    assert "exams.id = " in compiled
    assert "exams.company_id = " in compiled
    assert "exams.deleted_at IS NULL" in compiled


@pytest.mark.asyncio
async def test_get_owned_extra_predicates_narrow_the_query() -> None:
    """``**extra`` can only add an AND, so no caller can widen past company_id."""
    db = _db(object())
    exam_id = uuid.uuid4()
    await get_owned(
        db, ExamQuestion, _COMPANY, uuid.uuid4(), noun="Question", exam_id=exam_id
    )

    compiled = str(db.scalar.await_args.args[0])
    assert "exam_questions.company_id" in compiled
    assert "exam_questions.exam_id" in compiled


@pytest.mark.asyncio
async def test_get_owned_raises_404_with_the_callers_noun() -> None:
    with pytest.raises(HTTPException) as exc:
        await get_owned(_db(None), Exam, _COMPANY, uuid.uuid4(), noun="Exam")

    assert exc.value.status_code == 404
    assert exc.value.detail == "Exam not found."
    # 403 would confirm the row exists, which is how ids get enumerated.
    assert exc.value.status_code != 403


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "helper", "args", "model", "detail"),
    [
        ("app.routers.hr_applicants", "_get_owned", 1, Applicant, "Applicant not found."),
        ("app.routers.hr_exams", "_get_owned_exam", 1, Exam, "Exam not found."),
        ("app.routers.hr_exams", "_get_owned_question", 2, ExamQuestion, "Question not found."),
        (
            "app.routers.hr_coding",
            "_get_owned_coding_question",
            2,
            CodingQuestion,
            "Question not found.",
        ),
        ("app.routers.hr_rounds", "_get_owned_round", 2, ExamRound, "Round not found."),
        ("app.routers.hr_rounds", "_get_owned_section", 2, ExamSection, "Section not found."),
    ],
)
async def test_every_ownership_helper_still_404s_cross_tenant(
    module: str, helper: str, args: int, model: type, detail: str
) -> None:
    """The seven helpers are now thin wrappers, so the behaviour they each used
    to implement has to be re-asserted at each name — a wrapper that forgot to
    pass company_id would look identical at the call site.

    ``_get_owned_invite`` is covered separately below because its model is
    imported by a heavier module.
    """
    fn = getattr(__import__(module, fromlist=[helper]), helper)
    db = _db(None)
    ids = [uuid.uuid4() for _ in range(args)]

    with pytest.raises(HTTPException) as exc:
        await fn(db, _COMPANY, *ids)

    assert exc.value.status_code == 404
    assert exc.value.detail == detail
    compiled = str(db.scalar.await_args.args[0])
    assert f"{model.__tablename__}.company_id" in compiled
    assert f"{model.__tablename__}.deleted_at IS NULL" in compiled


@pytest.mark.asyncio
async def test_invite_ownership_helper_still_404s_cross_tenant() -> None:
    from app.models import InterviewInvite
    from app.routers.hr_interviews import _get_owned_invite

    db = _db(None)
    with pytest.raises(HTTPException) as exc:
        await _get_owned_invite(db, _COMPANY, uuid.uuid4())

    assert exc.value.status_code == 404
    assert exc.value.detail == "Invite not found."
    compiled = str(db.scalar.await_args.args[0])
    assert f"{InterviewInvite.__tablename__}.company_id" in compiled
    assert f"{InterviewInvite.__tablename__}.deleted_at IS NULL" in compiled
