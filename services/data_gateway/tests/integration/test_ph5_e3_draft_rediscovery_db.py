"""PH5-E3, code review FIX 2 — the rediscovery opt-in through the DRAFT door.

Before this fix ``POST /apply/{id}/draft`` had nowhere to keep the ticked box
and ``submit_draft`` never looked for one, so a candidate who ticked it and
clicked "Save for later" had their consent silently dropped. These tests go
through the real router functions (``start_draft`` / ``submit_draft``)
against a real, migrated Postgres — the ``test_ph5_e2_corpus_http.py``
``committed_db`` shape, since the handlers under test commit their own work,
rather than the "one rolled-back transaction" shape ``test_ph5_e3_*_db.py``
uses for the pure service layer.

No S3, no PDF parsing: ``full_name`` / ``resume_s3_key`` / ``confirmed_at``
are written directly onto the draft row, which is what every OTHER
``submit_draft`` precondition test in ``test_ph3_drafts_and_confirmation.py``
also does with a mocked session — the CV pipeline is not what this fix is
about.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import pytest_asyncio
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import rediscovery
from app.application_drafts import hash_token
from app.config import settings
from app.routers.public_apply import DraftStartIn, start_draft, submit_draft
from tests.integration.seed_helpers import approve_for_publish

pytestmark = pytest.mark.integration


class _FakeRequest:
    """The minimal duck-typed ``Request`` the handlers actually read from:
    ``.client.host`` and ``.headers.get(...)`` for the hashed IP/UA — nothing
    else is touched on this path. Calling the router functions directly
    (never through HTTP) means a real Starlette ``Request`` is not needed."""

    def __init__(self, ip: str = "127.0.0.1", ua: str = "pytest") -> None:
        self.client = SimpleNamespace(host=ip)
        self.headers: dict[str, str] = {"User-Agent": ua}


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A session that COMMITS. ``start_draft``/``submit_draft`` call
    ``db.commit()`` themselves, so wrapping this in a rolled-back transaction
    (the other PH5-E3 db test files' shape) would fight the code under test.
    This repo's own disposable database; every test mints fresh ids, so
    nothing needs cleaning up between tests (the ``test_ph5_e2_corpus_http.py``
    precedent)."""
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


async def _seed_company(db: AsyncSession, company_id: uuid.UUID) -> None:
    await db.execute(
        text(
            "INSERT INTO companies (id, name, slug, is_active, created_at, updated_at)"
            " VALUES (:i, 'E3 Draft Co', :s, true, now(), now())"
        ),
        {"i": company_id, "s": f"e3draft-{company_id.hex[:10]}"},
    )


async def _seed_open_requisition(
    db: AsyncSession, *, company_id: uuid.UUID, title: str,
) -> uuid.UUID:
    """A requisition genuinely visible to ``_open_posting`` — every gate
    ``app/publishing.py::visible_sql`` checks, including a PUBLISHED workflow
    (PH4-O6's own draft -> approved -> published route, via
    ``seed_helpers.approve_for_publish`` — the ``smoke_ph3_apply.py``
    precedent for seeding an opening the public apply routes will accept)."""
    req_id = uuid.uuid4()
    wf_id = uuid.uuid4()
    owner = uuid.uuid4()
    now = datetime.now(tz=UTC)
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:i, :e, :c)"),
        {"i": owner, "e": f"owner-{req_id.hex[:10]}@e3draft.example", "c": company_id},
    )
    await db.execute(
        text(
            "INSERT INTO job_requisitions (id,company_id,title,level,jd_text,status,"
            " from_backfill,public_apply_enabled,created_by_user_id,owner_user_id,"
            " approval_status,approval_decided_at,created_at,updated_at)"
            " VALUES (:i,:c,:t,'mid','Fit the role.','open',"
            " false,true,:u,:u,'approved',:n,:n,:n)"
        ),
        {"i": req_id, "c": company_id, "t": title, "u": owner, "n": now},
    )
    await db.execute(
        text(
            "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
            " auto_score_on_apply,auto_assign_first_round,auto_advance_rounds,"
            " reminders_enabled,hold_band,created_at,updated_at)"
            " VALUES (:i,:c,:r,1,'draft',true,true,true,true,10,:n,:n)"
        ),
        {"i": wf_id, "c": company_id, "r": req_id, "n": now},
    )
    await approve_for_publish(db, workflow_id=wf_id, company_id=company_id)
    await db.execute(
        text("UPDATE workflows SET status = 'published', published_at = :n WHERE id = :i"),
        {"i": wf_id, "n": now},
    )
    return req_id


async def _confirm_ready_to_submit(db: AsyncSession, *, token: str, full_name: str) -> None:
    """Satisfy ``submit_draft``'s preconditions directly — a name, a CV key
    and a confirmation timestamp — without touching S3 or a PDF parser."""
    await db.execute(
        text(
            "UPDATE application_drafts SET full_name = :n, resume_s3_key = 'drafts/x.pdf',"
            " confirmed_at = :t WHERE token_hash = :h"
        ),
        {"n": full_name, "t": datetime.now(tz=UTC), "h": hash_token(token)},
    )


async def _active_rediscovery_count(db: AsyncSession, *, user_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            text(
                "SELECT count(*) FROM dpdp_consent_ledger"
                " WHERE user_id = :u AND consent_type = 'talent_pool_rediscovery'"
                "   AND granted AND revoked_at IS NULL"
            ),
            {"u": user_id},
        )
        or 0
    )


