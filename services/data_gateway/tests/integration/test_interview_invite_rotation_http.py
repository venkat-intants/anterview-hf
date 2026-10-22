"""AI interview invites over HTTP: one live link per application (IV-H2), a
link that outlives its scheduled slot (IV-H1), and the status guards on
revoke and reschedule (IV-M4, IV-M12, IV-M10, IV-L1).

WHY OVER HTTP AND AGAINST A REAL DATABASE. The defect in IV-H2 lived in the
join between two things a unit test fakes: the form never sends ``job_id``, so
``_resolve_job`` minted a new Job per request, and the "rotate the prior
invite" query filtered on that brand-new job id. Each half looked right on its
own. Only a real request against real rows shows whether the second invite
actually revokes the first.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from shared.auth.jwt import issue_access_token
from shared.db.engine import build_engine, build_session_factory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def committed_db() -> AsyncIterator[AsyncSession]:
    engine = build_engine(
        database_url=settings.database_url, database_ssl=settings.database_ssl, pool_size=2,
    )
    factory = build_session_factory(engine)
    try:
        async with factory() as session:
            yield session
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", timeout=30.0,
    ) as ac, app.router.lifespan_context(app):
        yield ac


@pytest_asyncio.fixture
async def seed(committed_db: AsyncSession) -> AsyncIterator[dict[str, uuid.UUID]]:
    """One company, one HR manager, one shortlisted application and one
    application that has already been decided (rejected)."""
    ids = {k: uuid.uuid4() for k in ("company", "hr", "req", "a1", "e1", "a2", "e2", "exam",
                                     "round")}
    tag = ids["company"].hex[:10]
    p = {**ids, "slug": f"inv-rot-{tag}", "mail": f"hr-{tag}@inv.test",
         "m1": f"asha-{tag}@inv.test"}
    for sql in (
        "INSERT INTO companies (id, name, slug) VALUES (:company, 'Invite co', :slug)",
        "INSERT INTO users (id, email, company_id) VALUES (:hr, :mail, :company)",
        "INSERT INTO job_requisitions (id, company_id, title, created_at, updated_at)"
        " VALUES (:req, :company, 'Engineer', now(), now())",
        "INSERT INTO applicants (id, company_id, full_name, target_job_title, email)"
        " VALUES (:a1, :company, 'Asha', 'Engineer', :m1),"
        " (:a2, :company, 'Ravi', 'Engineer', NULL)",
        "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, target_job_title,"
        " status, created_at, updated_at) VALUES"
        " (:e1, :company, :req, :a1, 'Engineer', 'shortlisted', now(), now()),"
        " (:e2, :company, :req, :a2, 'Engineer', 'rejected', now(), now())",
        # Ravi passed an exam before he was rejected, so only the decision can
        # keep him off the invite gate.
        "INSERT INTO exams (id, company_id, title, created_by_user_id, created_at, updated_at)"
        " VALUES (:exam, :company, 'Aptitude', :hr, now(), now())",
        "INSERT INTO exam_rounds (id, exam_id, company_id, round_number, title, position,"
        " created_at, updated_at) VALUES (:round, :exam, :company, 1, 'R1', 0, now(), now())",
        "INSERT INTO exam_attempts (company_id, exam_id, applicant_id, round_id, score_percent,"
        " passed, status, started_at, submitted_at)"
        " VALUES (:company, :exam, :a2, :round, 90, true, 'submitted', now(), now())",
    ):
        await committed_db.execute(text(sql), p)
    await committed_db.commit()
    try:
        yield ids
    finally:
        await committed_db.execute(
            text("DELETE FROM interview_invites WHERE company_id = :c"), {"c": ids["company"]}
        )
        await committed_db.execute(
            text("DELETE FROM companies WHERE id = :c"), {"c": ids["company"]}
        )
        await committed_db.execute(text("DELETE FROM users WHERE id = :u"), {"u": ids["hr"]})
        await committed_db.commit()


def _auth(user_id: uuid.UUID) -> dict[str, str]:
    token = issue_access_token(
        str(user_id), ["hr_manager"], settings.jwt_secret, issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
    )
    return {"Authorization": f"Bearer {token}"}


async def _invites(db: AsyncSession, applicant: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    rows = (await db.execute(text(
        "SELECT id, status FROM interview_invites WHERE applicant_id = :a ORDER BY created_at"
    ), {"a": applicant})).all()
    return [(r[0], r[1]) for r in rows]


async def _emails(db: AsyncSession, invite_ids: list[uuid.UUID]) -> int:
    return int(await db.scalar(text(
        "SELECT count(*) FROM email_events WHERE related_id = ANY(:i)"
    ), {"i": invite_ids}) or 0)


async def test_reinvite_needs_confirmation_and_then_revokes_the_old_link(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    r = await client.post("/hr/interviews", json=body, headers=h)
    assert r.status_code == 201, r.text
    first = uuid.UUID(r.json()["invite_id"])

    # A different request (another language) without confirming is refused,
    # and the candidate keeps the one link they hold.
    r = await client.post("/hr/interviews", json={**body, "language": "hi"}, headers=h)
    assert r.status_code == 409, r.text
    assert "already has an active interview link" in r.json()["detail"]
    assert await _invites(committed_db, seed["a1"]) == [(first, "invited")]

    # Confirmed: the old link is revoked, a new one is minted. Exactly one live.
    r = await client.post(
        "/hr/interviews", json={**body, "language": "hi", "replace_existing": True}, headers=h,
    )
    assert r.status_code == 201, r.text
    second = uuid.UUID(r.json()["invite_id"])
    committed_db.expire_all()
    assert await _invites(committed_db, seed["a1"]) == [(first, "revoked"), (second, "invited")]
    replaced = await committed_db.scalar(text(
        "SELECT details->>'reason' FROM audit_log WHERE resource_id = :i"
        " AND action = 'interview_invite.revoked'"), {"i": first})
    assert replaced == "replaced"


async def test_a_repeated_identical_request_mints_no_second_link_or_email(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"]),
            "replace_existing": True}
    r1 = await client.post("/hr/interviews", json=body, headers=h)
    r2 = await client.post("/hr/interviews", json=body, headers=h)
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 200, r2.text
    assert r2.json()["reused"] is True and r2.json()["magic_link"] == ""
    assert r2.json()["invite_id"] == r1.json()["invite_id"]
    rows = await _invites(committed_db, seed["a1"])
    assert [s for _i, s in rows] == ["invited"]
    assert await _emails(committed_db, [i for i, _s in rows]) == 1


async def test_a_started_interview_is_neither_replaced_revoked_nor_moved(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    r = await client.post("/hr/interviews", json=body, headers=h)
    invite = r.json()["invite_id"]
    await committed_db.execute(
        text("UPDATE interview_invites SET status = 'consumed' WHERE id = :i"), {"i": invite}
    )
    await committed_db.commit()

    r = await client.post("/hr/interviews", json={**body, "replace_existing": True}, headers=h)
    assert r.status_code == 409 and "already started" in r.json()["detail"], r.text
    r = await client.post(f"/hr/interviews/{invite}/revoke", headers=h)
    assert r.status_code == 409 and "already started" in r.json()["detail"], r.text
    later = (datetime.now(tz=UTC) + timedelta(days=1)).isoformat()
    r = await client.patch(f"/hr/interviews/{invite}", json={"scheduled_at": later}, headers=h)
    assert r.status_code == 409, r.text
    committed_db.expire_all()
    assert await _invites(committed_db, seed["a1"]) == [(uuid.UUID(invite), "consumed")]


async def test_a_link_scheduled_past_the_ttl_still_works_at_the_slot(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    slot = datetime.now(tz=UTC).replace(microsecond=0) + timedelta(days=5)
    window = timedelta(minutes=settings.interview_join_window_minutes)
    r = await client.post("/hr/interviews", json={
        "applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"]),
        "scheduled_at": slot.isoformat(),
    }, headers=h)
    assert r.status_code == 201, r.text
    assert datetime.fromisoformat(r.json()["expires_at"]) > slot + window

    # Moving it further out extends the link again.
    later = slot + timedelta(days=4)
    r = await client.patch(
        f"/hr/interviews/{r.json()['invite_id']}", json={"scheduled_at": later.isoformat()},
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert datetime.fromisoformat(r.json()["expires_at"]) > later + window


async def test_a_missed_slot_is_flagged_and_can_be_rescheduled(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    r = await client.post("/hr/interviews", json={
        "applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"]),
    }, headers=h)
    invite = r.json()["invite_id"]
    await committed_db.execute(text(
        "UPDATE interview_invites SET scheduled_at = now() - interval '1 hour' WHERE id = :i"
    ), {"i": invite})
    await committed_db.commit()

    rows = (await client.get("/hr/interviews", headers=h)).json()
    row = next(x for x in rows if x["invite_id"] == invite)
    assert row["status"] == "invited"
    assert row["missed_slot"] is True and row["can_reschedule"] is True


async def test_revoke_refuses_a_link_that_is_already_dead(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    r = await client.post("/hr/interviews", json={
        "applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"]),
    }, headers=h)
    invite = r.json()["invite_id"]
    assert (await client.post(f"/hr/interviews/{invite}/revoke", headers=h)).status_code == 200
    r = await client.post(f"/hr/interviews/{invite}/revoke", headers=h)
    assert r.status_code == 409, r.text


async def test_a_decided_application_cannot_be_invited(
    client: AsyncClient, seed: dict[str, uuid.UUID],
) -> None:
    # Ravi passed an exam, then his application was rejected. A decided
    # application is off the eligible list, and the create gate now agrees.
    r = await client.post("/hr/interviews", json={
        "applicant_id": str(seed["a2"]), "enrolment_id": str(seed["e2"]),
    }, headers=_auth(seed["hr"]))
    assert r.status_code == 409, r.text
