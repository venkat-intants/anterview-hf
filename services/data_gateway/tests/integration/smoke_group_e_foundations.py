"""Group E foundations smoke test — against a real Postgres.

Standalone script, not a pytest module. Needs a THROWAWAY PostgreSQL at head
(it TRUNCATEs what it touches):

    cd services/data_gateway
    DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke \
      python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_e_foundations.py

What it checks:
  1. One definition of "awaiting a decision": the decision queue, the watcher
     and the requisition counts agree, and never-started applicants are not in it.
  2. Wait times come from the stage ledger, not updated_at.
  3. Each application keeps the CV it was submitted with: a re-application does
     not delete it, and the reconciler scores the earlier application against it.
  4. Rows nothing will ever score are settled rather than "being read" forever.
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

SCORE = {"overall": 7, "breakdown": {"skills": 7}, "strengths": ["x"], "concerns": ["y"],
         "recommendation": "consider", "summary": "ok"}


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
            " stage_transitions, workflows, workflow_rounds, round_criteria,"
            " reconciliation_state CASCADE"))

    cid, hr = uuid.uuid4(), uuid.uuid4()
    req = {k: uuid.uuid4() for k in ("a", "b", "c", "d")}
    wf, wf_off = uuid.uuid4(), uuid.uuid4()
    r_ai, r_review = uuid.uuid4(), uuid.uuid4()
    async with f() as db:
        await db.execute(text(
            "INSERT INTO companies (id,name,slug,is_active,created_at,updated_at)"
            " VALUES (:i,'Acme','acme',true,:n,:n)"), {"i": cid, "n": now})
        await db.execute(text(
            "INSERT INTO users (id,email,full_name,password_hash,company_id,preferred_language,"
            " is_active,notify_login_email,must_change_password,created_at,updated_at)"
            " VALUES (:i,'hr@acme.example.com','HR','x',:c,'en',true,false,false,:n,:n)"),
            {"i": hr, "c": cid, "n": now})
        for key, title in (("a", "Python Developer"), ("b", "Data Analyst"),
                           ("c", "QA Engineer"), ("d", "Support Lead")):
            await db.execute(text(
                "INSERT INTO job_requisitions (id,company_id,title,level,status,from_backfill,"
                " public_apply_enabled,owner_user_id,created_by_user_id,created_at,updated_at)"
                " VALUES (:i,:c,:t,'mid','open',false,true,:u,:u,:n,:n)"),
                {"i": req[key], "c": cid, "t": title, "u": hr, "n": now})

        # Opening A: AI interview -> human review, built as a draft then published.
        # Opening D: a draft whose settings switch scoring off.
        for wid, rid, auto in ((wf, req["a"], True), (wf_off, req["d"], False)):
            await db.execute(text(
                "INSERT INTO workflows (id,company_id,requisition_id,version,status,"
                " auto_score_on_apply,auto_assign_first_round,auto_advance_rounds,"
                " reminders_enabled,hold_band,created_at,updated_at)"
                " VALUES (:i,:c,:r,1,'draft',:auto,true,true,true,10,:n,:n)"),
                {"i": wid, "c": cid, "r": rid, "auto": auto, "n": now})
        await db.execute(text(
            "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
            " pass_threshold,deadline_days,created_at,updated_at)"
            " VALUES (:i,:c,:w,1,'Final Review','human_review',NULL,5,:n,:n)"),
            {"i": r_review, "c": cid, "w": wf, "n": now})
        await db.execute(text(
            "INSERT INTO workflow_rounds (id,company_id,workflow_id,position,title,kind,"
            " pass_threshold,deadline_days,on_pass_next_round_id,created_at,updated_at)"
            " VALUES (:i,:c,:w,0,'AI Interview','ai_interview',60,7,:nx,:n,:n)"),
            {"i": r_ai, "c": cid, "w": wf, "nx": r_review, "n": now})
        await db.execute(text(
            "UPDATE workflows SET status='published', published_at=:n WHERE id=:i"),
            {"i": wf, "n": now})

        async def person(name: str, *, pending: bool = False) -> uuid.UUID:
            aid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,status,pending_enrichment,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,'x','mid','new',:p,:n,:n)"),
                {"i": aid, "c": cid, "u": hr, "fn": name, "em": f"{name}@example.com",
                 "p": pending, "n": now})
            return aid

        async def enrol(aid: uuid.UUID, rid: uuid.UUID, status: str, *,
                        workflow: uuid.UUID | None, round_id: uuid.UUID | None) -> uuid.UUID:
            eid = uuid.uuid4()
            await db.execute(text(
                "INSERT INTO enrolments (id,company_id,requisition_id,applicant_id,status,"
                " target_job_title,target_level,workflow_id,current_round_id,held_at,held_reason,"
                " created_at,updated_at)"
                " VALUES (:i,:c,:r,:a,:s,'x','mid',:w,:cr,:ha,:hr,:n,:n)"),
                {"i": eid, "c": cid, "r": rid, "a": aid, "s": status, "w": workflow,
                 "cr": round_id, "ha": now if status == "held" else None,
                 "hr": "below threshold" if status == "held" else None, "n": now})
            return eid

        cases = {
            "never_shortlisted": ("new", None),
            "shortlisted_waiting": ("shortlisted", None),
            "held": ("held", r_ai),
            "finished": ("interviewed", None),
            "on_review": ("shortlisted", r_review),
            "mid_round": ("shortlisted", r_ai),
            "hired": ("hired", None),
        }
        eids = {}
        for name, (status, rnd) in cases.items():
            eids[name] = await enrol(await person(name), req["a"], status,
                                     workflow=wf, round_id=rnd)

        # The held candidate has been held for ten days; a rescore touched the row today.
        await db.execute(text(
            "INSERT INTO stage_transitions (company_id,enrolment_id,from_status,to_status,"
            " actor_user_id,automated,reason,occurred_at)"
            " VALUES (:c,:e,'shortlisted','held',NULL,true,'below threshold',:t)"),
            {"c": cid, "e": eids["held"], "t": now - timedelta(days=10)})
        await db.execute(text("UPDATE enrolments SET updated_at = :n WHERE id = :e"),
                         {"n": now, "e": eids["held"]})

        # Settling: one person whose only application has scoring switched off,
        # one whose application has no workflow yet.
        off = await person("scoring_off", pending=True)
        await enrol(off, req["d"], "new", workflow=wf_off, round_id=None)
        undecided = await person("no_workflow_yet", pending=True)
        await enrol(undecided, req["d"], "new", workflow=None, round_id=None)
        await db.commit()

    # ── 1. One definition of "awaiting a decision" ─────────────────────────
    from app.agents.watch_runner import OPENING_HEALTH_SQL
    from app.workflow_runner import decision_queue

    async with f() as db:
        queue = await decision_queue(db, company_id=cid, requisition_id=req["a"])
        health = {str(r.id): r for r in (await db.execute(
            OPENING_HEALTH_SQL, {"cid": str(cid), "limit": 50})).all()}
    names = {row["full_name"] for row in queue}
    check("the queue holds the held, the finished and the one on a review round",
          names == {"held", "finished", "on_review"}, str(sorted(names)))
    check("applicants nobody has shortlisted are not shown as finished",
          not names & {"never_shortlisted", "shortlisted_waiting"}, str(sorted(names)))
    check("the watcher counts exactly the people the queue shows",
          int(health[str(req["a"])].awaiting) == len(queue),
          f"watcher={health[str(req['a'])].awaiting} queue={len(queue)}")

    from app.database import get_db_session
    from app.dependencies import get_hr_company
    from app.main import app

    async def _db():  # noqa: ANN202
        async with f() as session:
            yield session

    app.dependency_overrides[get_hr_company] = lambda: (hr, cid)
    app.dependency_overrides[get_db_session] = _db
    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://t")
    got = (await ac.get(f"/hr/requisitions/{req['a']}")).json()
    listed = {r["id"]: r for r in (await ac.get("/hr/requisitions")).json()}
    dash = (await ac.get(f"/hr/requisitions/{req['a']}/dashboard")).json()
    check("the opening's 'awaiting your decision' is the queue's count",
          got.get("awaiting_decision") == 3 and listed[str(req["a"])]["awaiting_decision"] == 3
          and dash["requisition"]["awaiting_decision"] == 3, str(got.get("awaiting_decision")))
    check("…and 'unresolved' still counts everyone without a final decision",
          got.get("unresolved") == 6, str(got.get("unresolved")))

    # ── 2. Time from the ledger ────────────────────────────────────────────
    wait = float(health[str(req["a"])].longest_wait_days)
    check("the longest wait comes from the ledger, not the row's last update",
          9.5 <= wait <= 10.5, f"longest_wait_days={wait:.2f}")
    check("the dashboard median is measured the same way",
          dash.get("median_days_in_stage") is not None, str(dash.get("median_days_in_stage")))

    # ── 3. Each application keeps its CV ───────────────────────────────────
    import app.routers.public_apply as pa
    import app.routers.resume as resume

    stored: list[str] = []
    deleted: list[str] = []
    texts = iter(["first cv", "second cv"])

    async def _put(_raw: bytes, key: str) -> None:
        stored.append(key)

    async def _delete(key: str) -> None:
        deleted.append(key)

    async def _text(_raw: bytes) -> str:
        return next(texts)

    pa._upload_to_s3 = _put  # type: ignore[assignment]
    pa._delete_from_s3 = _delete  # type: ignore[assignment]
    pa._extract_pdf_text = _text  # type: ignore[assignment]

    form = {"full_name": "Meera Rao", "email": "meera@example.com", "consent_granted": "true"}
    pdf = ("cv.pdf", b"%PDF-1.4", "application/pdf")
    first = await ac.post(f"/apply/{req['b']}", data=form, files={"resume": pdf})
    second = await ac.post(f"/apply/{req['c']}", data=form, files={"resume": pdf})
    check("both applications are accepted", first.status_code == 201 and second.status_code == 201,
          f"{first.status_code} {first.text[:160]} / {second.status_code} {second.text[:160]}")

    async with f() as db:
        apps = {r["requisition_id"]: dict(r) for r in (await db.execute(text(
            "SELECT e.requisition_id, e.id, e.applied_resume_s3_key, a.resume_s3_key"
            "  FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
            " WHERE a.email = 'meera@example.com'"))).mappings().all()}
    b_app, c_app = apps.get(req["b"], {}), apps.get(req["c"], {})
    check("each application records the CV it was submitted with",
          len(stored) == 2 and b_app.get("applied_resume_s3_key") == stored[0]
          and c_app.get("applied_resume_s3_key") == stored[1], f"{stored} {apps}")
    check("re-applying does not delete the CV an unscored application still needs",
          stored[0] not in deleted, f"deleted={deleted}")

    import app.reconciliation as rec

    scored: dict[str, str] = {}

    async def _score(**kw: object) -> dict:
        scored[str(kw["job_title"])] = str(kw["resume_text"])
        return SCORE

    async def _download(key: str) -> bytes:
        assert key == stored[0], key
        return b"%PDF-1.4"

    async def _old_text(_raw: bytes) -> str:
        return "first cv"

    rec.score_resume_remote = _score  # type: ignore[assignment]
    resume._download_from_s3 = _download  # type: ignore[assignment]
    resume._extract_pdf_text = _old_text  # type: ignore[assignment]
    async with f() as db:
        await rec._score_pass(db, rec.PassResult())
    async with f() as db:
        keys = {r["requisition_id"]: r["scored_resume_s3_key"] for r in (await db.execute(text(
            "SELECT e.requisition_id, e.scored_resume_s3_key FROM enrolments e"
            "  JOIN applicants a ON a.id = e.applicant_id"
            " WHERE a.email = 'meera@example.com'"))).mappings().all()}
    check("the earlier application is scored against the CV it was sent with",
          scored.get("Data Analyst") == "first cv", str(scored))
    check("the later application is scored against its own, newer CV",
          scored.get("QA Engineer") == "second cv", str(scored))
    check("each score records the CV that produced it",
          keys.get(req["b"]) == stored[0] and keys.get(req["c"]) == stored[1], str(keys))

    # ── 4. Settling what nothing will score ────────────────────────────────
    async with f() as db:
        await rec._settle_pass(db, rec.PassResult())
    async with f() as db:
        flags = {r["full_name"]: r["pending_enrichment"] for r in (await db.execute(text(
            "SELECT full_name, pending_enrichment FROM applicants"
            " WHERE full_name IN ('scoring_off','no_workflow_yet')"))).mappings().all()}
    check("an application whose workflow switches scoring off stops 'being read'",
          flags.get("scoring_off") is False, str(flags))
    check("one with no workflow yet stays pending — publishing may still score it",
          flags.get("no_workflow_yet") is True, str(flags))

    await ac.aclose()
    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'='*64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
