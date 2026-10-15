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
from sqlalchemy.exc import DBAPIError
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
                " (id, user_id, consent_type, granted, granted_at, purpose, revoked_at, evidence)"
                " VALUES (:i, :u, 'application_data', true, now(), 'recruitment',"
                "         CASE WHEN :revoked THEN now() END,"
                "         jsonb_build_object('company_id', CAST(:co AS text)))"
            ),
            {"i": uuid.uuid4(), "u": f.candidate, "revoked": consent == "revoked",
             # The shape public_apply._record_apply_consent writes.
             "co": str(uuid.uuid4() if consent == "other_company" else f.company)},
        )
    return f


async def _enrol(
    db: AsyncSession, f: F, *, actor: uuid.UUID | None, source: str = "careers_site",
    markers: list[str] | None | str = "clean", scored_key: str = CV_KEY,
    reason: str | None = None, verified: str | None = "after",
) -> uuid.UUID:
    """Enrol through the real writer, then score through the real writer.

    `reason` defaults to what the caller would write: the public form's marker
    for an actor-less (self) application, HR's wording otherwise. `verified`
    stamps the applicant's address as proven after ("after") or before
    ("before") the enrolment, or leaves it unproven (None).
    """
    if verified == "before":
        await db.execute(text("UPDATE users SET email_verified_at = now() - interval '1 day'"
                              " WHERE id = :u"), {"u": f.candidate})
    out = await wr.enrol_applicant(
        db, company_id=f.company, applicant_id=f.applicant, requisition_id=f.requisition,
        target_job_title="Engineer", actor_user_id=actor, source=source,
        reason=reason or (wr.PUBLIC_APPLY_REASON if actor is None else "added by HR upload"),
    )
    eid = uuid.UUID(out.enrolment_id or "")
    if verified == "after":
        # clock_timestamp(), not now(): now() is the transaction start, which
        # precedes the enrolment's created_at (stamped in Python).
        await db.execute(
            text("UPDATE users SET email_verified_at = clock_timestamp() + interval '1 second'"
                 " WHERE id = :u"),
            {"u": f.candidate},
        )
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


# ---------------------------------------------------------------------------
# Code review SHOULD FIX 2 — the rollback, the CHECK and the publish cap, on
# a real database rather than against mocks.
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_shortlist_whose_round_did_not_start_is_undone_in_the_database(
    db: AsyncSession, _stub_workflow: AsyncMock
) -> None:
    """The savepoint must take the status change AND its ledger row with it,
    leaving the applicant 'new' where HR's queue will show them."""
    _stub_workflow.return_value = wr.RunnerOutcome(action="noop", reason="workflow has no rounds")
    f = await _build(db)
    eid = await _enrol(db, f, actor=None)

    out = await wr.maybe_auto_shortlist(db, enrolment_id=eid)

    assert out.action == "noop"
    assert (out.reason or "").startswith("undone:")
    assert await db.scalar(text("SELECT status FROM enrolments WHERE id = :e"), {"e": eid}) == "new"
    assert await _shortlist_rows(db, eid) == []
    # The surrounding transaction is still usable — the caller goes on to
    # commit the score this call was made after.
    assert await db.scalar(text("SELECT ats_overall FROM enrolments WHERE id = :e"),
                           {"e": eid}) == 90


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("threshold", "assign"),
    [(None, True), (0, True), (7, False)],
    ids=["no-bar", "zero-bar", "first-round-manual"],
)
async def test_the_check_refuses_every_incoherent_auto_shortlist(
    db: AsyncSession, threshold: int | None, assign: bool
) -> None:
    f = await _build(db)
    sp = await db.begin_nested()
    with pytest.raises(DBAPIError, match="ck_workflows_auto_shortlist_needs_threshold"):
        await db.execute(
            text(
                "UPDATE workflows SET auto_shortlist = true, shortlist_ats_threshold = :t,"
                " auto_assign_first_round = :a WHERE id = :w"
            ),
            {"t": threshold, "a": assign, "w": f.workflow},
        )
    await sp.rollback()
    # ... and the coherent combination is accepted.
    await db.execute(
        text("UPDATE workflows SET auto_shortlist = true, shortlist_ats_threshold = 7,"
             " auto_assign_first_round = true WHERE id = :w"),
        {"w": f.workflow},
    )


