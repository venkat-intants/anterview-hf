"""PH4 Wave 4: the offer and preboarding guarantees that hold AT THE DATABASE.

An offer starts as a draft, is approved by someone other than its author and
submitter, is sent with a link and a deadline, cannot be accepted after it,
and once answered stays answered; its terms are frozen from submission; a
hire is undone only by a rejection. A document arrives as submitted, its file
never changes, it is reviewed by a named person and never verified expired;
preboarding completes only with every mandatory document verified; the
histories are append-only, and an HRMS payload leaves only with erasure.

Against a real, migrated Postgres. Every refusal is checked for its REASON.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

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
        self.hr, self.hr2, self.admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.req, self.applicant, self.enrolment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.offer = uuid.uuid4()
        self.req_doc, self.opt_doc = uuid.uuid4(), uuid.uuid4()


async def _build(db: AsyncSession, *, status: str = "hired") -> F:
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "h": f.hr, "h2": f.hr2, "ad": f.admin, "r": f.req, "a": f.applicant,
        "e": f.enrolment, "o": f.offer, "rq": f.req_doc, "op": f.opt_doc, "st": status,
        "slug": f"w4-{tag}", "m1": f"h-{tag}@w4.test", "m2": f"h2-{tag}@w4.test",
        "m3": f"ad-{tag}@w4.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'W4 co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :m1, :c), (:h2, :m2, :c),"
        " (:ad, :m3, :c)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title)"
        " VALUES (:a, :c, 'Asha', 'Engineer')",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
        " status, created_at, updated_at) VALUES (:e, :c, :r, :a, 'Engineer', :st, now(), now())",
        "INSERT INTO offers (id, company_id, enrolment_id, applicant_id, requisition_id,"
        " job_title, base_salary, created_by_user_id) VALUES (:o, :c, :e, :a, :r, 'Engineer',"
        " 1200000, :h)",
        "INSERT INTO document_requirements (id, company_id, requisition_id, name, doc_type,"
        " mandatory, requires_expiry, created_at, updated_at) VALUES"
        " (:rq, :c, :r, 'Passport', 'identity', true, true, now(), now()),"
        " (:op, :c, :r, 'Photo', 'photo', false, false, now(), now())",
    ):
        await db.execute(text(sql), p)
    return f


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


async def _refused(db: AsyncSession, sql: str, params: dict[str, Any], reason: str) -> None:
    sp = await db.begin_nested()
    try:
        await db.execute(text(sql), params)
        await db.flush()
    except DBAPIError as exc:
        await sp.rollback()
        message = str(exc.orig)
        assert reason in message, f"refused for the WRONG reason — expected {reason!r}: {message[:240]}"
        return
    await sp.rollback()
    pytest.fail(f"was allowed, expected refusal ({reason}): {sql[:90]}")


async def _allowed(db: AsyncSession, sql: str, params: dict[str, Any]) -> None:
    sp = await db.begin_nested()
    await db.execute(text(sql), params)
    await sp.commit()


SUBMIT = "UPDATE offers SET status = 'pending_approval', submitted_by_user_id = :u WHERE id = :o"
DECIDE = "UPDATE offers SET status = :s, decided_by_user_id = :u WHERE id = :o"
SEND = ("UPDATE offers SET status = 'sent', token_hash = :t, sent_at = now(),"
        " expires_at = now() + interval '7 days' WHERE id = :o")
ACCEPT = ("UPDATE offers SET status = 'accepted', responded_at = now(), accepted_name = 'Asha'"
          " WHERE id = :o")


async def _to(db: AsyncSession, f: F, state: str) -> None:
    """Walk the offer to ``state`` the only way the database allows."""
    if state == "draft":
        return
    await db.execute(text(SUBMIT), {"u": f.hr, "o": f.offer})
    if state == "pending_approval":
        return
    await db.execute(text(DECIDE), {"s": "approved", "u": f.admin, "o": f.offer})
    if state == "approved":
        return
    await db.execute(text(SEND), {"t": uuid.uuid4().hex, "o": f.offer})
    if state == "sent":
        return
    await db.execute(text(ACCEPT), {"o": f.offer})


# ===========================================================================
# The offer lifecycle
# ===========================================================================
@pytest.mark.asyncio
async def test_an_offer_is_born_a_draft(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, "INSERT INTO offers (id, company_id, enrolment_id, applicant_id, job_title,"
            " base_salary, status) VALUES (gen_random_uuid(), :c, :e, :a, 'X', 1, 'sent')",
        {"c": f.company, "e": f.enrolment, "a": f.applicant}, "starts as a draft",
    )


@pytest.mark.asyncio
async def test_no_step_can_be_skipped(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(db, DECIDE, {"s": "approved", "u": f.admin, "o": f.offer},
                   "cannot go from draft to approved")
    await _refused(db, SEND, {"t": "t", "o": f.offer}, "cannot go from draft to sent")
    await _to(db, f, "approved")
    await _refused(db, ACCEPT, {"o": f.offer}, "cannot go from approved to accepted")


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["hr", "hr2"])
async def test_nobody_approves_an_offer_they_wrote_or_submitted(db: AsyncSession, who: str) -> None:
    f = await _build(db)
    # hr wrote it; hr2 submits it — neither may approve.
    await db.execute(text(SUBMIT), {"u": f.hr2, "o": f.offer})
    await _refused(db, DECIDE, {"s": "approved", "u": getattr(f, who), "o": f.offer},
                   "approved by someone other than its author and submitter")
    await _allowed(db, DECIDE, {"s": "approved", "u": f.admin, "o": f.offer})


@pytest.mark.asyncio
async def test_an_offer_is_sent_with_a_link_and_a_future_deadline(db: AsyncSession) -> None:
    f = await _build(db)
    await _to(db, f, "approved")
    await _refused(db, "UPDATE offers SET status = 'sent', sent_at = now(),"
                       " expires_at = now() + interval '1 day' WHERE id = :o",
                   {"o": f.offer}, "sent with a link and an expiry")
    await _refused(db, "UPDATE offers SET status = 'sent', token_hash = 'x', sent_at = now(),"
                       " expires_at = now() - interval '1 minute' WHERE id = :o",
                   {"o": f.offer}, "sent with a link and an expiry")


@pytest.mark.asyncio
async def test_an_expired_offer_cannot_be_accepted(db: AsyncSession) -> None:
    f = await _build(db)
    await _to(db, f, "sent")
    # Let the deadline pass. now() is fixed for a transaction and a sent
    # offer's deadline is frozen, so the clock is simulated by lifting the
    # trigger for one write — inside this test's transaction, which is always
    # rolled back, so no other session ever sees it lifted.
    await db.execute(text("ALTER TABLE offers DISABLE TRIGGER offers_lifecycle"))
    await db.execute(text("UPDATE offers SET expires_at = now() - interval '1 second'"
                          " WHERE id = :o"), {"o": f.offer})
    await db.execute(text("ALTER TABLE offers ENABLE TRIGGER offers_lifecycle"))
    await _refused(db, ACCEPT, {"o": f.offer}, "has expired and can no longer be accepted")
    await _allowed(db, "UPDATE offers SET status = 'expired' WHERE id = :o", {"o": f.offer})


@pytest.mark.asyncio
async def test_an_offer_cannot_expire_early(db: AsyncSession) -> None:
    f = await _build(db)
    await _to(db, f, "sent")
    await _refused(db, "UPDATE offers SET status = 'expired' WHERE id = :o", {"o": f.offer},
                   "has not reached its expiry")


@pytest.mark.asyncio
async def test_an_answer_is_final(db: AsyncSession) -> None:
    f = await _build(db)
    await _to(db, f, "accepted")
    await _refused(db, "UPDATE offers SET status = 'declined' WHERE id = :o", {"o": f.offer},
                   "is accepted and final")
    await _refused(db, "UPDATE offers SET status = 'withdrawn' WHERE id = :o", {"o": f.offer},
                   "is accepted and final")
    await _refused(db, "UPDATE offers SET accepted_name = 'Someone else' WHERE id = :o",
                   {"o": f.offer}, "the answer cannot be rewritten")


@pytest.mark.asyncio
async def test_a_declined_offer_cannot_be_accepted(db: AsyncSession) -> None:
    f = await _build(db)
    await _to(db, f, "sent")
    await db.execute(text("UPDATE offers SET status = 'declined', responded_at = now()"
                          " WHERE id = :o"), {"o": f.offer})
    await _refused(db, ACCEPT, {"o": f.offer}, "is declined and final")


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending_approval", "approved", "sent"])
async def test_what_was_approved_is_what_is_offered(db: AsyncSession, state: str) -> None:
    f = await _build(db)
    await _to(db, f, state)
    await _refused(db, "UPDATE offers SET base_salary = 9999999 WHERE id = :o", {"o": f.offer},
                   "its base_salary cannot change")
    await _refused(db, "UPDATE offers SET terms = 'new terms' WHERE id = :o", {"o": f.offer},
                   "its terms cannot change")


@pytest.mark.asyncio
async def test_one_offer_in_play_per_application(db: AsyncSession) -> None:
    f = await _build(db)
    await _refused(
        db, "INSERT INTO offers (id, company_id, enrolment_id, applicant_id, job_title,"
            " base_salary) VALUES (gen_random_uuid(), :c, :e, :a, 'X', 1)",
        {"c": f.company, "e": f.enrolment, "a": f.applicant}, "uq_offers_live_per_enrolment",
    )


@pytest.mark.asyncio
async def test_a_sent_offer_is_kept_not_deleted(db: AsyncSession) -> None:
    f = await _build(db)
    await _to(db, f, "sent")
    await _refused(db, "DELETE FROM offers WHERE id = :o", {"o": f.offer}, "kept, not deleted")


@pytest.mark.asyncio
async def test_offer_history_is_append_only(db: AsyncSession) -> None:
    f = await _build(db)
    ev = uuid.uuid4()
    await db.execute(text("INSERT INTO offer_events (id, company_id, offer_id, action,"
                          " actor_type) VALUES (:i, :c, :o, 'created', 'user')"),
                     {"i": ev, "c": f.company, "o": f.offer})
    await _refused(db, "UPDATE offer_events SET action = 'approved' WHERE id = :i", {"i": ev},
                   "append-only")
    await _refused(db, "DELETE FROM offer_events WHERE id = :i", {"i": ev}, "append-only")


# ===========================================================================
# A hire is undone only by a rejection
# ===========================================================================
@pytest.mark.asyncio
@pytest.mark.parametrize("to", ["new", "shortlisted", "interviewed", "held"])
async def test_a_hire_cannot_slide_back_into_the_pipeline(db: AsyncSession, to: str) -> None:
    f = await _build(db)
    await _refused(db, "UPDATE enrolments SET status = :s WHERE id = :e",
                   {"s": to, "e": f.enrolment}, "a hire is undone only by recording a rejection")


@pytest.mark.asyncio
async def test_a_hire_can_be_reversed_by_a_rejection(db: AsyncSession) -> None:
    f = await _build(db)
    await _allowed(db, "UPDATE enrolments SET status = 'rejected' WHERE id = :e",
                   {"e": f.enrolment})


# ===========================================================================
# Documents
# ===========================================================================
async def _doc(db: AsyncSession, f: F, *, req: uuid.UUID | None = None,
               expires: str | None = "2099-01-01", status: str = "submitted") -> uuid.UUID:
    did = uuid.uuid4()
    await db.execute(
        text("INSERT INTO candidate_documents (id, company_id, offer_id, requirement_id,"
             " enrolment_id, storage_key, content_type, size_bytes, sha256, expires_on, status,"
             " uploaded_at) VALUES (:i, :c, :o, :r, :e, :k, 'application/pdf', 100, :h,"
             " :x, :s, now())"),
        {"i": did, "c": f.company, "o": f.offer, "r": req or f.req_doc, "e": f.enrolment,
         "k": f"k/{did}", "h": "a" * 64,
         "x": date.fromisoformat(expires) if expires else None, "s": status},
    )
    return did


@pytest.mark.asyncio
async def test_a_document_arrives_as_submitted_and_its_file_is_fixed(db: AsyncSession) -> None:
    f = await _build(db)
    sp = await db.begin_nested()
    with pytest.raises(DBAPIError, match="arrives as submitted"):
        await _doc(db, f, status="verified")
        await db.flush()
    await sp.rollback()
    did = await _doc(db, f)
    await _refused(db, "UPDATE candidate_documents SET sha256 = :h WHERE id = :i",
                   {"h": "b" * 64, "i": did}, "upload a new version instead")


@pytest.mark.asyncio
async def test_a_review_names_its_reviewer(db: AsyncSession) -> None:
    f = await _build(db)
    did = await _doc(db, f)
    await _refused(db, "UPDATE candidate_documents SET status = 'verified' WHERE id = :i",
                   {"i": did}, "reviewed by a named person")
    await _allowed(db, "UPDATE candidate_documents SET status = 'verified',"
                       " reviewed_by_user_id = :u, reviewed_at = now() WHERE id = :i",
                   {"u": f.hr, "i": did})
    await _refused(db, "UPDATE candidate_documents SET status = 'rejected',"
                       " reviewed_by_user_id = :u, reviewed_at = now() WHERE id = :i",
                   {"u": f.hr, "i": did}, "cannot go from verified to rejected")


@pytest.mark.asyncio
async def test_an_expired_document_is_never_verified(db: AsyncSession) -> None:
    f = await _build(db)
    did = await _doc(db, f, expires="2020-01-01")
    await _refused(db, "UPDATE candidate_documents SET status = 'verified',"
                       " reviewed_by_user_id = :u, reviewed_at = now() WHERE id = :i",
                   {"u": f.hr, "i": did}, "has expired and cannot be verified")


@pytest.mark.asyncio
async def test_preboarding_completes_only_with_every_mandatory_document_verified(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    await _to(db, f, "accepted")
    complete = ("UPDATE offers SET preboarding_completed_at = now(),"
                " preboarding_completed_by = :u WHERE id = :o")
    await _refused(db, complete, {"u": f.hr, "o": f.offer}, "1 mandatory document(s) not verified")
    did = await _doc(db, f)
    await _refused(db, complete, {"u": f.hr, "o": f.offer}, "not verified")
    await db.execute(text("UPDATE candidate_documents SET status = 'verified',"
                          " reviewed_by_user_id = :u, reviewed_at = now() WHERE id = :i"),
                     {"u": f.hr, "i": did})
    # The optional photo is still missing — it does not block.
    await _allowed(db, complete, {"u": f.hr, "o": f.offer})
    await _refused(db, "UPDATE offers SET preboarding_completed_at = now() + interval '1 day'"
                       " WHERE id = :o", {"o": f.offer}, "recorded once")


@pytest.mark.asyncio
async def test_preboarding_follows_an_accepted_offer(db: AsyncSession) -> None:
    f = await _build(db)
    await _to(db, f, "sent")
    # No document outstanding, so it is the offer's state that refuses it.
    await db.execute(text("UPDATE document_requirements SET deleted_at = now()"
                          " WHERE requisition_id = :r"), {"r": f.req})
    await _refused(db, "UPDATE offers SET preboarding_completed_at = now(),"
                       " preboarding_completed_by = :u WHERE id = :o",
                   {"u": f.hr, "o": f.offer}, "ck_offers_preboarding_needs_acceptance")


@pytest.mark.asyncio
async def test_an_hrms_payload_leaves_only_with_erasure(db: AsyncSession) -> None:
    f = await _build(db)
    x = uuid.uuid4()
    await db.execute(text("INSERT INTO hrms_exports (id, company_id, offer_id, payload, signature,"
                          " key_id, created_at) VALUES (:i, :c, :o, '{}'::jsonb, :s, 'k', now())"),
                     {"i": x, "c": f.company, "o": f.offer, "s": "c" * 64})
    await _refused(db, "DELETE FROM hrms_exports WHERE id = :i", {"i": x}, "append-only")
    await _refused(db, "UPDATE hrms_exports SET key_id = 'z' WHERE id = :i", {"i": x},
                   "append-only")
    await db.execute(text("UPDATE offers SET redacted_at = now() WHERE id = :o"), {"o": f.offer})
    await _allowed(db, "DELETE FROM hrms_exports WHERE id = :i", {"i": x})


@pytest.mark.asyncio
async def test_a_sent_offer_s_deadline_cannot_move(db: AsyncSession) -> None:
    f = await _build(db)
    await _to(db, f, "sent")
    await _refused(db, "UPDATE offers SET expires_at = expires_at + interval '30 days'"
                       " WHERE id = :o", {"o": f.offer}, "its deadline cannot change")
    await _allowed(db, "UPDATE offers SET token_hash = :t WHERE id = :o",
                   {"t": uuid.uuid4().hex, "o": f.offer})  # a re-send rotates only the link
