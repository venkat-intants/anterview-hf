"""Auto-shortlist eligibility, against a real migrated Postgres.

The unit suite mocks the database, so the SQL that decides who may be advanced
with no person looking is only pinned there by source text. These tests run it
on real rows, created through the real writers (enrol_applicant,
apply_ats_to_enrolment), so a predicate that is wrong in SQL — three-valued
logic, a misnamed column, an ON DELETE SET NULL nobody remembered — fails here.

Collaborators that need a whole published workflow (the workflow loader and
round assignment) are stubbed; everything this file is about is real.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import workflow_runner as wr
from app.applicant_enrichment import apply_ats_to_enrolment
from app.config import settings

pytestmark = pytest.mark.integration

CV_KEY = "applicants/as/cv-1.pdf"


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(database_url=settings.database_url, database_ssl=settings.database_ssl,
                          pool_size=2)
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


@pytest.fixture(autouse=True)
def _stub_workflow(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    monkeypatch.setattr(
        wr, "_load_workflow",
        AsyncMock(return_value={"status": "published", "auto_shortlist": True,
                                "shortlist_ats_threshold": 7}),
    )
    started = AsyncMock(return_value=wr.RunnerOutcome(action="advanced", to_round="Aptitude"))
    monkeypatch.setattr(wr, "on_shortlisted", started)
    return started


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.hr = uuid.uuid4()
        self.candidate = uuid.uuid4()
        self.requisition = uuid.uuid4()
        self.workflow = uuid.uuid4()
        self.applicant = uuid.uuid4()


async def _build(db: AsyncSession, *, consent: str | None = "granted") -> F:
    """A company, an HR manager, and an applicant with their own identity."""
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "h": f.hr, "u": f.candidate, "r": f.requisition, "w": f.workflow,
        "a": f.applicant, "slug": f"as-{tag}", "mh": f"hr-{tag}@as.test",
        "mu": f"cand-{tag}@as.test", "k": CV_KEY,
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'AS co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :mh, :c)",
        "INSERT INTO users (id, email) VALUES (:u, :mu)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO workflows (id, company_id, requisition_id, version, status, created_at,"
        " updated_at) VALUES (:w, :c, :r, 1, 'draft', now(), now())",
        "INSERT INTO applicants (id, company_id, user_id, full_name, target_job_title,"
        " resume_s3_key, resume_text)"
        " VALUES (:a, :c, :u, 'Asha', 'Engineer', :k, 'Five years building payment APIs.')",
    ):
        await db.execute(text(sql), p)
    if consent is not None:
        await db.execute(
            text(
                "INSERT INTO dpdp_consent_ledger"
                " (id, user_id, consent_type, granted, granted_at, purpose, revoked_at)"
                " VALUES (:i, :u, 'application_data', true, now(), 'recruitment',"
                "         CASE WHEN :revoked THEN now() END)"
            ),
            {"i": uuid.uuid4(), "u": f.candidate, "revoked": consent == "revoked"},
        )
    return f


async def _enrol(
    db: AsyncSession, f: F, *, actor: uuid.UUID | None, source: str = "careers_site",
    markers: list[str] | None | str = "clean", scored_key: str = CV_KEY,
) -> uuid.UUID:
    """Enrol through the real writer, then score through the real writer."""
    out = await wr.enrol_applicant(
        db, company_id=f.company, applicant_id=f.applicant, requisition_id=f.requisition,
        target_job_title="Engineer", actor_user_id=actor, source=source,
    )
    eid = uuid.UUID(out.enrolment_id or "")
    # Nothing is published in this fixture, so attach the workflow by hand.
    await db.execute(text("UPDATE enrolments SET workflow_id = :w WHERE id = :e"),
                     {"w": f.workflow, "e": eid})
    score: dict[str, Any] = {"overall": 90}
    if markers == "clean":
        score["injection_markers"] = []
    elif markers is not None:
        score["injection_markers"] = markers
    await apply_ats_to_enrolment(db, enrolment_id=eid, score=score, resume_key=scored_key)
    return eid


async def _shortlist_rows(db: AsyncSession, eid: uuid.UUID) -> list[Any]:
    return list((await db.execute(
        text("SELECT actor_user_id, automated FROM stage_transitions"
             " WHERE enrolment_id = :e AND to_status = 'shortlisted'"),
        {"e": eid},
    )).all())


@pytest.mark.asyncio
async def test_a_self_applicant_with_consent_and_a_clean_cv_is_advanced(db: AsyncSession) -> None:
    f = await _build(db)
    eid = await _enrol(db, f, actor=None)
    out = await wr._auto_shortlist(db, eid)
    assert out.action == "advanced", out.reason
    # Recorded as the system's doing, naming no person.
    assert [(r.actor_user_id, r.automated) for r in await _shortlist_rows(db, eid)] == [(None, True)]


@pytest.mark.asyncio
async def test_an_hr_upload_labelled_referral_is_not_advanced(db: AsyncSession) -> None:
    """Re-audit M5: the source label is HR's choice and proves nothing. The
    applicant even holds their own consent here (they applied elsewhere)."""
    f = await _build(db)
    eid = await _enrol(db, f, actor=f.hr, source="referral")
    out = await wr._auto_shortlist(db, eid)
    assert out.action == "noop"
    assert "HR-created" in (out.reason or "")
    assert await _shortlist_rows(db, eid) == []


@pytest.mark.asyncio
async def test_deleting_the_uploader_does_not_turn_an_upload_into_an_application(
    db: AsyncSession,
) -> None:
    """stage_transitions.actor_user_id is ON DELETE SET NULL. Once the HR account
    is gone the creating row's actor is NULL — `automated` (false) is what still
    says a person filed it."""
    f = await _build(db)
    eid = await _enrol(db, f, actor=f.hr)
    await db.execute(text("DELETE FROM users WHERE id = :h"), {"h": f.hr})
    actor = await db.scalar(
        text("SELECT actor_user_id FROM stage_transitions"
             " WHERE enrolment_id = :e AND from_status IS NULL"),
        {"e": eid},
    )
    assert actor is None  # the precondition this test exists for
    out = await wr._auto_shortlist(db, eid)
    assert out.action == "noop"
    assert "HR-created" in (out.reason or "")


@pytest.mark.asyncio
@pytest.mark.parametrize("consent", [None, "revoked"])
async def test_no_live_application_consent_is_not_advanced(
    db: AsyncSession, consent: str | None
) -> None:
    f = await _build(db, consent=consent)
    eid = await _enrol(db, f, actor=None)
    out = await wr._auto_shortlist(db, eid)
    assert out.action == "noop"
    assert "consent" in (out.reason or "")


@pytest.mark.asyncio
async def test_the_scorers_markers_decline_a_score_on_a_cv_other_than_the_current_one(
    db: AsyncSession,
) -> None:
    """Re-audit H2: a returning applicant's scored CV is not the applicant
    row's. The current CV text is clean; the scorer flagged the one it scored."""
    f = await _build(db)
    eid = await _enrol(db, f, actor=None, markers=["resume:ignore_previous"],
                       scored_key="applicants/as/applied-here.pdf")
    stored = await db.scalar(text("SELECT ats_injection_markers FROM enrolments WHERE id = :e"),
                             {"e": eid})
    assert stored == ["resume:ignore_previous"]
    out = await wr._auto_shortlist(db, eid)
    assert out.action == "noop"
    assert "prompt injection" in (out.reason or "")


@pytest.mark.asyncio
async def test_no_markers_on_record_for_another_cv_is_not_clean(db: AsyncSession) -> None:
    f = await _build(db)
    eid = await _enrol(db, f, actor=None, markers=None,
                       scored_key="applicants/as/applied-here.pdf")
    assert await db.scalar(
        text("SELECT ats_injection_markers IS NULL FROM enrolments WHERE id = :e"), {"e": eid}
    )
    out = await wr._auto_shortlist(db, eid)
    assert out.action == "noop"
    assert "no injection check on record" in (out.reason or "")