@pytest.mark.asyncio
async def test_publish_offers_at_most_the_limit_oldest_first_and_counts_the_rest(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Real enrolments through the real attach query; the publish validation
    and the per-applicant runner are stubbed, because this is about which
    applicants are offered and how many."""
    from app.routers import hr_workflows
    from app.workflows import ValidationReport

    f = await _build(db)
    limit = hr_workflows.PUBLISH_AUTO_SHORTLIST_LIMIT
    total = limit + 5
    ids: list[uuid.UUID] = []
    for i in range(total):
        a, e = uuid.uuid4(), uuid.uuid4()
        await db.execute(
            text("INSERT INTO applicants (id, company_id, full_name, target_job_title)"
                 " VALUES (:a, :c, 'P', 'Engineer')"),
            {"a": a, "c": f.company},
        )
        await db.execute(
            text("INSERT INTO enrolments (id, company_id, requisition_id, applicant_id,"
                 " target_job_title, status, created_at, updated_at)"
                 " VALUES (:e, :c, :r, :a, 'Engineer', 'new',"
                 "         now() - make_interval(secs => :age), now())"),
            {"e": e, "c": f.company, "r": f.requisition, "a": a, "age": total - i},
        )
        ids.append(e)  # oldest first

    offered: list[uuid.UUID] = []

    async def offer(_db: Any, *, enrolment_id: uuid.UUID) -> wr.RunnerOutcome:
        offered.append(enrolment_id)
        return wr.RunnerOutcome(action="noop")

    monkeypatch.setattr(hr_workflows, "maybe_auto_shortlist", offer)
    monkeypatch.setattr(hr_workflows, "publish", AsyncMock(return_value=ValidationReport()))
    monkeypatch.setattr(hr_workflows, "_profile_competencies", AsyncMock(return_value=None))
    monkeypatch.setattr(hr_workflows, "get_workflow", AsyncMock(return_value={}))
    monkeypatch.setattr(db, "commit", AsyncMock())  # keep it inside the test transaction

    out = await hr_workflows.publish_workflow(f.workflow, (f.hr, f.company), db)

    assert out["attached_candidates"] == total
    assert out["auto_shortlist_deferred"] == total - limit
    assert offered == ids[:limit]


# ---------------------------------------------------------------------------
# Security re-audit #2 — M-1, M-2, L-1, and the activation hook
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("verified", [None, "before"], ids=["never", "before-this-application"])
async def test_an_address_not_proven_since_this_application_is_not_advanced(
    db: AsyncSession, verified: str | None
) -> None:
    """M-1. Anyone can type an address into the public form. Without proof of
    the mailbox AFTER this application, it may not be the owner applying."""
    f = await _build(db)
    eid = await _enrol(db, f, actor=None, verified=verified)
    out = await wr._auto_shortlist(db, eid)
    assert out.action == "noop"
    assert "confirmed their email" in (out.reason or "")


@pytest.mark.asyncio
async def test_consent_given_to_another_company_does_not_count(db: AsyncSession) -> None:
    """M-2."""
    f = await _build(db, consent="other_company")
    eid = await _enrol(db, f, actor=None)
    out = await wr._auto_shortlist(db, eid)
    assert out.action == "noop"
    assert "consent" in (out.reason or "")


@pytest.mark.asyncio
async def test_an_actorless_row_that_is_not_the_public_form_is_not_a_self_application(
    db: AsyncSession,
) -> None:
    """L-1. A bulk upload whose uploader is unknown writes a NULL-actor,
    automated creating row — indistinguishable without the public form's
    marker. (bulk_ingest passes its own reason.)"""
    f = await _build(db)
    eid = await _enrol(db, f, actor=None, reason="added by HR bulk upload")
    out = await wr._auto_shortlist(db, eid)
    assert out.action == "noop"
    assert "HR-created" in (out.reason or "")


@pytest.mark.asyncio
async def test_claiming_the_account_offers_the_waiting_application(
    db: AsyncSession, _stub_workflow: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The score is usually written before the candidate activates, when the
    proven-address check can only decline. Activation is when it becomes true,
    so activation must offer the application again — or nobody is ever
    auto-shortlisted. Driven through activate() itself; only the token lookup
    and its consumption are stubbed (the token is not what is under test)."""
    from app import apply_activation

    f = await _build(db)
    eid = await _enrol(db, f, actor=None, verified=None)
    assert (await wr._auto_shortlist(db, eid)).action == "noop"  # not yet proven

    address = await db.scalar(text("SELECT email FROM users WHERE id = :u"), {"u": f.candidate})
    await db.execute(text("UPDATE applicants SET email = :e WHERE id = :a"),
                     {"e": address, "a": f.applicant})
    monkeypatch.setattr(apply_activation, "_live_token_user", AsyncMock(return_value=f.candidate))
    monkeypatch.setattr(apply_activation, "_consume", AsyncMock())
    monkeypatch.setattr(apply_activation.settings, "password_hash_rounds", 4)

    out = await apply_activation.activate(db, raw_token="t", new_password="a-long-test-passphrase")

    assert out["linked"] == "new"
    assert [(r.actor_user_id, r.automated) for r in await _shortlist_rows(db, eid)] == [(None, True)]
    _stub_workflow.assert_awaited_once()
