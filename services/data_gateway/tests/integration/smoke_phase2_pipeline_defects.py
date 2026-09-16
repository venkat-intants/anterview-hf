"""Smoke test — defects the whole-pipeline test found in a real run (2026-09-13).

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head:

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_phase2_pipeline_defects.py

What broke live:
* a workflow published with a DRAFT exam round emailed shortlisted candidates
  exam links that could never open;
* attaching an exam round with no questions satisfied validation too;
* a candidate who applied in Hindi got the shortlist email and exam invite in
  English.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:  # noqa: PLR0915 — one scenario, read top to bottom
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria,"
            " exams, exam_rounds, exam_sections, exam_questions, exam_assignments,"
            " notifications, email_events CASCADE"))

    cid, uid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    exam, er, er_empty, section = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with f() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'Acme','acme',true,:t,:t)"), {"i": cid, "t": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.test','HR','x',:c,'en',true,false,false,:t,:t)"),
            {"i": uid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,level,created_at,updated_at)"
            " VALUES (:i,:c,'Python Developer','mid',:t,:t)"), {"i": rid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO exams (id,company_id,title,kind,status,created_by_user_id,created_at,updated_at)"
            " VALUES (:i,:c,'Aptitude','mcq','published',:u,:t,:t)"),
            {"i": exam, "c": cid, "u": uid, "t": now})
        # The exam is published; its round is still a draft — exactly the state
        # the live run reached through PATCH /hr/exams/{id} status=published.
        await db.execute(text(
            "INSERT INTO exam_rounds (id,exam_id,company_id,round_number,title,position,status,"
            " created_at,updated_at) VALUES (:i,:e,:c,1,'Round 1',0,'draft',:t,:t)"),
            {"i": er, "e": exam, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO exam_sections (id,round_id,exam_id,company_id,title,kind,position)"
            " VALUES (:i,:r,:e,:c,'Section 1','mcq',0)"),
            {"i": section, "r": er, "e": exam, "c": cid})
        await db.execute(text(
            "INSERT INTO exam_questions (id,exam_id,section_id,company_id,prompt,options,"
            " correct_index,position) VALUES (:i,:e,:s,:c,'2 + 2?',CAST(:o AS jsonb),1,0)"),
            {"i": uuid.uuid4(), "e": exam, "s": section, "c": cid, "o": json.dumps(["3", "4"])})
        # A published round with no questions at all.
        await db.execute(text(
            "INSERT INTO exam_rounds (id,exam_id,company_id,round_number,title,position,status,"
            " created_at,updated_at) VALUES (:i,:e,:c,2,'Round 2',1,'published',:t,:t)"),
            {"i": er_empty, "e": exam, "c": cid, "t": now})
        await db.commit()

    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (uid, cid)
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    # ── A one-round draft pointing at the DRAFT exam round ──────────────────
    r = await ac.post(f"/hr/requisitions/{rid}/workflows", json={})
    check("a draft workflow is created", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
    wf_id = r.json()["id"]
    await ac.patch(f"/hr/workflows/{wf_id}", json={"auto_assign_first_round": True})
    r = await ac.post(f"/hr/workflows/{wf_id}/rounds", json={
        "title": "Aptitude", "kind": "mcq", "pass_threshold": 60, "deadline_days": 5,
        "exam_round_id": str(er)})
    check("an MCQ round is added", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
    round_id = next(x["id"] for x in r.json()["rounds"] if x["title"] == "Aptitude")

    # ── A round's criteria round-trip through the builder unchanged ─────────
    comps = (await ac.get(f"/hr/requisitions/{rid}/role-model")).json()["competencies"][:2]
    r = await ac.put(f"/hr/workflows/{wf_id}/rounds/{round_id}/criteria",
                     json={"criteria": [{k: c[k] for k in ("id", "name", "kind", "weight",
                                                         "anchors", "probes")} for c in comps]})
    got = next(x for x in r.json()["rounds"] if x["id"] == round_id)["criteria"]
    check("a round's criteria come back with the ids the builder keys on",
          r.status_code == 200 and sorted(c.get("id") for c in got) == sorted(c["id"] for c in comps)
          and all(c.get("name") and "kind" in c for c in got), f"{r.status_code} {got}")
    r = await ac.put(f"/hr/workflows/{wf_id}/rounds/{round_id}/criteria",
                     json={"criteria": [{k: c[k] for k in ("id", "name", "kind", "weight",
                                                         "anchors", "probes")} for c in got]})
    check("…and sending them back as the builder does is accepted", r.status_code == 200,
          f"{r.status_code} {r.text[:200]}")

    report = (await ac.get(f"/hr/workflows/{wf_id}/validate")).json()
    check("validation names the draft exam round as blocking",
          any(e.startswith("Aptitude:") and "draft" in e for e in report["errors"])
          and report["publishable"] is False, str(report["errors"]))
    r = await ac.post(f"/hr/workflows/{wf_id}/publish")
    check("publishing it is refused (422)", r.status_code == 422, f"{r.status_code} {r.text[:200]}")

    # ── The same round pointing at a published but EMPTY exam round ─────────
    await ac.patch(f"/hr/workflows/{wf_id}/rounds/{round_id}", json={"exam_round_id": str(er_empty)})
    report = (await ac.get(f"/hr/workflows/{wf_id}/validate")).json()
    check("validation names the empty exam round as blocking",
          any(e.startswith("Aptitude:") and "no questions" in e for e in report["errors"]),
          str(report["errors"]))
    r = await ac.post(f"/hr/workflows/{wf_id}/publish")
    check("publishing it is refused (422)", r.status_code == 422, str(r.status_code))

    # ── Publish the exam round as HR would, then the workflow goes live ─────
    r = await ac.patch(f"/hr/exams/{exam}/rounds/{er}", json={"status": "published"})
    check("the exam round with a question can be published", r.status_code == 200,
          f"{r.status_code} {r.text[:200]}")
    await ac.patch(f"/hr/workflows/{wf_id}/rounds/{round_id}", json={"exam_round_id": str(er)})
    report = (await ac.get(f"/hr/workflows/{wf_id}/validate")).json()
    check("with a published round that has questions, nothing blocks",
          report["errors"] == [] and report["publishable"] is True, str(report["errors"]))
    r = await ac.post(f"/hr/workflows/{wf_id}/publish")
    check("the workflow publishes", r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    # ── Unpublishing an exam round a live workflow uses is refused ──────────
    r = await ac.patch(f"/hr/exams/{exam}/rounds/{er}", json={"status": "draft"})
    check("unpublishing the round the live workflow uses is refused (409)",
          r.status_code == 409 and "Python Developer" in r.text, f"{r.status_code} {r.text[:200]}")
    r = await ac.patch(f"/hr/exams/{exam}/rounds/{er_empty}", json={"status": "draft"})
    check("…while a round no live workflow uses can still be unpublished",
          r.status_code == 200, f"{r.status_code} {r.text[:200]}")

    # ── Two Hindi-speaking applicants on the live workflow ──────────────────
    async def applicant(name: str, email: str) -> tuple[uuid.UUID, uuid.UUID]:
        aid, guest, en = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        async with f() as db:
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,company_id,preferred_language,is_active,"
                " notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,:n,:c,'hi',true,false,false,:t,:t)"),
                {"i": guest, "e": f"guest+{guest}@applicants.invalid", "n": name, "c": cid, "t": now})
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,full_name,email,target_job_title,user_id,"
                " created_at,updated_at) VALUES (:i,:c,:n,:e,'Python Developer',:u,:t,:t)"),
                {"i": aid, "c": cid, "n": name, "e": email, "u": guest, "t": now})
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                " target_job_title,workflow_id,created_at,updated_at)"
                " VALUES (:i,:c,:r,:a,'new','Python Developer',:w,:t,:t)"),
                {"i": en, "c": cid, "r": rid, "a": aid, "w": wf_id, "t": now})
            await db.commit()
        return aid, en

    # The runner's own guard: the exam round goes back to draft AFTER the
    # workflow is live (bypassing the endpoint guard, as a stray UPDATE would).
    async with f() as db:
        await db.execute(text("UPDATE exam_rounds SET status = 'draft' WHERE id = :i"), {"i": er})
        await db.commit()
    a1, e1 = await applicant("Arjun Rao", "arjun@acme.test")
    r = await ac.patch(f"/hr/applicants/{a1}", json={"status": "shortlisted", "enrolment_id": str(e1)})
    check("shortlisting still succeeds", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    async with f() as db:
        links = await db.scalar(text(
            "SELECT count(*) FROM exam_assignments WHERE applicant_id = :a"), {"a": a1})
        told = await db.scalar(text(
            "SELECT count(*) FROM notifications WHERE user_id = :u AND kind = 'round_not_ready'"
            " AND body LIKE '%draft%'"), {"u": uid})
        exam_mail = await db.scalar(text(
            "SELECT count(*) FROM email_events WHERE to_email = 'arjun@acme.test'"
            " AND template = 'exam_link'"))
        decision_lang = await db.scalar(text(
            "SELECT lang FROM email_events WHERE to_email = 'arjun@acme.test'"
            " AND template = 'decision'"))
    check("no exam link is minted for a draft exam round", links == 0, str(links))
    check("…and none is emailed", exam_mail == 0, str(exam_mail))
    check("the workflow owner is told what to fix", told == 1, str(told))
    check("the shortlist email goes out in Hindi", decision_lang == "hi", str(decision_lang))

    # With the exam round live again, a shortlist sends a working link in Hindi.
    async with f() as db:
        await db.execute(text("UPDATE exam_rounds SET status = 'published' WHERE id = :i"), {"i": er})
        await db.commit()
    a2, e2 = await applicant("Meena Iyer", "meena@acme.test")
    r = await ac.patch(f"/hr/applicants/{a2}", json={"status": "shortlisted", "enrolment_id": str(e2)})
    async with f() as db:
        links = await db.scalar(text(
            "SELECT count(*) FROM exam_assignments WHERE applicant_id = :a AND status = 'invited'"),
            {"a": a2})
        exam_lang = await db.scalar(text(
            "SELECT lang FROM email_events WHERE to_email = 'meena@acme.test'"
            " AND template = 'exam_link'"))
    check("a live exam round gets its link", r.status_code == 200 and links == 1,
          f"{r.status_code} {links}")
    check("the exam invite goes out in Hindi", exam_lang == "hi", str(exam_lang))

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
