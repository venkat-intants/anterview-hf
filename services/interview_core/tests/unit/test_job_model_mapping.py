"""Regression: every live interview ran as a generic "the role" interview.

``interview_worker._lookup_session`` reads ``job.competencies``,
``job.department`` and ``job.interview_type``. interview_core's ``Job`` model did
not map them, so the read raised AttributeError, the function's broad ``except``
swallowed it (logging only the exception *type*) and returned a default
``SessionContext`` with no title, JD, resume or skills. Found by the 2026-10 load
test; nothing in the suite caught it because ``_lookup_session`` needs a database
and was only ever pinned by source inspection.

These tests exercise the real function against fake DB plumbing, using REAL ORM
instances. Constructing ``Job(competencies=...)`` itself raises TypeError when the
column is unmapped, so the fixture cannot paper over the bug it guards.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import pytest

from app import database
from app.models import Job, User
from app.models import Session as InterviewSession
from app.worker import interview_worker
from app.worker.interview_worker import SessionContext, _lookup_session

_SID = uuid.uuid4()
_UID = uuid.uuid4()
_JID = uuid.uuid4()


class _Result:
    def __init__(self, row: object) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object:
        return self._row


class _FakeDb:
    """Answers the three SELECTs _lookup_session issues, in order."""

    def __init__(self, rows: list[object]) -> None:
        self._rows = list(rows)

    async def __aenter__(self) -> _FakeDb:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def execute(self, _stmt: object) -> _Result:
        return _Result(self._rows.pop(0))


def _install(monkeypatch: pytest.MonkeyPatch, db: _FakeDb) -> None:
    monkeypatch.setattr(database, "init_engine", lambda: None)
    monkeypatch.setattr(database, "get_session_factory", lambda: (lambda: db))


def _rows() -> list[object]:
    sess = InterviewSession(
        id=_SID,
        user_id=_UID,
        job_id=_JID,
        language="te",
        presenter_id=None,
        avatar_mode="avatar",
    )
    user = User(id=_UID, resume_text="Five years of welding at L&T.")
    job = Job(
        id=_JID,
        title="Senior Welder",
        description="Weld pressure vessels to ASME standards.",
        level="senior",
        company_name="Acme Fabrication",
        competencies={"required": ["TIG welding", "blueprint reading"]},
        department="Fabrication",
        interview_type="technical",
    )
    return [sess, user, job]


def test_job_model_maps_the_columns_the_worker_reads() -> None:
    cols = Job.__table__.c
    for name in ("competencies", "department", "interview_type"):
        assert name in cols, f"jobs.{name} is read by _lookup_session but not mapped"
    assert cols["competencies"].nullable is False
    assert cols["department"].nullable is True
    assert cols["interview_type"].nullable is False
    assert cols["interview_type"].type.length == 16


async def test_lookup_returns_the_real_job_not_the_generic_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install(monkeypatch, _FakeDb(_rows()))

    ctx = await _lookup_session(str(_SID))

    assert ctx != SessionContext()
    assert ctx.job_title == "Senior Welder"
    assert ctx.jd_text == "Weld pressure vessels to ASME standards."
    assert ctx.resume_text == "Five years of welding at L&T."
    assert ctx.company_name == "Acme Fabrication"
    assert ctx.required_skills == ["TIG welding", "blueprint reading"]
    assert ctx.department == "Fabrication"
    assert ctx.interview_type == "technical"
    assert ctx.experience_level == "senior"
    assert ctx.language == "te"
    assert ctx.session_id == str(_SID)


async def test_a_programming_error_is_logged_loudly_with_its_message(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The fallback still keeps the interview alive, but it must say WHY, at
    ERROR, with the message - the old log carried only 'AttributeError'."""
    rows = _rows()
    job = rows[2]
    assert isinstance(job, Job)

    class _Broken:
        """A job object missing a field the worker reads."""

        def __getattr__(self, name: str) -> Any:
            raise AttributeError(f"'Job' object has no attribute '{name}'")

    rows[2] = _Broken()
    _install(monkeypatch, _FakeDb(rows))

    with caplog.at_level(logging.WARNING, logger=interview_worker.logger.name):
        ctx = await _lookup_session(str(_SID))

    assert ctx == SessionContext()  # still degrades rather than raising
    records = [r for r in caplog.records if "_lookup_session failed" in r.getMessage()]
    assert records, "the swallowed exception was not logged at all"
    record = records[0]
    assert record.levelno == logging.ERROR
    assert "AttributeError" in record.getMessage()
    assert "has no attribute" not in record.getMessage()  # type only, never the message
    assert record.exc_info is not None  # traceback attached


async def test_a_db_outage_is_a_warning_not_an_error(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _Down(_FakeDb):
        async def execute(self, _stmt: object) -> _Result:
            raise ConnectionError("db unreachable")

    _install(monkeypatch, _Down([]))
    with caplog.at_level(logging.WARNING, logger=interview_worker.logger.name):
        ctx = await _lookup_session(str(_SID))

    assert ctx == SessionContext()
    records = [r for r in caplog.records if "_lookup_session failed" in r.getMessage()]
    assert records
    assert records[0].levelno == logging.WARNING
    assert "db unreachable" not in records[0].getMessage()
    assert "exc_type=ConnectionError" in records[0].getMessage()


@pytest.mark.asyncio
async def test_a_driver_error_logs_its_sqlstate_but_never_its_parameters(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """SQLAlchemy/asyncpg text can carry '[parameters: (...)]'; ERROR lines reach
    Sentry (US). Type + SQLSTATE say what failed without echoing data (L1)."""

    class _OrigError(Exception):
        sqlstate = "57P01"

    class _DriverError(Exception):
        orig = _OrigError("terminating connection")

    class _Leaky(_FakeDb):
        async def execute(self, _stmt: object) -> _Result:
            raise _DriverError("SELECT ... [parameters: ('priya@example.com', 'resume text')]")

    _install(monkeypatch, _Leaky([]))
    with caplog.at_level(logging.WARNING, logger=interview_worker.logger.name):
        await _lookup_session(str(_SID))

    message = next(r for r in caplog.records if "_lookup_session failed" in r.getMessage()).getMessage()
    assert "sqlstate=57P01" in message
    assert "driver_type=_OrigError" in message
    assert "priya@example.com" not in message
    assert "resume text" not in message
