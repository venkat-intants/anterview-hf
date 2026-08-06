"""Unit tests for exam magic links (HR workflow Phase 2).

Two halves:
  * the token mint/hash primitives in ``app.exam_link``;
  * the link *guards* — expiry, revocation and single-use — which live in the
    ``exam_assignments`` row (see the module docstring of ``app/exam_link.py``)
    and are enforced by ``get_exam_link_ctx`` / ``start_attempt``.

The guard tests drive a fake session that actually EVALUATES the statement's
WHERE clause against the rows it holds. A constant-returning AsyncMock would
stay green with the expiry/revocation predicates deleted from the router, which
is the whole failure mode these tests exist to catch.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy.sql import operators as sa_ops
from sqlalchemy.sql.elements import BinaryExpression, BooleanClauseList, Null

from app.exam_link import hash_exam_token, mint_exam_token

# ---------------------------------------------------------------------------
# mint / hash primitives
# ---------------------------------------------------------------------------


def test_mint_is_long_urlsafe_and_unique() -> None:
    a = mint_exam_token()
    b = mint_exam_token()
    assert a != b  # random
    assert len(a) >= 32
    # token_urlsafe output is URL-safe base64 (no +,/,=).
    assert all(c.isalnum() or c in "-_" for c in a)


def test_hash_is_deterministic_for_same_secret() -> None:
    assert hash_exam_token("tok", "secret") == hash_exam_token("tok", "secret")


def test_hash_is_keyed_different_secret_different_hash() -> None:
    assert hash_exam_token("tok", "secret-A") != hash_exam_token("tok", "secret-B")


def test_hash_differs_per_token() -> None:
    assert hash_exam_token("tok-1", "s") != hash_exam_token("tok-2", "s")


def test_hash_is_hex_sha256_length() -> None:
    h = hash_exam_token("tok", "secret")
    assert len(h) == 64
    int(h, 16)  # hex-decodable (raises if not)


# ---------------------------------------------------------------------------
# A session fake that honours the emitted WHERE clause
# ---------------------------------------------------------------------------


def _bound_value(clause: Any) -> Any:
    """The Python value on the right-hand side of a comparison."""
    if isinstance(clause, Null):  # col.is_(None)
        return None
    return getattr(clause, "value", clause)


def _clause_matches(clause: Any, row: Any) -> bool:
    """Evaluate a SQLAlchemy WHERE clause against an ORM instance in Python."""
    if isinstance(clause, BooleanClauseList):
        results = [_clause_matches(c, row) for c in clause.clauses]
        if clause.operator is sa_ops.or_:
            return any(results)
        return all(results)  # and_ — what .where(a, b, c) emits
    if isinstance(clause, BinaryExpression):
        actual = getattr(row, clause.left.key)
        expected = _bound_value(clause.right)
        op = clause.operator
        if op is sa_ops.is_:
            return actual is expected
        if op is sa_ops.is_not:
            return actual is not expected
        if op is sa_ops.in_op:
            return actual in expected
        if op is sa_ops.not_in_op:
            return actual not in expected
        return bool(op(actual, expected))
    raise AssertionError(f"fake session cannot evaluate {clause!r}")


class _QueryAwareDb:
    """Stand-in for AsyncSession that resolves ``scalar()`` against real rows.

    Returns the first held row whose type matches the statement's entity AND
    whose column values satisfy the statement's WHERE clause — so a router that
    stops filtering on expiry/status starts seeing rows it must not see.
    """

    def __init__(self, *rows: Any) -> None:
        self._rows = rows

    async def scalar(self, stmt: Any, params: Any = None) -> Any:
        entity = stmt.column_descriptions[0]["entity"]
        for row in self._rows:
            if isinstance(row, entity) and _clause_matches(stmt.whereclause, row):
                return row
        return None


_RAW_TOKEN = "raw-exam-token-for-tests"


def _link_rows(**assignment_overrides: Any) -> tuple[Any, ...]:
    """A consistent exam/round/applicant/assignment set for one live magic link."""
    from app.config import settings
    from app.models import Applicant, Exam, ExamAssignment, ExamRound

    company_id, exam_id, round_id, applicant_id = (uuid.uuid4() for _ in range(4))
    now = datetime.now(tz=UTC)
    assignment = ExamAssignment(
        id=uuid.uuid4(),
        company_id=company_id,
        exam_id=exam_id,
        round_id=round_id,
        applicant_id=applicant_id,
        token_hash=hash_exam_token(_RAW_TOKEN, settings.exam_link_secret),
        status=assignment_overrides.pop("status", "invited"),
        expires_at=assignment_overrides.pop("expires_at", now + timedelta(days=3)),
        deleted_at=None,
        **assignment_overrides,
    )
    return (
        assignment,
        Exam(id=exam_id, company_id=company_id, status="published", deleted_at=None),
        ExamRound(id=round_id, company_id=company_id, status="published", deleted_at=None),
        Applicant(id=applicant_id, company_id=company_id, deleted_at=None),
    )


# ---------------------------------------------------------------------------
# get_exam_link_ctx — expiry + revocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_live_link_resolves() -> None:
    """Control case: with the same fake, a live link DOES resolve — so the 404s
    below are the guards firing, not the fake refusing everything."""
    from app.routers.exam_take import get_exam_link_ctx

    rows = _link_rows()
    ctx = await get_exam_link_ctx(_QueryAwareDb(*rows), x_exam_token=_RAW_TOKEN)  # type: ignore[arg-type]
    assert ctx.assignment is rows[0]


@pytest.mark.asyncio
async def test_expired_link_is_404() -> None:
    """A link past expires_at must stop working — a URL mailed six months ago
    cannot still mint exam access."""
    from app.routers.exam_take import get_exam_link_ctx

    rows = _link_rows(expires_at=datetime.now(tz=UTC) - timedelta(seconds=1))
    with pytest.raises(HTTPException) as exc:
        await get_exam_link_ctx(_QueryAwareDb(*rows), x_exam_token=_RAW_TOKEN)  # type: ignore[arg-type]
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_revoked_link_is_404() -> None:
    """HR revocation is immediate: every request re-resolves the row by hash."""
    from app.routers.exam_take import get_exam_link_ctx

    rows = _link_rows(status="revoked")
    with pytest.raises(HTTPException) as exc:
        await get_exam_link_ctx(_QueryAwareDb(*rows), x_exam_token=_RAW_TOKEN)  # type: ignore[arg-type]
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_expired_status_link_is_404() -> None:
    """status='expired' (set by the sweeper) is refused independently of the
    expires_at timestamp."""
    from app.routers.exam_take import get_exam_link_ctx

    rows = _link_rows(status="expired")
    with pytest.raises(HTTPException) as exc:
        await get_exam_link_ctx(_QueryAwareDb(*rows), x_exam_token=_RAW_TOKEN)  # type: ignore[arg-type]
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_soft_deleted_link_is_404() -> None:
    from app.routers.exam_take import get_exam_link_ctx

    rows = _link_rows()
    rows[0].deleted_at = datetime.now(tz=UTC)
    with pytest.raises(HTTPException) as exc:
        await get_exam_link_ctx(_QueryAwareDb(*rows), x_exam_token=_RAW_TOKEN)  # type: ignore[arg-type]
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_wrong_token_does_not_resolve_a_live_row() -> None:
    """The lookup is by keyed hash — a different raw token must not match."""
    from app.routers.exam_take import get_exam_link_ctx

    rows = _link_rows()
    with pytest.raises(HTTPException) as exc:
        await get_exam_link_ctx(_QueryAwareDb(*rows), x_exam_token="some-other-token")  # type: ignore[arg-type]
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# start_attempt — single-use consumption of the round
# ---------------------------------------------------------------------------


def _take_ctx(*, allow_retake: bool) -> Any:
    from app.routers.exam_take import ExamTakeCtx

    assignment, exam, rnd, applicant = _link_rows()
    exam.allow_retake = allow_retake
    rnd.time_limit_seconds = None
    return ExamTakeCtx(
        company_id=assignment.company_id, exam=exam, exam_round=rnd,
        applicant=applicant, assignment=assignment,
    )


@pytest.mark.asyncio
async def test_start_after_submit_is_409_when_retake_disallowed() -> None:
    """Single-use: once the round is submitted the link cannot open a second
    attempt, so a leaked URL cannot be replayed for a fresh sitting."""
    from app.routers.exam_take import start_attempt

    db = AsyncMock()
    db.add = MagicMock()  # Session.add is sync — an AsyncMock would leak a coroutine
    # scalars in order: no in-progress attempt (None), submitted-attempt count (1).
    db.scalar = AsyncMock(side_effect=[None, 1])

    with pytest.raises(HTTPException) as exc:
        await start_attempt(_take_ctx(allow_retake=False), db)
    assert exc.value.status_code == 409
    db.add.assert_not_called()
    db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_after_submit_is_allowed_when_exam_permits_retake() -> None:
    """The 409 above must come from the retake flag, not from any submission."""
    from app.routers.exam_take import start_attempt

    db = AsyncMock()
    db.add = MagicMock()
    # no in-progress attempt, one submitted attempt, then max(attempt_no) = 1.
    db.scalar = AsyncMock(side_effect=[None, 1, 1])

    out = await start_attempt(_take_ctx(allow_retake=True), db)
    assert out.attempt_id
    db.commit.assert_awaited_once()
    assert db.add.call_args.args[0].attempt_no == 2


@pytest.mark.asyncio
async def test_start_is_idempotent_for_an_open_attempt() -> None:
    """A reopened tab must rejoin the live attempt, never start a second one."""
    from app.models import ExamAttempt
    from app.routers.exam_take import start_attempt

    open_attempt = ExamAttempt(
        id=uuid.uuid4(), status="in_progress", started_at=datetime.now(tz=UTC)
    )
    db = AsyncMock()
    db.add = MagicMock()
    db.scalar = AsyncMock(return_value=open_attempt)

    out = await start_attempt(_take_ctx(allow_retake=False), db)
    assert out.attempt_id == str(open_attempt.id)
    db.add.assert_not_called()
