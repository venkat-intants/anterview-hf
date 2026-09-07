"""Group C Stage 4 smoke — the workflow runner walking real candidates through.

Standalone script; needs a live PostgreSQL with pgvector.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres \
        -e POSTGRES_DB=intants_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    export DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke
    python -m alembic upgrade head
    PYTHONPATH=".;../.." python tests/integration/smoke_group_c_runner.py

Three candidates through one four-round workflow: one who passes everything, one
who falls below a threshold, and one who lands inside the hold band. The
property that matters most is that none of the three can be rejected by the
runner — every path ends at a human decision.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.workflow_runner import (
    decision_queue,
    enrol_applicant,
    on_shortlisted,
    record_result,
    release_hold,
)
from app.workflows import add_round, create_draft, publish

URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:55432/intants_smoke"
PASS, FAIL = [], []

PROFILE = [
    {"id": "python", "name": "Python proficiency", "kind": "technical", "weight": 0.4},
    {"id": "solving", "name": "Problem solving", "kind": "cognitive", "weight": 0.35},
    {"id": "comms", "name": "Communication", "kind": "behavioural", "weight": 0.25},
]


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}{(' — ' + detail) if detail and not cond else ''}")


async def main() -> None:
    eng = create_async_engine(URL)
    f = async_sessionmaker(eng, expire_on_commit=False)
    async with eng.begin() as c:
        await c.execute(text(
            "TRUNCATE companies, users, applicants, jobs, job_requisitions, enrolments,"
            " stage_transitions, workflows, workflow_rounds, round_criteria, round_results,"
            " exams, exam_rounds, exam_assignments, interview_invites, sessions,"
            " email_events, notifications CASCADE"))

    now = datetime.now(tz=UTC)
    cid, uid, rid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    exam_id, er1, er2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

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
            "INSERT INTO job_requisitions (id,company_id,title,created_at,updated_at)"
            " VALUES (:i,:c,'Python Developer',:t,:t)"), {"i": rid, "c": cid, "t": now})
        await db.execute(text(
            "INSERT INTO exams (id,company_id,title,created_by_user_id,created_at,updated_at)"
            " VALUES (:i,:c,'Screen',:u,:t,:t)"), {"i": exam_id, "c": cid, "u": uid, "t": now})
        for n, (eid, title) in enumerate([(er1, "Aptitude"), (er2, "Technical")], start=1):
            await db.execute(text(
                "INSERT INTO exam_rounds (id,exam_id,company_id,round_number,title,position,"
                " status,created_at,updated_at) VALUES (:i,:e,:c,:n,:t,:p,'published',:ts,:ts)"),
                {"i": eid, "e": exam_id, "c": cid, "n": n, "t": title, "p": n - 1, "ts": now})
        await db.commit()

        # Build and publish a four-round workflow.
        wf = await create_draft(db, company_id=cid, requisition_id=rid, created_by=uid,
                                name="Standard technical")
        await db.commit()
        r1 = await add_round(db, company_id=cid, workflow_id=wf, title="Aptitude", kind="mcq",
                             pass_threshold=60, exam_round_id=er1, criteria=[PROFILE[1]])
        r2 = await add_round(db, company_id=cid, workflow_id=wf, title="Technical", kind="coding",
                             pass_threshold=50, exam_round_id=er2, criteria=[PROFILE[0]])
        r3 = await add_round(db, company_id=cid, workflow_id=wf, title="AI Interview",
                             kind="ai_interview", pass_threshold=60, criteria=PROFILE)
        r4 = await add_round(db, company_id=cid, workflow_id=wf, title="HR Final",
                             kind="human_review")
        await db.commit()
        rep = await publish(db, company_id=cid, workflow_id=wf, profile_competencies=PROFILE)
        await db.commit()
        check("workflow published", rep.publishable, str(rep.errors))

        cands = {}
        for name in ("Asha", "Bharat", "Chitra"):
            aid = uuid.uuid4()
            cands[name] = aid
            await db.execute(text(
                "INSERT INTO applicants (id,company_id,created_by_user_id,full_name,email,"
                " target_job_title,target_level,resume_text,ats_overall,status,created_at,updated_at)"
                " VALUES (:i,:c,:u,:fn,:em,'Python Developer','mid','cv',7,'new',:t,:t)"),
                {"i": aid, "c": cid, "u": uid, "fn": name,
                 "em": f"{name.lower()}@cand.test", "t": now})
        await db.commit()

    # ── C5: enrolment ────────────────────────────────────────────────────
    print("\n--- enrolment ---")
    enrols = {}
    async with f() as db:
        for name, aid in cands.items():
            out = await enrol_applicant(
                db, company_id=cid, applicant_id=aid, requisition_id=rid,
                target_job_title="Python Developer")
            enrols[name] = uuid.UUID(out.enrolment_id)
        await db.commit()
        check("three candidates enrolled", len(enrols) == 3)
        pinned = await db.scalar(text(
            "SELECT count(*) FROM enrolments WHERE workflow_id = :w"), {"w": wf})
        check("each enrolment pinned to the published workflow version", pinned == 3,
              f"count={pinned}")
        no_round = await db.scalar(text(
            "SELECT count(*) FROM enrolments WHERE current_round_id IS NULL"))
        check("nobody gets a round before the human shortlist gate", no_round == 3,
              f"count={no_round}")

        dup = await enrol_applicant(
            db, company_id=cid, applicant_id=cands["Asha"], requisition_id=rid,
            target_job_title="Python Developer")
        await db.rollback()
        check("re-applying is a no-op, not a duplicate", dup.action == "noop", str(dup.as_dict()))

    # ── The shortlist gate ───────────────────────────────────────────────
    print("\n--- the human shortlist gate ---")
    async with f() as db:
        out = await on_shortlisted(db, enrolment_id=enrols["Asha"], actor_user_id=uid)
        await db.commit()
        check("shortlisting assigns the first round", out.action == "advanced"
              and out.to_round == "Aptitude", str(out.as_dict()))
        asn = await db.scalar(text(
            "SELECT count(*) FROM exam_assignments WHERE enrolment_id = :e"),
            {"e": enrols["Asha"]})
        check("an exam link was minted for the round", asn == 1, f"count={asn}")
        mail = await db.scalar(text(
            "SELECT count(*) FROM email_events WHERE template='exam_link'"))
        check("the candidate was emailed the link", mail == 1, f"count={mail}")

        again = await on_shortlisted(db, enrolment_id=enrols["Asha"], actor_user_id=uid)
        await db.rollback()
        check("shortlisting twice does not re-assign", again.action == "noop",
              str(again.as_dict()))

    # ── A clean pass all the way through ─────────────────────────────────
    print("\n--- Asha passes everything ---")
    async with f() as db:
        out = await record_result(db, enrolment_id=enrols["Asha"], round_id=r1,
                                  score=18, max_score=20)
        await db.commit()
        check("90% clears a 60% threshold and advances", out.action == "advanced"
              and out.to_round == "Technical", str(out.as_dict()))

        out = await record_result(db, enrolment_id=enrols["Asha"], round_id=r2,
                                  score=8, max_score=10)
        await db.commit()
        check("advances into the AI interview", out.to_round == "AI Interview",
              str(out.as_dict()))
        inv = await db.scalar(text(
            "SELECT count(*) FROM interview_invites WHERE enrolment_id = :e"),
            {"e": enrols["Asha"]})
        check("an interview invite was minted", inv == 1, f"count={inv}")

        out = await record_result(
            db, enrolment_id=enrols["Asha"], round_id=r3, score=7.5, graded_by="ai",
            criterion_scores={"python": {"score": 8}, "solving": {"score": 7}},
            axes={"communication": 7, "technical": 8, "problem_solving": 7, "confidence": 8})
        await db.commit()
        check("interview score advances to the human round", out.to_round == "HR Final",
              str(out.as_dict()))

        out = await record_result(db, enrolment_id=enrols["Asha"], round_id=r4,
                                  score=None, graded_by="human", grader_user_id=uid,
                                  passed_override=True)
        await db.commit()
        check("the last round completes the workflow", out.action == "completed",
              str(out.as_dict()))
        check("completing is NOT an outcome — it awaits a person",
              "final human decision" in (out.reason or ""), str(out.as_dict()))
        st = await db.scalar(text("SELECT status FROM enrolments WHERE id=:i"),
                             {"i": enrols["Asha"]})
        check("status is never 'hired' by the runner", st != "hired", f"status={st}")

        both = (await db.execute(text(
            "SELECT criterion_scores, axes FROM round_results"
            " WHERE enrolment_id=:e AND round_id=:r"),
            {"e": enrols["Asha"], "r": r3})).mappings().first()
        check("two-layer score stored: criteria AND axes",
              both["criterion_scores"] is not None and both["axes"] is not None, str(both))

    # ── A clear miss ─────────────────────────────────────────────────────
    print("\n--- Bharat falls well short ---")
    async with f() as db:
        await on_shortlisted(db, enrolment_id=enrols["Bharat"], actor_user_id=uid)
        await db.commit()
        out = await record_result(db, enrolment_id=enrols["Bharat"], round_id=r1,
                                  score=4, max_score=20)
        await db.commit()
        check("20% against a 60% threshold is held", out.action == "held", str(out.as_dict()))
        row = (await db.execute(text(
            "SELECT status, held_at, held_reason FROM enrolments WHERE id=:i"),
            {"i": enrols["Bharat"]})).mappings().first()
        check("status is 'held', never 'rejected'", row["status"] == "held", str(dict(row)))
        check("held_at stamped", row["held_at"] is not None)
        check("the reason names the score and the threshold",
              "20%" in row["held_reason"] and "60%" in row["held_reason"],
              row["held_reason"])
        kept = await db.scalar(text(
            "SELECT count(*) FROM round_results WHERE enrolment_id=:e"),
            {"e": enrols["Bharat"]})
        check("a held candidate keeps the score they earned", kept == 1, f"count={kept}")

    # ── Inside the hold band ─────────────────────────────────────────────
    print("\n--- Chitra lands just under the line ---")
    async with f() as db:
        await on_shortlisted(db, enrolment_id=enrols["Chitra"], actor_user_id=uid)
        await db.commit()
        out = await record_result(db, enrolment_id=enrols["Chitra"], round_id=r1,
                                  score=11, max_score=20)  # 55% vs 60%, band 10
        await db.commit()
        check("a near miss is held", out.action == "held", str(out.as_dict()))
        reason = await db.scalar(text(
            "SELECT held_reason FROM enrolments WHERE id=:i"), {"i": enrols["Chitra"]})
        check("the near miss is flagged as within the band", "within 10 points" in reason,
              reason)

        rel = await release_hold(db, enrolment_id=enrols["Chitra"], actor_user_id=uid)
        await db.commit()
        check("a person can release a hold", rel.action == "advanced", str(rel.as_dict()))
        row = (await db.execute(text(
            "SELECT status, held_at FROM enrolments WHERE id=:i"),
            {"i": enrols["Chitra"]})).mappings().first()
        check("hold cleared", row["held_at"] is None and row["status"] == "shortlisted",
              str(dict(row)))
        led = (await db.execute(text(
            "SELECT automated, actor_user_id FROM stage_transitions"
            " WHERE enrolment_id=:e ORDER BY occurred_at DESC LIMIT 1"),
            {"e": enrols["Chitra"]})).mappings().first()
        check("the release is recorded as a human act",
              led["automated"] is False and led["actor_user_id"] == uid, str(dict(led)))

    # ── Idempotency ──────────────────────────────────────────────────────
    print("\n--- idempotency ---")
    async with f() as db:
        before = await db.scalar(text(
            "SELECT current_round_id FROM enrolments WHERE id=:i"), {"i": enrols["Asha"]})
        out = await record_result(db, enrolment_id=enrols["Asha"], round_id=r1,
                                  score=18, max_score=20)
        await db.commit()
        after = await db.scalar(text(
            "SELECT current_round_id FROM enrolments WHERE id=:i"), {"i": enrols["Asha"]})
        check("replaying an old round event does not move the candidate",
              out.action == "noop" and before == after, str(out.as_dict()))
        live = await db.scalar(text(
            "SELECT count(*) FROM round_results"
            " WHERE enrolment_id=:e AND round_id=:r AND superseded_at IS NULL"),
            {"e": enrols["Asha"], "r": r1})
        check("only one live result per round", live == 1, f"count={live}")
        total = await db.scalar(text(
            "SELECT count(*) FROM round_results WHERE enrolment_id=:e AND round_id=:r"),
            {"e": enrols["Asha"], "r": r1})
        check("the superseded attempt is retained for audit", total == 2, f"count={total}")

    # ── C9: settings actually gate ───────────────────────────────────────
    print("\n--- per-workflow settings ---")
    async with f() as db:
        await db.execute(text(
            "UPDATE workflows SET auto_advance_rounds = false WHERE id=:i"), {"i": wf})
        await db.commit()
        out = await record_result(db, enrolment_id=enrols["Chitra"], round_id=r1,
                                  score=19, max_score=20)
        await db.commit()
        check("auto-advance off stops progression", out.action == "noop"
              and "disabled" in (out.reason or ""), str(out.as_dict()))
        check("...but it is still not a rejection",
              (await db.scalar(text("SELECT status FROM enrolments WHERE id=:i"),
                               {"i": enrols["Chitra"]})) != "rejected")
        await db.execute(text(
            "UPDATE workflows SET auto_advance_rounds = true WHERE id=:i"), {"i": wf})
        await db.commit()

    # ── The decision queue ───────────────────────────────────────────────
    print("\n--- the final decision queue ---")
    async with f() as db:
        q = await decision_queue(db, company_id=cid, requisition_id=rid)
        names = {r["full_name"] for r in q}
        check("held and completed candidates appear in ONE queue",
              {"Asha", "Bharat"} <= names, str(sorted(names)))
        bharat = next(r for r in q if r["full_name"] == "Bharat")
        check("a held candidate carries the reason they stopped",
              bharat["held"] and bharat["held_reason"], str(bharat))
        asha = next(r for r in q if r["full_name"] == "Asha")
        check("a completed candidate is in the same list", not asha["held"], str(asha))
        check("held candidates are listed first so they are not overlooked",
              q[0]["held"] is True, str([(r["full_name"], r["held"]) for r in q]))

    # ── D-05, checked structurally ───────────────────────────────────────
    print("\n--- D-05 ---")
    async with f() as db:
        rejected = await db.scalar(text(
            "SELECT count(*) FROM enrolments WHERE status = 'rejected'"))
        check("the runner rejected nobody, at any point", rejected == 0, f"count={rejected}")
        auto_terminal = await db.scalar(text(
            "SELECT count(*) FROM stage_transitions"
            " WHERE automated = true AND to_status IN ('hired','rejected')"))
        check("no automated transition ever reached a terminal status",
              auto_terminal == 0, f"count={auto_terminal}")

    await eng.dispose()
    print(f"\n{'=' * 64}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 64)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
