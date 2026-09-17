"""End-to-end smoke for PH4-A1 — interviewer scorecards through the real API.

Run against a throwaway, migrated Postgres (never the shared one):

    DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_dev \\
      DATABASE_SSL= python -m alembic upgrade head
    SMOKE_DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph4_dev \\
      PYTHONPATH=".;../.." python tests/integration/smoke_ph4_scorecards.py

The company contexts are overridden so the smoke can act as different people,
but OWNERSHIP IS NOT: every interviewer read goes through the real query that
filters on the caller. Switching the acting interviewer and getting a 404 is the
proof that one interviewer cannot reach another's scorecard.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from datetime import UTC, datetime

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
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:  # noqa: PLR0915 — one linear script, read top to bottom
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    now = datetime.now(tz=UTC)
    tag = uuid.uuid4().hex[:8]

    cid, other_cid = uuid.uuid4(), uuid.uuid4()
    hr_uid, admin_uid = uuid.uuid4(), uuid.uuid4()
    iv1, iv2, outsider = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    req, wf, rnd = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    applicant, enrolment = uuid.uuid4(), uuid.uuid4()

    async with factory() as db:
        for company, slug in ((cid, f"acme-{tag}"), (other_cid, f"globex-{tag}")):
            await db.execute(text(
                "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                " VALUES (:i,:s,:s,true,:n,:n)"), {"i": company, "s": slug, "n": now})
        people = (
            (hr_uid, cid, "hr_manager", "Hema HR"),
            (admin_uid, cid, "super_admin", "Sam Admin"),
            (iv1, cid, "interviewer", "Amit Interviewer"),
            (iv2, cid, "interviewer", "Priya Interviewer"),
            (outsider, other_cid, "interviewer", "Olga Outsider"),
        )
        for uid, company, role, name in people:
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,"
                " preferred_language,is_active,notify_login_email,must_change_password,"
                " created_at,updated_at)"
                " VALUES (:i,:e,:fn,'x',:c,'en',true,false,false,:n,:n)"),
                {"i": uid, "e": f"{uid.hex[:10]}@{tag}.test", "fn": name, "c": company, "n": now})
            await db.execute(text(
                "INSERT INTO user_roles (user_id, role_id, assigned_at)"
                " SELECT :u, id, :n FROM roles WHERE name = :r"),
                {"u": uid, "r": role, "n": now})
        await db.execute(text(
            "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
            " public_apply_enabled,created_by_user_id,owner_user_id,approval_status,"
            " approval_decided_at,created_at,updated_at)"
            " VALUES (:i,:c,'Platform Engineer','senior','open',false,false,:u,:u,"
            " 'approved',:n,:n,:n)"),
            {"i": req, "c": cid, "u": hr_uid, "n": now})
        await db.execute(text(
            "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
            " created_by_user_id,created_at,updated_at)"
            " VALUES (:w,:c,:r,1,'draft',:u,:n,:n)"),
            {"w": wf, "c": cid, "r": req, "u": hr_uid, "n": now})
        await db.execute(text(
            "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
            " deadline_days,created_at,updated_at)"
            " VALUES (:i,:c,:w,0,'Technical Interview','human_review',7,:n,:n)"),
            {"i": rnd, "c": cid, "w": wf, "n": now})
        await db.execute(text(
            "INSERT INTO round_criteria (id,company_id,round_id,competency_id,competency_name,"
            " weight,created_at) VALUES"
            " (gen_random_uuid(),:c,:r,'problem_solving','Problem Solving',0.6,:n),"
            " (gen_random_uuid(),:c,:r,'system_design','System Design',0.4,:n)"),
            {"c": cid, "r": rnd, "n": now})
        await db.execute(text(
            "UPDATE workflows SET status='published', published_at=:n WHERE id=:w"),
            {"w": wf, "n": now})
        await db.execute(text(
            "INSERT INTO applicants (id,company_id,full_name,email,target_job_title,status,"
            " created_by_user_id,created_at,updated_at)"
            " VALUES (:i,:c,'John Doe',:e,'Platform Engineer','shortlisted',:u,:n,:n)"),
            {"i": applicant, "c": cid, "e": f"john-{tag}@cand.test", "u": hr_uid, "n": now})
        await db.execute(text(
            "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,target_job_title,"
            " workflow_id,current_round_id,status,created_at,updated_at)"
            " VALUES (:e,:c,:r,:a,'Platform Engineer',:w,:rd,'shortlisted',:n,:n)"),
            {"e": enrolment, "c": cid, "r": req, "a": applicant, "w": wf, "rd": rnd, "n": now})
        await db.commit()

    from app.database import get_db_session
    from app.dependencies import get_auth_provider_dep, get_hr_company, get_interviewer_company
    from app.main import app
    from app.routers.admin_hr import get_company_admin_ctx

    async def _db():  # noqa: ANN202
        async with factory() as session:
            yield session

    acting: dict[str, uuid.UUID] = {"interviewer": iv1, "company": cid}
    app.dependency_overrides[get_db_session] = _db
    app.dependency_overrides[get_hr_company] = lambda: (hr_uid, cid)
    app.dependency_overrides[get_company_admin_ctx] = lambda: (admin_uid, cid)
    app.dependency_overrides[get_interviewer_company] = lambda: (
        acting["interviewer"], acting["company"]
    )

    class _Auth:
        """The provider is built by the startup hook, which the smoke never runs.
        Only session revocation is reached here."""

        revoked: list[str] = []

        async def logout_all(self, user_id: str) -> None:
            self.revoked.append(user_id)

    auth_stub = _Auth()
    app.dependency_overrides[get_auth_provider_dep] = lambda: auth_stub

    def act_as(uid: uuid.UUID, company: uuid.UUID = cid) -> None:
        acting["interviewer"], acting["company"] = uid, company

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as client:
        print("\nPH4-A1 — who can interview")
        r = await client.get("/hr/interviewers")
        ids = {p["user_id"] for p in r.json()} if r.status_code == 200 else set()
        check("HR lists interviewers", r.status_code == 200, r.text[:120])
        check("both interviewers are assignable", {str(iv1), str(iv2)} <= ids)
        check("an HR manager can also be assigned", str(hr_uid) in ids)
        check("another company's interviewer is NOT listed", str(outsider) not in ids)

        print("\nPH4-A1 — assignment")
        r = await client.post(f"/hr/enrolments/{enrolment}/scorecards",
                              json={"round_id": str(rnd), "interviewer_user_ids": [str(iv1), str(iv2)]})
        check("HR assigns two interviewers", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
        created = r.json().get("created", []) if r.status_code == 201 else []
        check("each gets their own scorecard", len(created) == 2)
        card1 = next((c["scorecard_id"] for c in created if c["interviewer_user_id"] == str(iv1)), "")
        card2 = next((c["scorecard_id"] for c in created if c["interviewer_user_id"] == str(iv2)), "")

        r = await client.post(f"/hr/enrolments/{enrolment}/scorecards",
                              json={"round_id": str(rnd), "interviewer_user_ids": [str(iv1)]})
        check("re-assigning the same interviewer is idempotent",
              r.status_code == 201 and r.json().get("already_assigned") == [str(iv1)], r.text[:160])

        r = await client.post(f"/hr/enrolments/{enrolment}/scorecards",
                              json={"round_id": str(rnd), "interviewer_user_ids": [str(outsider)]})
        check("another company's interviewer cannot be assigned", r.status_code == 422, str(r.status_code))

        r = await client.post(f"/hr/enrolments/{enrolment}/scorecards",
                              json={"round_id": str(uuid.uuid4()), "interviewer_user_ids": [str(iv1)]})
        check("a round from nowhere is refused", r.status_code == 404, str(r.status_code))

        print("\nPH4-A1 — interviewers see only their own")
        act_as(iv1)
        r = await client.get("/interviewer/assignments")
        mine = r.json() if r.status_code == 200 else []
        check("interviewer 1 sees exactly one assignment", len(mine) == 1, r.text[:160])
        check("it names the candidate and round",
              bool(mine) and mine[0]["candidate_name"] == "John Doe"
              and mine[0]["round_title"] == "Technical Interview")

        r = await client.get(f"/interviewer/scorecards/{card1}")
        check("interviewer 1 opens their scorecard", r.status_code == 200, r.text[:160])
        crit = [c["competency_id"] for c in r.json().get("criteria", [])] if r.status_code == 200 else []
        check("criteria come from the frozen round criteria",
              crit == ["problem_solving", "system_design"], str(crit))

        act_as(iv2)
        r = await client.get(f"/interviewer/scorecards/{card1}")
        check("interviewer 2 CANNOT open interviewer 1's scorecard", r.status_code == 404, str(r.status_code))
        r = await client.put(f"/interviewer/scorecards/{card1}",
                             json={"scores": [{"competency_id": "problem_solving", "score": 1}]})
        check("interviewer 2 CANNOT write to it either", r.status_code == 404, str(r.status_code))

        # With THEIR OWN company context — the real cross-tenant case, not just
        # a different user inside Acme.
        act_as(outsider, other_cid)
        r = await client.get(f"/interviewer/scorecards/{card1}")
        check("another company's interviewer cannot reach it", r.status_code == 404, str(r.status_code))
        r = await client.get("/interviewer/assignments")
        check("and sees none of Acme's assignments", r.status_code == 200 and r.json() == [], r.text[:120])

        print("\nPH4-A1 — drafting and submission")
        act_as(iv1)
        r = await client.put(f"/interviewer/scorecards/{card1}",
                             json={"scores": [{"competency_id": "made_up", "score": 5}]})
        check("a criterion outside the rubric is refused in words", r.status_code == 422, r.text[:120])

        r = await client.put(f"/interviewer/scorecards/{card1}", json={"scores": [
            {"competency_id": "problem_solving", "score": 4, "evidence": "Clear trade-offs."}]})
        check("a partial draft saves", r.status_code == 200, r.text[:160])
        r = await client.get("/interviewer/assignments")
        check("the assignment is now in progress",
              r.status_code == 200 and r.json()[0]["status"] == "in_progress")

        r = await client.post(f"/interviewer/scorecards/{card1}/submit", json={"scores": [
            {"competency_id": "problem_solving", "score": 4, "evidence": "Clear trade-offs."}]})
        check("an incomplete scorecard is refused, naming what is missing",
              r.status_code == 422 and "System Design" in r.text, r.text[:160])

        full = {"summary": "Strong, structured thinker.", "scores": [
            {"competency_id": "problem_solving", "score": 4, "evidence": "Clear trade-offs."},
            {"competency_id": "system_design", "not_assessed": True},
        ]}
        r = await client.post(f"/interviewer/scorecards/{card1}/submit", json=full)
        check("a complete scorecard submits (not-assessed counts as an answer)",
              r.status_code == 200 and r.json().get("status") == "submitted", r.text[:160])

        r = await client.put(f"/interviewer/scorecards/{card1}", json={"scores": [
            {"competency_id": "problem_solving", "score": 1}]})
        check("a submitted scorecard cannot be edited", r.status_code == 409, str(r.status_code))

        print("\nPH4-A1 — a scorecard never decides")
        async with factory() as db:
            row = (await db.execute(text(
                "SELECT status, current_round_id FROM enrolments WHERE id=:e"),
                {"e": enrolment})).mappings().first()
        check("the candidate's status did not move", row["status"] == "shortlisted", str(row["status"]))
        check("the candidate is still on the same round", row["current_round_id"] == rnd)

        print("\nPH4-A1 — HR reads the evidence")
        r = await client.get(f"/hr/enrolments/{enrolment}/scorecards")
        rounds = r.json().get("rounds", []) if r.status_code == 200 else []
        cards = {c["scorecard_id"]: c for c in (rounds[0]["scorecards"] if rounds else [])}
        check("HR sees the round with both scorecards", len(cards) == 2, r.text[:200])
        check("the submitted scorecard's scores are visible",
              bool(cards.get(card1, {}).get("scores", {}).get("problem_solving")))
        check("the unsubmitted draft's content is NOT shown",
              cards.get(card2, {}).get("scores") is None and cards.get(card2, {}).get("summary") is None)

        # HR assigns themself as a third interviewer — and is then blinded.
        r = await client.post(f"/hr/enrolments/{enrolment}/scorecards",
                              json={"round_id": str(rnd), "interviewer_user_ids": [str(hr_uid)]})
        check("HR can assign themself as an interviewer", r.status_code == 201, r.text[:120])
        r = await client.get(f"/hr/enrolments/{enrolment}/scorecards")
        rnd0 = r.json()["rounds"][0] if r.status_code == 200 else {}
        c1 = next((c for c in rnd0.get("scorecards", []) if c["scorecard_id"] == card1), {})
        check("an HR reader who still owes a scorecard is blinded to the others",
              rnd0.get("hidden_until_you_submit") is True and c1.get("scores") is None,
              str(rnd0.get("hidden_until_you_submit")))

        print("\nPH4-A1 — the audited correction path")
        act_as(iv1)
        r = await client.post(f"/interviewer/scorecards/{card1}/correction", json={"reason": "short"})
        check("a correction needs a real reason", r.status_code == 422, str(r.status_code))
        r = await client.post(f"/interviewer/scorecards/{card1}/correction",
                              json={"reason": "I scored system design as not assessed by mistake."})
        check("a correction opens", r.status_code == 200, r.text[:160])
        card1b = r.json().get("scorecard_id", "") if r.status_code == 200 else ""
        r = await client.get(f"/interviewer/scorecards/{card1b}")
        check("the correction starts as a copy of the original",
              r.status_code == 200 and r.json()["criteria"][0]["score"] == 4
              and r.json()["is_correction"] is True, r.text[:160])
        r = await client.get(f"/interviewer/scorecards/{card1}")
        check("the original is marked superseded, not overwritten",
              r.status_code == 200 and r.json()["superseded"] is True
              and r.json()["can_edit"] is False)
        r = await client.post(f"/interviewer/scorecards/{card1b}/submit", json={
            "summary": "Strong, structured thinker.",
            "scores": [
                {"competency_id": "problem_solving", "score": 4, "evidence": "Clear trade-offs."},
                {"competency_id": "system_design", "score": 3, "evidence": "Reasonable design."},
            ]})
        check("the correction submits", r.status_code == 200, r.text[:160])
        async with factory() as db:
            orig = await db.scalar(text(
                "SELECT score FROM interviewer_scorecard_scores"
                " WHERE scorecard_id=:s AND competency_id='problem_solving'"), {"s": uuid.UUID(card1)})
            orig_sd = await db.scalar(text(
                "SELECT not_assessed FROM interviewer_scorecard_scores"
                " WHERE scorecard_id=:s AND competency_id='system_design'"), {"s": uuid.UUID(card1)})
        check("the ORIGINAL scores are still on record", orig == 4 and orig_sd is True)

        print("\nPH4-A1 — withdrawal")
        r = await client.post(f"/hr/scorecards/{card2}/withdraw", json={"reason": "Travelling"})
        check("HR withdraws an unsubmitted assignment", r.status_code == 204, f"{r.status_code} {r.text[:120]}")
        act_as(iv2)
        r = await client.get("/interviewer/assignments")
        check("it disappears from that interviewer's list", r.status_code == 200 and r.json() == [])
        r = await client.post(f"/hr/scorecards/{card1b}/withdraw", json={})
        check("a SUBMITTED scorecard cannot be withdrawn", r.status_code == 409, str(r.status_code))

        print("\nPH4-A1 — decisions lock the evidence")
        async with factory() as db:
            await db.execute(text("UPDATE enrolments SET status='rejected' WHERE id=:e"), {"e": enrolment})
            await db.commit()
        act_as(iv1)
        r = await client.post(f"/interviewer/scorecards/{card1b}/correction",
                              json={"reason": "Trying to change it after the decision."})
        check("no correction once a final decision exists", r.status_code == 409, str(r.status_code))

        print("\nPH4-A1 — audit trail")
        async with factory() as db:
            actions = set((await db.execute(text(
                "SELECT action FROM audit_log WHERE resource_type='interviewer_scorecard'"
                " AND details->>'company_id' = :c"), {"c": str(cid)})).scalars().all())
        for a in ("scorecard.assigned", "scorecard.started", "scorecard.submitted",
                  "scorecard.correction_opened", "scorecard.withdrawn"):
            check(f"audited: {a}", a in actions, str(sorted(actions)))

        print("\nPH4-A1 — removing an interviewer")
        async with factory() as db:
            await db.execute(text("UPDATE enrolments SET status='shortlisted' WHERE id=:e"), {"e": enrolment})
            await db.commit()
        r = await client.post(f"/hr/enrolments/{enrolment}/scorecards",
                              json={"round_id": str(rnd), "interviewer_user_ids": [str(iv2)]})
        check("iv2 re-assigned after withdrawal", r.status_code == 201 and len(r.json()["created"]) == 1,
              r.text[:120])
        r = await client.delete(f"/admin/interviewers/{iv2}")
        check("super admin removes interviewer 2", r.status_code == 204, f"{r.status_code} {r.text[:120]}")
        async with factory() as db:
            live = await db.scalar(text(
                "SELECT count(*) FROM interviewer_scorecards WHERE interviewer_user_id=:u"
                " AND status IN ('assigned','in_progress')"), {"u": iv2})
        check("their unsubmitted assignments were withdrawn with them", live == 0, str(live))
        check("and their sessions were revoked", str(iv2) in auth_stub.revoked)

        print("\nPH4-A1 — no session, no access")
        app.dependency_overrides.pop(get_interviewer_company, None)
        app.dependency_overrides.pop(get_hr_company, None)
        r = await client.get("/interviewer/assignments")
        check("the interviewer console refuses an anonymous caller", r.status_code in (401, 403), str(r.status_code))
        r = await client.get(f"/hr/enrolments/{enrolment}/scorecards")
        check("HR scorecards refuse an anonymous caller", r.status_code in (401, 403), str(r.status_code))

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    raise SystemExit(1 if FAIL else 0)


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
