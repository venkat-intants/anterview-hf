"""AR-5, closed: the two append-only triggers that now permit redaction.

``stage_transitions_block_mutation()`` and ``audit_log_block_mutation()``
(migration ``f2a4c6e8b0d3``) each gained exactly one new permitted UPDATE
shape: ``redacted_at`` NULL -> now(), the free-text column (or JSONB key)
moving to the fixed marker ``'[redacted]'``, and nothing else on the row
changing. Everything else these triggers already refused must still be
refused — a redaction exception that also opened the door to an ordinary
edit would be worse than no exception at all.

Against a real, migrated Postgres, on the ``smoke_group_b_ledger.py`` /
``test_ph5_w1_checkins.py`` precedent: a trigger is schema, and a mock cannot
tell you whether a CHECK constraint or a PL/pgSQL function accepts a
statement. Every refusal is checked for its REASON, not just its failure,
so a trigger that started rejecting everything (including the redaction
shape) would not accidentally read as "passing".
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

pytestmark = pytest.mark.integration


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.hr = uuid.uuid4()
        self.candidate_user = uuid.uuid4()
        self.applicant = uuid.uuid4()
        self.req = uuid.uuid4()
        self.enrolment = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    now = datetime.now(tz=UTC)
    p = {
        "c": f.company, "h": f.hr, "u": f.candidate_user, "a": f.applicant,
        "r": f.req, "e": f.enrolment, "slug": f"ar5-{tag}",
        "hm": f"hr-{tag}@ar5.test", "cm": f"candidate-{tag}@ar5.test", "now": now,
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'AR5 co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :hm, :c)",
        "INSERT INTO users (id, email) VALUES (:u, :cm)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, email, target_job_title, user_id)"
        " VALUES (:a, :c, 'Priya', NULL, 'Engineer', :u)",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, status,"
        " target_job_title, created_at, updated_at)"
        " VALUES (:e, :c, :r, :a, 'rejected', 'Engineer', :now, :now)",
    ):
        await db.execute(text(sql), p)
    return f


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            await session.begin()
            try:
                yield session
            finally:
                await session.rollback()
    finally:
        await engine.dispose()


async def _refused(db: AsyncSession, sql: str, params: dict, reason: str) -> None:
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql), params)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        message = str(exc.orig)
        assert reason in message, f"refused for the WRONG reason — expected {reason!r}: {message[:300]}"
        return
    await sp.rollback()
    pytest.fail(f"was allowed, expected refusal ({reason}): {sql[:200]}")


async def _allowed(db: AsyncSession, sql: str, params: dict) -> None:
    sp = await db.begin_nested()
    await db.execute(text(sql), params)
    await sp.commit()


# ===========================================================================
# stage_transitions_block_mutation() — the redaction shape, and only it
# ===========================================================================


async def test_stage_transitions_ordinary_edit_still_refused(db: AsyncSession) -> None:
    f = await _build(db)
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at, reason_code, reason_label)"
            " VALUES (:c, :e, 'interviewed', 'rejected', :h, false, 'a reason', now(),"
            " 'skills_fit', 'Skills fit')"
        ),
        {"c": f.company, "e": f.enrolment, "h": f.hr},
    )
    await _refused(
        db, "UPDATE stage_transitions SET to_status = 'held' WHERE enrolment_id = :e",
        {"e": f.enrolment}, "append-only",
    )


async def test_stage_transitions_rewriting_reason_to_other_text_is_refused(
    db: AsyncSession,
) -> None:
    """Setting redacted_at is not a blank cheque — the new reason must be the marker."""
    f = await _build(db)
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at, reason_code, reason_label)"
            " VALUES (:c, :e, 'interviewed', 'rejected', :h, false, 'a reason', now(),"
            " 'skills_fit', 'Skills fit')"
        ),
        {"c": f.company, "e": f.enrolment, "h": f.hr},
    )
    await _refused(
        db,
        "UPDATE stage_transitions SET reason = 'something else', redacted_at = now()"
        " WHERE enrolment_id = :e",
        {"e": f.enrolment}, "append-only",
    )


async def test_stage_transitions_redaction_shape_is_allowed(db: AsyncSession) -> None:
    f = await _build(db)
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at, reason_code, reason_label)"
            " VALUES (:c, :e, 'interviewed', 'rejected', :h, false, 'a reason', now(),"
            " 'skills_fit', 'Skills fit')"
        ),
        {"c": f.company, "e": f.enrolment, "h": f.hr},
    )
    await _allowed(
        db,
        "UPDATE stage_transitions SET reason = '[redacted]', redacted_at = now()"
        " WHERE enrolment_id = :e",
        {"e": f.enrolment},
    )
    row = (
        await db.execute(
            text(
                "SELECT reason, redacted_at, actor_user_id, to_status FROM stage_transitions"
                " WHERE enrolment_id = :e"
            ),
            {"e": f.enrolment},
        )
    ).mappings().first()
    assert row is not None
    assert row["reason"] == "[redacted]"
    assert row["redacted_at"] is not None
    assert row["actor_user_id"] == f.hr
    assert row["to_status"] == "rejected"


async def test_stage_transitions_redaction_is_one_way(db: AsyncSession) -> None:
    f = await _build(db)
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at, reason_code, reason_label)"
            " VALUES (:c, :e, 'interviewed', 'rejected', :h, false, 'a reason', now(),"
            " 'skills_fit', 'Skills fit')"
        ),
        {"c": f.company, "e": f.enrolment, "h": f.hr},
    )
    await _allowed(
        db,
        "UPDATE stage_transitions SET reason = '[redacted]', redacted_at = now()"
        " WHERE enrolment_id = :e",
        {"e": f.enrolment},
    )
    await _refused(
        db,
        "UPDATE stage_transitions SET reason = '[redacted]', redacted_at = now()"
        " WHERE enrolment_id = :e",
        {"e": f.enrolment}, "append-only",
    )


async def test_stage_transitions_null_reason_stays_null_on_redaction(db: AsyncSession) -> None:
    """A transition with no reason at all (an automated move) may still be
    stamped redacted_at without inventing a marker where nothing was written."""
    f = await _build(db)
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at)"
            " VALUES (:c, :e, 'new', 'shortlisted', NULL, true, NULL, now())"
        ),
        {"c": f.company, "e": f.enrolment},
    )
    await _allowed(
        db,
        "UPDATE stage_transitions SET reason = NULL, redacted_at = now()"
        " WHERE enrolment_id = :e AND reason IS NULL",
        {"e": f.enrolment},
    )


async def test_stage_transitions_check_constraint_is_a_second_guard(db: AsyncSession) -> None:
    """Even with the trigger disabled, the CHECK constraint alone refuses live
    prose sitting next to a non-NULL redacted_at."""
    f = await _build(db)
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at, reason_code, reason_label)"
            " VALUES (:c, :e, 'interviewed', 'rejected', :h, false, 'a reason', now(),"
            " 'skills_fit', 'Skills fit')"
        ),
        {"c": f.company, "e": f.enrolment, "h": f.hr},
    )
    await db.execute(text("ALTER TABLE stage_transitions DISABLE TRIGGER stage_transitions_no_mutation"))
    try:
        sp = await db.begin_nested()
        try:
            await db.execute(
                text(
                    "UPDATE stage_transitions SET redacted_at = now() WHERE enrolment_id = :e"
                ),
                {"e": f.enrolment},
            )
            await db.flush()
        except DBAPIError as exc:
            await sp.rollback()
            assert "ck_stage_transitions_redacted" in str(exc.orig)
        else:
            await sp.rollback()
            pytest.fail("the CHECK constraint let live prose through with redacted_at set")
    finally:
        await db.execute(text("ALTER TABLE stage_transitions ENABLE TRIGGER stage_transitions_no_mutation"))


# ===========================================================================
# audit_log_block_mutation() — the redaction shape, and only it
# ===========================================================================

_AUDIT_INSERT = (
    "INSERT INTO audit_log (event_id, actor_id, actor_type, action, resource_type,"
    " resource_id, details, event_ts)"
    " VALUES (:id, :h, 'user', 'enrolment.decision.rejected', 'enrolment', :e,"
    " jsonb_build_object('reason', 'a rationale',"
    " 'reason_code', 'skills_fit', 'reason_label', 'Skills fit', 'reversal', false), now())"
)


async def test_audit_log_unrelated_column_edit_still_refused(db: AsyncSession) -> None:
    f = await _build(db)
    event_id = uuid.uuid4()
    await db.execute(text(_AUDIT_INSERT), {"id": event_id, "h": f.hr, "e": f.enrolment})
    await _refused(
        db, "UPDATE audit_log SET actor_type = 'admin' WHERE event_id = :id",
        {"id": event_id}, "append-only",
    )


async def test_audit_log_rewriting_reason_to_other_text_is_refused(db: AsyncSession) -> None:
    f = await _build(db)
    event_id = uuid.uuid4()
    await db.execute(text(_AUDIT_INSERT), {"id": event_id, "h": f.hr, "e": f.enrolment})
    await _refused(
        db,
        "UPDATE audit_log SET details = jsonb_set(details, '{reason}', '\"leaked\"'),"
        " redacted_at = now() WHERE event_id = :id",
        {"id": event_id}, "append-only",
    )


async def test_audit_log_touching_a_different_key_is_refused(db: AsyncSession) -> None:
    """The marker on `reason` does not buy a free edit to `reason_code` too."""
    f = await _build(db)
    event_id = uuid.uuid4()
    await db.execute(text(_AUDIT_INSERT), {"id": event_id, "h": f.hr, "e": f.enrolment})
    await _refused(
        db,
        "UPDATE audit_log SET details = jsonb_set("
        "  jsonb_set(details, '{reason}', '\"[redacted]\"'), '{reason_code}', '\"tampered\"'"
        "), redacted_at = now() WHERE event_id = :id",
        {"id": event_id}, "append-only",
    )


async def test_audit_log_redaction_shape_is_allowed(db: AsyncSession) -> None:
    f = await _build(db)
    event_id = uuid.uuid4()
    await db.execute(text(_AUDIT_INSERT), {"id": event_id, "h": f.hr, "e": f.enrolment})
    await _allowed(
        db,
        "UPDATE audit_log SET details = jsonb_set(details, '{reason}', '\"[redacted]\"'::jsonb),"
        " redacted_at = now() WHERE event_id = :id",
        {"id": event_id},
    )
    row = (
        await db.execute(
            text("SELECT details, redacted_at FROM audit_log WHERE event_id = :id"),
            {"id": event_id},
        )
    ).mappings().first()
    assert row is not None
    assert row["details"]["reason"] == "[redacted]"
    assert row["details"]["reason_code"] == "skills_fit"
    assert row["details"]["reason_label"] == "Skills fit"
    assert row["redacted_at"] is not None


async def test_audit_log_redaction_is_one_way(db: AsyncSession) -> None:
    f = await _build(db)
    event_id = uuid.uuid4()
    await db.execute(text(_AUDIT_INSERT), {"id": event_id, "h": f.hr, "e": f.enrolment})
    await _allowed(
        db,
        "UPDATE audit_log SET details = jsonb_set(details, '{reason}', '\"[redacted]\"'::jsonb),"
        " redacted_at = now() WHERE event_id = :id",
        {"id": event_id},
    )
    await _refused(
        db,
        "UPDATE audit_log SET details = jsonb_set(details, '{reason}', '\"[redacted]\"'::jsonb),"
        " redacted_at = now() WHERE event_id = :id",
        {"id": event_id}, "append-only",
    )


async def test_audit_log_rationale_key_also_covered(db: AsyncSession) -> None:
    """applicant.decision.* rows carry `rationale`, not `reason` — same shape."""
    f = await _build(db)
    event_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO audit_log (event_id, actor_id, actor_type, action, resource_type,"
            " resource_id, details, event_ts)"
            " VALUES (:id, :h, 'user', 'applicant.decision.rejected', 'applicant', :a,"
            " jsonb_build_object('rationale', 'notice period too long'), now())"
        ),
        {"id": event_id, "h": f.hr, "a": f.applicant},
    )
    await _allowed(
        db,
        "UPDATE audit_log SET details = jsonb_set(details, '{rationale}', '\"[redacted]\"'::jsonb),"
        " redacted_at = now() WHERE event_id = :id",
        {"id": event_id},
    )


async def test_audit_log_check_constraint_is_a_second_guard(db: AsyncSession) -> None:
    """Even with the trigger disabled, the CHECK constraint alone refuses live
    prose sitting next to a non-NULL redacted_at."""
    f = await _build(db)
    event_id = uuid.uuid4()
    await db.execute(text(_AUDIT_INSERT), {"id": event_id, "h": f.hr, "e": f.enrolment})
    await db.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_mutation"))
    try:
        sp = await db.begin_nested()
        try:
            await db.execute(
                text("UPDATE audit_log SET redacted_at = now() WHERE event_id = :id"),
                {"id": event_id},
            )
            await db.flush()
        except DBAPIError as exc:
            await sp.rollback()
            assert "ck_audit_log_redacted" in str(exc.orig)
        else:
            await sp.rollback()
            pytest.fail("the CHECK constraint let live prose through with redacted_at set")
    finally:
        await db.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_mutation"))
