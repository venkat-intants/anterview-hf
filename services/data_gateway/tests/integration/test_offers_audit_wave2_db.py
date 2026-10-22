"""Offers audit, wave 2: what the service does with a lapsed offer, a finished
preboarding and a cleared template field, against a real migrated Postgres.

- OF-F4: a sent offer past its deadline is expired the moment staff read it
  (detail, company list, the drawer's list), and Re-send and Withdraw on it are
  refused in words, recording the expiry rather than a withdrawal.
- OF-F5: no document review once preboarding is complete.
- OF-F3 / M9: clearing a template field stores null.
- OF-L10: HR notifications point at the offer itself, not the list.

Every test runs in one transaction that is rolled back.
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
from sqlalchemy.ext.asyncio import AsyncSession

from app import offers as svc
from app import preboarding as docs
from app.config import settings
from app.interviewer_scorecards import RequestMeta
from app.offers import OfferError

pytestmark = pytest.mark.integration


class F:
    def __init__(self) -> None:
        self.company = uuid.uuid4()
        self.hr, self.admin = uuid.uuid4(), uuid.uuid4()
        self.req, self.applicant, self.enrolment = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        self.offer = uuid.uuid4()
        self.req_doc = uuid.uuid4()


async def _build(db: AsyncSession) -> F:
    f = F()
    tag = f.company.hex[:10]
    p: dict[str, Any] = {
        "c": f.company, "h": f.hr, "ad": f.admin, "r": f.req, "a": f.applicant,
        "e": f.enrolment, "o": f.offer, "rq": f.req_doc, "slug": f"ofw2-{tag}",
        "m1": f"h-{tag}@ofw2.test", "m2": f"ad-{tag}@ofw2.test", "m3": f"c-{tag}@ofw2.test",
    }
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:c, 'OFW2 co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:h, :m1, :c), (:ad, :m2, :c)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:r, :c, 'Engineer', now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, email, target_job_title)"
        " VALUES (:a, :c, 'Asha', :m3, 'Engineer')",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
        " status, created_at, updated_at) VALUES (:e, :c, :r, :a, 'Engineer', 'hired', now(),"
        " now())",
        "INSERT INTO offers (id, company_id, enrolment_id, applicant_id, requisition_id,"
        " job_title, base_salary, created_by_user_id) VALUES (:o, :c, :e, :a, :r, 'Engineer',"
        " 1200000, :h)",
        "INSERT INTO document_requirements (id, company_id, requisition_id, name, doc_type,"
        " mandatory, requires_expiry, created_at, updated_at) VALUES"
        " (:rq, :c, :r, 'Passport', 'identity', true, false, now(), now())",
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


async def _sent(db: AsyncSession, f: F, *, lapsed: bool) -> None:
    """Walk the offer to sent. With ``lapsed``, put its deadline in the past: a
    sent offer's deadline is frozen, so the trigger is lifted for that one write,
    inside this test's transaction, which is always rolled back."""
    await db.execute(text("UPDATE offers SET status = 'pending_approval', submitted_by_user_id = :u"
                          " WHERE id = :o"), {"u": f.hr, "o": f.offer})
    await db.execute(text("UPDATE offers SET status = 'approved', decided_by_user_id = :u"
                          " WHERE id = :o"), {"u": f.admin, "o": f.offer})
    await db.execute(text("UPDATE offers SET status = 'sent', token_hash = :t, sent_at = now(),"
                          " sent_by_user_id = :u, expires_at = now() + interval '7 days'"
                          " WHERE id = :o"),
                     {"t": uuid.uuid4().hex, "u": f.hr, "o": f.offer})
    if lapsed:
        await db.execute(text("ALTER TABLE offers DISABLE TRIGGER offers_lifecycle"))
        await db.execute(text("UPDATE offers SET expires_at = now() - interval '1 hour'"
                              " WHERE id = :o"), {"o": f.offer})
        await db.execute(text("ALTER TABLE offers ENABLE TRIGGER offers_lifecycle"))


async def _status(db: AsyncSession, f: F) -> str:
    return str(await db.scalar(text("SELECT status FROM offers WHERE id = :o"), {"o": f.offer}))


async def _outcome(db: AsyncSession, f: F) -> str | None:
    return await db.scalar(text("SELECT offer_outcome FROM enrolments WHERE id = :e"),
                           {"e": f.enrolment})


