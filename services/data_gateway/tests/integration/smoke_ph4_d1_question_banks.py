#!/usr/bin/env python3
"""PH4-D1 end to end: reusable question banks, review by a second HR manager,
copying into an exam section, the published-round lock, and the DPDP-facing
event/audit trail — through the real API, against a real Postgres.

    cd services/data_gateway
    PYTHONPATH=".;../.." DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_w5 \\
      DATABASE_SSL= python tests/integration/smoke_ph4_d1_question_banks.py

Seeds its own companies; leaves them behind (unique slugs), like the other smokes.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get("SMOKE_DATABASE_URL", "postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_w5")
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {detail}" if not cond and detail else ""))


async def main() -> None:  # noqa: PLR0915 — one linear script, read top to bottom
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    tag = uuid.uuid4().hex[:8]
    cid = uuid.uuid4()
    other_cid = uuid.uuid4()
    hr_a1, hr_a2, hr_b = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    super_admin = uuid.uuid4()  # a distinct actor: never this company's author or submitter

    async with factory() as db:
        await db.execute(
            text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                 " VALUES (:i,'Acme D1',:s,true,:n,:n), (:i2,'Beta D1',:s2,true,:n,:n)"),
            {"i": cid, "s": f"d1-{tag}", "i2": other_cid, "s2": f"d1o-{tag}", "n": now},
        )
        for uid, company, name in ((hr_a1, cid, "Hema HR"), (hr_a2, cid, "Owen HR"),
                                    (hr_b, other_cid, "Bea HR"), (super_admin, cid, "Sam Admin")):
            await db.execute(
                text("INSERT INTO users (id,email,full_name,password_hash,company_id,"
                     " preferred_language,is_active,notify_login_email,must_change_password,"
                     " created_at,updated_at)"
                     " VALUES (:i,:e,:fn,'x',:c,'en',true,false,false,:n,:n)"),
                {"i": uid, "e": f"{uid.hex[:10]}@{tag}.test", "fn": name, "c": company, "n": now},
            )
            if uid is super_admin:
                continue  # the super_admin role is granted via the dependency override, not roles
            await db.execute(
                text("INSERT INTO user_roles (user_id, role_id, assigned_at)"
                     " SELECT :u, id, :n FROM roles WHERE name = 'hr_manager'"),
                {"u": uid, "n": now},
            )
        await db.commit()

    from shared.auth.base import User

    from app.database import get_db_session
    from app.dependencies import get_current_user, get_hr_company, get_super_admin_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with factory() as session:
            yield session

    acting = {"hr": hr_a1, "company": cid, "admin": super_admin}
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], acting["company"])
    app.dependency_overrides[get_super_admin_company] = lambda: (acting["admin"], acting["company"])

    async def events_for(qid: str) -> list[str]:
        async with factory() as db:
            rows = (await db.execute(
                text("SELECT action FROM bank_question_events WHERE bank_question_id = :q"
                     " ORDER BY created_at"), {"q": uuid.UUID(qid)},
            )).scalars().all()
        return list(rows)

    async def audit_actions(resource_id: str) -> set[str]:
        async with factory() as db:
            rows = (await db.execute(
                text("SELECT action FROM audit_log WHERE resource_id = :r"),
                {"r": uuid.UUID(resource_id)},
            )).scalars().all()
        return set(rows)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as c:
        print("\nPH4-D1 — a bank and a question, authored and reviewed")
        r = await c.post("/hr/question-banks", json={"name": "Aptitude", "description": "General"})
        bank = r.json()
        bank_id = bank["id"]
        check("HR A creates a bank", r.status_code == 201 and bank["counts"] == {}, r.text[:200])
        r = await c.post("/hr/question-banks", json={"name": "aptitude"})
        check("bank names are unique per company (case-insensitive)", r.status_code == 409, r.text[:160])

        r = await c.post(
            f"/hr/question-banks/{bank_id}/questions",
            json={"kind": "mcq", "prompt": "What is 2 + 2?", "options": ["3", "4", "5"],
                  "correct_index": 1, "points": 1, "difficulty": "easy", "language": "en",
                  "competencies": [{"id": "numeracy", "name": "Numeracy"}], "tags": ["maths"]},
        )
        q = r.json()
        qid = q["id"]
        check("HR A authors a draft MCQ question", r.status_code == 201 and q["status"] == "draft"
              and q["version"] == 1 and q["root_id"] == qid, r.text[:200])
        r = await c.post(f"/hr/bank-questions/{qid}/submit")
        check("HR A submits it for review", r.json().get("status") == "in_review", r.text[:160])

        print("\nPH4-D1 — review: not the author or submitter")
        r = await c.post(f"/hr/bank-questions/{qid}/approve", json={})
        check("HR A's own approval is refused, as an offer's is", r.status_code == 403,
              r.text[:200])
        acting["hr"] = hr_a2
        r = await c.post(f"/hr/bank-questions/{qid}/approve", json={"note": "Looks good"})
        check("HR B (a second HR manager) approves", r.status_code == 200
              and r.json()["status"] == "approved", r.text[:200])
        acting["hr"] = hr_a1

        print("\nPH4-D1 — copying into an exam section")
        r = await c.post("/hr/exams", json={"title": "Screening", "kind": "mcq"})
        exam = r.json()
        exam_id = exam["id"]
        check("HR A creates an exam", r.status_code == 201, r.text[:160])
        r = await c.get(f"/hr/exams/{exam_id}/structure")
        structure = r.json()
        round_id = structure["rounds"][0]["id"]
        section_id = structure["rounds"][0]["sections"][0]["id"]
        check("the exam has its default round and section", bool(round_id and section_id),
              r.text[:200])

        r = await c.post(f"/hr/exams/{exam_id}/sections/{section_id}/bank-questions",
                         json={"ids": [qid]})
        add1 = r.json()
        check("adding the approved question copies it into the section",
              add1["added"] == 1 and not add1["skipped"], r.text[:200])
        r = await c.post(f"/hr/exams/{exam_id}/sections/{section_id}/bank-questions",
                         json={"ids": [qid]})
        add2 = r.json()
        check("adding it again is skipped as a duplicate", add2["added"] == 0
              and add2["skipped"] == [{"id": qid, "reason": "already in this exam"}], r.text[:200])

        r = await c.get(f"/hr/exams/{exam_id}")
        copied = r.json()["questions"][0]
        eq_id = copied["id"]
        check("the exam's copy carries the bank prompt and answer",
              copied["prompt"] == "What is 2 + 2?" and copied["correct_index"] == 1, r.text[:200])
        # The exam editor's "From bank - vN" chip reads these. The copy always
        # wrote them, but no response returned them, so the chip never
        # rendered -- the acceptance evidence pass found it; no test had.
        check("the exam's copy says which bank question and version it came from",
              copied.get("source_bank_question_id") == qid
              and copied.get("source_bank_root_id") == qid
              and copied.get("source_bank_version") == 1, str(copied)[:300])

        # Reuse ACROSS assessments -- the criterion's own words. The
        # duplicate refusal above is per exam, so the same approved question
        # must go into a second exam as a fresh copy.
        r = await c.post("/hr/exams", json={"title": "Second screening", "kind": "mcq"})
        exam2_id = r.json()["id"]
        r = await c.get(f"/hr/exams/{exam2_id}/structure")
        section2_id = r.json()["rounds"][0]["sections"][0]["id"]
        r = await c.post(f"/hr/exams/{exam2_id}/sections/{section2_id}/bank-questions",
                         json={"ids": [qid]})
        check("the same approved question is reused in a second exam",
              r.status_code == 200 and r.json().get("added") == 1, r.text[:200])
        r = await c.get(f"/hr/exams/{exam2_id}")
        copy2 = r.json()["questions"][0]
        check("…as its own copy, with the same provenance",
              copy2["id"] != eq_id and copy2.get("source_bank_root_id") == qid
              and copy2.get("source_bank_version") == 1, str(copy2)[:300])

        print("\nPH4-D1 — publish, and a candidate takes it")
        r = await c.patch(f"/hr/exams/{exam_id}/rounds/{round_id}", json={"status": "published"})
        check("HR A publishes the round", r.json().get("status") == "published", r.text[:160])

        applicant_id = uuid.uuid4()
        async with factory() as db:
            await db.execute(
                text("INSERT INTO applicants (id,company_id,full_name,target_job_title)"
                     " VALUES (:i,:c,'Asha','Engineer')"),
                {"i": applicant_id, "c": cid},
            )
            await db.commit()
        r = await c.post(f"/hr/exams/{exam_id}/assignments",
                         json={"applicant_ids": [str(applicant_id)]})
        assigned = r.json()
        token = assigned[0]["magic_link"].split("#")[-1] if assigned else ""
        check("HR A assigns the exam and gets a magic link", r.status_code == 201 and bool(token),
              r.text[:200])

        headers = {"X-Exam-Token": token}
        r = await c.get("/exam", headers=headers)
        take = r.json()
        check("the candidate opens the round", r.status_code == 200
              and take["total_questions"] == 1, r.text[:200])
        r = await c.post("/exam/start", headers=headers)
        attempt_id = r.json()["attempt_id"]
        check("the candidate starts an attempt", r.status_code == 200 and bool(attempt_id),
              r.text[:160])
        r = await c.post("/exam/submit", headers=headers,
                         json={"attempt_id": attempt_id, "answers": {eq_id: 1}})
        result = r.json()
        check("the candidate submits and passes", r.status_code == 200
              and result["passed"] is True and result["score_percent"] == 100, r.text[:200])

        print("\nPH4-D1 — a published round's content is fixed")
        r = await c.patch(f"/hr/exams/{exam_id}/questions/{eq_id}", json={"correct_index": 0})
        check("editing the copied question on a published (and now taken) round is refused",
              r.status_code == 409 and "content is fixed" in r.json()["detail"], r.text[:200])
        r = await c.post(f"/hr/exams/{exam_id}/sections/{section_id}/bank-questions",
                         json={"ids": [qid]})
        check("…and adding another bank question to it is refused the same way",
              r.status_code == 409, r.text[:200])

        print("\nPH4-D1 — a new version, and the exam's copy is unaffected")
        r = await c.post(
            f"/hr/bank-questions/{qid}/new-version",
            json={"prompt": "What is 2 + 2? (v2)", "options": ["3", "4", "5"], "correct_index": 1},
        )
        v2 = r.json()
        v2_id = v2["id"]
        check("HR A drafts a new version", r.status_code == 200 and v2["version"] == 2
              and v2["root_id"] == qid, r.text[:200])
        await c.post(f"/hr/bank-questions/{v2_id}/submit")
        acting["hr"] = hr_a2
        r = await c.post(f"/hr/bank-questions/{v2_id}/approve", json={})
        check("…and a second HR manager approves it", r.json().get("status") == "approved",
              r.text[:200])
        acting["hr"] = hr_a1
        async with factory() as db:
            v1_status = await db.scalar(text("SELECT status FROM bank_questions WHERE id = :q"),
                                        {"q": uuid.UUID(qid)})
        check("approving v2 retires v1 automatically", v1_status == "retired", str(v1_status))

        r = await c.get(f"/hr/exams/{exam_id}")
        still = r.json()["questions"][0]
        check("the exam's existing copy is unchanged byte for byte",
              still["prompt"] == "What is 2 + 2?" and still["id"] == eq_id, r.text[:200])

        print("\nPH4-D1 — retiring hides a question from the picker")
        r = await c.get("/hr/bank-questions", params={"status": "approved", "bank_id": bank_id})
        check("v2 is in the approved picker list", any(x["id"] == v2_id for x in r.json()),
              r.text[:200])
        r = await c.post(f"/hr/bank-questions/{v2_id}/retire")
        check("HR A retires v2", r.json().get("status") == "retired", r.text[:160])
        r = await c.get("/hr/bank-questions", params={"status": "approved", "bank_id": bank_id})
        check("…and it is gone from the approved picker list",
              not any(x["id"] == v2_id for x in r.json()), r.text[:200])

        print("\nPH4-D1 — tenant isolation")
        acting["company"] = other_cid
        acting["hr"] = hr_b
        r = await c.patch(f"/hr/question-banks/{bank_id}", json={"name": "Stolen"})
        check("another company's HR gets 404 on the bank", r.status_code == 404, r.text[:160])
        r = await c.get(f"/hr/bank-questions/{qid}")
        check("…and 404 on the question", r.status_code == 404, r.text[:160])
        r = await c.get("/hr/bank-questions", params={"bank_id": bank_id, "status": "approved"})
        check("…and search across banks finds nothing of another company's",
              r.status_code == 200 and r.json() == [], r.text[:200])
        acting["company"] = cid
        acting["hr"] = hr_a1

        print("\nPH4-D1 — role gates")
        del app.dependency_overrides[get_hr_company]
        interviewer, candidate = uuid.uuid4(), uuid.uuid4()
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=str(interviewer), full_name="Ivy", email="ivy@x.test", roles=["interviewer"])
        r = await c.post("/hr/question-banks", json={"name": "Nope"})
        check("an interviewer gets 403 on a bank route", r.status_code == 403, r.text[:160])
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=str(candidate), full_name="Cand", email="cand@x.test", roles=["candidate"])
        r = await c.post("/hr/question-banks", json={"name": "Nope"})
        check("a candidate gets 403 on a bank route", r.status_code == 403, r.text[:160])
        del app.dependency_overrides[get_current_user]
        app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], acting["company"])

        print("\nPH4-D1 — the optional super-admin review path")
        r = await c.post(
            f"/hr/question-banks/{bank_id}/questions",
            json={"kind": "mcq", "prompt": "Third question?", "options": ["a", "b"],
                  "correct_index": 0, "points": 1, "difficulty": "easy", "language": "en"},
        )
        q3 = r.json()["id"]
        acting["hr"] = hr_a2
        await c.post(f"/hr/bank-questions/{q3}/submit")
        acting["hr"] = hr_a1
        r = await c.post(f"/admin/bank-questions/{q3}/approve", json={})
        check("the company super admin can also approve a question", r.status_code == 200
              and r.json()["status"] == "approved", r.text[:200])
        acting["hr"] = hr_a1

        print("\nPH4-D1 — events and audit")
        evs = await events_for(qid)
        check("v1's history has created, submitted, approved, copied and (once v2 approved) retired",
              {"created", "submitted", "approved", "copied_to_exam", "retired"} <= set(evs), str(evs))
        v2_evs = await events_for(v2_id)
        check("v2's own history records that it is a new version, then approved and retired",
              {"versioned", "submitted", "approved", "retired"} <= set(v2_evs), str(v2_evs))
        acts = await audit_actions(qid)
        check("the audit log recorded the submission and approval",
              {"bank_question.submitted", "bank_question.approve"} <= acts, str(acts))

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
