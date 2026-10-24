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

import asyncio
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
        # Sessions a test provisioned reference the minted jobs (ON DELETE
        # RESTRICT), so they go first.
        await committed_db.execute(
            text("DELETE FROM sessions WHERE user_id = :u"), {"u": ids["hr"]}
        )
        # Each invite mints a Job owned by the HR user. Deleting the user would
        # only NULL its owner (ON DELETE SET NULL), and a job with no owner is a
        # PUBLIC catalogue job: test_jobs_endpoints then counts it.
        await committed_db.execute(
            text("DELETE FROM jobs WHERE created_by_user_id = :u"), {"u": ids["hr"]}
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
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    r1 = await client.post("/hr/interviews", json=body, headers=h)
    r2 = await client.post("/hr/interviews", json=body, headers=h)
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 200, r2.text
    assert r2.json()["reused"] is True and r2.json()["magic_link"] == ""
    assert r2.json()["invite_id"] == r1.json()["invite_id"]
    rows = await _invites(committed_db, seed["a1"])
    assert [s for _i, s in rows] == ["invited"]
    assert await _emails(committed_db, [i for i, _s in rows]) == 1


async def test_a_confirmed_replacement_is_honoured_even_inside_the_duplicate_window(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    # HR confirms "Replace interview link" with the same settings minutes after
    # the first (the candidate lost the email). That is a request for a new
    # link, not a double submit: the old one is revoked and a new one is sent.
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    r1 = await client.post("/hr/interviews", json=body, headers=h)
    r2 = await client.post("/hr/interviews", json={**body, "replace_existing": True}, headers=h)
    assert r1.status_code == 201, r1.text
    assert r2.status_code == 201, r2.text
    assert r2.json()["reused"] is False and r2.json()["magic_link"]
    first, second = uuid.UUID(r1.json()["invite_id"]), uuid.UUID(r2.json()["invite_id"])
    committed_db.expire_all()
    assert await _invites(committed_db, seed["a1"]) == [(first, "revoked"), (second, "invited")]
    assert await _emails(committed_db, [first, second]) == 2


async def test_two_concurrent_requests_leave_one_live_link(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    # A true double submit: both requests in flight at once. Without the
    # per-person lock both read "no live link", both mint a Job and both insert.
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    r1, r2 = await asyncio.gather(
        client.post("/hr/interviews", json=body, headers=h),
        client.post("/hr/interviews", json=body, headers=h),
    )
    assert sorted([r1.status_code, r2.status_code]) == [200, 201], (r1.text, r2.text)
    committed_db.expire_all()
    rows = await _invites(committed_db, seed["a1"])
    assert [s for _i, s in rows] == ["invited"]
    assert await _emails(committed_db, [i for i, _s in rows]) == 1


async def _start(
    db: AsyncSession, invite: str, user: uuid.UUID, session_status: str = "in_progress",
) -> uuid.UUID:
    """Mark an invite started, with the session interview_take would provision."""
    sid = uuid.uuid4()
    await db.execute(text(
        "INSERT INTO sessions (id, user_id, job_id, language, status, started_at, metadata,"
        " created_at, updated_at)"
        " SELECT :s, :u, job_id, 'en', :st, now(), '{}'::jsonb, now(), now()"
        "   FROM interview_invites WHERE id = :i"
    ), {"s": sid, "u": user, "st": session_status, "i": invite})
    await db.execute(text(
        "UPDATE interview_invites SET status = 'consumed', session_id = :s,"
        " consumed_at = now() WHERE id = :i"
    ), {"s": sid, "i": invite})
    await db.commit()
    return sid


async def test_a_started_interview_is_neither_replaced_revoked_nor_moved(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    r = await client.post("/hr/interviews", json=body, headers=h)
    invite = r.json()["invite_id"]
    await _start(committed_db, invite, seed["hr"])

    r = await client.post("/hr/interviews", json={**body, "replace_existing": True}, headers=h)
    assert r.status_code == 409 and "already started" in r.json()["detail"], r.text
    r = await client.post(f"/hr/interviews/{invite}/revoke", headers=h)
    assert r.status_code == 409 and "already started" in r.json()["detail"], r.text
    later = (datetime.now(tz=UTC) + timedelta(days=1)).isoformat()
    r = await client.patch(f"/hr/interviews/{invite}", json={"scheduled_at": later}, headers=h)
    assert r.status_code == 409, r.text
    committed_db.expire_all()
    assert await _invites(committed_db, seed["a1"]) == [(uuid.UUID(invite), "consumed")]


async def test_an_abandoned_started_interview_can_be_ended_and_reinvited(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    # The candidate opened the link and dropped out. Without a way to end it HR
    # could neither revoke, move nor replace it until the link expired, days on.
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    invite = (await client.post("/hr/interviews", json=body, headers=h)).json()["invite_id"]
    sid = await _start(committed_db, invite, seed["hr"])

    # Ending it is deliberate: it needs force AND a reason.
    r = await client.post(f"/hr/interviews/{invite}/revoke", json={"force": True}, headers=h)
    assert r.status_code == 422, r.text
    # The reason is a fixed code: free text would land in the append-only
    # audit log, which erasure does not redact for this action.
    r = await client.post(
        f"/hr/interviews/{invite}/revoke",
        json={"force": True, "reason": "Asha said she has a family emergency"}, headers=h,
    )
    assert r.status_code == 422, r.text
    r = await client.post(
        f"/hr/interviews/{invite}/revoke",
        json={"force": True, "reason": "candidate_left"}, headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "revoked"
    committed_db.expire_all()
    assert await committed_db.scalar(
        text("SELECT status FROM sessions WHERE id = :s"), {"s": sid}
    ) == "abandoned"
    audit = (await committed_db.execute(text(
        "SELECT details->>'reason', details->>'end_reason' FROM audit_log WHERE resource_id = :i"
        " AND action = 'interview_invite.revoked'"), {"i": invite})).one()
    assert audit[0] == "revoked_in_progress"
    assert audit[1] == "candidate_left"

    # The ended link no longer redeems, and a new invite goes through.
    r = await client.post("/hr/interviews", json=body, headers=h)
    assert r.status_code == 201, r.text


async def test_a_finished_interview_cannot_be_ended_and_does_not_block_a_new_invite(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    invite = (await client.post("/hr/interviews", json=body, headers=h)).json()["invite_id"]
    await _start(committed_db, invite, seed["hr"], session_status="completed")

    r = await client.post(
        f"/hr/interviews/{invite}/revoke", json={"force": True, "reason": "other"}, headers=h,
    )
    assert r.status_code == 409 and "finished" in r.json()["detail"], r.text
    r = await client.post("/hr/interviews", json=body, headers=h)
    assert r.status_code == 201, r.text
    committed_db.expire_all()
    assert [s for _i, s in await _invites(committed_db, seed["a1"])] == ["consumed", "invited"]


async def test_an_expired_link_is_retired_by_the_next_invite_and_cannot_be_revived(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    old = (await client.post("/hr/interviews", json=body, headers=h)).json()["invite_id"]
    await committed_db.execute(text(
        "UPDATE interview_invites SET expires_at = now() - interval '1 hour',"
        " created_at = now() - interval '3 days' WHERE id = :i"
    ), {"i": old})
    await committed_db.commit()

    # A dead link needs no confirmation to be replaced; it is retired, audited.
    r = await client.post("/hr/interviews", json=body, headers=h)
    assert r.status_code == 201, r.text
    new = r.json()["invite_id"]
    committed_db.expire_all()
    assert await _invites(committed_db, seed["a1"]) == [
        (uuid.UUID(old), "revoked"), (uuid.UUID(new), "invited"),
    ]
    reason = await committed_db.scalar(text(
        "SELECT details->>'reason' FROM audit_log WHERE resource_id = :i"
        " AND action = 'interview_invite.revoked'"), {"i": old})
    assert reason == "superseded"

    # Even a row left 'invited' by older code (nothing swept it) cannot be
    # brought back by a reschedule once a newer invite exists: moving the slot
    # would re-extend it next to the new link.
    await committed_db.execute(
        text("UPDATE interview_invites SET status = 'invited' WHERE id = :i"), {"i": old}
    )
    await committed_db.commit()
    later = (datetime.now(tz=UTC) + timedelta(days=1)).isoformat()
    r = await client.patch(f"/hr/interviews/{old}", json={"scheduled_at": later}, headers=h)
    assert r.status_code == 409 and "newer interview invite" in r.json()["detail"], r.text
    rows = {x["invite_id"]: x for x in (await client.get("/hr/interviews", headers=h)).json()}
    assert rows[old]["can_reschedule"] is False
    assert rows[new]["can_reschedule"] is True
    committed_db.expire_all()
    still = await committed_db.scalar(
        text("SELECT expires_at < now() FROM interview_invites WHERE id = :i"), {"i": old}
    )
    assert still is True


async def test_a_decided_or_withdrawn_application_cannot_be_rescheduled(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    invite = (await client.post("/hr/interviews", json=body, headers=h)).json()["invite_id"]
    later = (datetime.now(tz=UTC) + timedelta(days=1)).isoformat()

    for sql in (
        "UPDATE enrolments SET status = 'rejected' WHERE id = :e",
        # Withdrawn: the application is soft deleted.
        "UPDATE enrolments SET status = 'shortlisted', deleted_at = now() WHERE id = :e",
    ):
        await committed_db.execute(text(sql), {"e": seed["e1"]})
        await committed_db.commit()
        r = await client.patch(
            f"/hr/interviews/{invite}", json={"scheduled_at": later}, headers=h,
        )
        assert r.status_code == 409 and "decided or withdrawn" in r.json()["detail"], r.text
        r = await client.get(f"/hr/interviews/{invite}", headers=h)
        assert r.json()["can_reschedule"] is False
    emails = await _emails(committed_db, [uuid.UUID(invite)])
    assert emails == 1  # only the original invite, never a "rescheduled" one


async def test_an_explicit_job_with_a_started_interview_is_a_clean_conflict(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    invite = (await client.post("/hr/interviews", json=body, headers=h)).json()["invite_id"]
    await _start(committed_db, invite, seed["hr"])
    await committed_db.execute(text(
        "UPDATE interview_invites SET expires_at = now() - interval '1 hour' WHERE id = :i"
    ), {"i": invite})
    await committed_db.commit()
    job = await committed_db.scalar(
        text("SELECT job_id FROM interview_invites WHERE id = :i"), {"i": invite}
    )
    r = await client.post("/hr/interviews", json={**body, "job_id": str(job)}, headers=h)
    assert r.status_code == 409 and "started interview on record" in r.json()["detail"], r.text


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


async def _eligible_flags(
    client: AsyncClient, hr: uuid.UUID, enrolment: uuid.UUID,
) -> tuple[bool, bool]:
    r = await client.get("/hr/interviews/eligible-applicants?source=any", headers=_auth(hr))
    assert r.status_code == 200, r.text
    row = next(x for x in r.json() if x["enrolment_id"] == str(enrolment))
    return row["has_active_invite"], row["has_started_invite"]


async def test_the_eligible_list_flags_only_a_link_the_candidate_can_still_use(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    # Re-audit (interviews, has_active_invite): an expired link and a finished
    # interview were both reported as "already invited", so the form claimed
    # the old link would stop working and offered a replacement the server
    # then refused for a started interview.
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    assert await _eligible_flags(client, seed["hr"], seed["e1"]) == (False, False)

    invite = (await client.post("/hr/interviews", json=body, headers=h)).json()["invite_id"]
    assert await _eligible_flags(client, seed["hr"], seed["e1"]) == (True, False)

    # Lapsed but never swept: still 'invited' on disk, no longer usable.
    await committed_db.execute(text(
        "UPDATE interview_invites SET expires_at = now() - interval '1 minute' WHERE id = :i"
    ), {"i": invite})
    await committed_db.commit()
    assert await _eligible_flags(client, seed["hr"], seed["e1"]) == (False, False)

    # Started and not over: active AND started, so the form must not offer a
    # replacement.
    await committed_db.execute(text(
        "UPDATE interview_invites SET expires_at = now() + interval '1 day' WHERE id = :i"
    ), {"i": invite})
    await committed_db.commit()
    sid = await _start(committed_db, invite, seed["hr"])
    assert await _eligible_flags(client, seed["hr"], seed["e1"]) == (True, True)
    # The server agrees: replacing that started interview is refused.
    r = await client.post("/hr/interviews", json={**body, "replace_existing": True}, headers=h)
    assert r.status_code == 409 and "already started" in r.json()["detail"], r.text

    # Dropped out ('abandoned' stays reconnectable): still started.
    await committed_db.execute(
        text("UPDATE sessions SET status = 'abandoned' WHERE id = :s"), {"s": sid}
    )
    await committed_db.commit()
    assert await _eligible_flags(client, seed["hr"], seed["e1"]) == (True, True)

    # A started link past its expiry redeems nothing: neither flag.
    await committed_db.execute(text(
        "UPDATE interview_invites SET expires_at = now() - interval '1 minute' WHERE id = :i"
    ), {"i": invite})
    await committed_db.commit()
    assert await _eligible_flags(client, seed["hr"], seed["e1"]) == (False, False)

    # Finished (the worker marked the session completed) while still in date:
    # neither flag.
    await committed_db.execute(text(
        "UPDATE interview_invites SET expires_at = now() + interval '1 day' WHERE id = :i"
    ), {"i": invite})
    await committed_db.execute(
        text("UPDATE sessions SET status = 'completed' WHERE id = :s"), {"s": sid}
    )
    await committed_db.commit()
    assert await _eligible_flags(client, seed["hr"], seed["e1"]) == (False, False)


async def test_a_scored_interview_is_not_an_active_invite(
    client: AsyncClient, committed_db: AsyncSession, seed: dict[str, uuid.UUID],
) -> None:
    h = _auth(seed["hr"])
    body = {"applicant_id": str(seed["a1"]), "enrolment_id": str(seed["e1"])}
    invite = (await client.post("/hr/interviews", json=body, headers=h)).json()["invite_id"]
    # Scored while the session row still says in_progress (the worker had not
    # caught up): the scorecard alone ends it, as in _interview_finished.
    sid = await _start(committed_db, invite, seed["hr"])
    assert await _eligible_flags(client, seed["hr"], seed["e1"]) == (True, True)
    await committed_db.execute(text(
        "INSERT INTO scorecards (scorecard_id, session_id, scores, summary, lang, created_at)"
        " VALUES (:sc, :s, '{}'::jsonb, 'ok', 'en', now())"
    ), {"sc": uuid.uuid4(), "s": sid})
    await committed_db.commit()
    try:
        assert await _eligible_flags(client, seed["hr"], seed["e1"]) == (False, False)
    finally:
        await committed_db.execute(
            text("DELETE FROM scorecards WHERE session_id = :s"), {"s": sid}
        )
        await committed_db.commit()
