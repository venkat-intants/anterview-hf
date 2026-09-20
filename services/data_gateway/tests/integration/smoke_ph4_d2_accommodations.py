#!/usr/bin/env python3
"""PH4-D2 end to end: an accommodation recorded for one candidate, its exam
deadline and link expiry extended, its interviewer note (and only that)
reaching one interviewer, identical scoring for identical answers, and the
DPDP-facing revoke/redact/retention trail — through the real API, against a
real Postgres.

    cd services/data_gateway
    PYTHONPATH=".;../.." DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_w5 \\
      DATABASE_SSL= python tests/integration/smoke_ph4_d2_accommodations.py

Seeds its own companies; leaves them behind (unique slugs), like the other smokes.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("SMOKE_DATABASE_URL", "postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_w5")
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {detail}" if not cond and detail else ""))


class _FakeTestResult:
    """Stands in for ``CodingTestResult`` — always passes, whatever the source
    or test case, so the smoke never depends on a real code-execution provider."""

    def __init__(self, index: int, is_sample: bool, weight: int) -> None:
        self.index = index
        self.is_sample = is_sample
        self.weight = weight
        self.passed = True
        self.timed_out = False
        self.error = None
        self.actual_output = "3"
        self.stderr = ""
        self.stdin = "1 2"
        self.expected_output = "3"


async def _fake_run_tests(
    *, language: str, source: str, test_cases: list[dict], time_limit_ms: int, include_hidden: bool,
) -> list[_FakeTestResult]:
    return [
        _FakeTestResult(i, bool(tc.get("is_sample")), int(tc.get("weight", 1)))
        for i, tc in enumerate(test_cases)
    ]


async def main() -> None:  # noqa: PLR0915 — one linear script, read top to bottom
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    tag = uuid.uuid4().hex[:8]
    cid = uuid.uuid4()
    hr_a = uuid.uuid4()
    interviewer = uuid.uuid4()

    async with factory() as db:
        await db.execute(
            text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                 " VALUES (:i,'Acme D2',:s,true,:n,:n)"),
            {"i": cid, "s": f"d2-{tag}", "n": now},
        )
        for uid, name, roles in (
            (hr_a, "Hema HR", ("hr_manager",)), (interviewer, "Ivy Interviewer", ("interviewer",)),
        ):
            await db.execute(
                text("INSERT INTO users (id,email,full_name,password_hash,company_id,"
                     " preferred_language,is_active,notify_login_email,must_change_password,"
                     " created_at,updated_at)"
                     " VALUES (:i,:e,:fn,'x',:c,'en',true,false,false,:n,:n)"),
                {"i": uid, "e": f"{uid.hex[:10]}@{tag}.test", "fn": name, "c": cid, "n": now},
            )
            for role in roles:
                await db.execute(
                    text("INSERT INTO user_roles (user_id, role_id, assigned_at)"
                         " SELECT :u, id, :n FROM roles WHERE name = :r"),
                    {"u": uid, "n": now, "r": role},
                )
        await db.commit()

    from shared.auth.base import User

    import app.routers.exam_take as exam_take_mod
    from app.database import get_db_session
    from app.dependencies import get_current_user, get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with factory() as session:
            yield session

    acting = {"hr": hr_a, "company": cid}
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], acting["company"])
    exam_take_mod.run_tests = _fake_run_tests  # type: ignore[assignment]

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as c:
        print("\nPH4-D2 — a coding round, published")
        r = await c.post("/hr/exams", json={"title": "Coding screen", "kind": "coding",
                                            "allow_retake": True})
        exam = r.json()
        exam_id = exam["id"]
        check("HR creates an exam (allow_retake)", r.status_code == 201, r.text[:200])
        r = await c.get(f"/hr/exams/{exam_id}/structure")
        round_id = r.json()["rounds"][0]["id"]
        r = await c.patch(
            f"/hr/exams/{exam_id}/rounds/{round_id}",
            json={"time_limit_seconds": 60, "pass_threshold": 50},
        )
        check("round time limit + pass threshold set", r.status_code == 200, r.text[:200])
        # The exam's own default section (kind='coding', matching exam.kind) —
        # a second, manually-created section would leave this one at index 0
        # with no question in it, and the question at index 1.
        r = await c.get(f"/hr/exams/{exam_id}/structure")
        section_id = r.json()["rounds"][0]["sections"][0]["id"]
        r = await c.patch(
            f"/hr/exams/{exam_id}/rounds/{round_id}/sections/{section_id}",
            json={"time_limit_seconds": 60},
        )
        check("the default section's time limit is set", r.status_code == 200, r.text[:200])
        r = await c.post(
            f"/hr/exams/{exam_id}/sections/{section_id}/coding-questions",
            json={
                "prompt": "Add two numbers", "allowed_languages": ["python"],
                "test_cases": [
                    {"stdin": "1 2", "expected_output": "3", "is_sample": True, "weight": 1},
                    {"stdin": "4 5", "expected_output": "9", "is_sample": False, "weight": 1},
                ],
                "time_limit_ms": 2000, "points": 100,
            },
        )
        check("HR adds a coding question", r.status_code == 201, r.text[:200])
        r = await c.patch(f"/hr/exams/{exam_id}/rounds/{round_id}", json={"status": "published"})
        check("HR publishes the round", r.json().get("status") == "published", r.text[:200])

        print("\nPH4-D2 — two applicants, one with an accommodation")
        applicant_a, applicant_b = uuid.uuid4(), uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text("INSERT INTO applicants (id,company_id,full_name,target_job_title)"
                     " VALUES (:a,:c,'Asha','Engineer'), (:b,:c,'Bilal','Engineer')"),
                {"a": applicant_a, "b": applicant_b, "c": cid},
            )
            await db.commit()

        r = await c.post(
            f"/hr/applicants/{applicant_a}/accommodations",
            json={
                "extra_time_percent": 50, "deadline_extension_days": 2,
                "basis": "hr_initiated", "interviewer_note": "Please allow extra breaks.",
                "internal_note": "Diagnosed condition on file with HR.",
            },
        )
        accommodation_id = r.json().get("id")
        check("HR records an accommodation for A", r.status_code == 201 and bool(accommodation_id),
              r.text[:200])

        print("\nPH4-D2 — assignment: the link's deadline is extended for A only")
        r = await c.post(f"/hr/exams/{exam_id}/assignments",
                         json={"applicant_ids": [str(applicant_a), str(applicant_b)]})
        assigned = {row["applicant_id"]: row for row in r.json()}
        check("both applicants are assigned", r.status_code == 201 and len(assigned) == 2,
              r.text[:200])
        exp_a = datetime.fromisoformat(assigned[str(applicant_a)]["expires_at"])
        exp_b = datetime.fromisoformat(assigned[str(applicant_b)]["expires_at"])
        check("A's link expires ~2 days later than B's",
              timedelta(hours=47) < (exp_a - exp_b) < timedelta(hours=49),
              str(exp_a - exp_b))
        token_a = assigned[str(applicant_a)]["magic_link"].split("#")[-1]
        token_b = assigned[str(applicant_b)]["magic_link"].split("#")[-1]

        print("\nPH4-D2 — the candidate take payload: adjusted for A, unchanged for B")
        headers_a = {"X-Exam-Token": token_a}
        headers_b = {"X-Exam-Token": token_b}
        r = await c.get("/exam", headers=headers_a)
        take_a = r.json()
        check("A sees the extra-time adjustment", take_a["adjustments"] is not None
              and take_a["adjustments"]["extra_time_percent"] == 50
              and take_a["adjustments"]["deadline_extended"] is True, r.text[:300])
        check("A's round + section time limits are scaled to 90s",
              take_a["time_limit_seconds"] == 90
              and take_a["sections"][0]["time_limit_seconds"] == 90, r.text[:300])
        r = await c.get("/exam", headers=headers_b)
        take_b = r.json()
        check("B sees no adjustment", take_b["adjustments"] is None, r.text[:300])
        check("B's time limits are the plain 60s",
              take_b["time_limit_seconds"] == 60
              and take_b["sections"][0]["time_limit_seconds"] == 60, r.text[:300])

        print("\nPH4-D2 — same answers score the same; only the deadline differs")
        r = await c.post("/exam/start", headers=headers_a)
        attempt_a = r.json()["attempt_id"]
        r = await c.post("/exam/start", headers=headers_b)
        attempt_b = r.json()["attempt_id"]
        check("both attempts start", bool(attempt_a) and bool(attempt_b))

        async with factory() as db:
            extra_a, extra_b = (await db.execute(
                text("SELECT id, extra_time_seconds FROM exam_attempts WHERE id = ANY(:ids)"),
                {"ids": [uuid.UUID(attempt_a), uuid.UUID(attempt_b)]},
            )).all()
            by_id = {str(r_[0]): r_[1] for r_ in (extra_a, extra_b)}
            check("A's attempt was frozen with +30s (50% of 60)", by_id[attempt_a] == 30,
                  str(by_id))
            check("B's attempt has no extra time", by_id[attempt_b] == 0, str(by_id))
            # Simulate 100 elapsed seconds for both, without sleeping: past B's
            # deadline+grace (60+30=90) but within A's (90+30=120). Timestamped
            # fresh, right before the two /exam/submit calls that follow, so the
            # margin does not erode with however long setup above took.
            elapsed_start = datetime.now(tz=UTC) - timedelta(seconds=100)
            await db.execute(
                text("UPDATE exam_attempts SET started_at = :t WHERE id = ANY(:ids)"),
                {"t": elapsed_start, "ids": [uuid.UUID(attempt_a), uuid.UUID(attempt_b)]},
            )
            await db.commit()

        coding_qid = take_a["sections"][0]["coding_questions"][0]["id"]
        submission = {"submissions": {coding_qid: {"language": "python", "source": "print(3)"}}}
        r = await c.post("/exam/submit", headers=headers_a,
                         json={"attempt_id": attempt_a, **submission})
        result_a = r.json()
        r = await c.post("/exam/submit", headers=headers_b,
                         json={"attempt_id": attempt_b, **submission})
        result_b = r.json()
        check("A is submitted (within the extended deadline)", result_a["status"] == "submitted",
              r.text[:200])
        check("B is expired (past the plain deadline)", result_b["status"] == "expired",
              r.text[:200])
        check("identical answers score identically",
              result_a["score_percent"] == result_b["score_percent"]
              and result_a["passed"] == result_b["passed"], f"{result_a} vs {result_b}")

        print("\nPH4-D2 — the interviewer sees only the interviewer note")
        req_id, wf_id, wround_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        enrolment_a, enrolment_c, applicant_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        scorecard_a, scorecard_c = uuid.uuid4(), uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text("INSERT INTO applicants (id,company_id,full_name,target_job_title)"
                     " VALUES (:i,:c,'Chetan','Engineer')"),
                {"i": applicant_c, "c": cid},
            )
            await db.execute(
                text("INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
                     " VALUES (:i,:c,'Engineer',:n,:n)"),
                {"i": req_id, "c": cid, "n": now},
            )
            await db.execute(
                text("INSERT INTO workflows (id,company_id,requisition_id,version,status,"
                     "created_at,updated_at) VALUES (:i,:c,:r,1,'draft',:n,:n)"),
                {"i": wf_id, "c": cid, "r": req_id, "n": now},
            )
            await db.execute(
                text("INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
                     "created_at,updated_at) VALUES (:i,:c,:w,0,'Panel','human_review',:n,:n)"),
                {"i": wround_id, "c": cid, "w": wf_id, "n": now},
            )
            await db.execute(
                text("INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,"
                     "target_job_title,created_at,updated_at)"
                     " VALUES (:e1,:c,:r,:a,'Engineer',:n,:n), (:e2,:c,:r,:a2,'Engineer',:n,:n)"),
                {"e1": enrolment_a, "e2": enrolment_c, "c": cid, "r": req_id, "a": applicant_a,
                 "a2": applicant_c, "n": now},
            )
            await db.execute(
                text("INSERT INTO interviewer_scorecards (id,company_id,enrolment_id,round_id,"
                     "interviewer_user_id,assigned_by_user_id,status,due_at,created_at,updated_at)"
                     " VALUES (:s1,:c,:e1,:r,:iv,:hr,'assigned',:due,:n,:n),"
                     "        (:s2,:c,:e2,:r,:iv,:hr,'assigned',:due,:n,:n)"),
                {"s1": scorecard_a, "s2": scorecard_c, "c": cid, "e1": enrolment_a,
                 "e2": enrolment_c, "r": wround_id, "iv": interviewer, "hr": hr_a,
                 "due": now + timedelta(days=1), "n": now},
            )
            await db.commit()

        del app.dependency_overrides[get_hr_company]
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=str(interviewer), full_name="Ivy", email="ivy@x.test", roles=["interviewer"])
        r = await c.get(f"/interviewer/scorecards/{scorecard_a}")
        card_a = r.json()
        check("A's interviewer sees exactly the interviewer note",
              r.status_code == 200 and card_a.get("adjustments_note") == "Please allow extra breaks.",
              r.text[:300])
        check("the payload never carries the internal note or the parameters",
              "internal_note" not in card_a and "extra_time_percent" not in card_a
              and "Diagnosed condition" not in str(card_a), str(card_a))
        r = await c.get(f"/interviewer/scorecards/{scorecard_c}")
        card_c = r.json()
        check("the interviewer for an unrelated candidate sees no note",
              r.status_code == 200 and card_c.get("adjustments_note") is None, r.text[:300])
        del app.dependency_overrides[get_current_user]
        app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], acting["company"])

        print("\nPH4-D2 — a super admin has no route here")
        # The real get_hr_company (with its embedded hr_manager role check) must
        # run here — the shortcut override above bypasses that check entirely,
        # which would make this a false pass regardless of the caller's role.
        del app.dependency_overrides[get_hr_company]
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=str(uuid.uuid4()), full_name="Sam", email="sam@x.test", roles=["super_admin"])
        r = await c.post(f"/hr/applicants/{applicant_a}/accommodations",
                         json={"extra_time_percent": 50})
        check("a super admin gets 403 on an accommodation route", r.status_code == 403, r.text[:200])
        del app.dependency_overrides[get_current_user]
        app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], acting["company"])

        print("\nPH4-D2 — revoking removes the adjustment going forward")
        r = await c.post(f"/hr/accommodations/{accommodation_id}/revoke", json={"reason": "no longer needed"})
        check("HR revokes the accommodation", r.status_code == 200, r.text[:200])
        r = await c.get("/exam", headers=headers_a)
        check("A sees no adjustment any more", r.json()["adjustments"] is None, r.text[:300])
        r = await c.post("/exam/start", headers=headers_a)
        started_a2 = r.json()
        check("A's next attempt has the plain 60s deadline", bool(started_a2.get("deadline")),
              str(started_a2))
        async with factory() as db:
            row = (await db.execute(
                text("SELECT extra_time_seconds FROM exam_attempts WHERE id = :i"),
                {"i": uuid.UUID(started_a2["attempt_id"])},
            )).scalar()
            check("…and zero extra time frozen onto it", row == 0, str(row))

        print("\nPH4-D2 — tenant isolation")
        other_cid = uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                     " VALUES (:i,'Beta D2',:s,true,:n,:n)"),
                {"i": other_cid, "s": f"d2o-{tag}", "n": now},
            )
            await db.commit()
        other_hr = uuid.uuid4()
        acting["hr"] = other_hr
        acting["company"] = other_cid
        r = await c.get(f"/hr/applicants/{applicant_a}/accommodations")
        check("another company's HR gets 404 on the applicant", r.status_code == 404, r.text[:200])
        acting["hr"] = hr_a
        acting["company"] = cid

        print("\nPH4-D2 — retention")
        from app import accommodations as accommodations_svc

        applicant_d = uuid.uuid4()
        old_acc_id = uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text("INSERT INTO applicants (id,company_id,full_name,target_job_title)"
                     " VALUES (:i,:c,'Deepa','Engineer')"),
                {"i": applicant_d, "c": cid},
            )
            await db.execute(
                text(
                    "INSERT INTO candidate_accommodations (id, company_id, applicant_id,"
                    " other_adjustment, basis, effective_from, effective_until, status,"
                    " recorded_by_user_id, created_at, updated_at)"
                    " VALUES (:i, :c, :a, 'Quiet room', 'hr_initiated', :ef, :eu, 'active',"
                    " :rec, :n, :n)"
                ),
                {"i": old_acc_id, "c": cid, "a": applicant_d,
                 "ef": now - timedelta(days=400), "eu": now - timedelta(days=200),
                 "rec": hr_a, "n": now},
            )
            await db.commit()

        async with factory() as db:
            dry = await accommodations_svc.purge(db, retention_days=180, dry_run=True)
            await db.commit()
        check("a retention dry run reports the candidate without redacting", dry >= 1, str(dry))
        async with factory() as db:
            still = await db.scalar(
                text("SELECT other_adjustment FROM candidate_accommodations WHERE id = :i"),
                {"i": old_acc_id},
            )
        check("…and dry run really changed nothing", still == "Quiet room", str(still))
        async with factory() as db:
            purged = await accommodations_svc.purge(db, retention_days=180, dry_run=False)
            await db.commit()
        check("the real run redacts it", purged >= 1, str(purged))
        async with factory() as db:
            row = (await db.execute(
                text("SELECT other_adjustment, redacted_at FROM candidate_accommodations"
                     " WHERE id = :i"),
                {"i": old_acc_id},
            )).first()
        check("the note is redacted and the row is stamped",
              row[0] == "[redacted]" and row[1] is not None, str(row))

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
