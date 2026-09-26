"""PH5-E3 DB-level tests — talent pools, against a real, migrated Postgres.

Companion to ``test_ph5_e3_rediscovery_db.py``, which explicitly leaves the
pools service and erasure step 5k to "another agent" — this file is that
other half. Service functions are called directly, never through HTTP, on the
same precedent: one transaction per test, always rolled back.

What is covered here and not in the unit file
(``tests/unit/test_ph5_e3_pools_rules.py``): tenancy, the append-only trigger,
soft removal + re-add, the eligibility join on pool reads (reusing
``rediscovery.ELIGIBLE_CTE`` via ``rediscovery.eligibility_for_applicants``),
erasure step ordering at the database level, and invite idempotency.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
import sqlalchemy.exc
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import rediscovery as rd
from app import talent_pools as pools
from app.config import settings
from app.interviewer_scorecards import RequestMeta

pytestmark = pytest.mark.integration

META = RequestMeta(ip_address="127.0.0.1", user_agent="pytest")


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """A rolled-back transaction per test — the ``test_ph5_e3_rediscovery_db.py``
    precedent. A test that deliberately triggers a database-level error (the
    append-only trigger) aborts the rest of ITS OWN transaction, which is fine:
    teardown rolls back unconditionally either way."""
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2
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


class F:
    """One company A with an HR user and two candidates, plus company B for
    tenancy, and one open requisition at A for the invite tests."""

    def __init__(self) -> None:
        self.company_a = uuid.uuid4()
        self.company_b = uuid.uuid4()
        self.hr_a = uuid.uuid4()
        self.cand1 = uuid.uuid4()
        self.cand2 = uuid.uuid4()
        self.applicant1 = uuid.uuid4()  # company A
        self.applicant2 = uuid.uuid4()  # company A
        self.applicant_b = uuid.uuid4()  # company B
        self.requisition = uuid.uuid4()
        self.tag = self.company_a.hex[:8]


async def _seed(db: AsyncSession) -> F:
    f = F()
    now = datetime.now(tz=UTC)
    await db.execute(
        text("INSERT INTO companies (id, name, slug) VALUES (:ca,'E3 Pools A',:sa),(:cb,'E3 Pools B',:sb)"),
        {"ca": f.company_a, "cb": f.company_b, "sa": f"e3pa-{f.tag}", "sb": f"e3pb-{f.tag}"},
    )
    await db.execute(
        text(
            "INSERT INTO users (id, email, company_id) VALUES"
            " (:ha,:eha,:ca), (:c1,:ec1,:ca), (:c2,:ec2,:ca)"
        ),
        {"ha": f.hr_a, "eha": f"hr-a-{f.tag}@e3p.test", "ca": f.company_a,
         "c1": f.cand1, "ec1": f"cand1-{f.tag}@e3p.test",
         "c2": f.cand2, "ec2": f"cand2-{f.tag}@e3p.test"},
    )
    for aid, cid, uid, name in (
        (f.applicant1, f.company_a, f.cand1, "Asha K"),
        (f.applicant2, f.company_a, f.cand2, "Bala R"),
    ):
        await db.execute(
            text(
                "INSERT INTO applicants (id, company_id, user_id, full_name, email,"
                " target_job_title, created_at, updated_at)"
                " VALUES (:i,:c,:u,:n,:e,'Fitter',:t,:t)"
            ),
            {"i": aid, "c": cid, "u": uid, "n": name,
             "e": f"{name.split()[0].lower()}-{f.tag}@e3p.test", "t": now},
        )
    # A company-B applicant, no user_id — used for the not_this_company skip.
    await db.execute(
        text(
            "INSERT INTO applicants (id, company_id, full_name, target_job_title,"
            " created_at, updated_at) VALUES (:i,:c,'Cara Q','Fitter',:t,:t)"
        ),
        {"i": f.applicant_b, "c": f.company_b, "t": now},
    )
    await db.execute(
        text(
            "INSERT INTO job_requisitions (id, company_id, title, level, status,"
            " created_at, updated_at) VALUES (:i,:c,'Maintenance Fitter','mid','open',:t,:t)"
        ),
        {"i": f.requisition, "c": f.company_a, "t": now},
    )
    return f


async def _opt_in(db: AsyncSession, f: F, *, user_id: uuid.UUID) -> None:
    await rd.record_opt_in(
        db, user_id=user_id, company_id=f.company_a, applicant_id=f.applicant1,
        requisition_id=None, source="my_applications", meta=rd.OptInMeta(),
    )


async def _create_pool(db: AsyncSession, f: F, name: str = "Fitters, Vizag") -> str:
    out = await pools.create_pool(
        db, company_id=f.company_a, actor=f.hr_a, name=name, description=None, meta=META,
    )
    return out["id"]


# ===========================================================================
# Pools: CRUD, tenancy, uniqueness, limits
# ===========================================================================
@pytest.mark.asyncio
async def test_a_pool_from_another_company_is_invisible(db: AsyncSession) -> None:
    f = await _seed(db)
    pool_id = await _create_pool(db, f)
    assert await pools.get_pool_with_members(
        db, company_id=f.company_b, pool_id=uuid.UUID(pool_id)
    ) is None


@pytest.mark.asyncio
async def test_pool_names_are_unique_per_company_case_insensitively(db: AsyncSession) -> None:
    f = await _seed(db)
    await _create_pool(db, f, name="Fitters, Vizag")
    with pytest.raises(pools.PoolError) as caught:
        await _create_pool(db, f, name="fitters, vizag")
    assert caught.value.code == "pool_name_taken"


@pytest.mark.asyncio
async def test_the_same_name_is_free_again_once_the_first_pool_is_deleted(db: AsyncSession) -> None:
    f = await _seed(db)
    first = await _create_pool(db, f, name="Fitters, Vizag")
    await pools.delete_pool(db, company_id=f.company_a, actor=f.hr_a,
                            pool_id=uuid.UUID(first), meta=META)
    # Should not raise: a soft-deleted pool's name does not block a new one.
    await _create_pool(db, f, name="Fitters, Vizag")


@pytest.mark.asyncio
async def test_creating_a_pool_past_the_per_company_limit_is_refused(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = await _seed(db)
    monkeypatch.setattr(settings, "talent_pool_max_per_company", 1)
    await _create_pool(db, f, name="Pool 1")
    with pytest.raises(pools.PoolError) as caught:
        await _create_pool(db, f, name="Pool 2")
    assert caught.value.code == "pool_limit_reached"


# ===========================================================================
# Members: add, skip reasons, soft removal + re-add, member_count
# ===========================================================================
@pytest.mark.asyncio
async def test_manual_add_needs_no_rediscovery_consent(db: AsyncSession) -> None:
    """The purpose limitation (design §4.3): adding a name HR already holds
    under the application consent needs no opt-in. Only CONTACTING does."""
    f = await _seed(db)
    pool_id = await _create_pool(db, f)
    out = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note="Strong fault diagnosis",
        match_reason=None, meta=META,
    )
    assert out["skipped"] == []
    assert len(out["added"]) == 1

    fetched = await pools.get_pool_with_members(db, company_id=f.company_a, pool_id=uuid.UUID(pool_id))
    assert fetched is not None
    member = fetched["members"][0]
    assert member["eligible"] is False  # never opted in
    assert member["ineligible_reason"] is None  # not withdrawn/expired/erased — simply never given
    assert member["source"] == "manual"
    assert member["note"] == "Strong fault diagnosis"
    assert fetched["pool"]["member_count"] == 1


@pytest.mark.asyncio
async def test_rediscovery_add_is_refused_for_an_ineligible_candidate(db: AsyncSession) -> None:
    f = await _seed(db)
    pool_id = await _create_pool(db, f)
    out = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="rediscovery", note=None,
        match_reason={"why": [{"signal": "resume_similarity", "freshness": "fresh"}]},
        meta=META,
    )
    assert out["added"] == []
    assert out["skipped"] == [{"applicant_id": str(f.applicant1), "reason": "not_eligible"}]


@pytest.mark.asyncio
async def test_rediscovery_add_succeeds_once_opted_in_and_freezes_the_freshness_band(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1)
    pool_id = await _create_pool(db, f)
    out = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="rediscovery", note=None,
        match_reason={
            "why": [
                {"signal": "resume_similarity", "contribution": 40, "explainable": False,
                 "freshness": "fresh", "note": rd.SIMILARITY_NOTE,
                 "snippet": "CV prose that must not survive"},
            ],
            "query_terms": ["hydraulics"],
        },
        meta=META,
    )
    assert len(out["added"]) == 1
    fetched = await pools.get_pool_with_members(db, company_id=f.company_a, pool_id=uuid.UUID(pool_id))
    assert fetched is not None
    member = fetched["members"][0]
    assert member["eligible"] is True
    assert member["evidence_freshness"] == "fresh"
    assert member["match_reason"] is not None
    blob = str(member["match_reason"])
    assert "must not survive" not in blob
    assert rd.SIMILARITY_NOTE not in blob


@pytest.mark.asyncio
async def test_adding_the_same_applicant_twice_is_skipped_as_already_a_member(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    pool_id = await _create_pool(db, f)
    kwargs: dict[str, Any] = {
        "db": db, "company_id": f.company_a, "actor": f.hr_a, "pool_id": uuid.UUID(pool_id),
        "applicant_ids": [f.applicant1], "source": "manual", "note": None,
        "match_reason": None, "meta": META,
    }
    await pools.add_members(**kwargs)
    out = await pools.add_members(**kwargs)
    assert out["added"] == []
    assert out["skipped"] == [{"applicant_id": str(f.applicant1), "reason": "already_a_member"}]


@pytest.mark.asyncio
async def test_an_applicant_from_another_company_is_skipped(db: AsyncSession) -> None:
    f = await _seed(db)
    pool_id = await _create_pool(db, f)
    out = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant_b], source="manual", note=None, match_reason=None, meta=META,
    )
    assert out["added"] == []
    assert out["skipped"] == [{"applicant_id": str(f.applicant_b), "reason": "not_this_company"}]


@pytest.mark.asyncio
async def test_the_pool_is_full_past_its_member_limit(
    db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    f = await _seed(db)
    monkeypatch.setattr(settings, "talent_pool_max_members", 1)
    pool_id = await _create_pool(db, f)
    out = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1, f.applicant2], source="manual", note=None,
        match_reason=None, meta=META,
    )
    assert len(out["added"]) == 1
    assert out["skipped"] == [{"applicant_id": str(f.applicant2), "reason": "pool_full"}]


@pytest.mark.asyncio
async def test_removal_is_soft_and_re_adding_is_a_new_row_with_history_intact(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    pool_id = await _create_pool(db, f)
    added = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note="first note",
        match_reason=None, meta=META,
    )
    first_member_id = added["added"][0]

    await pools.remove_member(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        member_id=uuid.UUID(first_member_id), reason="no longer relevant", meta=META,
    )
    fetched = await pools.get_pool_with_members(db, company_id=f.company_a, pool_id=uuid.UUID(pool_id))
    assert fetched is not None
    assert fetched["members"] == []  # removed members do not list
    assert fetched["pool"]["member_count"] == 0

    # The removed row survives, soft-removed, in the underlying table.
    removed_row = (
        await db.execute(
            text("SELECT removed_at, removed_reason FROM talent_pool_members WHERE id = :i"),
            {"i": first_member_id},
        )
    ).mappings().first()
    assert removed_row is not None
    assert removed_row["removed_at"] is not None
    assert removed_row["removed_reason"] == "no longer relevant"

    # Re-adding is a NEW row, not a resurrection of the old one.
    readded = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note="second note",
        match_reason=None, meta=META,
    )
    second_member_id = readded["added"][0]
    assert second_member_id != first_member_id
    fetched_again = await pools.get_pool_with_members(
        db, company_id=f.company_a, pool_id=uuid.UUID(pool_id)
    )
    assert fetched_again is not None
    assert len(fetched_again["members"]) == 1
    assert fetched_again["members"][0]["note"] == "second note"

    # Both member_added events survive, in order, alongside the removal.
    events = (
        await db.execute(
            text(
                "SELECT action FROM talent_pool_events WHERE pool_id = :p"
                " ORDER BY created_at ASC"
            ),
            {"p": pool_id},
        )
    ).scalars().all()
    assert events.count("member_added") == 2
    assert events.count("member_removed") == 1


@pytest.mark.asyncio
async def test_deleting_a_pool_removes_its_members_with_it(db: AsyncSession) -> None:
    f = await _seed(db)
    pool_id = await _create_pool(db, f)
    await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1, f.applicant2], source="manual", note=None,
        match_reason=None, meta=META,
    )
    out = await pools.delete_pool(db, company_id=f.company_a, actor=f.hr_a,
                                  pool_id=uuid.UUID(pool_id), meta=META)
    assert out == {"deleted": True, "members_removed": 2}
    assert await pools.get_pool_with_members(
        db, company_id=f.company_a, pool_id=uuid.UUID(pool_id)
    ) is None


# ===========================================================================
# Eligibility on pool reads (design §7 row 4) — reuses rediscovery.ELIGIBLE_CTE
# ===========================================================================
@pytest.mark.asyncio
async def test_a_member_becomes_ineligible_the_instant_consent_is_withdrawn(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1)
    pool_id = await _create_pool(db, f)
    await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note=None, match_reason=None, meta=META,
    )
    before = await pools.get_pool_with_members(db, company_id=f.company_a, pool_id=uuid.UUID(pool_id))
    assert before is not None
    assert before["members"][0]["eligible"] is True

    await rd.revoke_opt_ins(db, user_id=f.cand1, company_id=f.company_a)
    after = await pools.get_pool_with_members(db, company_id=f.company_a, pool_id=uuid.UUID(pool_id))
    assert after is not None
    member = after["members"][0]
    assert member["eligible"] is False
    assert member["ineligible_reason"] == "consent_withdrawn"


@pytest.mark.asyncio
async def test_an_ineligible_member_cannot_be_evidence_reviewed_or_invited(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    pool_id = await _create_pool(db, f)  # never opted in
    added = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note=None, match_reason=None, meta=META,
    )
    member_id = uuid.UUID(added["added"][0])

    with pytest.raises(pools.PoolError) as reviewed_err:
        await pools.mark_evidence_reviewed(
            db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
            member_id=member_id, meta=META,
        )
    assert reviewed_err.value.code == "not_eligible"

    with pytest.raises(pools.PoolError) as invite_err:
        await pools.invite_member(
            db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
            member_id=member_id, requisition_id=f.requisition, meta=META,
        )
    assert invite_err.value.code == "not_eligible"


# ===========================================================================
# Invite: idempotency, requisition-not-open, and no other write
# ===========================================================================
@pytest.mark.asyncio
async def test_invite_is_idempotent_and_creates_no_second_enrolment(db: AsyncSession) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1)
    pool_id = await _create_pool(db, f)
    added = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note=None, match_reason=None, meta=META,
    )
    member_id = uuid.UUID(added["added"][0])

    first = await pools.invite_member(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        member_id=member_id, requisition_id=f.requisition, meta=META,
    )
    assert first["already_enrolled"] is False
    assert first["enrolment_id"]

    second = await pools.invite_member(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        member_id=member_id, requisition_id=f.requisition, meta=META,
    )
    assert second["already_enrolled"] is True
    assert second["enrolment_id"] == first["enrolment_id"]

    count = await db.scalar(
        text(
            "SELECT count(*) FROM enrolments"
            " WHERE applicant_id = :a AND requisition_id = :r AND deleted_at IS NULL"
        ),
        {"a": f.applicant1, "r": f.requisition},
    )
    assert count == 1

    # Nothing beyond an enrolment at status 'new': no stage past new, no round
    # result, no decision (design §6.6.1 / CLAUDE.md D-05).
    status_ = await db.scalar(
        text("SELECT status FROM enrolments WHERE id = :i"), {"i": uuid.UUID(first["enrolment_id"])}
    )
    assert status_ == "new"

    events = (
        await db.execute(
            text(
                "SELECT count(*) FROM talent_pool_events"
                " WHERE pool_id = :p AND action = 'member_invited'"
            ),
            {"p": pool_id},
        )
    ).scalar()
    assert events == 2  # once per call — a fact about each read, not a dedup


@pytest.mark.asyncio
async def test_invite_against_a_closed_requisition_is_refused(db: AsyncSession) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1)
    await db.execute(
        text("UPDATE job_requisitions SET status = 'closed' WHERE id = :r"),
        {"r": f.requisition},
    )
    pool_id = await _create_pool(db, f)
    added = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note=None, match_reason=None, meta=META,
    )
    with pytest.raises(pools.PoolError) as caught:
        await pools.invite_member(
            db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
            member_id=uuid.UUID(added["added"][0]), requisition_id=f.requisition, meta=META,
        )
    assert caught.value.code == "requisition_not_open"


# ===========================================================================
# Criterion 14 — a control, not a sentence (design §6.6 point 3): inviting on
# stale, unreviewed evidence is refused unless reviewed or acknowledged.
# ===========================================================================
async def _seed_stale_member(db: AsyncSession, f: F) -> tuple[uuid.UUID, uuid.UUID]:
    """A pool member whose only evidence — the CV itself — is old enough to
    band as ``stale`` (well past ``rediscovery_stale_days``), and who has
    never been evidence-reviewed. The lowest-effort way to make
    ``evidence_freshness_for_applicant`` return anything but ``fresh``."""
    await _opt_in(db, f, user_id=f.cand1)
    await db.execute(
        text("UPDATE applicants SET updated_at = :t WHERE id = :a"),
        {"t": datetime.now(tz=UTC) - timedelta(days=400), "a": f.applicant1},
    )
    pool_id = await _create_pool(db, f)
    added = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note=None, match_reason=None, meta=META,
    )
    return uuid.UUID(pool_id), uuid.UUID(added["added"][0])


@pytest.mark.asyncio
async def test_invite_on_stale_unreviewed_evidence_is_refused(db: AsyncSession) -> None:
    f = await _seed(db)
    pool_id, member_id = await _seed_stale_member(db, f)
    with pytest.raises(pools.PoolError) as caught:
        await pools.invite_member(
            db, company_id=f.company_a, actor=f.hr_a, pool_id=pool_id,
            member_id=member_id, requisition_id=f.requisition, meta=META,
        )
    assert caught.value.code == "stale_evidence_unreviewed"
    assert caught.value.status_code == 422
    # Refusing must not have side effects: no enrolment, no event.
    enrolled = await db.scalar(
        text("SELECT count(*) FROM enrolments WHERE applicant_id = :a"), {"a": f.applicant1},
    )
    assert enrolled == 0
    invited_events = await db.scalar(
        text("SELECT count(*) FROM talent_pool_events WHERE action = 'member_invited'"),
    )
    assert invited_events == 0


@pytest.mark.asyncio
async def test_invite_on_stale_evidence_is_allowed_once_reviewed(db: AsyncSession) -> None:
    f = await _seed(db)
    pool_id, member_id = await _seed_stale_member(db, f)
    await pools.mark_evidence_reviewed(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=pool_id, member_id=member_id, meta=META,
    )
    out = await pools.invite_member(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=pool_id,
        member_id=member_id, requisition_id=f.requisition, meta=META,
    )
    assert out["enrolment_id"]


@pytest.mark.asyncio
async def test_invite_on_stale_evidence_is_allowed_with_acknowledgement(db: AsyncSession) -> None:
    f = await _seed(db)
    pool_id, member_id = await _seed_stale_member(db, f)
    out = await pools.invite_member(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=pool_id,
        member_id=member_id, requisition_id=f.requisition, acknowledged_stale=True, meta=META,
    )
    assert out["enrolment_id"]


@pytest.mark.asyncio
async def test_the_acknowledgement_is_recorded_on_the_event_facts_only(db: AsyncSession) -> None:
    f = await _seed(db)
    pool_id, member_id = await _seed_stale_member(db, f)
    await pools.invite_member(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=pool_id,
        member_id=member_id, requisition_id=f.requisition, acknowledged_stale=True, meta=META,
    )
    event = (
        await db.execute(
            text(
                "SELECT details FROM talent_pool_events"
                " WHERE pool_id = :p AND action = 'member_invited'"
            ),
            {"p": pool_id},
        )
    ).mappings().first()
    assert event is not None
    assert event["details"]["acknowledged_stale"] is True
    assert event["details"]["freshness"] in ("ageing", "stale", "unverifiable")
    # Facts only: no note text, no candidate name, nothing beyond ids/booleans.
    blob = str(event["details"])
    assert "Asha" not in blob


@pytest.mark.asyncio
async def test_the_gate_reads_live_evidence_not_the_frozen_add_time_column(
    db: AsyncSession,
) -> None:
    """The mutation this proves: a member whose STORED
    ``evidence_freshness`` says ``fresh`` (frozen at add time) but whose
    ACTUAL evidence has since aged past the threshold must still be gated —
    proving ``invite_member`` recomputes the band rather than trusting the
    column (design §6.5's whole argument, criterion 14's teeth)."""
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1)
    pool_id = await _create_pool(db, f)
    # Added via "rediscovery" while genuinely fresh, so the FROZEN column
    # really does say "fresh" — not a strawman.
    added = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="rediscovery", note=None,
        match_reason={"why": [{"signal": "resume_similarity", "freshness": "fresh"}]},
        meta=META,
    )
    member_id = uuid.UUID(added["added"][0])
    stored_band = await db.scalar(
        text("SELECT evidence_freshness FROM talent_pool_members WHERE id = :i"),
        {"i": member_id},
    )
    assert stored_band == "fresh"

    # Time passes: the CV is now stale, but nobody touched the pool row.
    await db.execute(
        text("UPDATE applicants SET updated_at = :t WHERE id = :a"),
        {"t": datetime.now(tz=UTC) - timedelta(days=400), "a": f.applicant1},
    )

    with pytest.raises(pools.PoolError) as caught:
        await pools.invite_member(
            db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
            member_id=member_id, requisition_id=f.requisition, meta=META,
        )
    assert caught.value.code == "stale_evidence_unreviewed"


# ===========================================================================
# FIX 1 (code review) — a review EXPIRES, closing the hole where one review in
# 2026 cleared the gate forever while the evidence it accepted kept ageing.
# ===========================================================================
@pytest.mark.asyncio
async def test_a_review_that_has_expired_no_longer_clears_the_gate(db: AsyncSession) -> None:
    """Review the member, invite (succeeds), then move the clock past
    ``rediscovery_review_valid_days`` by BACKDATING THE STORED TIMESTAMP —
    never by re-deriving the bound in Python and asserting against it — and
    the SAME member on the SAME requisition is refused again. Idempotency
    does not mask this: ``invite_member`` re-checks the gate before it ever
    reaches the idempotent ``enrol_applicant`` call."""
    f = await _seed(db)
    pool_id, member_id = await _seed_stale_member(db, f)
    await pools.mark_evidence_reviewed(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=pool_id, member_id=member_id, meta=META,
    )
    first = await pools.invite_member(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=pool_id,
        member_id=member_id, requisition_id=f.requisition, meta=META,
    )
    assert first["enrolment_id"]

    await db.execute(
        text("UPDATE talent_pool_members SET evidence_reviewed_at = :t WHERE id = :i"),
        {"t": datetime.now(tz=UTC) - timedelta(days=settings.rediscovery_review_valid_days + 1),
         "i": member_id},
    )
    with pytest.raises(pools.PoolError) as caught:
        await pools.invite_member(
            db, company_id=f.company_a, actor=f.hr_a, pool_id=pool_id,
            member_id=member_id, requisition_id=f.requisition, meta=META,
        )
    assert caught.value.code == "stale_evidence_unreviewed"
    assert caught.value.status_code == 422


# ===========================================================================
# FIX 4 (code review) — pin the refusal order: candidate-side-first (not
# eligible, then stale evidence) beats a requisition-state refusal, so a
# future reorder is a visible test failure rather than an accident.
# ===========================================================================
@pytest.mark.asyncio
async def test_stale_evidence_is_refused_before_a_closed_requisition_is_even_considered(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    pool_id, member_id = await _seed_stale_member(db, f)
    await db.execute(
        text("UPDATE job_requisitions SET status = 'closed' WHERE id = :r"),
        {"r": f.requisition},
    )
    with pytest.raises(pools.PoolError) as caught:
        await pools.invite_member(
            db, company_id=f.company_a, actor=f.hr_a, pool_id=pool_id,
            member_id=member_id, requisition_id=f.requisition, meta=META,
        )
    assert caught.value.code == "stale_evidence_unreviewed"


# ===========================================================================
# The append-only trigger (criterion 16)
# ===========================================================================
@pytest.mark.asyncio
async def test_talent_pool_events_cannot_be_updated(db: AsyncSession) -> None:
    f = await _seed(db)
    pool_id = await _create_pool(db, f)  # writes a 'created' event
    event_id = await db.scalar(
        text("SELECT id FROM talent_pool_events WHERE pool_id = :p LIMIT 1"), {"p": pool_id},
    )
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="append-only"):
        await db.execute(
            text("UPDATE talent_pool_events SET action = 'deleted' WHERE id = :i"),
            {"i": event_id},
        )


@pytest.mark.asyncio
async def test_talent_pool_events_cannot_be_deleted_while_the_pool_still_exists(
    db: AsyncSession,
) -> None:
    f = await _seed(db)
    pool_id = await _create_pool(db, f)
    event_id = await db.scalar(
        text("SELECT id FROM talent_pool_events WHERE pool_id = :p LIMIT 1"), {"p": pool_id},
    )
    with pytest.raises(sqlalchemy.exc.DBAPIError, match="append-only"):
        await db.execute(text("DELETE FROM talent_pool_events WHERE id = :i"), {"i": event_id})


# ===========================================================================
# Erasure — step 5k, at the database level (the executor itself is admin_ops';
# this proves the STATEMENT it runs actually does what step order requires)
# ===========================================================================
@pytest.mark.asyncio
async def test_erasure_request_makes_a_member_ineligible_immediately(db: AsyncSession) -> None:
    f = await _seed(db)
    await _opt_in(db, f, user_id=f.cand1)
    pool_id = await _create_pool(db, f)
    await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note=None, match_reason=None, meta=META,
    )
    await db.execute(
        text(
            "INSERT INTO erasure_requests"
            " (request_id, user_id, requested_by, status, scheduled_for, created_at)"
            " VALUES (gen_random_uuid(), :u, :u, 'pending', now(), now())"
        ),
        {"u": f.cand1},
    )
    fetched = await pools.get_pool_with_members(db, company_id=f.company_a, pool_id=uuid.UUID(pool_id))
    assert fetched is not None
    member = fetched["members"][0]
    assert member["eligible"] is False
    assert member["ineligible_reason"] == "erasure_requested"


@pytest.mark.asyncio
async def test_step_5k_deletes_the_membership_even_after_user_id_is_nulled(
    db: AsyncSession,
) -> None:
    """Proves the ORDERING HAZARD itself: step 5k's statement matches through
    ``applicants.user_id`` — seeded here, then cleared exactly as step 6 would,
    to show that running 5k AFTER that point deletes NOTHING, which is why the
    executor must run 5k first (asserted structurally for the real executor in
    ``services/admin_ops/tests/test_erasure_step_order.py``)."""
    f = await _seed(db)
    pool_id = await _create_pool(db, f)
    added = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note=None, match_reason=None, meta=META,
    )
    member_id = added["added"][0]

    # Simulate step 6 running FIRST (the wrong order).
    await db.execute(text("UPDATE applicants SET user_id = NULL WHERE id = :a"), {"a": f.applicant1})
    result = await db.execute(
        text(
            "DELETE FROM talent_pool_members"
            " WHERE applicant_id IN (SELECT id FROM applicants WHERE user_id = :uid)"
        ),
        {"uid": f.cand1},
    )
    assert (getattr(result, "rowcount", 0) or 0) == 0  # nothing matched — the hazard, demonstrated
    still_there = await db.scalar(
        text("SELECT 1 FROM talent_pool_members WHERE id = :i"), {"i": member_id}
    )
    assert still_there == 1  # the membership survived an erasure that "completed"


@pytest.mark.asyncio
async def test_step_5k_in_the_right_order_deletes_the_membership(db: AsyncSession) -> None:
    f = await _seed(db)
    pool_id = await _create_pool(db, f)
    added = await pools.add_members(
        db, company_id=f.company_a, actor=f.hr_a, pool_id=uuid.UUID(pool_id),
        applicant_ids=[f.applicant1], source="manual", note=None, match_reason=None, meta=META,
    )
    member_id = added["added"][0]

    # Step 5k BEFORE step 6, matching the executor's own SQL verbatim.
    result = await db.execute(
        text(
            "DELETE FROM talent_pool_members"
            " WHERE applicant_id IN (SELECT id FROM applicants WHERE user_id = :uid)"
        ),
        {"uid": f.cand1},
    )
    assert (getattr(result, "rowcount", 0) or 0) == 1
    await db.execute(text("UPDATE applicants SET user_id = NULL WHERE id = :a"), {"a": f.applicant1})

    gone = await db.scalar(text("SELECT 1 FROM talent_pool_members WHERE id = :i"), {"i": member_id})
    assert gone is None
    # talent_pools and talent_pool_events survive, per EXCLUDED_TABLES.
    pool_still_there = await db.scalar(
        text("SELECT 1 FROM talent_pools WHERE id = :i"), {"i": pool_id}
    )
    assert pool_still_there == 1
    events_still_there = await db.scalar(
        text("SELECT count(*) FROM talent_pool_events WHERE pool_id = :p"), {"p": pool_id}
    )
    assert events_still_there and events_still_there > 0