@pytest.mark.asyncio
async def test_a_draft_with_the_flag_then_submit_writes_a_ledger_row(
    db: AsyncSession,
) -> None:
    company_id = uuid.uuid4()
    await _seed_company(db, company_id)
    req_id = await _seed_open_requisition(db, company_id=company_id, title="Fitter, opt-in")

    body = DraftStartIn(
        email=f"asha-{uuid.uuid4().hex[:10]}@e3draft.example",
        consent_granted=True, rediscovery_opt_in=True,
    )
    started = await start_draft(
        requisition_id=req_id, body=body, request=_FakeRequest(), db=db,  # type: ignore[arg-type]
    )
    await _confirm_ready_to_submit(db, token=started.resume_token, full_name="Asha K")

    out = await submit_draft(request=_FakeRequest(), db=db, token=started.resume_token)  # type: ignore[arg-type]
    assert out.already_applied is False

    owner_user_id = await db.scalar(
        text("SELECT user_id FROM applicants WHERE id = :a"), {"a": uuid.UUID(out.applicant_id)},
    )
    assert owner_user_id is not None
    row = (
        await db.execute(
            text(
                "SELECT evidence FROM dpdp_consent_ledger"
                " WHERE user_id = :u AND consent_type = 'talent_pool_rediscovery'"
            ),
            {"u": owner_user_id},
        )
    ).mappings().first()
    assert row is not None
    assert row["evidence"]["source"] == "public_apply_form"
    assert row["evidence"]["company_id"] == str(company_id)
    assert await _active_rediscovery_count(db, user_id=uuid.UUID(str(owner_user_id))) == 1


@pytest.mark.asyncio
async def test_a_draft_without_the_flag_writes_no_rediscovery_row_at_all(
    db: AsyncSession,
) -> None:
    company_id = uuid.uuid4()
    await _seed_company(db, company_id)
    req_id = await _seed_open_requisition(db, company_id=company_id, title="Fitter, no opt-in")

    body = DraftStartIn(
        email=f"bala-{uuid.uuid4().hex[:10]}@e3draft.example", consent_granted=True,
    )
    assert body.rediscovery_opt_in is False  # the default this test relies on
    started = await start_draft(
        requisition_id=req_id, body=body, request=_FakeRequest(), db=db,  # type: ignore[arg-type]
    )
    await _confirm_ready_to_submit(db, token=started.resume_token, full_name="Bala R")

    out = await submit_draft(request=_FakeRequest(), db=db, token=started.resume_token)  # type: ignore[arg-type]
    assert out.already_applied is False

    owner_user_id = await db.scalar(
        text("SELECT user_id FROM applicants WHERE id = :a"), {"a": uuid.UUID(out.applicant_id)},
    )
    assert owner_user_id is not None
    count = await db.scalar(
        text(
            "SELECT count(*) FROM dpdp_consent_ledger"
            " WHERE user_id = :u AND consent_type = 'talent_pool_rediscovery'"
        ),
        {"u": owner_user_id},
    )
    assert count == 0


@pytest.mark.asyncio
async def test_a_withdrawn_candidate_is_not_re_granted_through_the_draft_door(
    db: AsyncSession,
) -> None:
    """The draft door is no more authenticated than the single-shot one:
    ticking the box again after a withdrawal must refuse on the same
    ``not_regranted_after_withdrawal`` terms as ``record_opt_in`` already
    enforces for ``public_apply_form``."""
    company_id = uuid.uuid4()
    await _seed_company(db, company_id)
    req1 = await _seed_open_requisition(db, company_id=company_id, title="Fitter I")
    req2 = await _seed_open_requisition(db, company_id=company_id, title="Fitter II")
    assert req1 != req2

    # An activated candidate already on file at this company (applied before,
    # no enrolment yet against req2) whose rediscovery consent for THIS
    # company was granted and then withdrawn.
    real_user = uuid.uuid4()
    applicant_id = uuid.uuid4()
    address = f"chandra-{uuid.uuid4().hex[:10]}@e3draft.example"
    now = datetime.now(tz=UTC)
    await db.execute(
        text("INSERT INTO users (id, email, company_id) VALUES (:i, :e, :c)"),
        {"i": real_user, "e": address, "c": company_id},
    )
    await db.execute(
        text(
            "INSERT INTO applicants (id, company_id, user_id, full_name, email,"
            " target_job_title, created_at, updated_at)"
            " VALUES (:i,:c,:u,'Chandra M',:e,'Fitter',:n,:n)"
        ),
        {"i": applicant_id, "c": company_id, "u": real_user, "e": address, "n": now},
    )
    await rediscovery.record_opt_in(
        db, user_id=real_user, company_id=company_id, applicant_id=applicant_id,
        requisition_id=None, source="my_applications", meta=rediscovery.OptInMeta(),
    )
    await rediscovery.revoke_opt_ins(db, user_id=real_user, company_id=company_id)
    assert await _active_rediscovery_count(db, user_id=real_user) == 0

    body = DraftStartIn(email=address, consent_granted=True, rediscovery_opt_in=True)
    started = await start_draft(
        requisition_id=req2, body=body, request=_FakeRequest(), db=db,  # type: ignore[arg-type]
    )
    await _confirm_ready_to_submit(db, token=started.resume_token, full_name="Chandra M")

    out = await submit_draft(request=_FakeRequest(), db=db, token=started.resume_token)  # type: ignore[arg-type]
    assert out.already_applied is False
    # The EXISTING applicant, not a fresh one — proves this exercised the
    # sticky-withdrawal branch for the real identity, not a brand new guest.
    assert out.applicant_id == str(applicant_id)

    assert await _active_rediscovery_count(db, user_id=real_user) == 0
