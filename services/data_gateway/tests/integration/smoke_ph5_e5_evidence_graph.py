#!/usr/bin/env python3
"""PH5-E5 — the evidence graph and its trace, end to end against real Postgres.

One application is walked across every stage: screening (ATS + answers), a
direct exam attempt and a legacy-attributed one, an AI interview with a
purged session and one without, a human_review round with an independence-
blinded viewer and a correction opened after the decision, a job_simulation
task submission with consent withdrawn, an offer, and a hire followed by a
reversal to reject.

    docker run -d --name intants-pgv -e POSTGRES_PASSWORD=postgres \\
      -e POSTGRES_DB=ph5_e5_smoke -p 55432:5432 pgvector/pgvector:pg16
    cd services/data_gateway
    export SMOKE_DATABASE_URL=postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph5_e5_smoke
    PYTHONPATH=".;../.." DATABASE_URL="$SMOKE_DATABASE_URL" DATABASE_SSL= \\
      REDIS_URL=redis://127.0.0.1:6379/0 python tests/integration/smoke_ph5_e5_evidence_graph.py

Exits non-zero on any failed check.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

URL = os.environ.get(
    "SMOKE_DATABASE_URL", "postgresql+asyncpg://ph3:ph3@127.0.0.1:55432/ph5_e5_smoke"
)
PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(label)
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f" — {detail}" if not cond and detail else ""))


NOW = datetime.now(tz=UTC)


def _days_ago(n: int) -> datetime:
    return NOW - timedelta(days=n)


async def main() -> None:
    eng = create_async_engine(URL)
    factory = async_sessionmaker(eng, expire_on_commit=False)

    async with eng.begin() as c:
        await c.execute(
            text(
                "TRUNCATE companies, users, applicants, job_requisitions, enrolments,"
                " stage_transitions, workflows, workflow_rounds, round_criteria,"
                " round_results, exams, jobs, sessions, scorecards, interview_invites,"
                " interviewer_scorecards, interviewer_scorecard_scores, interview_loops,"
                " interview_sessions, interview_session_interviewers, task_submissions,"
                " round_tasks, offers CASCADE"
            )
        )

    company_a, company_b = uuid.uuid4(), uuid.uuid4()
    hr_a, hr_b, iv1, sa_x, interviewer_x, candidate_x = (uuid.uuid4() for _ in range(6))
    req_a = uuid.uuid4()
    applicant_id = uuid.uuid4()
    guest_user_id = uuid.uuid4()
    job_id = uuid.uuid4()
    wf_id = uuid.uuid4()
    mcq_round, coding_round, ai_round, human_round, task_round = (uuid.uuid4() for _ in range(5))
    enrolment_id = uuid.uuid4()

    async def _user(db, uid: uuid.UUID, *, company: uuid.UUID | None, email: str, name: str) -> None:
        await db.execute(
            text(
                "INSERT INTO users (id, email, full_name, password_hash, company_id,"
                " preferred_language, is_active, notify_login_email, must_change_password,"
                " created_at, updated_at)"
                " VALUES (:i,:e,:n,'x',:c,'en',true,false,false,:t,:t)"
            ),
            {"i": uid, "e": email, "n": name, "c": company, "t": NOW},
        )

    async def _role(db, uid: uuid.UUID, role: str) -> None:
        await db.execute(
            text(
                "INSERT INTO user_roles (user_id, role_id, assigned_at)"
                " VALUES (:u, (SELECT id FROM roles WHERE name = :r), :t)"
            ),
            {"u": uid, "r": role, "t": NOW},
        )

    async with factory() as db:
        for cid, slug in ((company_a, "e5-acme"), (company_b, "e5-globex")):
            await db.execute(
                text(
                    "INSERT INTO companies (id, name, slug, is_active, created_at, updated_at)"
                    " VALUES (:i, :n, :s, true, :t, :t)"
                ),
                {"i": cid, "n": slug, "s": slug, "t": NOW},
            )
        await _user(db, hr_a, company=company_a, email="hr_a@e5.test", name="Hana HR")
        await _role(db, hr_a, "hr_manager")
        await _user(db, hr_b, company=company_a, email="hr_b@e5.test", name="Beth HR")
        await _role(db, hr_b, "hr_manager")
        await _role(db, hr_b, "interviewer")
        await _user(db, iv1, company=company_a, email="iv1@e5.test", name="Ivan Interviewer")
        await _role(db, iv1, "interviewer")
        await _user(db, sa_x, company=company_a, email="sa_x@e5.test", name="Sam SuperAdmin")
        await _role(db, sa_x, "super_admin")
        await _user(db, interviewer_x, company=company_a, email="ivx@e5.test", name="Ivy X")
        await _role(db, interviewer_x, "interviewer")
        await _user(db, candidate_x, company=None, email="cand_x@e5.test", name="Cand X")
        await _role(db, candidate_x, "candidate")

        await db.execute(
            text(
                "INSERT INTO job_requisitions (id, company_id, title, level, jd_text,"
                " created_by_user_id, status, from_backfill, created_at, updated_at)"
                " VALUES (:i,:c,'Backend Engineer','mid','build things',:u,'open',false,:t,:t)"
            ),
            {"i": req_a, "c": company_a, "u": hr_a, "t": NOW},
        )

        await _user(db, guest_user_id, company=None, email="guest+e5@applicants.invalid", name="Asha Applicant")
        await _role(db, guest_user_id, "guest_candidate")
        await db.execute(
            text(
                "INSERT INTO applicants (id, company_id, user_id, full_name, email,"
                " target_job_title, ats_overall, ats_recommendation, created_at, updated_at)"
                " VALUES (:i,:c,:u,'Asha Applicant','asha@e5.test','Backend Engineer',"
                " 82,'strong_yes',:t,:t)"
            ),
            {"i": applicant_id, "c": company_a, "u": guest_user_id, "t": _days_ago(30)},
        )

        # ------------------------------------------------------------------
        # Workflow: mcq -> coding -> ai_interview -> human_review -> job_simulation
        # ------------------------------------------------------------------
        # PH4-O6: the database refuses any workflow INSERT that is not an
        # unreviewed draft — born a draft, walked through review at the SQL
        # level (seed_helpers.approve_for_publish), then published.
        from tests.integration.seed_helpers import approve_for_publish

        await db.execute(
            text(
                "INSERT INTO workflows (id, company_id, requisition_id, version, status,"
                " auto_score_on_apply, auto_assign_first_round, auto_advance_rounds,"
                " reminders_enabled, hold_band, created_by_user_id, created_at, updated_at)"
                " VALUES (:i,:c,:r,1,'draft',true,true,true,true,10,:u,:t,:t)"
            ),
            {"i": wf_id, "c": company_a, "r": req_a, "u": hr_a, "t": NOW},
        )
        rounds = [
            (mcq_round, 0, "Aptitude", "mcq"),
            (coding_round, 1, "Coding Test", "coding"),
            (ai_round, 2, "AI Screen", "ai_interview"),
            (human_round, 3, "Panel", "human_review"),
            (task_round, 4, "Case Study", "job_simulation"),
        ]
        for rid, pos, title, kind in rounds:
            await db.execute(
                text(
                    "INSERT INTO workflow_rounds (id, company_id, workflow_id, position, title,"
                    " kind, deadline_days, created_at, updated_at)"
                    " VALUES (:i,:c,:w,:p,:t,:k,7,:ts,:ts)"
                ),
                {"i": rid, "c": company_a, "w": wf_id, "p": pos, "t": title, "k": kind, "ts": NOW},
            )
        await db.execute(
            text(
                "INSERT INTO round_criteria (id, company_id, round_id, competency_id,"
                " competency_name, weight, created_at)"
                " VALUES (:i,:c,:r,'communication','Communication',0.5,:t)"
            ),
            {"i": uuid.uuid4(), "c": company_a, "r": human_round, "t": NOW},
        )

        # exam_assignments/exam_attempts key off exam_rounds, not
        # workflow_rounds — workflow_rounds.exam_round_id is the pointer
        # between the two (Group C), and it must be wired BEFORE the workflow
        # publishes: a published workflow's rounds are frozen.
        exam_mcq, exam_coding = uuid.uuid4(), uuid.uuid4()
        exam_round_mcq, exam_round_coding = uuid.uuid4(), uuid.uuid4()
        for exam_id, exam_round_id, title, kind, wr_id in (
            (exam_mcq, exam_round_mcq, "Aptitude Exam", "mcq", mcq_round),
            (exam_coding, exam_round_coding, "Coding Exam", "coding", coding_round),
        ):
            await db.execute(
                text(
                    "INSERT INTO exams (id, company_id, created_by_user_id, title, pass_threshold,"
                    " allow_retake, status, kind, created_at, updated_at)"
                    " VALUES (:i,:c,:u,:t,60,false,'published',:k,:ts,:ts)"
                ),
                {"i": exam_id, "c": company_a, "u": hr_a, "t": title, "k": kind, "ts": NOW},
            )
            await db.execute(
                text(
                    "INSERT INTO exam_rounds (id, exam_id, company_id, title, round_number,"
                    " pass_threshold, advances_to_interview, status, position, created_at, updated_at)"
                    " VALUES (:i,:e,:c,:t,1,60,true,'published',0,:ts,:ts)"
                ),
                {"i": exam_round_id, "e": exam_id, "c": company_a, "t": title, "ts": NOW},
            )
            await db.execute(
                text("UPDATE workflow_rounds SET exam_round_id = :er WHERE id = :wr"),
                {"er": exam_round_id, "wr": wr_id},
            )

        await approve_for_publish(db, workflow_id=wf_id, company_id=company_a)
        await db.execute(
            text("UPDATE workflows SET status = 'published', published_at = :t WHERE id = :i"),
            {"i": wf_id, "t": NOW},
        )

        await db.execute(
            text(
                "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, status,"
                " target_job_title, workflow_id, current_round_id, ats_overall,"
                " ats_recommendation, scored_at, source, created_at, updated_at)"
                " VALUES (:i,:c,:r,:a,'shortlisted','Backend Engineer',:w,:cr,82,'strong_yes',"
                " :scored,'referral',:created,:t)"
            ),
            {
                "i": enrolment_id, "c": company_a, "r": req_a, "a": applicant_id, "w": wf_id,
                "cr": mcq_round, "scored": _days_ago(29), "created": _days_ago(30), "t": NOW,
            },
        )
        # A screening answer needs a real question — seed one directly.
        question_id = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO application_questions (id, company_id, requisition_id, prompt, kind,"
                " position, options, created_at, updated_at)"
                " VALUES (:i,:c,:r,'Why us?','short_text',0,'[]'::jsonb,:t,:t)"
            ),
            {"i": question_id, "c": company_a, "r": req_a, "t": _days_ago(30)},
        )
        await db.execute(
            text(
                "INSERT INTO application_answers (id, company_id, enrolment_id, question_id, answer, created_at)"
                " VALUES (:i,:c,:e,:q,'\"Because\"'::jsonb,:t)"
            ),
            {"i": uuid.uuid4(), "c": company_a, "e": enrolment_id, "q": question_id, "t": _days_ago(29)},
        )

        # ------------------------------------------------------------------
        # Exam attempts: mcq (direct) + coding (direct, for the code-evidence
        # chip attachment path). exams/exam_rounds were seeded above, before
        # the workflow published.
        # ------------------------------------------------------------------
        asg_mcq, asg_coding = uuid.uuid4(), uuid.uuid4()
        attempt_mcq, attempt_coding = uuid.uuid4(), uuid.uuid4()
        for asg_id, exam_id, exam_round_id in (
            (asg_mcq, exam_mcq, exam_round_mcq), (asg_coding, exam_coding, exam_round_coding),
        ):
            await db.execute(
                text(
                    "INSERT INTO exam_assignments (id, company_id, exam_id, round_id, applicant_id,"
                    " enrolment_id, token_hash, expires_at, status, created_at, updated_at)"
                    " VALUES (:i,:c,:e,:r,:a,:enr,:h,:x,'completed',:t,:t)"
                ),
                {
                    "i": asg_id, "c": company_a, "e": exam_id, "r": exam_round_id, "a": applicant_id,
                    "enr": enrolment_id, "h": f"hash-{asg_id}", "x": NOW + timedelta(days=7), "t": _days_ago(28),
                },
            )
        for attempt_id, asg_id, exam_id, exam_round_id, pct, passed in (
            (attempt_mcq, asg_mcq, exam_mcq, exam_round_mcq, 80, True),
            (attempt_coding, asg_coding, exam_coding, exam_round_coding, 70, True),
        ):
            await db.execute(
                text(
                    "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
                    " assignment_id, attempt_no, score_percent, passed, status, started_at,"
                    " submitted_at, created_at, updated_at)"
                    " VALUES (:i,:c,:e,:r,:a,:asg,1,:pct,:p,'submitted',:s,:s,:s,:s)"
                ),
                {
                    "i": attempt_id, "c": company_a, "e": exam_id, "r": exam_round_id, "a": applicant_id,
                    "asg": asg_id, "pct": pct, "p": passed, "s": _days_ago(27),
                },
            )

        # ------------------------------------------------------------------
        # AI interview: a purged one, and a live one, both direct
        # ------------------------------------------------------------------
        await db.execute(
            text(
                "INSERT INTO jobs (id, title, description, level, created_at, updated_at)"
                " VALUES (:i,'Backend Engineer','x','mid',:t,:t)"
            ),
            {"i": job_id, "t": NOW},
        )
        invite_purged, invite_live, session_live, scorecard_live = (uuid.uuid4() for _ in range(4))
        await db.execute(
            text(
                "INSERT INTO interview_invites (id, company_id, applicant_id, job_id, enrolment_id,"
                " token_hash, expires_at, status, created_at, updated_at)"
                " VALUES (:i,:c,:a,:j,:e,:h,:x,'completed',:t,:t)"
            ),
            {
                "i": invite_purged, "c": company_a, "a": applicant_id, "j": job_id, "e": enrolment_id,
                "h": "hash-purged", "x": NOW, "t": _days_ago(26),
            },
        )
        await db.execute(
            text(
                "INSERT INTO sessions (id, user_id, job_id, language, status, started_at,"
                " completed_at, metadata, created_at, updated_at)"
                " VALUES (:i,:u,:j,'en','completed',:s,:c,'{}'::jsonb,:s,:c)"
            ),
            {"i": session_live, "u": guest_user_id, "j": job_id, "s": _days_ago(24), "c": _days_ago(24)},
        )
        await db.execute(
            text(
                "INSERT INTO scorecards (scorecard_id, session_id, scores, composite_score,"
                " summary, lang, created_at)"
                " VALUES (:i,:s,'{}'::jsonb,7.5,'Strong technical answers.','en',:t)"
            ),
            {"i": scorecard_live, "s": session_live, "t": _days_ago(24)},
        )
        await db.execute(
            text(
                "INSERT INTO interview_invites (id, company_id, applicant_id, job_id, enrolment_id,"
                " session_id, token_hash, expires_at, status, created_at, updated_at)"
                " VALUES (:i,:c,:a,:j,:e,:s,:h,:x,'completed',:t,:t)"
            ),
            {
                "i": invite_live, "c": company_a, "a": applicant_id, "j": job_id, "e": enrolment_id,
                "s": session_live, "h": "hash-live", "x": NOW, "t": _days_ago(24),
            },
        )

        await db.commit()

    # ----------------------------------------------------------------------
    # Human interview: assign iv1 + hr_b together, iv1 submits, then opens a
    # correction AFTER the decision (to prove the trace flags it); hr_b never
    # submits, so hr_b "owes" a scorecard on this round (independence).
    # ----------------------------------------------------------------------
    from app.interviewer_scorecards import RequestMeta, assign, submit

    meta = RequestMeta()
    async with factory() as db:
        out = await assign(
            db, company_id=company_a, enrolment_id=enrolment_id, round_id=human_round,
            interviewer_user_ids=[iv1, hr_b], assigned_by=hr_a,
            due_at=NOW + timedelta(days=3), meta=meta, notify=False,
        )
        await db.commit()
    iv1_card_id = uuid.UUID(next(c["scorecard_id"] for c in out["created"] if c["interviewer_user_id"] == str(iv1)))

    async with factory() as db:
        await submit(
            db, scorecard_id=iv1_card_id, interviewer_user_id=iv1, company_id=company_a,
            scores=[{"competency_id": "communication", "score": 4, "not_assessed": False,
                     "evidence": "clear answers"}],
            summary="Solid communicator.", meta=meta,
        )
        await db.commit()

    # Move an interview_session under this round too, so the scorecard's
    # originates_from edge has something to point at.
    session_row_id = uuid.uuid4()
    loop_id = uuid.uuid4()
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO interview_loops (id, company_id, enrolment_id, applicant_id, title,"
                " status, candidate_timezone, buffer_minutes, self_schedule, created_by_user_id,"
                " created_at, updated_at)"
                " VALUES (:i,:c,:e,:a,'Panel loop','completed','Asia/Kolkata',15,false,:u,:t,:t)"
            ),
            {"i": loop_id, "c": company_a, "e": enrolment_id, "a": applicant_id, "u": hr_a, "t": _days_ago(23)},
        )
        await db.execute(
            text(
                "INSERT INTO interview_sessions (id, company_id, loop_id, enrolment_id, applicant_id,"
                " round_id, title, duration_minutes, starts_at, ends_at, blocked_until, status,"
                " created_at, updated_at)"
                " VALUES (:i,:c,:l,:e,:a,:r,'Panel',60,:s,:en,:b,'completed',:s,:s)"
            ),
            {
                "i": session_row_id, "c": company_a, "l": loop_id, "e": enrolment_id, "a": applicant_id,
                "r": human_round, "s": _days_ago(23), "en": _days_ago(23) + timedelta(hours=1),
                "b": _days_ago(23) + timedelta(hours=1, minutes=15),
            },
        )
        # interview_session_interviewers rows are created by assign()/booking
        # flows this smoke does not run; insert directly, matching the
        # trigger's own column set.
        await db.execute(
            text(
                "INSERT INTO interview_session_interviewers (session_id, company_id,"
                " interviewer_user_id, scorecard_id, created_at, updated_at)"
                " VALUES (:s,:c,:iv,:sc,:t,:t)"
                " ON CONFLICT (session_id, interviewer_user_id) DO UPDATE SET scorecard_id = :sc"
            ),
            {"s": session_row_id, "c": company_a, "iv": iv1, "sc": iv1_card_id, "t": _days_ago(23)},
        )
        await db.commit()

    # ------------------------------------------------------------------
    # Round result for the coding round (originates_from an exam_attempt),
    # and for the human_review round (no originates_from, by design).
    # ------------------------------------------------------------------
    rr_coding, rr_human = uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO round_results (id, company_id, enrolment_id, round_id, attempt_ref,"
                " percent, passed, criterion_scores, graded_by, evidence, created_at)"
                " VALUES (:i,:c,:e,:r,:ar,70,true,'{}'::jsonb,'deterministic',NULL,:t)"
            ),
            {"i": rr_coding, "c": company_a, "e": enrolment_id, "r": coding_round, "ar": attempt_coding, "t": _days_ago(27)},
        )
        await db.execute(
            text(
                "INSERT INTO round_results (id, company_id, enrolment_id, round_id, attempt_ref,"
                " percent, passed, criterion_scores, graded_by, grader_user_id, evidence, created_at)"
                " VALUES (:i,:c,:e,:r,NULL,80,true,"
                " CAST(:cs AS jsonb),'human',:g,'looked solid',:t)"
            ),
            {
                "i": rr_human, "c": company_a, "e": enrolment_id, "r": human_round, "g": hr_a,
                "cs": json.dumps({"communication": 4}), "t": _days_ago(22),
            },
        )
        await db.commit()

    # ------------------------------------------------------------------
    # Task submission (job_simulation), consent withdrawn: started, never
    # consented — the honest DPDP state the graph must show with no content.
    # ------------------------------------------------------------------
    task_id = uuid.uuid4()
    task_digest = "0" * 64
    async with factory() as db:
        # A task submission arrives 'assigned' (the lifecycle trigger insists),
        # then starts (which is when consent is given), then withdraws
        # consent — the honest DPDP state: started, but no longer consented.
        await db.execute(
            text(
                "INSERT INTO task_submissions (id, company_id, enrolment_id, round_id, applicant_id,"
                " kind, status, due_at, config_digest, attempt_no, created_at, updated_at)"
                " VALUES (:i,:c,:e,:r,:a,'job_simulation','assigned',:due,:dig,1,:s,:s)"
            ),
            {"i": task_id, "c": company_a, "e": enrolment_id, "r": task_round, "a": applicant_id,
             "due": NOW + timedelta(days=3), "dig": task_digest, "s": _days_ago(20)},
        )
        await db.execute(
            text(
                "INSERT INTO dpdp_consent_ledger (id, user_id, consent_type, granted, granted_at,"
                " purpose, evidence)"
                " VALUES (:id,:u,'assessment_submission',true,:t,'recruitment',CAST(:ev AS jsonb))"
            ),
            {"id": uuid.uuid4(), "u": guest_user_id, "t": _days_ago(20),
             "ev": json.dumps({"source": "task_start", "submission_id": str(task_id)})},
        )
        await db.execute(
            text(
                "UPDATE task_submissions SET status = 'in_progress', started_at = :s,"
                " consented_at = :s, updated_at = :s WHERE id = :i"
            ),
            {"i": task_id, "s": _days_ago(20)},
        )
        await db.execute(
            text("UPDATE task_submissions SET consented_at = NULL, updated_at = :n WHERE id = :i"),
            {"i": task_id, "n": _days_ago(19)},
        )
        await db.commit()

    # ------------------------------------------------------------------
    # HIRE, then a REVERSAL to reject — both through the real writer.
    # enrolment_awaits_human requires current_round_id to point at a
    # human_review round: move the candidate onto the panel round first.
    # ------------------------------------------------------------------
    from app.final_decision import record_final_decision

    async with factory() as db:
        await db.execute(
            text("UPDATE enrolments SET current_round_id = :r, status = 'interviewed' WHERE id = :e"),
            {"r": human_round, "e": enrolment_id},
        )
        await db.commit()

    async with factory() as db:
        _hire = await record_final_decision(
            db, company_id=company_a, enrolment_id=enrolment_id, decision="hired",
            reason="Strong panel result.", reason_code="skills_fit", actor_user_id=hr_a,
        )
        await db.commit()
    decision_hire_id = None
    async with factory() as db:
        decision_hire_id = await db.scalar(
            text(
                "SELECT id FROM stage_transitions WHERE enrolment_id = :e AND to_status = 'hired'"
                " ORDER BY occurred_at DESC LIMIT 1"
            ),
            {"e": enrolment_id},
        )

    # An offer, following the hire. offers_lifecycle (PH4-A3) insists a row
    # arrives as a draft and walks its own FSM — insert draft, then move it
    # on exactly like the real writer would (submit, approve, send).
    offer_id = uuid.uuid4()
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO offers (id, company_id, enrolment_id, applicant_id, requisition_id,"
                " status, job_title, employment_type, currency, pay_period, base_salary,"
                " valid_days, created_by_user_id, created_at, updated_at)"
                " VALUES (:i,:c,:e,:a,:r,'draft','Backend Engineer','full_time','INR','annual',"
                " 1200000, 7, :u, :t, :t)"
            ),
            {
                "i": offer_id, "c": company_a, "e": enrolment_id, "a": applicant_id, "r": req_a,
                "u": hr_a, "t": _days_ago(16),
            },
        )
        await db.execute(
            text(
                "UPDATE offers SET status = 'pending_approval', submitted_by_user_id = :u,"
                " updated_at = :t WHERE id = :i"
            ),
            {"i": offer_id, "u": hr_a, "t": _days_ago(16)},
        )
        await db.execute(
            text(
                "UPDATE offers SET status = 'approved', decided_by_user_id = :u,"
                " updated_at = :t WHERE id = :i"
            ),
            {"i": offer_id, "u": sa_x, "t": _days_ago(16)},
        )
        await db.execute(
            text(
                "UPDATE offers SET status = 'sent', token_hash = :h, sent_at = :sent,"
                " expires_at = :exp, updated_at = :sent WHERE id = :i"
            ),
            {
                "i": offer_id, "h": f"hash-offer-{offer_id}", "sent": _days_ago(15),
                "exp": NOW + timedelta(days=7),
            },
        )
        await db.commit()

    # Now, AFTER the hire decision, a correction appears on iv1's scorecard —
    # this must show up as `after_decision`, and flag the ORIGINAL as
    # changed_after_decision. The real service (open_correction) REFUSES this
    # once a decision is recorded ("the evidence a decision rested on is
    # locked") — by construction, no live path can produce it — but the
    # trace's honesty must still hold for whatever the data says (a historical
    # row, e.g. from before that guard existed), so this seeds the shape
    # directly rather than pretending the guard does not exist.
    correction_id = uuid.uuid4()
    correction_at = datetime.now(tz=UTC)
    async with factory() as db:
        await db.execute(
            text(
                "UPDATE interviewer_scorecards SET superseded_at = :n, superseded_by_id = :new"
                " WHERE id = :old"
            ),
            {"n": correction_at, "new": correction_id, "old": iv1_card_id},
        )
        # Arrives 'in_progress' (scores are frozen the moment a scorecard is
        # 'submitted'), gets its copied scores, then is marked submitted.
        await db.execute(
            text(
                "INSERT INTO interviewer_scorecards (id, company_id, enrolment_id, round_id,"
                " interviewer_user_id, assigned_by_user_id, status, due_at, summary, started_at,"
                " corrects_id, correction_reason, corrected_after_peers_visible,"
                " created_at, updated_at)"
                " SELECT :new, company_id, enrolment_id, round_id, interviewer_user_id,"
                "        assigned_by_user_id, 'in_progress', due_at, summary, :n, id, :why,"
                "        false, :n, :n"
                "   FROM interviewer_scorecards WHERE id = :old"
            ),
            {"new": correction_id, "old": iv1_card_id, "n": correction_at,
             "why": "Missed a strength worth recording on reflection."},
        )
        await db.execute(
            text(
                "INSERT INTO interviewer_scorecard_scores (scorecard_id, company_id, round_id,"
                " competency_id, score, not_assessed, evidence, updated_at)"
                " SELECT :new, company_id, round_id, competency_id, score, not_assessed,"
                "        evidence, :n"
                "   FROM interviewer_scorecard_scores WHERE scorecard_id = :old"
            ),
            {"new": correction_id, "old": iv1_card_id, "n": correction_at},
        )
        await db.execute(
            text(
                "UPDATE interviewer_scorecards SET status = 'submitted', submitted_at = :n,"
                " updated_at = :n WHERE id = :new"
            ),
            {"new": correction_id, "n": correction_at},
        )
        await db.commit()

    # The reversal.
    async with factory() as db:
        _reject = await record_final_decision(
            db, company_id=company_a, enrolment_id=enrolment_id, decision="rejected",
            reason="Reference check raised a concern.", reason_code="skills_fit", actor_user_id=hr_a,
        )
        await db.commit()
    async with factory() as db:
        decision_reject_id = await db.scalar(
            text(
                "SELECT id FROM stage_transitions WHERE enrolment_id = :e AND to_status = 'rejected'"
                " ORDER BY occurred_at DESC LIMIT 1"
            ),
            {"e": enrolment_id},
        )

    # ------------------------------------------------------------------
    # Legacy attribution: a second applicant with exactly one live
    # application and an exam attempt whose assignment carries NO
    # enrolment_id at all.
    # ------------------------------------------------------------------
    legacy_applicant, legacy_enrolment = uuid.uuid4(), uuid.uuid4()
    legacy_asg, legacy_attempt = uuid.uuid4(), uuid.uuid4()
    async with factory() as db:
        await db.execute(
            text(
                "INSERT INTO applicants (id, company_id, full_name, target_job_title, created_at, updated_at)"
                " VALUES (:i,:c,'Legacy Larry','Backend Engineer',:t,:t)"
            ),
            {"i": legacy_applicant, "c": company_a, "t": _days_ago(60)},
        )
        await db.execute(
            text(
                "INSERT INTO enrolments (id, company_id, requisition_id, applicant_id, status,"
                " target_job_title, source, created_at, updated_at)"
                " VALUES (:i,:c,:r,:a,'new','Backend Engineer','unknown',:t,:t)"
            ),
            {"i": legacy_enrolment, "c": company_a, "r": req_a, "a": legacy_applicant, "t": _days_ago(60)},
        )
        await db.execute(
            text(
                "INSERT INTO exam_assignments (id, company_id, exam_id, round_id, applicant_id,"
                " enrolment_id, token_hash, expires_at, status, created_at, updated_at)"
                " VALUES (:i,:c,:e,:r,:a,NULL,:h,:x,'completed',:t,:t)"
            ),
            {
                "i": legacy_asg, "c": company_a, "e": exam_mcq, "r": exam_round_mcq, "a": legacy_applicant,
                "h": "hash-legacy", "x": NOW + timedelta(days=7), "t": _days_ago(59),
            },
        )
        await db.execute(
            text(
                "INSERT INTO exam_attempts (id, company_id, exam_id, round_id, applicant_id,"
                " assignment_id, attempt_no, score_percent, passed, status, started_at,"
                " submitted_at, created_at, updated_at)"
                " VALUES (:i,:c,:e,:r,:a,:asg,1,90,true,'submitted',:s,:s,:s,:s)"
            ),
            {
                "i": legacy_attempt, "c": company_a, "e": exam_mcq, "r": exam_round_mcq, "a": legacy_applicant,
                "asg": legacy_asg, "s": _days_ago(58),
            },
        )
        await db.commit()

    # ========================================================================
    # 1. build_graph() — pure module calls, no HTTP, no audit noise
    # ========================================================================
    print("\n--- build_graph() over the seeded application ---")
    from app.evidence_graph import (
        EvidenceGraphError,
        blinded_round_count,
        build_graph,
        decision_trace_from_graph,
        latest_decision_id,
        resolve_decision,
    )

    async with factory() as db:
        graph = await build_graph(db, company_id=company_a, enrolment_id=enrolment_id, viewer_user_id=hr_a)
    kinds = {n.kind for n in graph.nodes}
    check(
        "every node kind but application-answers-count-only shows up",
        {
            "application", "screening_ats", "screening_answers", "stage_move", "exam_attempt",
            "round_result", "ai_interview", "interview_session", "human_scorecard",
            "task_submission", "offer", "decision",
        } <= kinds,
        str(sorted(kinds)),
    )
    check("two decisions on the ledger (hire, then reversal)", len(graph.decisions) == 2, str(graph.decisions))
    ai_nodes = [n for n in graph.nodes if n.kind == "ai_interview"]
    check("both AI interview invites appear", len(ai_nodes) == 2, str(len(ai_nodes)))
    purged = [n for n in ai_nodes if n.lifecycle == "purged"]
    check("the purged invite has no content and says why", len(purged) == 1 and purged[0].content is None
          and purged[0].content_hidden_reason is not None, str(purged))
    live_ai = [n for n in ai_nodes if n.lifecycle != "purged"][0]
    check("the live AI interview carries its composite score", live_ai.content is not None
          and live_ai.content.get("composite_0_10") == 7.5, str(live_ai.content))

    task_nodes = [n for n in graph.nodes if n.kind == "task_submission"]
    check("the task submission is flagged consent_withdrawn with no content", len(task_nodes) == 1
          and task_nodes[0].lifecycle == "consent_withdrawn" and task_nodes[0].content is None,
          str(task_nodes))

    exam_attempt_nodes = {n.source.id: n for n in graph.nodes if n.kind == "exam_attempt"}
    check("the direct mcq attempt is attributed directly", exam_attempt_nodes[str(attempt_mcq)].provenance.attribution == "direct")
    coding_node = next(n for n in graph.nodes if n.kind == "exam_attempt" and n.source.id == str(attempt_coding))
    check("the coding attempt carries a code_evidence chip", isinstance(coding_node.content, dict)
          and "code_evidence" in coding_node.content, str(coding_node.content))
    check(
        "omitted[] declares every real exclusion, including compensation/answers/AI prose/contact",
        {o.kind for o in graph.omitted} >= {
            "offer_compensation", "screening_answer_text", "ai_prose", "candidate_contact_details",
        },
        str({o.kind for o in graph.omitted}),
    )

    # ========================================================================
    # 2. Independence blinding — hr_b owes a scorecard on the human round
    # ========================================================================
    print("\n--- independence blinding for hr_b, who owes a scorecard ---")
    async with factory() as db:
        graph_blinded = await build_graph(db, company_id=company_a, enrolment_id=enrolment_id, viewer_user_id=hr_b)
    iv1_card_node = next(
        n for n in graph_blinded.nodes if n.kind == "human_scorecard" and n.source.id == str(iv1_card_id)
    )
    check(
        "hr_b, who owes a scorecard on this round, cannot see iv1's content",
        iv1_card_node.content is None and iv1_card_node.content_hidden_reason
        and "independence" in iv1_card_node.content_hidden_reason,
        str(iv1_card_node),
    )
    check("blinded_round_count is 1 for hr_b", blinded_round_count(graph_blinded) == 1)
    iv1_card_node_open = next(
        n for n in graph.nodes if n.kind == "human_scorecard" and n.source.id == str(iv1_card_id)
    )
    check("hr_a, who owes nothing, sees the same card in full", iv1_card_node_open.content is not None)

    # ========================================================================
    # 3. Trace — the hire, and its reversal
    # ========================================================================
    print("\n--- trace(): the hire decision ---")
    async with factory() as db:
        latest_id = await latest_decision_id(db, company_id=company_a, enrolment_id=enrolment_id)
    check("latest_decision_id is the reversal, not the original hire", latest_id == decision_reject_id,
          f"{latest_id} vs {decision_reject_id}")

    async with factory() as db:
        graph_for_trace = await build_graph(db, company_id=company_a, enrolment_id=enrolment_id, viewer_user_id=hr_a)
        decision_row = await resolve_decision(db, company_id=company_a, decision_id=decision_hire_id)
    hire_trace = decision_trace_from_graph(graph_for_trace, decision=decision_row, decision_id=decision_hire_id)
    check("the hire trace is not a reversal", hire_trace.decision.reversal is False)
    check("exactly one other decision (the reversal)", len(hire_trace.other_decisions) == 1
          and hire_trace.other_decisions[0].reversal is True, str(hire_trace.other_decisions))
    corrected_original = next(
        (i for i in hire_trace.evidence if i.node.source.id == str(iv1_card_id)), None
    )
    check("the original scorecard is available at decision time, flagged for its later correction",
          corrected_original is not None and corrected_original.changed_after_decision == "corrected after the decision",
          str(corrected_original))
    after_ids = {a.node.kind for a in hire_trace.after_decision}
    check("the correction itself is in after_decision, not evidence", "human_scorecard" in after_ids
          and any(a.node.source.id != str(iv1_card_id) for a in hire_trace.after_decision), str(after_ids))
    check(
        "the offer, sent after the hire, is available at the hire decision",
        any(i.node.kind == "offer" for i in hire_trace.evidence),
    )
    check("ai_involvement counts the AI interview evidence", hire_trace.ai_involvement.ai_produced_evidence >= 1,
          str(hire_trace.ai_involvement))

    print("\n--- edges: decision -> human_scorecard -> interview_session, walked purely through edges[] ---")
    decision_node_id = f"decision:{decision_hire_id}"
    scorecard_node_id = f"human_scorecard:{iv1_card_id}"
    session_node_id = f"interview_session:{session_row_id}"
    by_from: dict[str, list[Any]] = {}
    for e in hire_trace.edges:
        by_from.setdefault(e.from_, []).append(e)
    hop1 = [e for e in by_from.get(decision_node_id, []) if e.to == scorecard_node_id]
    check(
        "edges[]: decision -available_at_decision-> human_scorecard",
        bool(hop1) and hop1[0].kind == "available_at_decision", str(hop1),
    )
    hop2 = [e for e in by_from.get(scorecard_node_id, []) if e.to == session_node_id]
    check(
        "edges[]: human_scorecard -originates_from-> interview_session",
        bool(hop2) and hop2[0].kind == "originates_from", str(hop2),
    )
    check(
        "no available_at_decision edge points at the correction (it is after the decision)",
        not any(
            e.kind == "available_at_decision" and e.to.startswith("human_scorecard:")
            and e.to != scorecard_node_id
            for e in hire_trace.edges
        ),
    )

    print("\n--- trace(): the reversal ---")
    async with factory() as db:
        decision_row_2 = await resolve_decision(db, company_id=company_a, decision_id=decision_reject_id)
    reject_trace = decision_trace_from_graph(graph_for_trace, decision=decision_row_2, decision_id=decision_reject_id)
    check("the reversal trace says reversal=true", reject_trace.decision.reversal is True)
    check("the reversal sees the correction as available (it now precedes the reversal)",
          any(i.node.source.id != str(iv1_card_id) and i.node.kind == "human_scorecard"
              for i in reject_trace.evidence),
          str([i.node.source.id for i in reject_trace.evidence if i.node.kind == "human_scorecard"]))

    # ========================================================================
    # 4. Tenant isolation
    # ========================================================================
    print("\n--- tenant isolation ---")
    try:
        async with factory() as db:
            await build_graph(db, company_id=company_b, enrolment_id=enrolment_id, viewer_user_id=hr_a)
        check("company B cannot read company A's enrolment", False)
    except EvidenceGraphError as exc:
        check("company B cannot read company A's enrolment", exc.status_code == 404)

    async with factory() as db:
        foreign_decision = await resolve_decision(db, company_id=company_b, decision_id=decision_hire_id)
    check("company B's resolve_decision sees nothing", foreign_decision is None)

    # ========================================================================
    # 5. HTTP routes
    # ========================================================================
    print("\n--- HTTP: the two routes, role gates, and the audit rows ---")
    from shared.auth.base import User

    from app.database import get_db_session
    from app.dependencies import get_current_user, get_hr_company
    from app.main import app

    async def _db_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _db_override
    app.dependency_overrides[get_hr_company] = lambda: (hr_a, company_a)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://smoke") as c:
        r = await c.get(f"/hr/enrolments/{enrolment_id}/evidence-graph")
        check("GET evidence-graph -> 200", r.status_code == 200, r.text[:200])
        body = r.json()
        check("the response carries schema_version 1", body.get("schema_version") == 1)
        check("candidate name is present and not erased", body["candidate"]["name"] == "Asha Applicant"
              and body["candidate"]["erased"] is False)
        check(
            "the base graph's edges[] carry available_at_decision edges from both decisions",
            {e["kind"] for e in body["edges"]} >= {"part_of", "originates_from", "available_at_decision"},
            str({e["kind"] for e in body["edges"]}),
        )

        r2 = await c.get(f"/hr/decisions/{decision_hire_id}/trace")
        check("GET decision trace -> 200", r2.status_code == 200, r2.text[:200])
        trace_body = r2.json()
        check("the trace response is not a reversal for the hire", trace_body["decision"]["reversal"] is False)
        wire_hop1 = [
            e for e in trace_body["edges"]
            if e["from"] == f"decision:{decision_hire_id}" and e["to"] == f"human_scorecard:{iv1_card_id}"
        ]
        check(
            "GET trace's own JSON: decision -available_at_decision-> human_scorecard",
            bool(wire_hop1) and wire_hop1[0]["kind"] == "available_at_decision", str(wire_hop1),
        )
        wire_hop2 = [
            e for e in trace_body["edges"]
            if e["from"] == f"human_scorecard:{iv1_card_id}" and e["to"] == f"interview_session:{session_row_id}"
        ]
        check(
            "GET trace's own JSON: human_scorecard -originates_from-> interview_session",
            bool(wire_hop2) and wire_hop2[0]["kind"] == "originates_from", str(wire_hop2),
        )

        async with factory() as db:
            audit_rows = (
                await db.execute(
                    text(
                        "SELECT action, details FROM audit_log"
                        " WHERE action IN ('evidence.graph_viewed', 'evidence.trace_viewed')"
                        " ORDER BY event_ts"
                    )
                )
            ).all()
        check("both audit actions were written", {r[0] for r in audit_rows} == {
            "evidence.graph_viewed", "evidence.trace_viewed"
        }, str(audit_rows))
        check(
            "neither audit row carries a name",
            all("Asha" not in json.dumps(r[1]) for r in audit_rows), str(audit_rows),
        )

        print("\n--- another company's ids 404 over HTTP ---")
        app.dependency_overrides[get_hr_company] = lambda: (uuid.uuid4(), company_b)
        r3 = await c.get(f"/hr/enrolments/{enrolment_id}/evidence-graph")
        check("company B's HR gets 404 on the enrolment", r3.status_code == 404, str(r3.status_code))
        r4 = await c.get(f"/hr/decisions/{decision_hire_id}/trace")
        check("company B's HR gets 404 on the decision", r4.status_code == 404, str(r4.status_code))
        app.dependency_overrides[get_hr_company] = lambda: (hr_a, company_a)

        print("\n--- role gates ---")
        del app.dependency_overrides[get_hr_company]
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=str(interviewer_x), full_name="Ivy X", email="ivx@e5.test", roles=["interviewer"]
        )
        r5 = await c.get(f"/hr/enrolments/{enrolment_id}/evidence-graph")
        check("an interviewer gets 403", r5.status_code == 403, str(r5.status_code))
        app.dependency_overrides[get_current_user] = lambda: User(
            user_id=str(sa_x), full_name="Sam SuperAdmin", email="sa_x@e5.test", roles=["super_admin"]
        )
        r6 = await c.get(f"/hr/enrolments/{enrolment_id}/evidence-graph")
        check("a super_admin gets 403", r6.status_code == 403, str(r6.status_code))
        del app.dependency_overrides[get_current_user]
        app.dependency_overrides[get_hr_company] = lambda: (hr_a, company_a)

    # ========================================================================
    # 6. The copilot tool
    # ========================================================================
    print("\n--- the get_decision_trace tool ---")
    from shared.agents import ToolContext

    from app.agents.tools import registry

    async with factory() as db:
        ctx = ToolContext(actor_id=str(hr_a), role="hr_manager", company_id=str(company_a), resources={"db": db})
        result = await registry.invoke(
            "get_decision_trace", {"application_id": str(enrolment_id), "decision": "latest"}, ctx,
            call_id="c1",
        )
        check("the tool call succeeds", result.ok, result.error or "")
        data = json.loads(result.content)
        check("the tool reports the latest (reversal) decision", data["decision"]["reversal"] is True,
              str(data.get("decision")))
        check("the tool caps evidence at 40 items", len(data["evidence"]) <= 40)
        check("no check-in data anywhere in the tool payload", "checkin" not in result.content.lower()
              and "check_in" not in result.content.lower())
        check("the tool cites at least one interviewer_scorecard and one decision",
              {c.kind for c in result.citations} >= {"interviewer_scorecard", "decision"},
              str({c.kind for c in result.citations}))

        # Independence carries into the tool too.
        ctx_blinded = ToolContext(
            actor_id=str(hr_b), role="hr_manager", company_id=str(company_a), resources={"db": db}
        )
        result_blinded = await registry.invoke(
            "get_decision_trace", {"application_id": str(enrolment_id), "decision": "latest"}, ctx_blinded,
            call_id="c2",
        )
        blinded_data = json.loads(result_blinded.content)
        blinded_cards = [e for e in blinded_data["evidence"] if e["kind"] == "human_scorecard"]
        check(
            "hr_b's tool call sees no content on iv1's scorecard either",
            all(c.get("content") is None for c in blinded_cards) if blinded_cards else True,
            str(blinded_cards),
        )

        # Foreign / unknown application.
        result_foreign = await registry.invoke(
            "get_decision_trace", {"application_id": str(uuid.uuid4())}, ctx, call_id="c3",
        )
        check(
            "an unknown application_id answers with the same 'no such application' shape",
            "no such application" in json.loads(result_foreign.content).get("error", ""),
            result_foreign.content,
        )

    # ========================================================================
    # 7. AR-5: an erased candidate's decision rationale is withheld everywhere
    # ========================================================================
    print("\n--- AR-5: erased candidate, decision rationale withheld ---")
    reject_reason_text = "Reference check raised a concern."
    async with factory() as db:
        await db.execute(
            text("UPDATE applicants SET full_name = '[redacted]', email = NULL WHERE id = :a"),
            {"a": applicant_id},
        )
        # The erasure_executor.py shape exactly: summary/evidence nulled,
        # redacted_at stamped, scores untouched (they survive by design).
        await db.execute(
            text(
                "UPDATE interviewer_scorecards SET summary = NULL, redacted_at = :n,"
                " updated_at = :n WHERE id = :i"
            ),
            {"i": correction_id, "n": NOW},
        )
        await db.execute(
            text(
                "UPDATE interviewer_scorecard_scores SET evidence = NULL, updated_at = :n"
                " WHERE scorecard_id = :i"
            ),
            {"i": correction_id, "n": NOW},
        )
        await db.commit()

    async with factory() as db:
        erased_graph = await build_graph(
            db, company_id=company_a, enrolment_id=enrolment_id, viewer_user_id=hr_a
        )
    check("candidate.erased is now true", erased_graph.candidate.erased is True)

    redacted_card = next(
        n for n in erased_graph.nodes
        if n.kind == "human_scorecard" and n.source.id == str(correction_id)
    )
    check(
        "a redacted scorecard's lifecycle alone says so — content stays populated",
        redacted_card.lifecycle == "redacted" and redacted_card.content is not None
        and redacted_card.content_hidden_reason is None,
        f"lifecycle={redacted_card.lifecycle} content_hidden_reason={redacted_card.content_hidden_reason}",
    )
    check(
        "the redacted scorecard's summary is gone but its scores survive",
        redacted_card.content["summary"] is None and redacted_card.content["scores"],
        str(redacted_card.content),
    )
    decision_node = next(
        n for n in erased_graph.nodes
        if n.kind == "decision" and n.source.id == str(decision_reject_id)
    )
    check(
        "the decision node's reason is withheld, but content is not entirely null",
        decision_node.content is not None and decision_node.content.get("reason") is None
        and decision_node.content_hidden_reason is not None,
        str(decision_node.content) + " / " + str(decision_node.content_hidden_reason),
    )
    check(
        "reason_code and reason_label survive erasure (the taxonomy, not personal data)",
        decision_node.content.get("reason_code") == "skills_fit"
        and decision_node.content.get("reason_label") is not None,
    )
    check(
        "omitted[] names the withheld decision reason",
        any(o.kind == "decision_reason" for o in erased_graph.omitted), str(erased_graph.omitted),
    )

    async with factory() as db:
        erased_decision_row = await resolve_decision(
            db, company_id=company_a, decision_id=decision_reject_id
        )
    erased_trace = decision_trace_from_graph(
        erased_graph, decision=erased_decision_row, decision_id=decision_reject_id
    )
    check("the trace's own decision.reason is None once erased", erased_trace.decision.reason is None)
    check("the trace's reason_label survives erasure", erased_trace.decision.reason_label is not None)
    check(
        "the trace's omitted[] also names the withheld reason",
        any(o.kind == "decision_reason" for o in erased_trace.omitted),
    )

    async with AsyncClient(transport=transport, base_url="http://smoke") as c2:
        r7 = await c2.get(f"/hr/enrolments/{enrolment_id}/evidence-graph")
        check("GET evidence-graph is still 200 for an erased candidate", r7.status_code == 200, str(r7.status_code))
        body7 = r7.json()
        check(
            "the erased candidate's name reads [redacted] over HTTP",
            body7["candidate"]["name"] == "[redacted]" and body7["candidate"]["erased"] is True,
        )
        decision_wire = next(
            n for n in body7["nodes"]
            if n["kind"] == "decision" and n["source"]["id"] == str(decision_reject_id)
        )
        check("the wire decision node's reason is null", decision_wire["content"]["reason"] is None)
        check(
            "the reject reason's actual prose never appears anywhere in the graph response",
            reject_reason_text not in r7.text,
        )

        r8 = await c2.get(f"/hr/decisions/{decision_reject_id}/trace")
        check("GET trace is still 200 for an erased candidate", r8.status_code == 200, str(r8.status_code))
        trace8 = r8.json()
        check("the wire trace's decision.reason is null", trace8["decision"]["reason"] is None)
        check(
            "the reject reason's actual prose never appears anywhere in the trace response",
            reject_reason_text not in r8.text,
        )

    async with factory() as db:
        ctx_erased = ToolContext(
            actor_id=str(hr_a), role="hr_manager", company_id=str(company_a), resources={"db": db}
        )
        result_erased = await registry.invoke(
            "get_decision_trace", {"application_id": str(enrolment_id), "decision": "latest"},
            ctx_erased, call_id="c4",
        )
        check("the tool call still succeeds for an erased candidate", result_erased.ok, result_erased.error or "")
        check(
            "the reject reason's actual prose never reaches the model",
            reject_reason_text not in result_erased.content,
        )
        erased_tool_data = json.loads(result_erased.content)
        check("the tool's own decision.reason is null", erased_tool_data["decision"]["reason"] is None)

    app.dependency_overrides.clear()
    await eng.dispose()
    print(f"\n{'=' * 60}\n  {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("  FAILED:", ", ".join(FAIL))
    print("=" * 60)
    raise SystemExit(1 if FAIL else 0)


asyncio.run(main())
