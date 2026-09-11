"""B2 smoke test — every candidate move lands in the ledger, and the ledger
cannot be edited.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head (it
TRUNCATEs what it touches):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_b_ledger.py

Drives the real endpoints (HR auth overridden, database real) and the real
workflow runner through a two-round workflow.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def seed(f) -> dict:
    now = datetime.now(tz=UTC)
    s = {k: uuid.uuid4() for k in ("company", "hr", "hr2", "req", "req2", "wf", "r1", "r2",
                                    "asha", "bharat", "e_asha", "e_bharat1", "e_bharat2")}
    async with f() as db:
        await db.execute(text("INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
                              " VALUES (:i,'Acme','acme',true,:n,:n)"), {"i": s["company"], "n": now})
        for uid, email in [(s["hr"], "hr@acme.test"), (s["hr2"], "hr2@acme.test")]:
            await db.execute(text(
                "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
                " is_active,notify_login_email,must_change_password,created_at,updated_at)"
                " VALUES (:i,:e,:fn,'x',:c,'en',true,false,false,:n,:n)"),
                {"i": uid, "e": email, "fn": email.split("@")[0].upper(), "c": s["company"],
                 "n": now})
        for rid, title in [(s["req"], "Staff Nurse"), (s["req2"], "Ward Manager")]:
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,created_at,updated_at)"
                " VALUES (:i,:c,:t,'mid','open',:n,:n)"), {"i": rid, "c": s["company"], "t": title,
                                                           "n": now})
        await db.execute(text(
            "INSERT INTO workflows (id,company_id,requisition_id,version,status,created_by_user_id,"
            " published_at,created_at,updated_at) VALUES (:i,:c,:r,1,'published',:u,:n,:n,:n)"),
            {"i": s["wf"], "c": s["company"], "r": s["req"], "u": s["hr"], "n": now})
        for rid, pos, title, nxt in [(s["r2"], 1, "Panel review", None),
                                     (s["r1"], 0, "Screening review", s["r2"])]:
            await db.execute(text(
                "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
                " on_pass_next_round_id,created_at,updated_at)"
                " VALUES (:i,:c,:w,:p,:t,'human_review',:nx,:n,:n)"),
                {"i": rid, "c": s["company"], "w": s["wf"], "p": pos, "t": title, "nx": nxt,
                 "n": now})
        for aid, name in [(s["asha"], "Asha"), (s["bharat"], "Bharat")]:
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,resume_text,status,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,'Staff Nurse','mid','cv','new',:n,:n)"),
                {"i": aid, "c": s["company"], "u": s["hr"], "fn": name,
                 "em": f"{name.lower()}@x.test", "n": now})
        for eid, aid, rid, wf in [(s["e_asha"], s["asha"], s["req"], s["wf"]),
                                  (s["e_bharat1"], s["bharat"], s["req"], None),
                                  (s["e_bharat2"], s["bharat"], s["req2"], None)]:
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                " target_job_title,workflow_id,created_at,updated_at)"
                " VALUES (:i,:c,:r,:a,'new','Staff Nurse',:w,:n,:n)"),
                {"i": eid, "c": s["company"], "r": rid, "a": aid, "w": wf, "n": now})
        await db.commit()
    return s


async def ledger(f, enrolment_id) -> list[dict]:
    async with f() as db:
        return [dict(r) for r in (await db.execute(text(
            "SELECT from_status, to_status, from_round_id, to_round_id, automated, actor_user_id,"
            "       reason FROM stage_transitions WHERE enrolment_id = :e ORDER BY occurred_at, id"),
            {"e": enrolment_id})).mappings().all()]


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text("TRUNCATE applicants, companies, users, job_requisitions, enrolments,"
                             " stage_transitions, workflows, workflow_rounds, round_results,"
                             " notifications, email_events CASCADE"))
    s = await seed(f)

    import app.routers.hr_applicants as hra
    import app.routers.hr_pipeline as hrp
    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app
    from app.workflow_runner import record_result, release_hold

    async def _no_email(*_: object, **__: object) -> None:
        return None

    hra.email_applicant_decision = _no_email  # type: ignore[assignment]
    hrp.email_applicant_decision = _no_email  # type: ignore[assignment]

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (s["hr"], s["company"])
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")

    # ── A single-application candidate, shortlisted from the applicant board ──
    r = await ac.patch(f"/hr/applicants/{s['asha']}", json={"status": "shortlisted"})
    check("board shortlist succeeds", r.status_code == 200, r.text[:200])
    moves = await ledger(f, s["e_asha"])
    check("the first round is recorded as a round move, by the system",
          any(m["to_round_id"] == s["r1"] and m["from_round_id"] is None and m["automated"]
              for m in moves), str(moves))
    check("the shortlist is recorded as HR's decision",
          any(m["from_status"] == "new" and m["to_status"] == "shortlisted"
              and not m["automated"] and m["actor_user_id"] == s["hr"] for m in moves))

    # ── Through both rounds: moves that do not change the status ─────────────
    async with f() as db:
        await record_result(db, enrolment_id=s["e_asha"], round_id=s["r1"], score=None,
                            graded_by="human", grader_user_id=s["hr"], passed_override=True)
        await db.commit()
    moves = await ledger(f, s["e_asha"])
    check("advancing between rounds is recorded even though the status stays the same",
          any(m["from_round_id"] == s["r1"] and m["to_round_id"] == s["r2"]
              and m["from_status"] == m["to_status"] == "shortlisted" for m in moves),
          str(moves))
    async with f() as db:
        await record_result(db, enrolment_id=s["e_asha"], round_id=s["r2"], score=None,
                            graded_by="human", grader_user_id=s["hr"], passed_override=True)
        await db.commit()
    moves = await ledger(f, s["e_asha"])
    check("finishing the last round is recorded", any(
        m["from_round_id"] == s["r2"] and m["to_round_id"] is None for m in moves), str(moves))
    check("and the move to 'interviewed' is recorded",
          any(m["to_status"] == "interviewed" for m in moves))

    # ── Hired from the pipeline board: through the ledger now ─────────────────
    r = await ac.post(f"/hr/applicants/{s['asha']}/decision",
                      json={"decision": "hired", "rationale": "strong panel"})
    check("pipeline hire succeeds", r.status_code == 200, r.text[:200])
    async with f() as db:
        st = (await db.execute(text(
            "SELECT e.status, a.status FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
            " WHERE e.id = :e"), {"e": s["e_asha"]})).first()
    moves = await ledger(f, s["e_asha"])
    check("the pipeline board's hire moves the enrolment, not just the applicant row",
          st is not None and st[0] == "hired" and st[1] == "hired", str(st))
    check("and is recorded as a person's decision, with their rationale",
          any(m["to_status"] == "hired" and not m["automated"] and m["actor_user_id"] == s["hr"]
              and m["reason"] == "strong panel" for m in moves), str(moves[-1:]))

    # ── Someone who applied to two openings ───────────────────────────────────
    r1 = await ac.post(f"/hr/applicants/{s['bharat']}/decision", json={"decision": "rejected"})
    r2 = await ac.patch(f"/hr/applicants/{s['bharat']}", json={"status": "rejected"})
    check("a decision that cannot name an opening is refused, not guessed (pipeline)",
          r1.status_code == 409 and "2 openings" in r1.text, r1.text[:200])
    check("...and from the applicant board", r2.status_code == 409, r2.text[:200])
    async with f() as db:
        untouched = await db.scalar(text(
            "SELECT count(*) FROM enrolments WHERE applicant_id = :a AND status = 'rejected'"),
            {"a": s["bharat"]})
    check("neither application was touched", untouched == 0)

    # ── A manual hold, and its release with the reviewer's reason ─────────────
    r = await ac.post(f"/hr/enrolments/{s['e_bharat1']}/status",
                      json={"status": "held", "reason": "waiting on references"})
    async with f() as db:
        held = (await db.execute(text("SELECT held_at, held_reason FROM enrolments WHERE id = :e"),
                                 {"e": s["e_bharat1"]})).first()
        out = await release_hold(db, enrolment_id=s["e_bharat1"], actor_user_id=s["hr"],
                                 reason="references came back fine")
        await db.commit()
    check("a hold made by hand sets when and why", r.status_code == 200 and held is not None
          and held[0] is not None and held[1] == "waiting on references", str(held))
    moves = await ledger(f, s["e_bharat1"])
    check("releasing a hold keeps the reviewer's own reason", out.action == "advanced" and any(
        "references came back fine" in (m["reason"] or "") for m in moves), str(moves[-1:]))

    # ── The history endpoint ──────────────────────────────────────────────────
    r = await ac.get(f"/hr/enrolments/{s['e_asha']}/history")
    hist = r.json() if r.status_code == 200 else []
    check("HR can read an application's history", r.status_code == 200 and len(hist) >= 6,
          f"{r.status_code} {len(hist)}")
    check("round moves come back with round names",
          any(h["from_round"] == "Screening review" and h["to_round"] == "Panel review"
              for h in hist), str(hist))
    check("a person's move names them; a system move names nobody",
          any(h["actor"] == "HR" and not h["automated"] for h in hist)
          and all(h["actor"] is None for h in hist if h["automated"]))
    r = await ac.get(f"/hr/enrolments/{uuid.uuid4()}/history")
    check("another company's (or no) application is 404", r.status_code == 404)
    await ac.aclose()
    app.dependency_overrides.clear()

    # ── The ledger is append-only ─────────────────────────────────────────────
    async def refused(sql: str, params: dict) -> bool:
        try:
            async with f() as db:
                await db.execute(text(sql), params)
                await db.commit()
            return False
        except DBAPIError as exc:
            return "append-only" in str(exc)

    check("an entry cannot be edited", await refused(
        "UPDATE stage_transitions SET to_status = 'rejected' WHERE enrolment_id = :e",
        {"e": s["e_asha"]}))
    check("an entry cannot be deleted while its application exists", await refused(
        "DELETE FROM stage_transitions WHERE enrolment_id = :e", {"e": s["e_asha"]}))

    # A user who made a move is deleted: their id is cleared, the entry stays.
    async with f() as db:
        await db.execute(text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at)"
            " VALUES (:c, :e, 'held', 'held', :u, false, 'note', now())"),
            {"c": s["company"], "e": s["e_bharat2"], "u": s["hr2"]})
        await db.commit()
    try:
        async with f() as db:
            await db.execute(text("DELETE FROM users WHERE id = :u"), {"u": s["hr2"]})
            await db.commit()
        deleted_user_ok = True
    except DBAPIError as exc:
        deleted_user_ok = False
        print("   ", str(exc)[:200])
    async with f() as db:
        kept = (await db.execute(text(
            "SELECT actor_user_id, automated FROM stage_transitions"
            " WHERE enrolment_id = :e AND reason = 'note'"), {"e": s["e_bharat2"]})).first()
    check("deleting a user clears their id and keeps the entry",
          deleted_user_ok and kept is not None and kept[0] is None and kept[1] is False, str(kept))

    # The application itself is deleted: its history goes with it.
    try:
        async with f() as db:
            await db.execute(text("DELETE FROM enrolments WHERE id = :e"), {"e": s["e_bharat2"]})
            await db.commit()
        async with f() as db:
            left = await db.scalar(text(
                "SELECT count(*) FROM stage_transitions WHERE enrolment_id = :e"),
                {"e": s["e_bharat2"]})
        check("deleting an application takes its history with it", left == 0, f"left={left}")
    except DBAPIError as exc:
        check("deleting an application takes its history with it", False, str(exc)[:200])

    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
