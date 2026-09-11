"""B5 smoke test — every screen reads applications, not people.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head (it
TRUNCATEs what it touches):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_b_applications.py

The person at the centre, Priya, applied to two openings. She is shortlisted
for Python and new for Nurse, and passes a Python exam. Before B5 every screen
showed her as one row with one status, one score and one exam — whichever
application had written her applicant row last — and the exam counted for both.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def seed(f) -> dict:
    now = datetime.now(tz=UTC)
    s: dict = {k: uuid.uuid4() for k in (
        "company", "hr", "python", "nurse", "exam", "round", "wf", "wf_round")}
    async with f() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'acme','acme',true,:n,:n)"), {"i": s["company"], "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:n,:n)"),
            {"i": s["hr"], "c": s["company"], "n": now})
        for rid, title in [(s["python"], "Python Developer"), (s["nurse"], "Staff Nurse")]:
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " jd_text,created_at,updated_at) VALUES (:i,:c,:t,'mid','open',false,:jd,:n,:n)"),
                {"i": rid, "c": s["company"], "t": title, "jd": f"JD for {title}", "n": now})
        await db.execute(text(
            "INSERT INTO exams (id,company_id,title,created_by_user_id,created_at,updated_at)"
            " VALUES (:i,:c,'Python screen',:u,:t,:t)"),
            {"i": s["exam"], "c": s["company"], "u": s["hr"], "t": now})
        await db.execute(text(
            "INSERT INTO exam_rounds (id,exam_id,company_id,round_number,title,position,status,"
            " created_at,updated_at) VALUES (:i,:e,:c,1,'Aptitude',0,'published',:t,:t)"),
            {"i": s["round"], "e": s["exam"], "c": s["company"], "t": now})
        # Python's workflow uses that exam round — which is how a hand-assigned
        # exam is known to be for the Python application.
        await db.execute(text(
            "INSERT INTO workflows (id,company_id,requisition_id,version,status,created_at,"
            " updated_at) VALUES (:i,:c,:r,1,'draft',:n,:n)"),
            {"i": s["wf"], "c": s["company"], "r": s["python"], "n": now})
        await db.execute(text(
            "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
            " exam_round_id,created_at,updated_at)"
            " VALUES (:i,:c,:w,0,'Aptitude','mcq',:er,:n,:n)"),
            {"i": s["wf_round"], "c": s["company"], "w": s["wf"], "er": s["round"], "n": now})

        async def person(key: str, name: str, apps: list[tuple], mirror: str) -> None:
            aid = uuid.uuid4()
            s[key] = aid
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,resume_text,status,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,'x','mid','cv',:st,:n,:n)"),
                {"i": aid, "c": s["company"], "u": s["hr"], "fn": name,
                 "em": f"{key}@x.test", "st": mirror, "n": now})
            for i, (rid, title, st, ats) in enumerate(apps):
                eid = uuid.uuid4()
                s[f"{key}_{'python' if rid == s['python'] else 'nurse'}"] = eid
                await db.execute(text(
                    "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                    " target_job_title,target_level,ats_overall,created_at,updated_at)"
                    " VALUES (:i,:c,:r,:a,:st,:t,'mid',:o,:n,:n)"),
                    {"i": eid, "c": s["company"], "r": rid, "a": aid, "st": st, "t": title,
                     "o": ats, "n": now + timedelta(seconds=i)})

        # Nurse is her LATEST application (created second): the applicant row
        # mirrors it, so her person-level status says 'new'.
        await person("priya", "Priya", [(s["python"], "Python Developer", "shortlisted", 80),
                                        (s["nurse"], "Staff Nurse", "new", 40)], mirror="new")
        await person("quinn", "Quinn", [(s["python"], "Python Developer", "new", 60)], "new")
        await person("unfiled", "Uma", [], "new")
        await db.commit()
    return s


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, exams, exam_rounds,"
            " exam_assignments, exam_attempts, interview_invites, jobs, email_events CASCADE"))
    s = await seed(f)

    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (s["hr"], s["company"])
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    # ── 1. A hand-assigned exam is attributed to the right application ────────
    r = await ac.post(f"/hr/exams/{s['exam']}/assignments",
                      json={"applicant_ids": [str(s["priya"]), str(s["quinn"])],
                            "round_id": str(s["round"])})
    check("manual exam assignment succeeds", r.status_code == 201, r.text[:200])
    async with f() as db:
        linked = {row[0]: row[1] for row in (await db.execute(text(
            "SELECT applicant_id, enrolment_id FROM exam_assignments"))).all()}
        # Priya passes it.
        await db.execute(text(
            "INSERT INTO exam_attempts (company_id,exam_id,applicant_id,assignment_id,round_id,"
            " score_percent,passed,status,started_at,submitted_at)"
            " SELECT company_id, exam_id, applicant_id, id, round_id, 85, true, 'submitted',"
            "        now(), now() FROM exam_assignments WHERE applicant_id = :a"),
            {"a": s["priya"]})
        await db.commit()
    check("with two applications, the exam goes to the one whose workflow uses it",
          linked.get(s["priya"]) == s["priya_python"], str(linked.get(s["priya"])))
    check("with one application, the exam goes to it",
          linked.get(s["quinn"]) == s["quinn_python"], str(linked.get(s["quinn"])))

    # ── 2. The board: one row per application ───────────────────────────────
    rows = (await ac.get("/hr/pipeline?limit=50")).json()
    by = {(x["full_name"], x["opening_title"]): x for x in rows["items"]}
    py, nu = by.get(("Priya", "Python Developer"), {}), by.get(("Priya", "Staff Nurse"), {})
    check("the board has a row per application, and the unfiled person",
          rows["count"] == 4 and ("Uma", "x") in by, f"{rows['count']} {sorted(by)}")
    check("each of Priya's rows has its own status and score",
          (py.get("status"), py.get("ats_overall"), nu.get("status"), nu.get("ats_overall"))
          == ("shortlisted", 80, "new", 40), f"{py} {nu}")
    check("her Python exam shows on Python and not on Nurse",
          py.get("exam_passed") is True and py.get("best_exam_percent") == 85
          and nu.get("exam_passed") is None and nu.get("total_exam_attempts") == 0, f"{py} {nu}")
    check("rows carry the application id to act on",
          py.get("enrolment_id") == str(s["priya_python"]))

    an = (await ac.get("/hr/analytics")).json()["funnel"]
    check("analytics count applications (and people separately)",
          (an["total_applicants"], an["total_applications"], an["shortlisted"],
           an["exam_passed"]) == (3, 3, 1, 1), str(an))

    # ── 3. Interview eligibility is per application ─────────────────────────
    el = (await ac.get("/hr/interviews/eligible-applicants?source=any")).json()
    check("only Priya's Python application is interview-eligible",
          [(e["full_name"], e["opening_title"]) for e in el] == [("Priya", "Python Developer")],
          str(el))
    r0 = await ac.post("/hr/interviews", json={"applicant_id": str(s["priya"])})
    check("an invite that does not say which application is refused",
          r0.status_code == 409 and "choose which one" in r0.text, r0.text[:160])
    r1 = await ac.post("/hr/interviews", json={"applicant_id": str(s["priya"]),
                                               "enrolment_id": str(s["priya_nurse"])})
    check("an invite for an application that is not eligible is refused",
          r1.status_code == 409, r1.text[:160])
    r2 = await ac.post("/hr/interviews", json={"applicant_id": str(s["priya"]),
                                               "enrolment_id": str(s["priya_python"])})
    async with f() as db:
        inv = (await db.execute(text(
            "SELECT i.enrolment_id, j.title, j.jd_text FROM interview_invites i"
            " JOIN jobs j ON j.id = i.job_id WHERE i.applicant_id = :a"),
            {"a": s["priya"]})).mappings().first()
    check("an invite for her Python application is created", r2.status_code == 201, r2.text[:200])
    check("…linked to it, and set up for the Python role — not her latest (Nurse)",
          inv is not None and inv["enrolment_id"] == s["priya_python"]
          and inv["title"] == "Python Developer" and inv["jd_text"] == "JD for Python Developer",
          str(inv))
    r3 = await ac.post("/hr/interviews", json={"applicant_id": str(s["priya"]),
                                               "enrolment_id": str(s["quinn_python"])})
    check("another person's application id is not accepted", r3.status_code == 404,
          str(r3.status_code))

    # ── 4. Decisions land on the application they name ───────────────────────
    d0 = await ac.post(f"/hr/applicants/{s['priya']}/decision", json={"decision": "rejected"})
    check("a decision that does not name an application is refused", d0.status_code == 409)
    d1 = await ac.post(f"/hr/applicants/{s['priya']}/decision",
                       json={"decision": "hired", "enrolment_id": str(s["priya_nurse"])})
    check("hiring from an application still 'new' is refused — whatever the person's row says",
          d1.status_code == 409, d1.text[:160])
    d2 = await ac.post(f"/hr/applicants/{s['priya']}/decision",
                       json={"decision": "hired", "enrolment_id": str(s["priya_python"])})
    async with f() as db:
        st = {r[0]: r[1] for r in (await db.execute(text(
            "SELECT id, status FROM enrolments WHERE applicant_id = :a"),
            {"a": s["priya"]})).all()}
        mirror = await db.scalar(text("SELECT status FROM applicants WHERE id = :a"),
                                 {"a": s["priya"]})
        ledger = await db.scalar(text(
            "SELECT count(*) FROM stage_transitions WHERE enrolment_id = :e AND to_status='hired'"),
            {"e": s["priya_python"]})
        mail = await db.scalar(text(
            "SELECT count(*) FROM email_events WHERE template = 'decision'"
            "   AND (body_text LIKE '%Python Developer%' OR subject LIKE '%Python Developer%')"))
    check("hiring her for Python (shortlisted there) succeeds", d2.status_code == 200,
          d2.text[:200])
    check("only the Python application moved, on the ledger",
          st.get(s["priya_python"]) == "hired" and st.get(s["priya_nurse"]) == "new"
          and ledger == 1, str(st))
    check("the person-level row still mirrors her latest application (Nurse: new)",
          mirror == "new", str(mirror))
    check("the decision email names the opening decided on", mail == 1, f"mail={mail}")

    d3 = await ac.patch(f"/hr/applicants/{s['priya']}",
                        json={"status": "rejected", "enrolment_id": str(s["priya_nurse"])})
    async with f() as db:
        mirror = await db.scalar(text("SELECT status FROM applicants WHERE id = :a"),
                                 {"a": s["priya"]})
    check("the applicant board's change lands on the named application, and the mirror"
          " follows it because it is her latest", d3.status_code == 200 and mirror == "rejected",
          f"{d3.status_code} {mirror}")

    # ── 5. Applicant list filters match any application ─────────────────────
    hired = {a["full_name"] for a in (await ac.get("/hr/applicants?status=hired")).json()}
    nurse = {a["full_name"] for a in (await ac.get("/hr/applicants?job=nurse")).json()}
    check("status filter finds her by an application, not only her latest",
          hired == {"Priya"}, str(hired))
    check("job filter finds everyone who applied for it", nurse == {"Priya"}, str(nurse))
    # ── 5b. The person's applications, each with its own assessment ────────
    apps = (await ac.get(f"/hr/applicants/{s['priya']}/applications")).json()
    check("a person's applications are listed, oldest first, each with its own score",
          [(x["opening_title"], x["ats_overall"], x["is_latest"]) for x in apps]
          == [("Python Developer", 80, False), ("Staff Nurse", 40, True)], str(apps))
    r5 = await ac.get(f"/hr/applicants/{uuid.uuid4()}/applications")
    check("another company's (or no) applicant's applications are not listed",
          r5.status_code == 404, str(r5.status_code))
    p0 = await ac.patch(f"/hr/applicants/{s['priya']}", json={"status": "shortlisted"})
    check("a shortlist that does not say which opening is refused (it used to change only"
          " the person's row)", p0.status_code == 409, f"{p0.status_code} {p0.text[:120]}")

    # ── 5c. An exam assigned to a named application is recorded against it ──
    ex = await ac.post(f"/hr/exams/{s['exam']}/assignments",
                       json={"enrolment_ids": [str(s["priya_nurse"]), str(uuid.uuid4())],
                             "round_id": str(s["round"])})
    body = ex.json() if ex.status_code == 201 else []
    check("an exam assigned to a named application is recorded against that one",
          ex.status_code == 201 and len(body) == 1
          and body[0]["enrolment_id"] == str(s["priya_nurse"]), f"{ex.status_code} {body}")
    ex0 = await ac.post(f"/hr/exams/{s['exam']}/assignments", json={"round_id": str(s["round"])})
    check("an assignment naming nobody is refused", ex0.status_code == 422, str(ex0.status_code))

    await ac.aclose()
    app.dependency_overrides.clear()

    # ── 6. The copilot and the watchers read the same thing ──────────────────
    from app.agents.tools import _PIPELINE_SQL
    from app.agents.watch_runner import gather_company_input

    async with f() as db:
        cop = (await db.execute(_PIPELINE_SQL, {"cid": s["company"], "status": None,
                                                "job": "%Nurse%", "limit": 25})).mappings().all()
        w = await gather_company_input(db, str(s["company"]))
    check("the copilot's job filter returns the Nurse application only",
          [(r["full_name"], r["status"]) for r in cop] == [("Priya", "rejected")], str(cop))
    stalled = sorted(str(x) for x in w.stalled) if hasattr(w, "stalled") else None
    check("the watchers no longer see Priya as stalled once both are decided",
          stalled is not None and not any("Priya" in x for x in stalled)
          and any("Quinn" in x for x in stalled), str(stalled))

    # ── 7. Auto-advance after an exam pass is for that application ──────────
    from app.models import Applicant
    from app.routers.hr_interviews import advance_applicant_to_interview

    async with f() as db:
        quinn = await db.get(Applicant, s["quinn"])
        inv = await advance_applicant_to_interview(
            db, company_id=s["company"], applicant=quinn, created_by_user_id=s["hr"],
            enrolment_id=s["quinn_python"])
        await db.commit()
        row = (await db.execute(text(
            "SELECT i.enrolment_id, j.title FROM interview_invites i JOIN jobs j ON j.id=i.job_id"
            " WHERE i.id = :i"), {"i": inv.id if inv else None})).mappings().first()
    check("an auto-advance invite is created linked to its application, for its role",
          row is not None and row["enrolment_id"] == s["quinn_python"]
          and row["title"] == "Python Developer", str(row))

    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
