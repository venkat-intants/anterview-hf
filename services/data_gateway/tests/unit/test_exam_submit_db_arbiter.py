"""The exam submit's DB-level guarantees, with every collaborator faked.

* The persist step locks the attempt row, so a second submit whose Redis claim
  failed open (or whose 180 s TTL lapsed mid-grade) returns the stored result
  instead of persisting, advancing and notifying twice.
* ``advance_applicant_to_interview`` runs in a SAVEPOINT, so a SQL failure inside
  it cannot poison the transaction the graded attempt is committed in.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.routers import exam_take


class _Savepoint:
    def __init__(self, log: list[str]) -> None:
        self.log = log

    async def __aenter__(self) -> None:
        self.log.append("savepoint:enter")

    async def __aexit__(self, exc_type: Any, *_a: Any) -> bool:
        self.log.append(f"savepoint:exit:{exc_type.__name__ if exc_type else 'ok'}")
        return False  # never swallow; the caller's except does that


def _attempt(status: str) -> SimpleNamespace:
    now = datetime.now(tz=UTC)
    return SimpleNamespace(
        id=uuid.uuid4(), status=status, started_at=now, submitted_at=now,
        extra_time_seconds=0, score_raw=3, score_max=5, score_percent=60, passed=True,
        answers=None, graded_snapshot=None, updated_at=None,
    )


def _ctx(advances: bool) -> Any:
    return SimpleNamespace(
        company_id=uuid.uuid4(),
        applicant=SimpleNamespace(id=uuid.uuid4(), full_name="A B"),
        assignment=SimpleNamespace(
            id=uuid.uuid4(), enrolment_id=None, created_by_user_id=None,
            status="open", consumed_at=None, updated_at=None,
        ),
        exam=SimpleNamespace(
            id=uuid.uuid4(), title="E", created_by_user_id=None, auto_advance_on_pass=True
        ),
        exam_round=SimpleNamespace(
            id=uuid.uuid4(), title="R", pass_threshold=0, advances_to_interview=advances
        ),
    )


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    m = exam_take
    log: list[str] = []
    monkeypatch.setattr(m, "get_redis", lambda: (_ for _ in ()).throw(RuntimeError("no redis")))
    monkeypatch.setattr(m, "_deadline", lambda *a, **k: None)
    monkeypatch.setattr(m, "_round_sections", AsyncMock(return_value=[]))
    monkeypatch.setattr(m, "_mcq_for_sections", AsyncMock(return_value=[]))
    monkeypatch.setattr(m, "_coding_for_sections", AsyncMock(return_value=[]))
    monkeypatch.setattr(m, "enrolment_awaiting_exam_round", AsyncMock(return_value=None))
    notify = AsyncMock()
    monkeypatch.setattr(m, "create_notification", notify)
    advance = AsyncMock()
    monkeypatch.setattr(m, "advance_applicant_to_interview", advance)
    return SimpleNamespace(log=log, notify=notify, advance=advance)


def _db(log: list[str], scalars: list[Any]) -> MagicMock:
    db = MagicMock()
    db.scalar = AsyncMock(side_effect=scalars)
    db.commit = AsyncMock(side_effect=lambda: log.append("commit"))
    db.begin_nested = lambda: _Savepoint(log)
    return db


async def test_second_submit_returns_stored_result_and_persists_nothing(
    wired: SimpleNamespace,
) -> None:
    live = _attempt("in_progress")
    done = _attempt("submitted")  # what the row says once the first submit committed
    db = _db(wired.log, [done, None])
    out = await exam_take._grade_and_finalize(db, _ctx(True), live, {}, {})  # type: ignore[arg-type]

    assert out.status == "submitted"
    assert out.score_percent == 60
    wired.advance.assert_not_awaited()
    wired.notify.assert_not_awaited()
    # the re-read that decides who persists must take a row lock
    stmt = db.scalar.await_args_list[0].args[0]
    assert "FOR UPDATE" in str(stmt.compile(dialect=postgresql.dialect()))
    # and must see the committed row, not a stale identity-map copy
    assert stmt.get_execution_options().get("populate_existing") is True


async def test_advance_failure_runs_in_a_savepoint_and_the_grade_still_commits(
    wired: SimpleNamespace,
) -> None:
    live = _attempt("in_progress")
    fresh = _attempt("in_progress")
    fresh.id = live.id
    wired.advance.side_effect = RuntimeError("sql blew up")
    db = _db(wired.log, [fresh, SimpleNamespace(status="open", consumed_at=None, updated_at=None)])
    ctx = _ctx(True)
    ctx.exam_round.pass_threshold = 0  # 0 of 0 -> percent 0 >= 0 -> passed

    out = await exam_take._grade_and_finalize(db, ctx, live, {}, {})  # type: ignore[arg-type]

    assert out.status == "submitted"
    assert wired.log.count("savepoint:enter") == 1
    assert "savepoint:exit:RuntimeError" in wired.log
    assert wired.log[-1] == "commit"  # the attempt still committed after the failure
    assert fresh.status == "submitted"