async def _actions(db: AsyncSession, f: F) -> list[str]:
    rows = await db.execute(text("SELECT action FROM offer_events WHERE offer_id = :o"
                                 " ORDER BY created_at"), {"o": f.offer})
    return [r[0] for r in rows.all()]


# ===========================================================================
# OF-F4: a lapsed offer is expired on read, and nothing is sent on it
# ===========================================================================
async def test_reading_a_lapsed_offer_expires_it(db: AsyncSession) -> None:
    f = await _build(db)
    await _sent(db, f, lapsed=True)

    out = await svc.get_offer(db, company_id=f.company, offer_id=f.offer)

    assert out["status"] == "expired"
    assert await _status(db, f) == "expired"
    assert await _outcome(db, f) == "offer_expired"
    assert "expired" in await _actions(db, f)


async def test_an_offer_still_in_its_window_is_left_sent(db: AsyncSession) -> None:
    f = await _build(db)
    await _sent(db, f, lapsed=False)

    out = await svc.get_offer(db, company_id=f.company, offer_id=f.offer)

    assert out["status"] == "sent"
    assert "expired" not in await _actions(db, f)


async def test_the_company_list_and_the_drawer_list_show_a_lapsed_offer_as_expired(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    await _sent(db, f, lapsed=True)

    listed = await svc.list_offers(db, company_id=f.company, status=None, limit=50, offset=0)
    assert [i["status"] for i in listed["items"]] == ["expired"]
    # Filtering by "sent" no longer finds it.
    sent = await svc.list_offers(db, company_id=f.company, status="sent", limit=50, offset=0)
    assert sent["items"] == []

    drawer = await svc.offers_for_enrolment(db, company_id=f.company, enrolment_id=f.enrolment)
    assert [o["status"] for o in drawer] == ["expired"]


async def test_a_list_read_never_touches_another_company_s_offers(db: AsyncSession) -> None:
    f = await _build(db)
    other = await _build(db)
    await _sent(db, f, lapsed=False)
    await _sent(db, other, lapsed=True)

    await svc.list_offers(db, company_id=f.company, status=None, limit=50, offset=0)
    await svc.offers_for_enrolment(db, company_id=f.company, enrolment_id=f.enrolment)

    assert await _status(db, other) == "sent"  # lapsed, but not this company's to expire


async def test_re_sending_a_lapsed_offer_is_refused_and_records_the_expiry(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    await _sent(db, f, lapsed=True)
    token_before = await db.scalar(text("SELECT token_hash FROM offers WHERE id = :o"),
                                   {"o": f.offer})

    with pytest.raises(OfferError) as err:
        await svc.send(db, company_id=f.company, offer_id=f.offer, actor=f.hr,
                       meta=RequestMeta(), again=True)

    assert err.value.status_code == 409
    assert err.value.keep is True  # the router commits the expiry
    assert "expired" in err.value.detail and "re-sent" in err.value.detail
    assert await _status(db, f) == "expired"
    assert "resent" not in await _actions(db, f)
    token_after = await db.scalar(text("SELECT token_hash FROM offers WHERE id = :o"),
                                  {"o": f.offer})
    assert token_after == token_before  # no fresh link was minted
    await db.flush()  # staged ORM rows reach a raw SELECT only once flushed
    queued = await db.scalar(text("SELECT count(*) FROM email_events WHERE related_id = :o"
                                  " AND template = 'offer_ready'"), {"o": f.offer})
    assert queued == 0


async def test_withdrawing_a_lapsed_offer_records_an_expiry_not_a_withdrawal(
    db: AsyncSession,
) -> None:
    f = await _build(db)
    await _sent(db, f, lapsed=True)

    with pytest.raises(OfferError) as err:
        await svc.withdraw(db, company_id=f.company, offer_id=f.offer, reason=None, actor=f.hr,
                           meta=RequestMeta())

    assert err.value.status_code == 409
    assert err.value.keep is True
    assert "withdrawn" in err.value.detail
    assert await _status(db, f) == "expired"
    assert await _outcome(db, f) == "offer_expired"
    assert "withdrawn" not in await _actions(db, f)


async def test_withdrawing_an_offer_in_its_window_still_works(db: AsyncSession) -> None:
    f = await _build(db)
    await _sent(db, f, lapsed=False)

    out = await svc.withdraw(db, company_id=f.company, offer_id=f.offer, reason=None, actor=f.hr,
                             meta=RequestMeta())

    assert out["status"] == "withdrawn"
    assert await _outcome(db, f) == "offer_withdrawn"


# ===========================================================================
# OF-L10: HR notifications open the offer itself
# ===========================================================================
async def test_hr_is_pointed_at_the_offer_not_the_list(db: AsyncSession) -> None:
    f = await _build(db)
    await _sent(db, f, lapsed=True)

    await svc.get_offer(db, company_id=f.company, offer_id=f.offer)
    await db.flush()

    links =(await db.execute(text("SELECT link FROM notifications WHERE user_id = :u"),
                              {"u": f.hr})).scalars().all()
    assert links and all(link == f"/hr/offers/{f.offer}" for link in links)


async def test_an_approval_points_its_author_at_the_offer(db: AsyncSession) -> None:
    f = await _build(db)
    await db.execute(text("UPDATE offers SET status = 'pending_approval', submitted_by_user_id = :u"
                          " WHERE id = :o"), {"u": f.hr, "o": f.offer})

    await svc.decide(db, company_id=f.company, offer_id=f.offer, approve=True, note=None,
                     reviewer=f.admin, meta=RequestMeta())
    await db.flush()

    link =await db.scalar(text("SELECT link FROM notifications WHERE user_id = :u"), {"u": f.hr})
    assert link == f"/hr/offers/{f.offer}"


# ===========================================================================
# OF-F5: no review once preboarding is complete
# ===========================================================================
async def _accepted_with_verified_document(db: AsyncSession, f: F) -> uuid.UUID:
    await _sent(db, f, lapsed=False)
    await db.execute(text("UPDATE offers SET status = 'accepted', responded_at = now(),"
                          " accepted_name = 'Asha' WHERE id = :o"), {"o": f.offer})
    did = uuid.uuid4()
    await db.execute(
        text("INSERT INTO candidate_documents (id, company_id, offer_id, requirement_id,"
             " enrolment_id, storage_key, content_type, size_bytes, sha256, expires_on, status,"
             " uploaded_at) VALUES (:i, :c, :o, :r, :e, :k, 'application/pdf', 100, :h,"
             " :x, 'submitted', now())"),
        {"i": did, "c": f.company, "o": f.offer, "r": f.req_doc, "e": f.enrolment,
         "k": f"k/{did}", "h": "a" * 64, "x": date(2099, 1, 1)},
    )
    await docs.review(db, company_id=f.company, document_id=did, action="verify", note=None,
                      actor=f.hr, meta=RequestMeta())
    return did


async def test_a_replacement_can_be_asked_for_during_preboarding(db: AsyncSession) -> None:
    f = await _build(db)
    did = await _accepted_with_verified_document(db, f)

    out = await docs.review(db, company_id=f.company, document_id=did,
                            action="request_replacement", note="Scan is blurred",
                            actor=f.hr, meta=RequestMeta())

    assert out["state"] == "replacement_requested"


async def test_no_review_once_preboarding_is_complete(db: AsyncSession) -> None:
    f = await _build(db)
    did = await _accepted_with_verified_document(db, f)
    await db.execute(text("UPDATE offers SET preboarding_completed_at = now(),"
                          " preboarding_completed_by = :u WHERE id = :o"),
                     {"u": f.hr, "o": f.offer})

    with pytest.raises(OfferError) as err:
        await docs.review(db, company_id=f.company, document_id=did,
                          action="request_replacement", note="Scan is blurred",
                          actor=f.hr, meta=RequestMeta())

    assert err.value.status_code == 409
    assert "Preboarding is complete" in err.value.detail
    status = await db.scalar(text("SELECT status FROM candidate_documents WHERE id = :i"),
                             {"i": did})
    assert status == "verified"


# ===========================================================================
# OF-F3 / M9: a cleared template field is stored as cleared
# ===========================================================================
async def test_clearing_a_template_field_stores_null(db: AsyncSession) -> None:
    f = await _build(db)
    made = await svc.save_template(
        db, company_id=f.company, template_id=None,
        fields={"name": "Standard", "benefits": "Health cover", "terms": "Standard terms",
                "probation_months": 6, "notice_period_days": 30},
        actor=f.hr, meta=RequestMeta(),
    )

    cleared = await svc.save_template(
        db, company_id=f.company, template_id=uuid.UUID(made["id"]),
        fields={"benefits": None, "terms": None, "probation_months": None,
                "notice_period_days": None},
        actor=f.hr, meta=RequestMeta(),
    )

    assert cleared["benefits"] is None
    assert cleared["terms"] is None
    assert cleared["probation_months"] is None
    assert cleared["notice_period_days"] is None
    assert cleared["name"] == "Standard"
