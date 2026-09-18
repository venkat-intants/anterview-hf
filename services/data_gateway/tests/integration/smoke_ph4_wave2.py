#!/usr/bin/env python3
"""PH4 Wave 2 end to end: review & approval (O6), branching (O3), dry run (O2),
stage owners / SLAs / exceptions (O1) — through the real API and runner.

    cd services/data_gateway
    PYTHONPATH=".;../.." DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_dev \\
      DATABASE_SSL= python tests/integration/smoke_ph4_wave2.py

Seeds its own companies; leaves them behind (unique slugs), like the other smokes.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from datetime import UTC, datetime, timedelta

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get(
    "SMOKE_DATABASE_URL", "postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_dev"
)
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {detail}" if not cond and detail else ""))


SENSITIVE = ("applicants", "enrolments", "stage_transitions", "email_events", "notifications",
             "exam_assignments", "interview_invites", "round_results")


async def main() -> None:  # noqa: PLR0915 — one linear script, read top to bottom
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    tag = uuid.uuid4().hex[:8]
    cid = uuid.uuid4()
    hr, hr2, admin = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    req = uuid.uuid4()

    async with factory() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,:s,:s,true,:n,:n)"), {"i": cid, "s": f"w2-{tag}", "n": now})
        for uid, role, name in ((hr, "hr_manager", "Hema HR"), (hr2, "hr_manager", "Owen Owner"),
                                (admin, "super_admin", "Sam Admin")):
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
                " is_active,notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,:fn,'x',:c,'en',true,false,false,:n,:n)"),
                {"i": uid, "e": f"{uid.hex[:10]}@{tag}.test", "fn": name, "c": cid, "n": now})
            await db.execute(text(
                "INSERT INTO user_roles (user_id, role_id, assigned_at)"
                " SELECT :u, id, :n FROM roles WHERE name = :r"), {"u": uid, "r": role, "n": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
            " public_apply_enabled,created_by_user_id,owner_user_id,approval_status,"
            " approval_decided_at,created_at,updated_at)"
            " VALUES (:i,:c,'Platform Engineer','senior','open',false,false,:u,:u,'approved',:n,:n,:n)"),
            {"i": req, "c": cid, "u": hr, "n": now})
        await db.commit()

    from app.database import get_db_session
    from app.dependencies import get_hr_company, get_super_admin_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with factory() as session:
            yield session

    acting = {"hr": hr}
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_hr_company] = lambda: (acting["hr"], cid)
    app.dependency_overrides[get_super_admin_company] = lambda: (admin, cid)

    async def counts() -> dict[str, int]:
        async with factory() as db:
            return {t: int(await db.scalar(text(f"SELECT count(*) FROM {t}")) or 0)  # noqa: S608
                    for t in SENSITIVE}

    crit = [{"id": "problem_solving", "name": "Problem Solving", "weight": 0.6},
            {"id": "communication", "name": "Communication", "weight": 0.4}]
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as c:
        print("\nPH4-O3 — building a branching workflow")
        r = await c.post(f"/hr/requisitions/{req}/workflows", json={})
        wf = r.json().get("id") if r.status_code in (200, 201) else None
        check("HR starts a draft", bool(wf), f"{r.status_code} {r.text[:160]}")
        for title, kind, th in (("AI screen", "ai_interview", 60), ("Tech review", "human_review", None),
                                ("Final panel", "human_review", None),
                                ("Second look", "human_review", None)):
            body = {"title": title, "kind": kind, "criteria": crit, "deadline_days": 5}
            if th is not None:
                body["pass_threshold"] = th
            r = await c.post(f"/hr/workflows/{wf}/rounds", json=body)
        rounds = {x["title"]: x for x in r.json()["rounds"]}
        screen, tech, panel, second = (rounds[t]["id"] for t in
                                       ("AI screen", "Tech review", "Final panel", "Second look"))

        r = await c.patch(f"/hr/workflows/{wf}/rounds/{screen}",
                          json={"fast_track_min_percent": 50, "on_fast_track_next_round_id": panel})
        v = await c.get(f"/hr/workflows/{wf}/validate")
        check("a fast-track below the pass bar is a validation error, in words",
              any("must be above" in e for e in v.json()["errors"]), str(v.json()["errors"]))
        r = await c.patch(f"/hr/workflows/{wf}/rounds/{screen}",
                          json={"fast_track_min_percent": 90, "on_fast_track_next_round_id": panel,
                                "on_fail_next_round_id": second})
        got = next(x for x in r.json()["rounds"] if x["id"] == screen)
        check("HR sets a fast-track and a below-threshold route",
              got["on_fast_track_next_round_id"] == panel and got["on_fail_next_round_id"] == second
              and got["fast_track_min_percent"] == 90.0, str(got))
        r = await c.patch(f"/hr/workflows/{wf}/rounds/{screen}", json={"fast_track_min_percent": 92})
        got = next(x for x in r.json()["rounds"] if x["id"] == screen)
        check("changing only the fast-track score keeps its destination",
              got["on_fast_track_next_round_id"] == panel and got["fast_track_min_percent"] == 92.0,
              str(got))
        r = await c.patch(f"/hr/workflows/{wf}/rounds/{screen}",
                          json={"on_fail_next_round_id": str(uuid.uuid4())})
        check("a branch into another workflow is refused", r.status_code == 409, str(r.status_code))
        r = await c.patch(f"/hr/workflows/{wf}/rounds/{tech}", json={"on_fail_next_round_id": screen})
        v = await c.get(f"/hr/workflows/{wf}/validate")
        check("a loop through a below-threshold route is caught",
              any("loop" in e for e in v.json()["errors"]), str(v.json()["errors"]))
        await c.patch(f"/hr/workflows/{wf}/rounds/{tech}", json={"on_fail_next_round_id": None})
        v = await c.get(f"/hr/workflows/{wf}/validate")
        check("the branching workflow validates", v.json()["publishable"], str(v.json()["errors"]))
        async with factory() as db:
            branch_audits = await db.scalar(text(
                "SELECT count(*) FROM audit_log WHERE action = 'workflow.branch.updated'"
                " AND resource_id = :w"), {"w": uuid.UUID(wf)})
        check("branch changes are audited with before and after", (branch_audits or 0) >= 3,
              str(branch_audits))

        print("\nPH4-O2 — dry run")
        before = await counts()
        r = await c.post(f"/hr/workflows/{wf}/simulate")
        sim = r.json() if r.status_code == 200 else {}
        check("a dry run runs", r.status_code == 200, r.text[:200])
        after = await counts()
        check("…and creates no applicant, enrolment, ledger row, email, notification, invite or"
              " result", before == after, f"{before} -> {after}")
        taken = {(s.get("round_id"), s.get("branch")) for sc in sim.get("scenarios", [])
                 for s in sc["steps"]}
        check("its synthetic candidates take every branch of the screen",
              {(screen, "pass"), (screen, "fast_track"), (screen, "fail")} <= taken, str(taken))
        check("every scenario ends at a person — the final decision or a hold",
              {sc["end"] for sc in sim.get("scenarios", [])} <= {"decision", "held"})
        check("errors and warnings are counted apart, and it did not fail",
              sim.get("status") in ("passed", "warnings") and sim.get("errors") == 0, str(sim.get("status")))
        r = await c.get(f"/hr/workflows/{wf}/simulation")
        check("the latest result is kept and not stale", r.status_code == 200
              and r.json()["simulation_id"] == sim.get("simulation_id") and r.json()["stale"] is False)
        async with factory() as db:
            audited = await db.scalar(text(
                "SELECT count(*) FROM audit_log WHERE action = 'workflow.simulated'"
                " AND resource_id = :w"), {"w": uuid.UUID(wf)})
        check("the dry run is audited", audited == 1, str(audited))

        print("\nPH4-O6 — review and approval")
        r = await c.post(f"/hr/workflows/{wf}/publish")
        check("an unapproved version cannot be published", r.status_code == 409
              and "not been approved" in r.text, f"{r.status_code} {r.text[:160]}")
        r = await c.post(f"/hr/workflows/{wf}/submit-review", json={"note": "Ready for a look"})
        check("HR submits it for review", r.status_code == 200
              and r.json()["review_status"] == "in_review", f"{r.status_code} {r.text[:200]}")
        r = await c.patch(f"/hr/workflows/{wf}/rounds/{tech}", json={"title": "Sneaky edit"})
        check("a version in review cannot be edited", r.status_code == 409, str(r.status_code))
        r = await c.get(f"/hr/workflows/{wf}")
        check("…and the builder is told it is locked", r.json()["editable"] is False
              and r.json()["review_status"] == "in_review")
        r = await c.get("/admin/workflow-reviews")
        check("the super admin's queue lists it", any(x["workflow_id"] == wf for x in r.json()),
              r.text[:200])
        r = await c.get(f"/admin/workflow-reviews/{wf}")
        detail = r.json() if r.status_code == 200 else {}
        check("the reviewer sees rounds, branches, validation and the dry run",
              r.status_code == 200 and detail.get("simulation") is not None
              and any(x["on_fail"] == "Second look" for x in detail.get("rounds", [])),
              r.text[:200])
        r = await c.post(f"/admin/workflow-reviews/{wf}/request-changes", json={"note": "no"})
        check("asking for changes needs a real note", r.status_code == 422, str(r.status_code))
        r = await c.post(f"/admin/workflow-reviews/{wf}/request-changes",
                         json={"note": "Give the second look a clearer title please."})
        check("the super admin asks for changes", r.status_code == 200
              and r.json()["review_status"] == "changes_requested", r.text[:160])
        r = await c.patch(f"/hr/workflows/{wf}/rounds/{second}", json={"title": "Second look review"})
        check("changes requested: HR can edit again", r.status_code == 200, str(r.status_code))
        r = await c.post(f"/hr/workflows/{wf}/submit-review", json={})
        check("…and resubmit", r.status_code == 200, r.text[:160])
        r = await c.post(f"/admin/workflow-reviews/{wf}/approve", json={"note": "Looks good"})
        check("the super admin approves", r.status_code == 200
              and r.json()["review_status"] == "approved", r.text[:160])
        actions = [h["action"] for h in r.json()["history"]]
        check("the history records each step", actions == ["submitted", "changes_requested",
                                                           "submitted", "approved"], str(actions))
        r = await c.patch(f"/hr/workflows/{wf}/rounds/{tech}", json={"title": "After approval"})
        check("an approved version is locked too", r.status_code == 409, str(r.status_code))
        r = await c.post(f"/hr/workflows/{wf}/publish")
        check("the approved version publishes", r.status_code == 200, f"{r.status_code} {r.text[:160]}")
        async with factory() as db:
            ev = await db.scalar(text(
                "SELECT count(*) FROM audit_log WHERE resource_id = :w"
                " AND action LIKE 'workflow.review.%'"), {"w": uuid.UUID(wf)})
        check("every review action is audited", ev == 4, str(ev))

        print("\nPH4-O3 — branches on real candidates")
        from app.workflow_runner import record_result

        people: dict[str, uuid.UUID] = {}
        async with factory() as db:
            for name in ("Fast Fatima", "Middle Mohan", "Low Lakshmi", "Waiting Wasim"):
                aid, eid = uuid.uuid4(), uuid.uuid4()
                people[name] = eid
                await db.execute(text(
                    "INSERT INTO applicants (id,company_id,full_name,email,target_job_title,status,"
                    " created_by_user_id,created_at,updated_at)"
                    " VALUES (:a,:c,:fn,:e,'Platform Engineer','shortlisted',:u,:n,:n)"),
                    {"a": aid, "c": cid, "fn": name, "e": f"{aid.hex[:8]}@cand.test", "u": hr, "n": now})
                await db.execute(text(
                    "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,target_job_title,"
                    " workflow_id,current_round_id,status,created_at,updated_at)"
                    " VALUES (:e,:c,:r,:a,'Platform Engineer',:w,:rd,'shortlisted',:n,:n)"),
                    {"e": eid, "c": cid, "r": req, "a": aid, "w": uuid.UUID(wf),
                     "rd": uuid.UUID(screen), "n": now})
            await db.commit()
        async with factory() as db:
            o1 = await record_result(db, enrolment_id=people["Fast Fatima"], round_id=uuid.UUID(screen),
                                     score=9.5, max_score=10)
            o2 = await record_result(db, enrolment_id=people["Middle Mohan"], round_id=uuid.UUID(screen),
                                     score=7, max_score=10)
            o3 = await record_result(db, enrolment_id=people["Low Lakshmi"], round_id=uuid.UUID(screen),
                                     score=3, max_score=10)
            await db.commit()
            where = dict((await db.execute(text(
                "SELECT e.id, wr.title FROM enrolments e JOIN workflow_rounds wr"
                " ON wr.id = e.current_round_id WHERE e.id = ANY(:ids)"),
                {"ids": list(people.values())[:3]})).all())
            statuses = set((await db.execute(text(
                "SELECT status FROM enrolments WHERE id = ANY(:ids)"),
                {"ids": list(people.values())})).scalars().all())
        check("95% takes the fast-track to the final panel",
              o1.action == "advanced" and where.get(people["Fast Fatima"]) == "Final panel", str(o1))
        check("70% takes the pass branch to the tech review",
              where.get(people["Middle Mohan"]) == "Tech review", str(o2))
        check("30% is routed to the second look — not held, not rejected",
              o3.action == "routed" and where.get(people["Low Lakshmi"]) == "Second look review",
              str(o3))
        check("nobody's status became an outcome", statuses <= {"shortlisted"}, str(statuses))

        print("\nPH4-O1 — stage owners, SLAs, exceptions")
        r = await c.put(f"/hr/workflows/{wf}/stages",
                        json={"round_id": second, "owner_user_id": str(hr2), "sla_hours": 1})
        stage = next((s for s in r.json() if s["round_id"] == second), {}) if r.status_code == 200 else {}
        check("HR sets an owner and a one-hour SLA on a LIVE version's stage",
              stage.get("owner_name") == "Owen Owner" and stage.get("sla_hours") == 1,
              f"{r.status_code} {r.text[:160]}")
        r = await c.put(f"/hr/workflows/{wf}/stages",
                        json={"round_id": None, "owner_user_id": str(hr), "sla_hours": 48})
        check("…and on the final decision", r.status_code == 200
              and r.json()[-1]["stage"] == "Final decision" and r.json()[-1]["sla_hours"] == 48)
        r = await c.put(f"/hr/workflows/{wf}/stages",
                        json={"round_id": second, "owner_user_id": str(uuid.uuid4()), "sla_hours": 1})
        check("an owner must be an HR manager of this company", r.status_code == 422, str(r.status_code))
        async with factory() as db:
            await db.execute(text(
                "UPDATE enrolments SET current_round_id = :rd WHERE id = :e"),
                {"rd": uuid.UUID(second), "e": people["Waiting Wasim"]})
            await db.execute(text(
                "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
                " from_round_id, to_round_id, automated, reason, occurred_at)"
                " VALUES (:c, :e, 'shortlisted', 'shortlisted', :f, :t, true, 'routed', :at)"),
                {"c": cid, "e": people["Waiting Wasim"], "f": uuid.UUID(screen),
                 "t": uuid.UUID(second), "at": now - timedelta(hours=3)})
            await db.commit()
        r = await c.get("/hr/stage-sla?state=overdue,due_soon")
        rows = {x["full_name"]: x for x in r.json()} if r.status_code == 200 else {}
        check("the board shows Wasim overdue, with the owner",
              rows.get("Waiting Wasim", {}).get("state") == "overdue"
              and rows["Waiting Wasim"]["owner_name"] == "Owen Owner", r.text[:240])
        check("…and Lakshmi, just routed, is not overdue",
              "Low Lakshmi" not in rows, str(list(rows)))
        from app.stage_sla import notify_overdue_stages
        async with factory() as db:
            first = await notify_overdue_stages(db)
            await db.commit()
            again = await notify_overdue_stages(db)
            await db.commit()
            told = await db.scalar(text(
                "SELECT count(*) FROM notifications WHERE user_id = :u AND kind = 'stage_overdue'"),
                {"u": hr2})
        check("the owner is told once, not on every sweep", first >= 1 and again == 0 and told == 1,
              f"{first} {again} {told}")

        r = await c.post(f"/hr/enrolments/{people['Waiting Wasim']}/exceptions",
                         json={"reason": "short"})
        check("an exception needs a real reason", r.status_code == 422, str(r.status_code))
        reason = "Candidate travelling until Friday; asked to reschedule."
        r = await c.post(f"/hr/enrolments/{people['Waiting Wasim']}/exceptions",
                         json={"reason": reason, "owner_user_id": str(hr2)})
        exc_id = r.json().get("exception_id") if r.status_code == 201 else None
        check("HR records an exception, owned by the stage owner", bool(exc_id), r.text[:160])
        r = await c.get("/hr/stage-sla?state=overdue")
        wasim = next((x for x in r.json() if x["full_name"] == "Waiting Wasim"), {})
        check("the board shows the open exception beside the overdue stage",
              wasim.get("open_exceptions") == 1, str(wasim))
        r = await c.patch(f"/hr/exceptions/{exc_id}", json={"owner_user_id": str(hr)})
        check("it can be reassigned", r.status_code == 200, r.text[:120])
        r = await c.post(f"/hr/exceptions/{exc_id}/resolve", json={"note": "Rescheduled for Monday."})
        check("and resolved", r.status_code == 200 and r.json()["status"] == "resolved")
        r = await c.post(f"/hr/exceptions/{exc_id}/resolve", json={})
        check("a resolved exception is not resolved twice", r.status_code == 409, str(r.status_code))
        r = await c.get(f"/hr/enrolments/{people['Waiting Wasim']}/exceptions")
        check("the exception history keeps who, when and how it ended",
              r.json()[0]["resolved_by_name"] == "Hema HR"
              and r.json()[0]["resolution_note"] == "Rescheduled for Monday.", r.text[:200])
        async with factory() as db:
            st = await db.scalar(text("SELECT status FROM enrolments WHERE id = :e"),
                                 {"e": people["Waiting Wasim"]})
            aud = (await db.execute(text(
                "SELECT details FROM audit_log WHERE action = 'stage.exception.raised'"
                " AND resource_id = :e"), {"e": people["Waiting Wasim"]})).scalars().first()
        check("an exception moved nobody", st == "shortlisted", str(st))
        check("the audit row has the reason's length, never its text",
              aud is not None and "travelling" not in str(aud)
              and aud.get("reason_chars") == len(reason),
              str(aud))
        r = await c.get(f"/hr/requisitions/{req}/decision-queue")
        check("the decision queue carries SLA fields", r.status_code == 200
              and all("sla" in x and "open_exceptions" in x for x in r.json()), r.text[:160])

        print("\nNo session, no access")
        app.dependency_overrides.pop(get_hr_company, None)
        app.dependency_overrides.pop(get_super_admin_company, None)
        for path in ("/hr/stage-sla", f"/hr/workflows/{wf}/simulation", "/admin/workflow-reviews"):
            r = await c.get(path)
            check(f"{path} refuses an anonymous caller", r.status_code in (401, 403), str(r.status_code))

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
