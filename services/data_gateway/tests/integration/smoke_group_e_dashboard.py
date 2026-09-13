"""E1 smoke test — the per-opening dashboard, against a real Postgres and the API.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head
(it TRUNCATEs what it touches):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_e_dashboard.py
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
            " exams, exam_rounds, exam_assignments, reconciliation_state CASCADE"))

    cid, other_cid, hr = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    req, req2, other_req = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    wf, wf2 = uuid.uuid4(), uuid.uuid4()
    r_test, r_ai, r_portfolio = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    exam, exam_round = uuid.uuid4(), uuid.uuid4()
    async with f() as db:
        for company, slug in ((cid, "acme"), (other_cid, "globex")):
            await db.execute(text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,:s,:s,true,:n,:n)"), {"i": company, "s": slug, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.example.com','Meera HR','x',:c,'en',true,false,false,:n,:n)"),
            {"i": hr, "c": cid, "n": now})
        for rid, company, title, target, closes in (
            (req, cid, "Python Developer", 3, now + timedelta(days=5)),
            (req2, cid, "Designer", None, None),
            (other_req, other_cid, "Welder", None, None),
        ):
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " public_apply_enabled,target_hires,closes_at,location,owner_user_id,"
                " created_by_user_id,created_at,updated_at)"
                " VALUES (:i,:c,:t,'mid','open',false,true,:th,:ca,'Hyderabad',NULL,NULL,:n,:n)"),
                {"i": rid, "c": company, "t": title, "th": target, "ca": closes, "n": now})

        async def workflow(wid: uuid.UUID, rid: uuid.UUID, rounds: list[tuple]) -> None:
            await db.execute(text(
                "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
                " auto_score_on_apply,auto_assign_first_round,auto_advance_rounds,"
                " reminders_enabled,hold_band,shortlist_ats_threshold,created_at,updated_at)"
                " VALUES (:i,:c,:r,1,'draft',true,true,true,true,10,7,:n,:n)"),
                {"i": wid, "c": cid, "r": rid, "n": now})
            nxt = None
            for rnd_id, pos, title, kind in reversed(rounds):
                await db.execute(text(
                    "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
                    " pass_threshold,deadline_days,on_pass_next_round_id,created_at,updated_at)"
                    " VALUES (:i,:c,:w,:p,:t,:k,:th,5,:nx,:n,:n)"),
                    {"i": rnd_id, "c": cid, "w": wid, "p": pos, "t": title, "k": kind,
                     "th": None if kind == "human_review" else 60, "nx": nxt, "n": now})
                nxt = rnd_id
            await db.execute(text(
                "UPDATE workflows SET status='published', published_at=:n WHERE id=:i"),
                {"i": wid, "n": now})

        await workflow(wf, req, [(r_test, 0, "Technical Test", "mcq"),
                                 (r_ai, 1, "AI Interview", "ai_interview")])
        await workflow(wf2, req2, [(r_portfolio, 0, "Portfolio Review", "human_review")])
        # A draft of the next version for the Python opening.
        await db.execute(text(
            "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
            " auto_score_on_apply,auto_assign_first_round,auto_advance_rounds,reminders_enabled,"
            " hold_band,created_at,updated_at)"
            " VALUES (:i,:c,:r,2,'draft',true,true,true,true,10,:n,:n)"),
            {"i": uuid.uuid4(), "c": cid, "r": req, "n": now})

        async def person(name: str, company: uuid.UUID = cid, pending: bool = False) -> uuid.UUID:
            aid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,full_name,email,target_job_title,"
                " target_level,status,pending_enrichment,created_at,updated_at)"
                " VALUES (:i,:c,:fn,:em,'x','mid','new',:p,:n,:n)"),
                {"i": aid, "c": company, "fn": name, "em": f"{name}@example.com", "p": pending,
                 "n": now})
            return aid

        async def enrol(aid: uuid.UUID, status: str, round_id: uuid.UUID | None, *,
                        applied_days_ago: float, ats: int | None = None,
                        rid: uuid.UUID = req, w: uuid.UUID | None = wf,
                        company: uuid.UUID = cid) -> uuid.UUID:
            eid = uuid.uuid4()
            held = status == "held"
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                " target_job_title,target_level,workflow_id,current_round_id,ats_overall,"
                " held_at,held_reason,created_at,updated_at)"
                " VALUES (:i,:c,:r,:a,:s,'x','mid',:w,:cr,:ats,:ha,:hr,:ca,:n)"),
                {"i": eid, "c": company, "r": rid, "a": aid, "s": status, "w": w, "cr": round_id,
                 "ats": ats, "ha": now - timedelta(days=4) if held else None,
                 "hr": "Scored 54% against 60% on Technical Test" if held else None,
                 "ca": now - timedelta(days=applied_days_ago), "n": now})
            await db.execute(text(
                "INSERT INTO stage_transitions (company_id,enrolment_id,from_status,to_status,"
                " actor_user_id,automated,reason,occurred_at)"
                " VALUES (:c,:e,NULL,'new',NULL,true,'applied',:t)"),
                {"c": company, "e": eid, "t": now - timedelta(days=applied_days_ago)})
            return eid

        async def move(eid: uuid.UUID, frm: str, to: str, days_after: float, applied: float, *,
                       to_round: uuid.UUID | None = None, automated: bool = True) -> None:
            await db.execute(text(
                "INSERT INTO stage_transitions (company_id,enrolment_id,from_status,to_status,"
                " actor_user_id,automated,reason,occurred_at,to_round_id)"
                " VALUES (:c,:e,:f,:t,:a,:auto,:r,:at,:tr)"),
                {"c": cid, "e": eid, "f": frm, "t": to, "a": None if automated else hr,
                 "auto": automated, "r": "smoke", "tr": to_round,
                 "at": now - timedelta(days=applied - days_after)})

        e = {}
        e["held"] = await enrol(await person("held"), "held", r_test, applied_days_ago=10, ats=70)
        await move(e["held"], "new", "shortlisted", 2, 10, automated=False)
        await move(e["held"], "shortlisted", "shortlisted", 2, 10, to_round=r_test)
        await move(e["held"], "shortlisted", "held", 6, 10)
        e["mid"] = await enrol(await person("mid"), "shortlisted", r_ai, applied_days_ago=8, ats=80)
        await move(e["mid"], "new", "shortlisted", 4, 8, automated=False)
        await move(e["mid"], "shortlisted", "shortlisted", 4, 8, to_round=r_test)
        await move(e["mid"], "shortlisted", "shortlisted", 6, 8, to_round=r_ai)
        e["finished"] = await enrol(await person("finished"), "interviewed", None,
                                    applied_days_ago=6, ats=90)
        e["ready"] = await enrol(await person("ready"), "new", None, applied_days_ago=1, ats=75)
        e["screen"] = await enrol(await person("screen"), "new", None, applied_days_ago=1, ats=40)
        e["hired"] = await enrol(await person("hired"), "hired", None, applied_days_ago=20, ats=88)
        await move(e["hired"], "new", "shortlisted", 2, 20, automated=False)
        await move(e["hired"], "interviewed", "hired", 12, 20, automated=False)
        e["pending"] = await enrol(await person("pending", pending=True), "new", None,
                                   applied_days_ago=0.5)
        e["gave_up"] = await enrol(await person("gave_up"), "new", None, applied_days_ago=3)
        await db.execute(text(
            "INSERT INTO reconciliation_state (kind, ref_id, attempts, last_error, gave_up_at)"
            " VALUES ('enrolment_ats', :e, 5, 'unreadable pdf', :n)"), {"e": e["gave_up"], "n": now})
        await enrol(await person("portfolio"), "shortlisted", r_portfolio, applied_days_ago=2,
                    rid=req2, w=wf2)
        await enrol(await person("foreign", other_cid), "held", None, applied_days_ago=2,
                    rid=other_req, w=None, company=other_cid)

        for eid, rnd, pct, passed in ((e["held"], r_test, 54, False), (e["mid"], r_test, 70, True),
                                      (e["finished"], r_test, 80, True),
                                      (e["finished"], r_ai, 90, True)):
            await db.execute(text(
                "INSERT INTO round_results (id,company_id,enrolment_id,round_id,percent,passed,"
                " graded_by,created_at) VALUES (:i,:c,:e,:r,:p,:ps,'deterministic',:n)"),
                {"i": uuid.uuid4(), "c": cid, "e": eid, "r": rnd, "p": pct, "ps": passed,
                 "n": now})

        # One assessment link expiring tomorrow, one that lapsed unused yesterday.
        await db.execute(text(
            "INSERT INTO exams (id,company_id,title,created_by_user_id,created_at,updated_at)"
            " VALUES (:i,:c,'Screen',:u,:n,:n)"), {"i": exam, "c": cid, "u": hr, "n": now})
        await db.execute(text(
            "INSERT INTO exam_rounds (id,exam_id,company_id,round_number,title,position,status,"
            " created_at,updated_at) VALUES (:i,:e,:c,1,'Aptitude',0,'published',:n,:n)"),
            {"i": exam_round, "e": exam, "c": cid, "n": now})
        aids = {r["id"]: r["applicant_id"] for r in (await db.execute(text(
            "SELECT id, applicant_id FROM enrolments WHERE id = ANY(:i)"),
            {"i": [e["mid"], e["ready"]]})).mappings().all()}
        for eid, expires, status in ((e["mid"], now + timedelta(hours=20), "invited"),
                                     (e["ready"], now - timedelta(days=1), "invited")):
            await db.execute(text(
                "INSERT INTO exam_assignments (id,company_id,exam_id,round_id,applicant_id,"
                " token_hash,expires_at,enrolment_id,status,created_at,updated_at)"
                " VALUES (:i,:c,:x,:r,:a,:t,:ex,:e,:s,:n,:n)"),
                {"i": uuid.uuid4(), "c": cid, "x": exam, "r": exam_round, "a": aids[eid],
                 "t": uuid.uuid4().hex, "ex": expires, "e": eid, "s": status, "n": now})
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
    r = await ac.get(f"/hr/requisitions/{req}/dashboard")
    check("the dashboard loads", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    d = r.json()
    p = d.get("progress", {})

    check("progress: applications, in a round, awaiting, held, hired",
          p.get("applications") == 8 and p.get("in_progress") == 1
          and p.get("awaiting_decision") == 2 and p.get("held") == 1 and p.get("hired") == 1,
          str(p))
    check("…and hires against the target", p.get("target_hires") == 3, str(p))
    check("the workflow state names the live version and the draft",
          d.get("workflow_state") == {"published_version": 1, "draft_version": 2},
          str(d.get("workflow_state")))

    check("the funnel is the published workflow's rounds",
          [x["title"] for x in d.get("rounds", [])] == ["Technical Test", "AI Interview"],
          str(d.get("rounds")))
    other = (await ac.get(f"/hr/requisitions/{req2}/dashboard")).json()
    check("another opening's funnel shows its own workflow's rounds",
          [x["title"] for x in other.get("rounds", [])] == ["Portfolio Review"],
          str(other.get("rounds")))

    timing = {t["label"]: t for t in d.get("stage_timing", [])}
    check("median time to shortlist comes from the ledger",
          timing.get("Shortlisted", {}).get("median_days") == 2.0
          and timing["Shortlisted"]["count"] == 3, str(timing.get("Shortlisted")))
    check("…to each round of the live workflow, in order",
          [t["label"] for t in d["stage_timing"]] == [
              "Shortlisted", "Reached Technical Test", "Reached AI Interview", "Hired"]
          and timing["Reached AI Interview"]["median_days"] == 6.0, str(d["stage_timing"]))
    check("…and to a hire", timing.get("Hired", {}).get("median_days") == 12.0,
          str(timing.get("Hired")))

    s = d.get("scores", {})
    # held 54, mid 70, finished (80 + 90) / 2 = 85 → (54 + 70 + 85) / 3 = 69.7.
    # A plain mean over the four results would be 73.5 — counting the finished
    # candidate twice for sitting two rounds.
    check("average composite is the mean of each candidate's own mean",
          s.get("avg_composite") == 69.7 and s.get("assessed_candidates") == 3, str(s))
    check("average resume match is shown", s.get("avg_ats") is not None, str(s))

    pool = d.get("held_pool", [])
    check("the held pool names who is held, why, and for how long",
          len(pool) == 1 and pool[0]["full_name"] == "held"
          and pool[0]["round_title"] == "Technical Test" and pool[0]["held_days"] == 4.0,
          str(pool))

    keys = {i["key"]: i for i in d.get("attention", [])}
    check("needs attention: the held candidate", "held" in keys, str(list(keys)))
    check("…an assessment link about to expire, and one that lapsed",
          "links_expiring" in keys and "links_lapsed" in keys, str(list(keys)))
    check("…scoring pending, and scoring that gave up",
          "scoring_pending" in keys and "scoring_failed" in keys, str(list(keys)))
    check("…the unpublished draft", "draft_pending" in keys, str(list(keys)))
    check("…closing soon short of target", "closing_short" in keys, str(list(keys)))

    steps = {x["key"]: x["count"] for x in d.get("manual_steps", [])}
    check("manual steps: ready to shortlist, to screen, held, finished",
          steps.get("ready_to_shortlist") == 1 and steps.get("held") == 1
          and steps.get("finished") == 1 and steps.get("to_screen", 0) >= 1, str(steps))

    activity = d.get("activity", [])
    check("activity tells automated moves from a person's, newest first",
          any(a["automated"] and a["actor"] is None for a in activity)
          and any(not a["automated"] and a["actor"] == "Meera HR" for a in activity)
          and activity[0]["occurred_at"] >= activity[-1]["occurred_at"], str(activity[:3]))
    summary = d.get("activity_summary", {})
    check("…with a 7-day count and when the automation last ran",
          summary.get("automated_7d", 0) >= 1 and summary.get("last_automated_at") is not None,
          str(summary))

    foreign = await ac.get(f"/hr/requisitions/{other_req}/dashboard")
    check("another company's opening is not found", foreign.status_code == 404,
          str(foreign.status_code))

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
