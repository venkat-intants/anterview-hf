"""E2 smoke test — the final decision, against a real Postgres and the real API.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head
(it TRUNCATEs what it touches):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_e_decisions.py

What it checks:
  1. The queue carries what a reviewer needs: stage, workflow version, each
     completed round, the mean of scored rounds (superseded attempts excluded).
  2. One guarded writer for hire and reject: a reason is required, a hire is
     refused mid-round and over a rejection, a reject reverses a hire.
  3. Every decision is recorded against the person — ledger and audit log — and
     takes the candidate off their round and out of the queue.
  4. Round results can be read for one application.
  5. Closing an opening leaves undecided candidates in the queue.
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


async def main() -> None:  # noqa: PLR0915 — one linear script
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria, round_results,"
            " reconciliation_state CASCADE"))
        # audit_log is append-only by trigger, so earlier runs' rows stay. The
        # checks below look rows up by this run's own enrolment ids instead.

    cid, other_cid, hr, other_hr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    req, legacy, other_req = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    wf, r_test, r_review = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    async with f() as db:
        for company, user, slug in ((cid, hr, "acme"), (other_cid, other_hr, "globex")):
            await db.execute(text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,:s,:s,true,:n,:n)"), {"i": company, "s": slug, "n": now})
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
                " is_active,notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,'HR','x',:c,'en',true,false,false,:n,:n)"),
                {"i": user, "e": f"hr@{slug}.example.com", "c": company, "n": now})
        for rid, company, title in ((req, cid, "Python Developer"), (legacy, cid, "Office Manager"),
                                    (other_req, other_cid, "Welder")):
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " owner_user_id,created_by_user_id,created_at,updated_at)"
                " VALUES (:i,:c,:t,'mid','open',false,:u,:u,:n,:n)"),
                {"i": rid, "c": company, "t": title, "u": hr if company == cid else other_hr,
                 "n": now})
        await db.execute(text(
            "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
            " auto_score_on_apply,auto_assign_first_round,auto_advance_rounds,reminders_enabled,"
            " hold_band,created_at,updated_at)"
            " VALUES (:i,:c,:r,1,'draft',true,true,true,true,10,:n,:n)"),
            {"i": wf, "c": cid, "r": req, "n": now})
        await db.execute(text(
            "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
            " pass_threshold,deadline_days,created_at,updated_at)"
            " VALUES (:i,:c,:w,1,'Final Review','human_review',NULL,5,:n,:n)"),
            {"i": r_review, "c": cid, "w": wf, "n": now})
        await db.execute(text(
            "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
            " pass_threshold,deadline_days,on_pass_next_round_id,created_at,updated_at)"
            " VALUES (:i,:c,:w,0,'Technical Test','mcq',60,5,:nx,:n,:n)"),
            {"i": r_test, "c": cid, "w": wf, "nx": r_review, "n": now})
        # PH4-O6: a workflow must be walked through review (draft -> in_review
        # -> approved, by two different people) before the database allows
        # status -> 'published'.
        from tests.integration.seed_helpers import approve_for_publish

        await approve_for_publish(db, workflow_id=wf, company_id=cid)
        await db.execute(text(
            "UPDATE workflows SET status='published', published_at=:n WHERE id=:i"),
            {"i": wf, "n": now})

        async def candidate(name: str, company: uuid.UUID = cid) -> uuid.UUID:
            aid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,status,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,'x','mid','new',:n,:n)"),
                {"i": aid, "c": company, "u": hr if company == cid else other_hr, "fn": name,
                 "em": f"{name}@example.com", "n": now})
            return aid

        async def enrol(aid: uuid.UUID, rid: uuid.UUID, status: str, *,
                        workflow: uuid.UUID | None = wf, round_id: uuid.UUID | None = None,
                        company: uuid.UUID = cid) -> uuid.UUID:
            eid = uuid.uuid4()
            held = status == "held"
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                " target_job_title,target_level,workflow_id,current_round_id,held_at,held_reason,"
                " created_at,updated_at)"
                " VALUES (:i,:c,:r,:a,:s,'x','mid',:w,:cr,:ha,:hr,:ca,:n)"),
                {"i": eid, "c": company, "r": rid, "a": aid, "s": status, "w": workflow,
                 "cr": round_id, "ha": now if held else None,
                 "hr": "Scored 54% against 60% on Technical Test" if held else None,
                 "ca": now - timedelta(days=6), "n": now})
            return eid

        async def result(eid: uuid.UUID, round_id: uuid.UUID, percent: float | None,
                         passed: bool, graded_by: str, *, superseded: bool = False) -> None:
            await db.execute(text(
                "INSERT INTO round_results (id,company_id,enrolment_id,round_id,percent,passed,"
                " graded_by,evidence,criterion_scores,created_at,superseded_at)"
                " VALUES (:i,:c,:e,:r,:p,:ps,:g,'evidence',CAST(:cs AS jsonb),:n,:sup)"),
                {"i": uuid.uuid4(), "c": cid, "e": eid, "r": round_id, "p": percent, "ps": passed,
                 "g": graded_by, "cs": '{"python": 7}', "n": now,
                 "sup": now if superseded else None})

        ids = {}
        ids["mid"] = await enrol(await candidate("mid_round"), req, "shortlisted", round_id=r_test)
        ids["held"] = await enrol(await candidate("held"), req, "held", round_id=r_test)
        ids["review"] = await enrol(await candidate("on_review"), req, "shortlisted",
                                    round_id=r_review)
        finished_person = await candidate("finished")
        ids["finished"] = await enrol(finished_person, req, "interviewed")
        ids["finished_2"] = await enrol(await candidate("also_finished"), req, "interviewed")
        ids["new"] = await enrol(await candidate("never_started"), req, "new")
        ids["legacy"] = await enrol(await candidate("legacy"), legacy, "shortlisted", workflow=None)
        # The same person has applied to a second opening, with a result of its own.
        ids["finished_elsewhere"] = await enrol(finished_person, legacy, "shortlisted",
                                                workflow=None)
        ids["foreign"] = await enrol(await candidate("foreign", other_cid), other_req,
                                     "interviewed", workflow=None, company=other_cid)

        await result(ids["held"], r_test, 54, False, "deterministic")
        await result(ids["finished"], r_test, 20, False, "deterministic", superseded=True)
        await result(ids["finished"], r_test, 80, True, "deterministic")
        await result(ids["finished"], r_review, None, True, "human")
        await result(ids["finished_elsewhere"], r_test, 40, False, "deterministic")
        await db.commit()

    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (hr, cid)
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    async def queue() -> dict[str, dict]:
        rows = (await ac.get(f"/hr/requisitions/{req}/decision-queue")).json()
        return {r["full_name"]: r for r in rows}

    async def decide(
        key: str, decision: str, reason: str | None, reason_code: str | None = "skills_fit"
    ) -> tuple[int, dict]:
        # PH4-O4: reason_code is required on FinalDecisionIn. Defaulted to a
        # valid code here so every call below is refused (or not) for the rule
        # it is testing, not for a missing dropdown value.
        body: dict[str, str] = {"decision": decision}
        if reason is not None:
            body["reason"] = reason
        if reason_code is not None:
            body["reason_code"] = reason_code
        r = await ac.post(f"/hr/enrolments/{ids[key]}/decision", json=body)
        return r.status_code, r.json()

    # ── 1. What the queue shows ────────────────────────────────────────────
    q = await queue()
    fin, held = q.get("finished", {}), q.get("held", {})
    check("the queue holds the held, the finished and the one on review",
          set(q) == {"held", "on_review", "finished", "also_finished"}, str(sorted(q)))
    check("each row names the applicant, so the evidence can be opened",
          fin.get("applicant_id") == str(finished_person), str(fin.get("applicant_id")))
    check("…the workflow version and where they are",
          held.get("workflow_version") == 1 and held.get("current_round_title") == "Technical Test"
          and held.get("total_rounds") == 2, str(held))
    check("…each completed round, in order",
          [x["title"] for x in fin.get("round_results", [])] == ["Technical Test", "Final Review"],
          str(fin.get("round_results")))
    check("…and the mean of scored rounds, ignoring a superseded attempt",
          fin.get("composite_percent") == 80.0, str(fin.get("composite_percent")))
    check("…and how long they have waited, from the ledger",
          fin.get("waiting_days") is not None, str(fin.get("waiting_days")))

    # ── 2. The rules ───────────────────────────────────────────────────────
    code, _ = await decide("held", "hired", "ok")
    check("no decision without a reason", code == 422, str(code))
    code, body = await decide("mid", "hired", "Doing well so far")
    check("a hire is refused while they are still in an automated round", code == 409,
          f"{code} {body}")
    code, _ = await decide("new", "hired", "Great CV")
    check("…and before anyone has started them", code == 409, str(code))
    code, _ = await decide("legacy", "hired", "Interviewed in person, strong")
    check("an opening with no workflow can hire once shortlisted", code == 200, str(code))

    # ── 3. Recorded against a person, off the round, out of the queue ──────
    code, body = await decide("held", "hired", "Near miss on the test, excellent in person")
    check("hiring a held candidate is recorded", code == 200 and body.get("status") == "hired",
          f"{code} {body}")
    code, body = await decide("review", "hired", "Final review passed by the panel")
    check("hiring someone on a review round is recorded", code == 200, f"{code} {body}")
    async with f() as db:
        state = {r["id"]: dict(r) for r in (await db.execute(text(
            "SELECT id, status, current_round_id, held_at FROM enrolments WHERE id = ANY(:i)"),
            {"i": [ids["held"], ids["review"]]})).mappings().all()}
        last = (await db.execute(text(
            "SELECT to_status, automated, actor_user_id, reason, reason_code, reason_label"
            " FROM stage_transitions WHERE enrolment_id = :e AND to_status = 'hired'"),
            {"e": ids["held"]})).mappings().first()
        left_round = await db.scalar(text(
            "SELECT count(*) FROM stage_transitions WHERE enrolment_id = :e"
            " AND from_round_id IS NOT NULL AND to_round_id IS NULL AND NOT automated"),
            {"e": ids["review"]})
        audit = (await db.execute(text(
            "SELECT actor_id, details FROM audit_log WHERE action = 'enrolment.decision.hired'"
            "   AND resource_id = :e"), {"e": ids["held"]})).mappings().first()
    check("the ledger says a person decided, who, and why",
          last is not None and last["automated"] is False and last["actor_user_id"] == hr
          and last["reason"] == "Near miss on the test, excellent in person", str(last))
    check("…and its structured reason code and label (PH4-O4)",
          last is not None and last["reason_code"] == "skills_fit"
          and last["reason_label"] == "Skills / competency fit", str(last))
    check("the audit log records the same decision against the same person",
          audit is not None and audit["actor_id"] == hr
          and audit["details"]["previous_status"] == "held", str(audit))
    check("the hold is cleared and they are off the round",
          state[ids["held"]]["held_at"] is None and state[ids["held"]]["current_round_id"] is None,
          str(state[ids["held"]]))
    check("someone hired from a review round is no longer counted on it",
          state[ids["review"]]["current_round_id"] is None and left_round == 1,
          f"{state[ids['review']]} moves={left_round}")
    q = await queue()
    check("decided candidates leave the queue", "held" not in q and "on_review" not in q,
          str(sorted(q)))

    code, body = await decide("held", "rejected", "Offer withdrawn after references")
    check("a reject reverses a hire, and says so", code == 200 and body.get("reversal") is True,
          f"{code} {body}")
    code, _ = await decide("mid", "rejected", "Withdrew their application")
    check("a reject is allowed from anywhere live", code == 200, str(code))
    code, _ = await decide("mid", "hired", "Changed our minds")
    check("hiring over a rejection is refused", code == 409, str(code))

    r = await ac.post(f"/hr/enrolments/{ids['finished']}/status", json={"status": "hired"})
    check("the generic mover no longer records a hire without a reason", r.status_code == 422,
          str(r.status_code))
    # PH4-O4: a reason_code is required too, even with a valid free-text reason.
    r = await ac.post(f"/hr/enrolments/{ids['finished']}/status",
                      json={"status": "hired", "reason": "Top of the cohort"})
    check("…and a reason with no reason_code is also refused", r.status_code == 422,
          str(r.status_code))
    r = await ac.post(f"/hr/enrolments/{ids['finished']}/status",
                      json={"status": "hired", "reason": "Top of the cohort",
                            "reason_code": "skills_fit"})
    check("…and records one with a reason and a reason_code through the same writer",
          r.status_code == 200 and r.json().get("status") == "hired", f"{r.status_code} {r.text[:120]}")

    r = await ac.post(f"/hr/enrolments/{ids['foreign']}/decision",
                      json={"decision": "rejected", "reason": "Not ours to decide",
                            "reason_code": "skills_fit"})
    check("another company's application is not found", r.status_code == 404, str(r.status_code))

    # ── 4. Round results for one application ───────────────────────────────
    both = (await ac.get(f"/hr/applicants/{finished_person}/round-results")).json()
    one = (await ac.get(f"/hr/applicants/{finished_person}/round-results",
                        params={"enrolment_id": str(ids["finished"])})).json()
    check("round results can be read for one application, not mixed with another",
          len(both) == 3 and len(one) == 2, f"all={len(both)} one={len(one)}")

    # ── 5. Closing leaves the undecided in the queue ───────────────────────
    r = await ac.post(f"/hr/requisitions/{req}/status", json={"status": "closed"})
    detail = r.json().get("detail", {}) if r.status_code == 409 else {}
    check("closing with undecided candidates asks first",
          r.status_code == 409 and detail.get("unresolved", 0) >= 1, f"{r.status_code} {detail}")
    r = await ac.post(f"/hr/requisitions/{req}/status",
                      json={"status": "closed", "acknowledge_unresolved": True})
    q = await queue()
    got = (await ac.get(f"/hr/requisitions/{req}")).json()
    check("once closed, the undecided are still in the queue, not rejected",
          r.status_code == 200 and "also_finished" in q and got.get("unresolved", 0) >= 1,
          f"{r.status_code} {sorted(q)} unresolved={got.get('unresolved')}")

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
