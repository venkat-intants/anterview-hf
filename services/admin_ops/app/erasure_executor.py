"""DPDP right-to-erasure executor — S5-004 (enforcement layer).

This module implements the *actual* PII deletion that the erasure endpoint
only schedules.  It runs as an in-process periodic task started during
``lifespan`` startup, and is safe to run on every deployed instance because
it claims rows with ``SELECT … FOR UPDATE SKIP LOCKED`` inside a single
transaction — two concurrent instances never double-process the same request.

Execution model
---------------
Every ``ERASURE_POLL_INTERVAL_SECONDS`` (default 300 s / 5 min) the task
wakes up, opens a session-factory session, and processes any erasure_requests
rows where:
  - status = 'pending'
  - scheduled_for <= NOW() UTC

For each claimed request (one at a time, SKIP LOCKED) it:

  1. COLLECTS every S3 / R2 object key first, before any DELETE:
       - resumes.resume_s3_key      (all versions, uploads bucket)
       - users.resume_s3_key        (uploads bucket)
       - applicants.resume_s3_key   (uploads bucket)
       - scorecards.report_pdf_key + transcript_key (scorecard bucket)
       - turns.audio_s3_key         (scorecard bucket; NULL until voice ships)
     Collection MUST precede deletion. It used to run the other way round, and
     because "DELETE FROM resumes" had already removed the rows inside the same
     transaction, every superseded resume PDF was orphaned in R2 while the
     executor stamped status='completed'. Under DPDP §12 a false completion
     claim is worse than an incomplete erasure that reports itself.
  2. Hard-deletes interview transcript turns for the user's sessions
     (via DELETE FROM turns WHERE session_id IN (SELECT id FROM sessions
      WHERE user_id = :uid) — turns.text_content is candidate speech PII).
  3. Hard-deletes resume version rows (resumes table — resume_text is PII).
  4. Hard-deletes scorecards for the user's sessions.
  5. Hard-deletes the sessions rows themselves (was soft-deleted on request;
     turns are already gone so the cascade is safe, but we delete explicitly).
  5b. Hard-deletes the user's in-app notifications. notifications.user_id is
     ON DELETE CASCADE, but the cascade never fires: step 7 anonymises the
     users row instead of deleting it (erasure_requests.user_id is ON DELETE
     RESTRICT). The rows carry a free-text title/body addressed to the person
     by name, so leaving them is leaving PII behind.
  6. Anonymises applicant rows linked to this user (full_name, email,
     resume_text, resume_s3_key, embedding, phone, years_experience,
     current_company, current_title, linkedin_url, github_url,
     parsed_full_name, parsed_email → redacted / NULL; user_id NULL).
     ``embedding`` is a halfvec(3072) derived from resume_text — leaving it
     behind keeps a dense representation of the erased CV and keeps the
     applicant semantically searchable via GET /hr/applicants?q=.
  7. Anonymises users columns in-place:
       email        → 'erased_{user_id}@deleted.invalid'
       full_name    → '[redacted]'
       phone        → NULL
       resume_text  → NULL
       resume_s3_key → NULL
       password_hash → NULL
       naipunyam_id  → NULL
       linkedin_url  → NULL
       github_url    → NULL
       avatar_url    → NULL
       headline      → NULL
       bio           → NULL
       official_email → NULL
  8. DELETES every object key collected in step 1 from S3/R2 storage:
       - scorecard PDFs, transcript JSON and turn audio (scorecard bucket)
       - resume PDFs from users, all resume versions, and applicant rows
         (uploads bucket), deduplicated
     Only proceeds to step 9 when ALL deletes succeed (or the key was
     already absent from the bucket).  If any delete fails — including
     "object storage is not configured", which is a failure and not a
     no-op — the transaction is rolled back and the row stays in 'pending'
     for the next poll cycle.
  9. Marks the erasure_request row: status='completed', completed_at=NOW(),
     artifacts=<summary dict>.
 10. Writes an audit_log entry with action='dpdp_erasure_completed'.

All ten steps happen inside a SINGLE DB transaction per request plus an S3
delete phase (step 8) that runs BEFORE the DB commit.  If the S3 delete
raises an exception the DB transaction is rolled back, the row is left in
'pending' (it will be retried next poll cycle), and the error is logged.

PII safety
----------
- User email / name / phone NEVER appear in any log line.
- Only user_id and request_id appear in log events.
- The executor itself does not log PII at any severity level.
- S3 object keys contain only UUIDs / scorecard IDs — no direct PII.

DPDP Act 2023 compliance note
------------------------------
§12(4): erasure must be completed within a "reasonable time" after the grace
period.  This executor fires every 5 minutes so completion happens within 5
minutes of the 30-day scheduled_for timestamp reaching NOW().

§12 false-claim prevention: the executor will NOT stamp status='completed'
unless ALL of the following have succeeded:
  a) All DB PII rows have been deleted / anonymised (steps 1-7).
  b) All collected S3 / R2 object keys have been physically deleted (step 8),
     as counted by the storage layer itself — ``delete_objects`` returns how
     many objects it removed and the executor compares that against how many
     keys it collected.
  If any S3 delete fails the executor rolls back the DB transaction and leaves
  the request in 'pending' so it will be retried on the next poll cycle.
  "S3 is not configured" is one of those failures. It used to be a silent
  no-op that still stamped 'completed' with a non-zero object count, because
  the unconfigured skip lived inside ``delete_objects`` and returned None
  exactly like a successful delete did. Missing credentials are an operator
  error to fix, not an erasure to claim.

The table inventory (DPDP-7)
----------------------------
This section used to be three bullet points headed "Tables NOT reached (flagged
for review)". Three is not the number of tables in the schema — it was the
number someone had thought about. Everything else was neither erased nor
declared, which is the state DPDP §12 does not forgive: an incomplete-but-
declared exclusion is defensible, an undeclared one is not.

The inventory therefore lives in ``ERASED_TABLES`` and ``EXCLUDED_TABLES``
below, as data rather than prose, and ``tests/test_erasure_inventory.py``
asserts that the two together partition every table the schema of record
declares — ``services/data_gateway/app/models.py`` plus every ``op.create_table``
in ``services/data_gateway/alembic/versions/``. Add a table in a migration and
that test fails until someone writes down which side it belongs on. A one-time
audit would have been correct on the day it was written and wrong by the next
migration; this cannot go stale without going red.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.code_redaction import redact_coding_answers, redact_graded_snapshot
from app.models import AuditLog, ErasureRequest
from app.s3_client import StorageNotConfiguredError

if TYPE_CHECKING:
    from app.config import Settings

log = structlog.get_logger(__name__)


class ErasureIncompleteError(RuntimeError):
    """Fewer objects were deleted than the erasure collected keys for.

    Raised so the caller rolls back and the request stays 'pending'. It exists
    to make the shortfall a *typed* failure rather than a smaller number in the
    artifacts record that nobody reads until an auditor does.
    """


# How often the executor wakes up and checks for due erasure requests.
# Configurable via the settings object passed at startup.
ERASURE_POLL_INTERVAL_SECONDS: int = 300  # 5 minutes


# ---------------------------------------------------------------------------
# The DPDP §12 table inventory (DPDP-7)
#
# Every table in the schema of record appears in exactly one of the two maps
# below. Both are keyed by table name; the value is the reason, written for the
# auditor who asks "and what happened to THIS one?" — which is the question the
# old three-bullet prose list could not answer.
#
# The partition is enforced by tests/test_erasure_inventory.py against
# services/data_gateway/app/models.py AND services/data_gateway/alembic/versions/.
# Both, not just the ORM: `integrity_events` and `feature_flags` exist only in
# migrations, so a models-only check would have declared the inventory complete
# while two user-linked tables were missing from it.
# ---------------------------------------------------------------------------

#: Tables this executor deletes from or anonymises. The value names the step.
ERASED_TABLES: dict[str, str] = {
    "offer_codes": "PH4-A3 — one-time codes sent to the candidate to accept or decline; "
                   "deleted in step 5f.",
    "offer_sessions": "PH4-A4 — hashed, hour-long preboarding sessions a candidate opened "
                      "with a code; deleted in step 5f.",
    "hrms_exports": "PH4-A4 — signed HRMS payloads carrying the candidate's name, email "
                    "and compensation; deleted in step 5f (after their offer is redacted, "
                    "which is when the append-only trigger lets them go).",
    "candidate_documents": "PH4-A4 — identity and other preboarding documents. The FILES "
                           "are collected in step 1 and deleted in step 8; the rows are "
                           "redacted in 5f (key, file name and review note cleared).",
    "users": "step 7 — anonymised in place (email → sentinel, every other "
             "personal column NULLed). Not deleted, because "
             "erasure_requests.user_id is ON DELETE RESTRICT and that row is "
             "the §12 proof the erasure happened.",
    "sessions": "step 5 — hard-deleted.",
    "turns": "step 2 — hard-deleted; text_content is candidate speech.",
    "scorecards": "step 4 — hard-deleted; PDF + transcript objects go in step 8.",
    "resumes": "step 3 — hard-deleted, every version, objects in step 8.",
    "notifications": "step 5b — hard-deleted; title/body address the person by name.",
    "applicants": "step 6 — anonymised (name/email/resume_text/embedding/user_id). "
                  "The row survives as the company's structural ATS record with "
                  "nothing left that identifies a person.",
    "integrity_events": "purged at REQUEST time by routers/erasure.py, not here: "
                        "gaze/face-derived proctoring signals are too sensitive to "
                        "sit through the 30-day grace window. Step 5 then cascades "
                        "any row written between request and execution.",
    "application_answers": "step 5c — hard-deleted. `answer` is prose the "
                           "CANDIDATE wrote, so unlike exam_attempts it keeps "
                           "identifying them after applicants is anonymised. "
                           "Deleted, not redacted: here the content IS the "
                           "personal data, with no structural residue to keep.",
    "application_drafts": "step 5e — hard-deleted, matched three genuinely "
                          "independent ways: the draft's user_id, the erased "
                          "user's address, and the address of any applicant "
                          "linked to them. So a draft whose user_id was "
                          "re-pointed by activation-linking (or was not) cannot "
                          "escape. MUST run before step 7, which overwrites "
                          "users.email. NOTE the one over-match we accept: a "
                          "genuinely shared mailbox means erasing one person "
                          "deletes another's in-progress draft. Data loss, not "
                          "disclosure, and the alternative is leaving personal "
                          "data behind. A half-finished application "
                          "holds the person's name, email, phone and CV, and "
                          "unlike `applicants` there is no structural record "
                          "worth keeping: nobody applied. Keyed on user_id "
                          "directly rather than through applicants, because a "
                          "draft that was never submitted has no applicant row "
                          "to be reached through. The CV object goes in step 8.",
    "upload_items": "step 5d — filename, s3_key and error redacted for the files that "
                    "became this person's applicant rows. HR names CVs after the "
                    "candidate, so the original filename is personal data; the "
                    "object itself is the applicant's resume_s3_key (step 1c).",
    "interviewer_notes": "step 5f — hard-deleted. An interviewer's private working "
                         "notes about the candidate: prose, never shown to HR, never "
                         "part of the submitted evidence (PH4-A5), so there is no "
                         "structural residue worth keeping.",
    "candidate_accommodations": "PH4-D2 — a recorded adjustment (extra time, a deadline "
                                "extension, relaxed auto-submit, or a free-text 'other' "
                                "adjustment) and two notes about the candidate "
                                "(interviewer-facing and HR-only). Kept as the company's "
                                "record that an adjustment applied — the numbers, on the "
                                "scorecard precedent — but in step 5g every active row is "
                                "REVOKED and both notes are REDACTED to NULL or "
                                "'[redacted]'.",
    "exam_attempts": "PH4-D3 — moved here from EXCLUDED_TABLES. Selections, timestamps and "
                     "the score are the company's structural assessment record against an "
                     "applicant row step 6 anonymises next (the reason it was excluded "
                     "before); a coding submission's SOURCE and its program's OUTPUT are "
                     "the candidate's own text, on the interviewer_notes precedent, and are "
                     "not. Step 5h redacts `answers.coding[*].source` and "
                     "`graded_snapshot.coding[*].tests[*].{actual_output,stderr}`, stamping "
                     "`code_redacted_at` — the ONE shape `exam_attempts_submission_frozen` "
                     "permits on an already-submitted/expired attempt. The row, and its "
                     "score, is otherwise kept.",
    "code_quality_reports": "PH4-D3 — a static-analysis pass over one coding answer: "
                            "metrics and smells computed FROM the candidate's source, "
                            "which step 5h has just redacted from the attempt it describes. "
                            "Deleted outright in step 5h — there is no structural residue "
                            "worth keeping once the source it analysed is gone.",
    "code_fingerprints": "PH4-D3 — winnowed hashes of one submission's normalised token "
                        "stream. Deleted outright in step 5h alongside the reports.",
    "code_similarity_signals": "PH4-D3 — SYSTEM evidence that a pair of submissions (or a "
                               "submission and the question's reference solution) "
                               "overlapped. Deleted outright in step 5h: it names an "
                               "erased attempt on one or both sides, and the evidence it "
                               "summarises is gone with the source.",
    "code_integrity_findings": "PH4-D3 — a NAMED HR manager's own judgement call, kept as "
                               "the company's record that a review happened and what it "
                               "concluded (outcome), on the interviewer_scorecards "
                               "precedent. Step 5h redacts only `rationale` to "
                               "'[redacted]', which can quote or name the candidate; "
                               "`outcome`, who recorded it and when are kept.",
    "task_submissions": "PH4-D4 — a candidate's job-simulation/portfolio attempt: status, "
                        "timing and the allowance snapshot are the company's structural "
                        "assessment record, on the exam_attempts precedent, and are kept. "
                        "Step 5i withdraws any row still open, then redacts every row of "
                        "theirs: token_hash cleared and redacted_at stamped, together, in "
                        "one statement — the only shape task_submissions_lifecycle permits "
                        "once a row is not open.",
    "task_responses": "PH4-D4 — what the candidate actually wrote, linked or uploaded: this "
                      "IS the personal data, with no structural residue worth keeping, on "
                      "the application_answers precedent. Step 5i clears text_value, "
                      "link_url, title, description, storage_key and original_name and "
                      "stamps redacted_at; the FILE a storage_key named is deleted in step 8.",
    "hire_checkins": "PH5-D5-2 — a 90-day post-hire outcome: employment/left_reason/"
                     "performance, no free text and no applicant_id (reached only through "
                     "enrolment_id -> enrolments.applicant_id). DPDP review decided this one "
                     "differently from round_results/exam_attempts: rather than keep a "
                     "structural record against the anonymised applicant, step 5j DELETES "
                     "every row outright. Ordinary 24-month retention "
                     "(app.hire_checkins.purge) deletes what erasure does not reach sooner.",
}

#: Tables deliberately left standing, each with the reason it is defensible.
EXCLUDED_TABLES: dict[str, str] = {
    # --- Records that exist to prove the erasure / prior consent -----------
    "erasure_requests": "the §12 record of this very erasure. Deleting it would "
                        "destroy the evidence that the request was honoured.",
    "audit_log": "immutable compliance trail: actor and resource UUIDs, action "
                 "names and structured details — never email or phone. NOTE, "
                 "because this used to claim 'action names only': details are "
                 "not always free of prose. Scorecard rows (PH4-A1) record only "
                 "whether a correction or withdrawal reason was given and its "
                 "length, never the text. But final-decision rows "
                 "(enrolment.decision.*, applicant.decision.*) carry HR's "
                 "free-text rationale, which can describe the candidate. It is "
                 "kept as the D-05 record of why a person decided, and the "
                 "applicant it concerns is anonymised in step 6; it is not "
                 "redacted here. Recorded in docs/ACCEPTED-RISKS.md (AR-5).",
    "dpdp_consent_ledger": "the consent record is the legal basis for the "
                           "processing that already happened; §7 requires being "
                           "able to demonstrate it. The request path stamps "
                           "revoked_at, so the row records a withdrawn consent "
                           "pointing at an anonymised user.",
    # --- Owned by another service's retention path -------------------------
    "email_events": "to_email / to_user_id are candidate data, but this is "
                    "data_gateway's outbox and admin_ops does not own it. The "
                    "DPDP §8(7) retention cron there deletes rows on a 90-day "
                    "roll. Reaching across the service boundary to delete them "
                    "here would race that cron's own transaction.",
    # --- User-linked but carrying no personal data -------------------------
    "auth_tokens": "single-use HMAC hashes for reset / verify, TTL ≤ 24 h. The "
                   "raw token never existed in the DB, and the soft-delete at "
                   "request time already blocks redemption.",
    "user_roles": "a role grant is a role id plus a timestamp. The anonymised "
                  "users row is retained, so its grants are retained with it; "
                  "login is impossible anyway (password_hash NULL, deleted_at set).",
    "feature_flags": "platform toggles. updated_by points at whichever operator "
                     "last flipped the flag — never a candidate.",
    # --- Structural / catalogue data owned by the company or platform ------
    "companies": "tenant record. created_by_user_id is provenance and now "
                 "resolves to the anonymised users row.",
    "jobs": "job catalogue. created_by_user_id likewise; sessions reference jobs "
            "ON DELETE RESTRICT, so the catalogue outlives its sessions by design.",
    "roles": "reference data — role names. No user column.",
    "nos_competencies": "NOS competency catalogue. Reference data, no user column.",
    "exams": "company-authored assessment content. No candidate column.",
    "exam_rounds": "company-authored assessment content. No candidate column.",
    "exam_sections": "company-authored assessment content. No candidate column.",
    "exam_questions": "company-authored assessment content. No candidate column.",
    "coding_questions": "company-authored assessment content. No candidate column.",
    "question_banks": "PH4-D1 — a company's reusable question library. created_by_user_id "
                      "is HR staff; no candidate column.",
    "bank_questions": "PH4-D1 — one version of a reusable question: prompt, options or test "
                      "cases, difficulty, tags. Company-authored content, on the exam_questions "
                      "precedent. created_by_user_id / submitted_by_user_id / "
                      "reviewed_by_user_id / retired_by_user_id are HR staff, not candidates, and "
                      "review_note is a reviewer's comment on the question itself.",
    "bank_question_events": "PH4-D1 — append-only history of a bank question (created, "
                            "submitted, approved, copied into an exam, ...): action, actor "
                            "(HR staff) and facts (ids), never question text.",
    "round_tasks": "PH4-D4 — a company's authored task-round configuration: brief, items, "
                   "portfolio settings. Company-authored content, the exam_questions "
                   "precedent; no candidate column.",
    "round_task_materials": "PH4-D4 — HR's own reference attachments for a task round. "
                            "Company-authored content; no candidate column. The object stays "
                            "in storage — it names no candidate, and a clone shares its key "
                            "with the original (workflows.clone_for_edit), so deleting it "
                            "here could break another round's copy.",
    "task_events": "PH4-D4 — append-only history of a task submission (issued, started, "
                   "saved, submitted, ...) or a round's configuration edit: action, actor "
                   "and facts (ids, which fields), never response content. The submission "
                   "and round it references may be redacted or gone; the append-only "
                   "trigger already lets a DELETE through once its FK target is.",
    # --- Candidate-DERIVED, but reached through applicants ------------------
    # These three are the judgement call in this list, so the reasoning is
    # written out rather than asserted: they hang off `applicants`, which step 6
    # anonymises rather than deletes. Once full_name is '[redacted]', email is
    # NULL and user_id is NULL, an assignment or a proctoring event belongs to
    # an applicant that identifies nobody — they are the COMPANY's assessment
    # record, not the erased user's. Deleting them would destroy a fiduciary's
    # own hiring evidence to no privacy gain. This is why they differ from
    # `integrity_events` above, which hangs off `sessions` — a session is the
    # user's own practice run and is hard-deleted, so its events go with it.
    # (`exam_attempts` itself used to be the fourth here; PH4-D3 moved it to
    # ERASED_TABLES because a coding submission's source is NOT covered by
    # this argument, even though everything else about the row still is.)
    "exam_assignments": "keys off applicants (anonymised in step 6); holds a "
                        "token hash and a schedule, no personal data.",
    "exam_integrity_events": "proctoring events for an exam_attempts row, which PH4-D3's "
                             "step 5h redacts (coding source) but does not delete — the "
                             "row this table's FK points at still exists. Event type + "
                             "timestamp only; raw camera/keystroke input never leaves the "
                             "browser.",
    "interview_invites": "keys off applicants/company. guest_user_id and "
                         "created_by_user_id resolve to anonymised users rows, "
                         "and session_id nulls itself (ON DELETE SET NULL) when "
                         "step 5 deletes the session.",
    "enrolments": "the company's assessment record for one applicant against "
                  "one requisition, on the exam_attempts precedent: it hangs "
                  "off applicants, which step 6 anonymises. The ats_* columns "
                  "are AI prose ABOUT the candidate, but they describe an "
                  "applicant row that by then names nobody, and they are the "
                  "fiduciary's own evidence for a hiring decision. NOTE the "
                  "one thing that is NOT covered by that argument: "
                  "scored_resume_s3_key points at a real resume object, so "
                  "step 1c-ii collects it for deletion in step 8. Excluding "
                  "the row must not mean orphaning the file.",
    "round_results": "per-round scores for an enrolment — score, percent, "
                     "criterion_scores, axes. Numbers and competency ids "
                     "against an anonymised applicant; same reasoning as "
                     "exam_attempts. grader_user_id is the HR grader, not the "
                     "candidate.",
    "stage_transitions": "the audit trail of who moved a candidate between "
                         "statuses and whether a human or the workflow did it. "
                         "Status enums, timestamps, the ACTOR's user id and, "
                         "for a final decision, a reason category (PH4-O4 "
                         "reason_code / reason_label — company taxonomy, no "
                         "personal data). No candidate column beyond "
                         "enrolment_id. NOTE: `reason` is free text a person "
                         "wrote and can describe the candidate; it is kept, "
                         "not redacted — docs/ACCEPTED-RISKS.md AR-5. This is "
                         "the D-05 evidence that a person, not the AI, decided; "
                         "deleting it would destroy proof the platform is "
                         "required to be able to show.",
    "upload_batches": "one bulk upload: the opening, the HR uploader, a file count "
                      "and a status. No candidate column — the per-file rows, "
                      "which do carry filenames, are upload_items (erased).",

    # --- Company-authored structure and content ----------------------------
    "job_requisitions": "the opening itself — title, JD, salary band, skills. "
                        "owner_user_id / created_by_user_id are HR staff.",
    "jd_versions": "the history of one opening's advert (PH3-B3). Company-"
                   "authored content on the job_requisitions precedent, and "
                   "nothing in it describes a candidate: the columns are the "
                   "JD prose, three skill lists, a change note and the HR "
                   "author's user id. Deleting it would destroy the record of "
                   "which JD a candidate was shown when they applied, which is "
                   "evidence FOR the candidate rather than data about them.",
    "application_questions": "HR-authored screening prompts attached to a "
                             "requisition. The company's form, not anyone's "
                             "answer — the answers are erased in step 5c.",
    "workflows": "the company's hiring process for a requisition: thresholds, "
                 "automation toggles, round order. created_by_user_id is HR.",
    "workflow_rounds": "a stage within that process (title, kind, pass "
                       "threshold, time limit). No candidate column.",
    "round_criteria": "the competencies and anchors a round scores against. "
                      "Frozen rubric content, authored by the company.",

    # --- Platform bookkeeping, no user column at all -----------------------
    "reconciliation_state": "retry bookkeeping for the background reconciler "
                            "(kind, ref_id, attempt counts, last error). "
                            "ref_id can point at an applicant, but the row "
                            "carries no personal data and is transient — the "
                            "reconciler drops it once the work succeeds or "
                            "gives up.",
    "scheduled_job_runs": "one row per named cron job with its last run time "
                          "and status. Operational telemetry; no user column.",
    "scheduled_job_run_log": "history of scheduled-job runs and failed "
                             "background passes (job id, trigger, status, "
                             "error text). Operational telemetry; no user "
                             "column, pruned after 90 days.",
    "interviewer_scorecards": "PH4-A1 — the company's structured interview record: "
                              "who assessed the candidate, when, and whether it was "
                              "submitted. Kept on the round_results precedent, but "
                              "step 5f first WITHDRAWS every open assignment (so no "
                              "new prose can be written), then REDACTS all free "
                              "text: summary=NULL, and correction_reason / "
                              "withdrawn_reason set to '[redacted]', with "
                              "redacted_at stamped — prose a person writes about "
                              "the candidate can quote or name them and "
                              "re-identify an anonymised applicant.",
    "interview_kits": "PH4-A5 — per-round interviewer guidance written by HR "
                      "(instructions, what to evaluate, probes). Company "
                      "configuration keyed by round; holds no candidate data.",
    "decision_reasons": "PH4-O4 — a company's decision-reason taxonomy (code, "
                        "label, applies_to). Configuration; the decision itself "
                        "lives in stage_transitions, which carries only the code "
                        "and a label snapshot, never candidate data.",
    "interviewer_scorecard_scores": "PH4-A1 — per-criterion 1-5 scores against frozen "
                                    "competency ids for an anonymised applicant, kept "
                                    "like round_results.criterion_scores. The "
                                    "free-text `evidence` column is REDACTED to NULL "
                                    "in step 5f for the same re-identification reason "
                                    "as the scorecard summary.",
    # --- PH4 Wave 2 ---------------------------------------------------------
    "workflow_review_events": "PH4-O6 — who submitted, approved or sent back a "
                              "workflow VERSION, and their note. Company "
                              "configuration history; no candidate column.",
    "workflow_simulations": "PH4-O2 — dry-run results for a workflow version. The "
                            "candidates in them are synthetic (SIM-001, ...), "
                            "never people, so there is nothing personal to erase.",
    "workflow_stage_settings": "PH4-O1 — a stage's owner (an HR manager) and SLA in "
                               "hours. Configuration; no candidate column.",
    "stage_exceptions": "PH4-O1 — why an application could not proceed normally. "
                        "Kept as the company's operational record, but its prose "
                        "(`reason`, `resolution_note`) is REDACTED to "
                        "'[redacted]' / NULL in step 5f: a person wrote it about "
                        "the candidate and it can name them.",
    "interviewer_availability": "PH4-A2 — when an interviewer (company staff) is free. "
                                "Staff scheduling configuration; no candidate column.",
    "interviewer_capacity": "PH4-O5 — how many sessions an interviewer should carry. "
                            "Staff configuration; no candidate column.",
    "interview_loops": "PH4-A2 — a set of interviews for one application: title, "
                       "timezone, status. Kept as the company's scheduling record; in "
                       "step 5f an open loop is CANCELLED and any cancel reason is "
                       "REDACTED to '[redacted]' (a person wrote it, it can name them).",
    "interview_sessions": "PH4-A2 — one interview's time, round and place. Kept as the "
                          "scheduling record; in step 5f every session not yet held is "
                          "CANCELLED (nobody should turn up to interview an erased "
                          "person) and any cancel reason REDACTED to '[redacted]'.",
    "interview_session_interviewers": "PH4-A2 — which staff sat on a session and their "
                                      "scorecard link. Staff-side record; the scorecard "
                                      "itself is redacted in 5f.",
    "offer_templates": "PH4-A3 — a company's offer boilerplate (terms, benefits). "
                       "Company configuration; no candidate column.",
    "offers": "PH4-A3 — an offer to one application. Kept as the company's record of "
              "what was offered; in step 5f an offer still in play is WITHDRAWN, the "
              "link is killed, and every piece of prose a person wrote (acceptance "
              "name, decline / withdrawal reason, approval note) is REDACTED.",
    "offer_events": "PH4-A3 — append-only history of an offer: action, actor, time and "
                    "facts (never prose). Company record; the offer it names is redacted.",
    "document_requirements": "PH4-A4 — which documents an opening asks for. Company "
                             "configuration; no candidate column.",
    "document_events": "PH4-A4 — append-only history of a candidate's documents: action, "
                       "actor, time and facts. No file content, name or reason text.",
    "accommodation_events": "PH4-D2 — append-only history of an accommodation: action, "
                            "actor and facts (scope, which fields are present, the "
                            "target). Never the notes or the parameter values, so there "
                            "is nothing here that identifies or describes the candidate "
                            "beyond the accommodation row it names, which is redacted "
                            "in step 5g.",
}


# ---------------------------------------------------------------------------
# Core erasure logic — executes one erasure request inside an open session
# ---------------------------------------------------------------------------


async def _execute_one_erasure(
    db: AsyncSession,
    request: ErasureRequest,
    system_actor_id: uuid.UUID,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Execute all PII deletion/anonymisation steps for a single erasure request.

    MUST be called inside an already-open session.  The caller owns the
    commit / rollback decision.  Returns an artifacts dict summarising what
    was erased (written to erasure_requests.artifacts on completion).

    S3 deletion (step 8) is performed BEFORE the DB row is stamped
    'completed'.  If any S3 delete fails this function raises an exception
    so the caller rolls back the DB transaction and leaves the request in
    'pending' for retry.

    Args:
        db:               Open async DB session owned by the caller.
        request:          The ErasureRequest ORM instance to process.
        system_actor_id:  UUID used as actor_id in the audit_log entry.
        settings:         Admin-ops Settings instance — supplies S3 credentials.
                          When None, an erasure that collected NO object keys
                          still completes (there is nothing to delete); one that
                          collected keys raises, because a completion claim we
                          cannot back up is worse than a retry.

    Raises:
        SQLAlchemyError:            On any DB failure — caller rolls back.
        ClientError:                When an S3 delete call fails (non-absent
                                    key) — caller rolls back so the request
                                    stays in 'pending'.
        StorageNotConfiguredError:  Object keys were collected but storage has
                                    no Settings / no credentials.
        ErasureIncompleteError:     Storage reported fewer deletions than the
                                    keys collected.
        Exception:                  Any other unexpected error — caller rolls
                                    back.
    """
    user_id: uuid.UUID = request.user_id
    uid_str = str(user_id)

    # ------------------------------------------------------------------
    # Step 1: Collect EVERY S3 / R2 object key, BEFORE deleting any row that
    #         holds one.
    #
    # 1a. Resume S3 keys from the resumes version table (uploads bucket).
    # 1b. Resume S3 key from users.resume_s3_key (uploads bucket).
    # 1c. Resume S3 keys from applicant rows (uploads bucket).
    # 1d. Scorecard PDF + transcript keys (scorecard bucket).
    # 1e. Interview audio keys from turns (scorecard bucket).
    #
    # Ordering is load-bearing and it used to be wrong: the deletes ran first
    # and collection second, so by the time we looked for resume keys the rows
    # holding them were already gone from the transaction. Only
    # users.resume_s3_key survived, so every superseded resume PDF stayed in R2
    # while the executor stamped status='completed' and wrote a
    # dpdp_erasure_completed audit row. Under DPDP §12 a false completion claim
    # in the audit trail is worse than an incomplete erasure that reports
    # itself. The old code knew — its own comment said "In a future refactor,
    # move key collection to before step 2."
    #
    # Collecting first is also what makes retry idempotent: if the S3 phase
    # fails we roll back and retry next cycle with the same key list.
    # ------------------------------------------------------------------

    # 1a — resume S3 keys from the resumes version table. This is the one the
    # old ordering lost. A candidate who re-uploaded their CV five times has
    # five objects here and only the newest is referenced by users.
    resume_keys_result = await db.execute(
        text(
            "SELECT resume_s3_key FROM resumes "
            "WHERE user_id = :uid AND resume_s3_key IS NOT NULL"
        ),
        {"uid": uid_str},
    )
    resume_version_keys: list[str] = [
        str(row[0]) for row in resume_keys_result.fetchall() if row[0]
    ]

    # 1b — resume_s3_key from users row
    user_s3_key_result = await db.execute(
        text("SELECT resume_s3_key FROM users WHERE id = :uid"),
        {"uid": uid_str},
    )
    user_s3_key_row = user_s3_key_result.fetchone()
    user_resume_s3_key: str | None = user_s3_key_row[0] if user_s3_key_row else None

    # 1c — applicant resume objects. The applicants UPDATE nulls
    # resume_s3_key; without collecting it first that nulling just orphans the
    # object in the bucket.
    applicant_keys_result = await db.execute(
        text(
            "SELECT resume_s3_key FROM applicants "
            "WHERE user_id = :uid AND resume_s3_key IS NOT NULL"
        ),
        {"uid": uid_str},
    )
    applicant_resume_keys: list[str] = [
        str(row[0]) for row in applicant_keys_result.fetchall() if row[0]
    ]

    # 1c-ii — the SCORED copy of the same resume. enrolments.scored_resume_s3_key
    # is a second object, written by the ATS scoring path, and nothing else in
    # this function collects it: the enrolments row is excluded from erasure
    # (it is the company's assessment record against an anonymised applicant),
    # so without this the object simply stays in the bucket after a completed
    # erasure — the exact orphaning 1c exists to prevent, one table over.
    #
    # 1c-iii — the SUBMITTED copy. enrolments.applied_resume_s3_key records the
    # CV each application was sent with, and it can be an older object than both
    # the applicant's current CV and any scored one (an application still
    # waiting to be scored when the person re-applied). Same orphaning, same fix.
    scored_keys_result = await db.execute(
        text(
            "SELECT e.scored_resume_s3_key, e.applied_resume_s3_key FROM enrolments e "
            "JOIN applicants a ON a.id = e.applicant_id "
            "WHERE a.user_id = :uid AND (e.scored_resume_s3_key IS NOT NULL "
            "OR e.applied_resume_s3_key IS NOT NULL)"
        ),
        {"uid": uid_str},
    )
    for row in scored_keys_result.fetchall():
        applicant_resume_keys += [str(k) for k in tuple(row)[:2] if k]

    # 1c-iv — the CV attached to an abandoned DRAFT (PH3-B4c). A draft that was
    # never submitted has no applicant row and no enrolment, so none of the
    # three collectors above reach it: without this, erasing somebody who
    # started an application and walked away leaves their CV in the bucket and
    # stamps the request 'completed'. Collected before step 5e deletes the row.
    draft_keys_result = await db.execute(
        text(
            "SELECT d.resume_s3_key FROM application_drafts d "
            "WHERE d.resume_s3_key IS NOT NULL AND ("
            "  d.user_id = :uid"
            "  OR lower(btrim(d.email)) IN ("
            "       SELECT lower(btrim(u.email)) FROM users u"
            "        WHERE u.id = :uid AND u.email IS NOT NULL)"
            "  OR lower(btrim(d.email)) IN ("
            "       SELECT lower(btrim(a.email)) FROM applicants a"
            "        WHERE a.user_id = :uid AND a.email IS NOT NULL)"
            ")"
        ),
        {"uid": uid_str},
    )
    applicant_resume_keys += [
        str(row[0]) for row in draft_keys_result.fetchall() if row[0]
    ]

    # 1c-bis — PH4-A4 preboarding documents (identity papers and the like), in
    # the same uploads bucket. Every version, superseded ones included.
    preboarding_keys_result = await db.execute(
        text(
            "SELECT d.storage_key FROM candidate_documents d"
            "  JOIN applicants a ON a.id = (SELECT o.applicant_id FROM offers o"
            "                                WHERE o.id = d.offer_id)"
            " WHERE a.user_id = :uid AND d.storage_key IS NOT NULL"
        ),
        {"uid": uid_str},
    )
    applicant_resume_keys += [
        str(row[0]) for row in preboarding_keys_result.fetchall() if row[0]
    ]
    # ...and anything under those offers' prefixes that no row names (an object
    # a failed commit orphaned) — listed from storage itself.
    offer_prefixes = await db.execute(
        text(
            "SELECT o.company_id, o.id FROM offers o JOIN applicants a ON a.id = o.applicant_id"
            " WHERE a.user_id = :uid"
        ),
        {"uid": uid_str},
    )
    if settings is not None:
        from app.s3_client import keys_under  # noqa: PLC0415 — see step 8's import note

        for company_id, offer_id in offer_prefixes.fetchall():
            applicant_resume_keys += await keys_under(
                settings.s3_bucket_name, f"preboarding/{company_id}/{offer_id}/",
                settings=settings,
            )

    # 1c-ter — PH4-D4 task-round artifacts (job simulation / portfolio
    # submissions), same uploads bucket. Reached through applicants.user_id,
    # exactly like the preboarding documents above.
    task_response_keys_result = await db.execute(
        text(
            "SELECT r.storage_key FROM task_responses r"
            "  JOIN task_submissions t ON t.id = r.submission_id"
            "  JOIN applicants a ON a.id = t.applicant_id"
            " WHERE a.user_id = :uid AND r.storage_key IS NOT NULL"
        ),
        {"uid": uid_str},
    )
    applicant_resume_keys += [
        str(row[0]) for row in task_response_keys_result.fetchall() if row[0]
    ]
    # ...and anything under those submissions' prefixes that no row names (an
    # object a failed commit orphaned) — listed from storage itself, the same
    # precedent as the offer prefixes just above.
    task_submission_prefixes = await db.execute(
        text(
            "SELECT t.company_id, t.id FROM task_submissions t"
            "  JOIN applicants a ON a.id = t.applicant_id WHERE a.user_id = :uid"
        ),
        {"uid": uid_str},
    )
    if settings is not None:
        from app.s3_client import keys_under  # noqa: PLC0415 — see step 8's import note

        for company_id, submission_id in task_submission_prefixes.fetchall():
            applicant_resume_keys += await keys_under(
                settings.s3_bucket_name, f"tasks/{company_id}/{submission_id}/",
                settings=settings,
            )

    # One delete per object: the same file is often the current, scored AND
    # submitted copy at once.
    applicant_resume_keys = list(dict.fromkeys(applicant_resume_keys))

    # 1d — scorecard PDF + transcript keys (from scorecards table)
    scorecard_keys_result = await db.execute(
        text(
            "SELECT report_pdf_key, transcript_key FROM scorecards "
            "WHERE session_id IN (SELECT id FROM sessions WHERE user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    scorecard_rows = scorecard_keys_result.fetchall()
    scorecard_keys: list[dict[str, str | None]] = [
        {"pdf": row[0], "transcript": row[1]}
        for row in scorecard_rows
    ]

    # 1e — turn audio. turns.audio_s3_key is a Sprint-3 placeholder that is NULL
    # today, so this collects nothing yet, and it MUST be read before step 2
    # deletes the turns. It is here deliberately: the day the voice pipeline
    # starts populating it, erasure covers it automatically instead of silently
    # leaving candidate speech recordings in the bucket because nobody
    # remembered to revisit this function.
    turn_audio_result = await db.execute(
        text(
            "SELECT audio_s3_key FROM turns "
            "WHERE session_id IN (SELECT id FROM sessions WHERE user_id = :uid) "
            "  AND audio_s3_key IS NOT NULL"
        ),
        {"uid": uid_str},
    )
    turn_audio_keys: list[str] = [
        str(row[0]) for row in turn_audio_result.fetchall() if row[0]
    ]

    # ------------------------------------------------------------------
    # Step 2: Hard-delete interview transcript turns
    # ------------------------------------------------------------------
    turns_result = await db.execute(
        text(
            "DELETE FROM turns "
            "WHERE session_id IN (SELECT id FROM sessions WHERE user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    turns_deleted: int = getattr(turns_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.turns_deleted",
        user_id=uid_str,
        request_id=str(request.request_id),
        count=turns_deleted,
    )

    # ------------------------------------------------------------------
    # Step 3: Hard-delete resume version rows (keys now safely collected)
    # ------------------------------------------------------------------
    resumes_result = await db.execute(
        text("DELETE FROM resumes WHERE user_id = :uid"),
        {"uid": uid_str},
    )
    resumes_deleted: int = getattr(resumes_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.resumes_deleted",
        user_id=uid_str,
        request_id=str(request.request_id),
        count=resumes_deleted,
    )

    # ------------------------------------------------------------------
    # Step 4: Hard-delete scorecards
    # ------------------------------------------------------------------
    scorecards_result = await db.execute(
        text(
            "DELETE FROM scorecards "
            "WHERE session_id IN (SELECT id FROM sessions WHERE user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    scorecards_deleted: int = getattr(scorecards_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.scorecards_deleted",
        user_id=uid_str,
        request_id=str(request.request_id),
        count=scorecards_deleted,
    )

    # ------------------------------------------------------------------
    # Step 5: Hard-delete sessions (was soft-deleted; turns are already gone)
    # ------------------------------------------------------------------
    sessions_result = await db.execute(
        text("DELETE FROM sessions WHERE user_id = :uid"),
        {"uid": uid_str},
    )
    sessions_deleted: int = getattr(sessions_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.sessions_deleted",
        user_id=uid_str,
        request_id=str(request.request_id),
        count=sessions_deleted,
    )

    # ------------------------------------------------------------------
    # Step 5b: Hard-delete in-app notifications.
    #
    # notifications.user_id is ON DELETE CASCADE, which is why this was assumed
    # to be handled and was in neither the erasure path nor the exclusion list
    # (DPDP-7). The cascade cannot fire: step 7 anonymises the users row rather
    # than deleting it, because erasure_requests.user_id is ON DELETE RESTRICT.
    # notifications.title / body are free text written for a human ("Welcome,
    # <name>") and link can embed session ids, so the rows are PII that survived
    # a "completed" erasure.
    # ------------------------------------------------------------------
    notifications_result = await db.execute(
        text("DELETE FROM notifications WHERE user_id = :uid"),
        {"uid": uid_str},
    )
    notifications_deleted: int = getattr(notifications_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.notifications_deleted",
        user_id=uid_str,
        request_id=str(request.request_id),
        count=notifications_deleted,
    )

    # ------------------------------------------------------------------
    # Step 5c: Hard-delete the candidate's own answers to screening questions
    # ------------------------------------------------------------------
    # application_answers.answer is free text the CANDIDATE typed — "why do you
    # want this role", "notice period", "anything else we should know". That is
    # what separates it from exam_attempts, which is excluded two lists up: an
    # attempt is a set of selections against company-authored questions and
    # identifies nobody once the applicant row is anonymised, whereas a prose
    # answer routinely contains the writer's name, employer, notice terms or
    # phone number. Anonymising `applicants` does not touch that text, so the
    # row would survive a "completed" erasure still naming the person.
    #
    # Deleted rather than redacted because here the content IS the personal
    # data — there is no structural residue worth keeping, unlike `applicants`
    # where the anonymised row remains the company's ATS record.
    #
    # MUST run before step 6: the join reaches these rows through
    # applicants.user_id, which step 6 sets to NULL.
    answers_result = await db.execute(
        text(
            "DELETE FROM application_answers WHERE enrolment_id IN ("
            "  SELECT e.id FROM enrolments e"
            "  JOIN applicants a ON a.id = e.applicant_id"
            "  WHERE a.user_id = :uid"
            ")"
        ),
        {"uid": uid_str},
    )
    application_answers_deleted: int = getattr(answers_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.application_answers_deleted",
        user_id=uid_str,
        request_id=str(request.request_id),
        count=application_answers_deleted,
    )

    # ------------------------------------------------------------------
    # Step 5d: Bulk-upload file records (E5)
    # ------------------------------------------------------------------
    # upload_items keeps each uploaded CV's ORIGINAL filename, and HR names CVs
    # after the person ("Priya_Sharma_CV.pdf"). The row is the upload's
    # processing record; the name in it is personal data. The object itself is
    # the applicant's resume_s3_key, already collected in step 1c.
    #
    # MUST run before step 6: it reaches these rows through applicants.user_id,
    # which step 6 sets to NULL.
    items_result = await db.execute(
        text(
            "UPDATE upload_items SET filename = '[redacted]', s3_key = NULL, error = NULL, "
            "updated_at = now() "
            "WHERE applicant_id IN (SELECT id FROM applicants WHERE user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    log.info(
        "erasure.executor.upload_items_redacted",
        user_id=uid_str,
        request_id=str(request.request_id),
        count=getattr(items_result, "rowcount", 0) or 0,
    )

    # ------------------------------------------------------------------
    # Step 5e: Abandoned application drafts (PH3-B4c)
    # ------------------------------------------------------------------
    # A half-finished application holds the person's name, email, phone and CV.
    # Deleted rather than anonymised: unlike `applicants`, there is no
    # structural record worth keeping, because nobody applied — an anonymised
    # draft is a row that means nothing to anyone.
    #
    # Keyed on user_id DIRECTLY, not through applicants. A draft that was never
    # submitted has no applicant row to be reached through, and reaching for one
    # is precisely how this table would have been missed.
    #
    # The CV object itself was collected in step 1c-iii and is deleted in step 8.
    # Matched THREE ways, not one. user_id alone was not enough: when a guest
    # activates into an account they already had, apply_activation re-points the
    # draft — and a draft written before that repair existed, or one whose
    # re-point failed, would be invisible here and survive a completed erasure.
    # The address and the linked applicants are independent routes to the same
    # row, so the executor no longer depends on a repair elsewhere being correct.
    drafts_result = await db.execute(
        text(
            "DELETE FROM application_drafts d"
            # 1. The draft still points at this user.
            " WHERE d.user_id = :uid"
            # 2. The draft carries the address this user is erasing under.
            #    MUST run before step 7, which overwrites users.email with the
            #    erased_{uid} sentinel — after that this route matches nothing.
            #    test_erasure_step_order.py asserts the ordering.
            "    OR lower(btrim(d.email)) IN ("
            "         SELECT lower(btrim(u.email)) FROM users u"
            "          WHERE u.id = :uid AND u.email IS NOT NULL)"
            # 3. The draft carries the address of an APPLICANT linked to this
            #    user. Genuinely independent of 1 and 2: it catches the
            #    returning applicant whose draft kept a throwaway guest id and
            #    whose account was never activated, so users.email is still the
            #    guest sentinel and route 2 cannot see them.
            #
            #    This predicate previously read `d.user_id IN (SELECT a.user_id
            #    FROM applicants a WHERE a.user_id = :uid)`, which is
            #    algebraically just route 1 — a third route in the comments and
            #    nowhere else.
            "    OR lower(btrim(d.email)) IN ("
            "         SELECT lower(btrim(a.email)) FROM applicants a"
            "          WHERE a.user_id = :uid AND a.email IS NOT NULL)"
        ),
        {"uid": uid_str},
    )
    application_drafts_deleted: int = getattr(drafts_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.application_drafts_deleted",
        user_id=uid_str,
        request_id=str(request.request_id),
        count=application_drafts_deleted,
    )

    # ------------------------------------------------------------------
    # Step 5f: Human interview evidence (PH4-A1 / PH4-A5)
    # ------------------------------------------------------------------
    # Scores are kept, prose is not. A 1-5 score against a competency id is the
    # company's evaluation record and identifies nobody once the applicant is
    # anonymised — the round_results precedent. Everything a PERSON wrote about
    # the candidate is different: an interviewer's evidence and summary, their
    # reason for correcting a scorecard, HR's reason for withdrawing an
    # assignment. Prose routinely says "Priya described her time at <employer>",
    # which re-identifies the person the rest of this executor is anonymising.
    #
    # In this order, for a reason each:
    #   i.   OPEN ASSIGNMENTS ARE WITHDRAWN. An erased candidate is not going to
    #        be interviewed, and an open scorecard is a place new prose about
    #        them could be written after this erasure reports "completed". The
    #        application refuses writes against an anonymised applicant too;
    #        this closes the rows themselves.
    #   ii.  EVIDENCE is set to NULL on every score.
    #   iii. SUMMARY is set to NULL, and CORRECTION / WITHDRAWAL REASONS to the
    #        fixed marker '[redacted]' (NULL where there was none), with
    #        redacted_at stamped. A marker rather than NULL for the reasons: a
    #        correction row must keep a reason (its CHECK pairs corrects_id with
    #        one), and '[redacted]' says the reason existed and was removed.
    #   iv.  Private NOTES are deleted outright — no structural residue.
    #
    # The scorecard triggers (migration d2f4a6c8e0b1) permit exactly these
    # changes on submitted and withdrawn rows and nothing else, so an erasure
    # cannot be used to rewrite a hiring record. Scores, competency ids, who
    # interviewed and when are untouched.
    #
    # MUST run before step 6: every join here reaches the rows through
    # applicants.user_id, which step 6 sets to NULL.
    # Written out in full rather than interpolated: an f-string building SQL is
    # a B608 finding even when the fragment is a constant, and a nosec is a
    # standing exception somebody later copies onto a string that is not.
    withdrawn_result = await db.execute(
        text(
            "UPDATE interviewer_scorecards SET status = 'withdrawn', withdrawn_at = now(),"
            " withdrawn_reason = NULL, updated_at = now()"
            " WHERE status IN ('assigned', 'in_progress') AND enrolment_id IN ("
            "   SELECT e.id FROM enrolments e"
            "     JOIN applicants a ON a.id = e.applicant_id"
            "    WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    interview_assignments_withdrawn: int = getattr(withdrawn_result, "rowcount", 0) or 0
    evidence_result = await db.execute(
        text(
            "UPDATE interviewer_scorecard_scores SET evidence = NULL, updated_at = now()"
            " WHERE evidence IS NOT NULL AND scorecard_id IN ("
            "   SELECT s.id FROM interviewer_scorecards s"
            "     JOIN enrolments e ON e.id = s.enrolment_id"
            "     JOIN applicants a ON a.id = e.applicant_id"
            "    WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    interview_evidence_redacted: int = getattr(evidence_result, "rowcount", 0) or 0
    redacted_result = await db.execute(
        text(
            "UPDATE interviewer_scorecards SET summary = NULL,"
            " correction_reason = CASE WHEN correction_reason IS NULL THEN NULL"
            "                          ELSE '[redacted]' END,"
            " withdrawn_reason = CASE WHEN withdrawn_reason IS NULL THEN NULL"
            "                         ELSE '[redacted]' END,"
            " redacted_at = now(), updated_at = now()"
            " WHERE redacted_at IS NULL AND enrolment_id IN ("
            "   SELECT e.id FROM enrolments e"
            "     JOIN applicants a ON a.id = e.applicant_id"
            "    WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    interview_scorecards_redacted: int = getattr(redacted_result, "rowcount", 0) or 0
    notes_result = await db.execute(
        text(
            "DELETE FROM interviewer_notes WHERE enrolment_id IN ("
            "   SELECT e.id FROM enrolments e"
            "     JOIN applicants a ON a.id = e.applicant_id"
            "    WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    interviewer_notes_deleted: int = getattr(notes_result, "rowcount", 0) or 0
    # PH4-O1 stage exceptions: the record stays (the company's operational
    # history), the prose about the candidate does not. An open one is closed
    # too — nobody should go on working an exception about an erased person.
    exceptions_result = await db.execute(
        text(
            "UPDATE stage_exceptions SET reason = '[redacted]',"
            " resolution_note = NULL, redacted_at = now(), updated_at = now(),"
            " status = 'resolved', resolved_at = COALESCE(resolved_at, now())"
            " WHERE redacted_at IS NULL AND enrolment_id IN ("
            "   SELECT e.id FROM enrolments e"
            "     JOIN applicants a ON a.id = e.applicant_id"
            "    WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    stage_exceptions_redacted: int = getattr(exceptions_result, "rowcount", 0) or 0
    # PH4-A2 interview loops: every session not yet held is cancelled — an
    # interviewer must not turn up for an erased person — and the prose a
    # person wrote when cancelling is redacted. Who sat on past sessions stays,
    # as the company's scheduling record. Sessions first: their status feeds
    # nothing on the loop that this step does not set itself.
    sessions_result = await db.execute(
        text(
            "UPDATE interview_sessions SET status = 'cancelled',"
            " cancelled_at = COALESCE(cancelled_at, now()), updated_at = now()"
            " WHERE status IN ('scheduled', 'awaiting_slot') AND applicant_id IN ("
            "   SELECT a.id FROM applicants a WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    interview_sessions_cancelled: int = getattr(sessions_result, "rowcount", 0) or 0
    await db.execute(
        text(
            "UPDATE interview_sessions SET cancel_reason = '[redacted]', updated_at = now()"
            " WHERE cancel_reason IS NOT NULL AND cancel_reason <> '[redacted]'"
            "   AND applicant_id IN (SELECT a.id FROM applicants a WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    loops_result = await db.execute(
        text(
            "UPDATE interview_loops SET"
            " status = CASE WHEN status IN ('draft', 'scheduled') THEN 'cancelled' ELSE status END,"
            " cancelled_at = CASE WHEN status IN ('draft', 'scheduled')"
            "                     THEN COALESCE(cancelled_at, now()) ELSE cancelled_at END,"
            " cancel_reason = CASE WHEN cancel_reason IS NULL THEN NULL ELSE '[redacted]' END,"
            " redacted_at = now(), updated_at = now()"
            " WHERE redacted_at IS NULL AND applicant_id IN ("
            "   SELECT a.id FROM applicants a WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    interview_loops_redacted: int = getattr(loops_result, "rowcount", 0) or 0
    # PH4-A3 offers: an offer still in play is withdrawn and its link killed;
    # the prose people wrote on every offer (the name typed to accept, reasons,
    # the approver's note) is redacted. The offer — what was offered and when —
    # stays as the company's record, against an anonymised applicant.
    offers_result = await db.execute(
        text(
            "UPDATE offers SET"
            " status = CASE WHEN status IN ('draft', 'pending_approval', 'approved',"
            "                               'rejected', 'sent') THEN 'withdrawn' ELSE status END,"
            " withdrawn_at = CASE WHEN status IN ('draft', 'pending_approval', 'approved',"
            "                                     'rejected', 'sent')"
            "                     THEN COALESCE(withdrawn_at, now()) ELSE withdrawn_at END,"
            " token_hash = NULL, accepted_name = NULL,"
            " decline_reason = CASE WHEN decline_reason IS NULL THEN NULL ELSE '[redacted]' END,"
            " withdraw_reason = CASE WHEN withdraw_reason IS NULL THEN NULL ELSE '[redacted]' END,"
            " approval_note = CASE WHEN approval_note IS NULL THEN NULL ELSE '[redacted]' END,"
            " redacted_at = now(), updated_at = now()"
            " WHERE redacted_at IS NULL AND applicant_id IN ("
            "   SELECT a.id FROM applicants a WHERE a.user_id = :uid)"
            " RETURNING id"
        ),
        {"uid": uid_str},
    )
    redacted_offer_ids = [row[0] for row in offers_result.fetchall()]
    offers_redacted: int = len(redacted_offer_ids)
    await db.execute(
        text("DELETE FROM offer_codes WHERE offer_id IN (SELECT o.id FROM offers o"
             " JOIN applicants a ON a.id = o.applicant_id WHERE a.user_id = :uid)"),
        {"uid": uid_str},
    )
    await db.execute(
        text("DELETE FROM offer_sessions WHERE offer_id IN (SELECT o.id FROM offers o"
             " JOIN applicants a ON a.id = o.applicant_id WHERE a.user_id = :uid)"),
        {"uid": uid_str},
    )
    await db.execute(
        text("DELETE FROM hrms_exports WHERE offer_id IN (SELECT o.id FROM offers o"
             " JOIN applicants a ON a.id = o.applicant_id WHERE a.user_id = :uid)"),
        {"uid": uid_str},
    )
    # PH4-A4 documents: the FILES go in step 8 (their keys were collected in
    # step 1); here the rows lose everything that points at them.
    documents_result = await db.execute(
        text(
            "UPDATE candidate_documents SET storage_key = NULL, original_name = NULL,"
            " review_note = NULL, redacted_at = now()"
            " WHERE redacted_at IS NULL AND offer_id IN (SELECT o.id FROM offers o"
            "   JOIN applicants a ON a.id = o.applicant_id WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    preboarding_documents_redacted: int = getattr(documents_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.interview_evidence_redacted",
        user_id=uid_str,
        request_id=str(request.request_id),
        assignments_withdrawn=interview_assignments_withdrawn,
        evidence_redacted=interview_evidence_redacted,
        scorecards=interview_scorecards_redacted,
        notes_deleted=interviewer_notes_deleted,
        stage_exceptions_redacted=stage_exceptions_redacted,
        interview_sessions_cancelled=interview_sessions_cancelled,
        interview_loops_redacted=interview_loops_redacted,
        offers_redacted=offers_redacted,
        preboarding_documents_redacted=preboarding_documents_redacted,
    )

    # ------------------------------------------------------------------
    # Step 5g: Candidate accommodations (PH4-D2)
    # ------------------------------------------------------------------
    # Any accommodation still active is REVOKED — an erased candidate is not
    # going to sit another assessment under it. The notes go: interviewer_note
    # (the one thing an assigned interviewer ever saw) is set to NULL, and
    # other_adjustment / internal_note / revoke_reason follow the
    # CASE-WHEN-NULL pattern used everywhere else in this executor, so a
    # column that was already empty stays empty rather than gaining a
    # spurious '[redacted]'. revoke_reason is included because it is free text
    # HR typed when withdrawing an adjustment — in practice the likeliest
    # place a health or disability explanation gets written, and it is no more
    # exempt from erasure than the other three notes just because it lives
    # next to the revoke columns. The PARAMETERS (extra_time_percent,
    # deadline_extension_days, relax_auto_submit) are kept — they are numbers,
    # on the interviewer_scorecard_scores precedent, and they describe the
    # company's assessment record (what was granted), not the candidate.
    #
    # MUST run before step 6: the join reaches these rows through
    # applicants.user_id, which step 6 sets to NULL.
    # revoked_at is set; revoked_by_user_id is deliberately left NULL (see
    # the paragraph below). An earlier version of this comment said the
    # revoke was attributed to the erasure's system actor -- it never was.
    #
    # BLOCKING 1: the revoke statement must not touch a row retention already
    # redacted (accommodations.purge sets redacted_at but, before F8, left
    # status='active' — an ordinary reachable row here). The guard trigger
    # raises on ANY update to a redacted row, and this statement had no
    # redacted_at guard, so the transaction rolled back and the erasure
    # request retried forever, never reaching step 6 onward. The redaction
    # statement below already carried the guard; this one did not.
    # revoked_by_user_id stays NULL, which is how the guard trigger records
    # "ended by the platform, not a person". system_actor_id cannot go here:
    # that column is a real FK to users and the id this task runs under
    # (00000000-...-0001) has no account, so naming it fails outright. It is
    # still the actor on the audit row at the end of this function, where the
    # column has no such constraint.
    accommodations_revoked_result = await db.execute(
        text(
            "UPDATE candidate_accommodations SET status = 'revoked',"
            " revoked_at = now(), updated_at = now()"
            " WHERE status = 'active' AND redacted_at IS NULL AND applicant_id IN ("
            "   SELECT id FROM applicants WHERE user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    accommodations_revoked: int = getattr(accommodations_revoked_result, "rowcount", 0) or 0
    accommodations_redacted_result = await db.execute(
        text(
            "UPDATE candidate_accommodations SET"
            " other_adjustment = CASE WHEN other_adjustment IS NULL THEN NULL"
            "                         ELSE '[redacted]' END,"
            " revoke_reason = CASE WHEN revoke_reason IS NULL THEN NULL"
            "                      ELSE '[redacted]' END,"
            " interviewer_note = NULL, internal_note = NULL, redacted_at = now(),"
            " updated_at = now()"
            " WHERE redacted_at IS NULL AND applicant_id IN ("
            "   SELECT id FROM applicants WHERE user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    accommodations_redacted: int = getattr(accommodations_redacted_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.accommodations_redacted",
        user_id=uid_str,
        request_id=str(request.request_id),
        revoked=accommodations_revoked,
        redacted=accommodations_redacted,
    )

    # ------------------------------------------------------------------
    # Step 5h: Coding-round SOURCE and program OUTPUT (PH4-D3)
    # ------------------------------------------------------------------
    # A coding submission's source, and what it printed while being graded,
    # are the candidate's own text and their program's own output — personal
    # data on the interviewer_notes precedent, not the company's structural
    # assessment record. The SCORE (raw, points, passed) IS that record and
    # is kept, the round_results precedent.
    #
    # exam_attempts moves from EXCLUDED to ERASED for exactly this reason:
    # every other column on it (selections, timestamps, the score) still
    # describes only the company's own assessment against an applicant row
    # step 6 anonymises next, same as before this feature. Only the coding
    # source and program stdout/stderr are new personal data.
    #
    # The redaction UPDATE below touches ONLY answers / graded_snapshot /
    # code_redacted_at — the one shape ``exam_attempts_submission_frozen``
    # permits on an already-submitted/expired attempt. The transform itself
    # is a PURE function (app/code_redaction.py) with its own unit test,
    # because admin_ops cannot import data_gateway to reuse its copy of the
    # same rule (services/data_gateway/app/code_evidence.py's retention path
    # uses an independently-written one).
    #
    # MUST run before step 6: the join reaches these rows through
    # applicants.user_id, which step 6 sets to NULL.
    #
    # MEDIUM-1(b): this used to filter on ``code_redacted_at IS NULL``, same
    # as the attempt UPDATE below -- so an attempt retention had ALREADY
    # redacted (e.g. because its application was decided and the retention
    # window had passed before this erasure ran) was skipped ENTIRELY here,
    # evidence cleanup and finding-rationale redaction included. A finding
    # recorded after that earlier redaction (record_finding now refuses one
    # on a redacted attempt, but a legacy row, or one written in the race
    # MEDIUM-1(a) closes, could still exist) would then keep its rationale
    # forever, because erasure never looked at the attempt again. The SELECT
    # below now reaches every one of this user's coding attempts regardless
    # of ``code_redacted_at``; only the attempt UPDATE two lines down stays
    # conditional on it being NULL, which is what makes it idempotent instead
    # of the freeze trigger's "code_redacted_at is frozen once set" firing.
    coding_attempts_result = await db.execute(
        text(
            "SELECT id, company_id, answers, graded_snapshot FROM exam_attempts"
            " WHERE answers -> 'coding' IS NOT NULL"
            "   AND applicant_id IN (SELECT id FROM applicants WHERE user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    coding_attempt_rows = coding_attempts_result.fetchall()
    code_attempts_redacted = 0
    code_reports_deleted = 0
    code_fingerprints_deleted = 0
    code_signals_deleted = 0
    code_findings_redacted = 0
    for coding_attempt_id, coding_attempt_company_id, coding_answers, coding_graded_snapshot in (
        coding_attempt_rows
    ):
        new_answers = redact_coding_answers(coding_answers)
        new_snapshot = redact_graded_snapshot(coding_graded_snapshot)
        redact_result = await db.execute(
            text(
                "UPDATE exam_attempts SET answers = CAST(:a AS jsonb),"
                " graded_snapshot = CAST(:g AS jsonb), code_redacted_at = now(),"
                " updated_at = now()"
                " WHERE id = :id AND code_redacted_at IS NULL"
            ),
            {"a": json.dumps(new_answers), "g": json.dumps(new_snapshot), "id": coding_attempt_id},
        )
        code_attempts_redacted += getattr(redact_result, "rowcount", 0) or 0
        # MEDIUM-1(c): redact the RATIONALE of every finding that names one of
        # this attempt's signals -- BEFORE clearing signal_id below -- even
        # one recorded against a SURVIVING candidate's own attempt (the other
        # side of the pair). A finding on the pair's other attempt otherwise
        # lost only its signal_id; its rationale -- which this file's own
        # inventory says "can quote or name the candidate" -- was kept
        # forever, because the final redact step below only ever matched
        # findings whose own attempt_id was this erased one. MUST run before
        # the signal_id nulling immediately below: once signal_id is NULL
        # there is no way left to find these findings through the signal.
        cross_findings_result = await db.execute(
            text(
                "UPDATE code_integrity_findings SET rationale = '[redacted]', redacted_at = now()"
                " WHERE company_id = :c AND redacted_at IS NULL AND signal_id IN ("
                "   SELECT id FROM code_similarity_signals"
                "    WHERE company_id = :c AND (attempt_low_id = :a OR attempt_high_id = :a)"
                " )"
            ),
            {"c": coding_attempt_company_id, "a": coding_attempt_id},
        )
        code_findings_redacted += getattr(cross_findings_result, "rowcount", 0) or 0
        # MUST run before the DELETE below: code_integrity_findings.signal_id
        # is RESTRICT, not SET NULL -- a composite FK's ON DELETE SET NULL
        # would null company_id (NOT NULL) along with it (see the PH4-D3
        # migration's docstring). A finding can sit on either attempt of the
        # pair, not just this one, so the match is on signal membership. Some
        # of these rows were just redacted above and some may already have
        # been redacted earlier for an unrelated reason -- the guard trigger
        # allows signal_id -> NULL on a redacted row for exactly this
        # (MEDIUM-2), so this never raises "is redacted and is fixed".
        await db.execute(
            text(
                "UPDATE code_integrity_findings SET signal_id = NULL"
                " WHERE company_id = :c AND signal_id IN ("
                "   SELECT id FROM code_similarity_signals"
                "    WHERE company_id = :c AND (attempt_low_id = :a OR attempt_high_id = :a)"
                " )"
            ),
            {"c": coding_attempt_company_id, "a": coding_attempt_id},
        )
        signals_result = await db.execute(
            text(
                "DELETE FROM code_similarity_signals WHERE company_id = :c"
                " AND (attempt_low_id = :a OR attempt_high_id = :a)"
            ),
            {"c": coding_attempt_company_id, "a": coding_attempt_id},
        )
        code_signals_deleted += getattr(signals_result, "rowcount", 0) or 0
        fingerprints_result = await db.execute(
            text("DELETE FROM code_fingerprints WHERE company_id = :c AND attempt_id = :a"),
            {"c": coding_attempt_company_id, "a": coding_attempt_id},
        )
        code_fingerprints_deleted += getattr(fingerprints_result, "rowcount", 0) or 0
        reports_result = await db.execute(
            text("DELETE FROM code_quality_reports WHERE company_id = :c AND attempt_id = :a"),
            {"c": coding_attempt_company_id, "a": coding_attempt_id},
        )
        code_reports_deleted += getattr(reports_result, "rowcount", 0) or 0
        # The catch-all: every OTHER finding directly on this attempt (never
        # referencing a signal, or referencing one that did not name this
        # attempt's pair) still gets its rationale redacted here.
        findings_result = await db.execute(
            text(
                "UPDATE code_integrity_findings SET rationale = '[redacted]', redacted_at = now()"
                " WHERE company_id = :c AND attempt_id = :a AND redacted_at IS NULL"
            ),
            {"c": coding_attempt_company_id, "a": coding_attempt_id},
        )
        code_findings_redacted += getattr(findings_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.code_evidence_redacted",
        user_id=uid_str,
        request_id=str(request.request_id),
        attempts_redacted=code_attempts_redacted,
        reports_deleted=code_reports_deleted,
        fingerprints_deleted=code_fingerprints_deleted,
        signals_deleted=code_signals_deleted,
        findings_redacted=code_findings_redacted,
    )

    # ------------------------------------------------------------------
    # Step 5i: Job simulation / portfolio submissions (PH4-D4)
    # ------------------------------------------------------------------
    # A submission still open (assigned/in_progress) is WITHDRAWN — an erased
    # candidate is not going to finish it — with its link killed in the same
    # statement (task_submissions_lifecycle allows both together on this one
    # transition; see the migration's docstring). Every submission of theirs,
    # whatever its status, is then REDACTED: token_hash cleared (a no-op where
    # it already is) and redacted_at stamped, together, in ONE statement — the
    # only shape the trigger allows once a row is not open. task_responses
    # content is cleared the same way; the FILES those rows named were
    # collected in step 1 and are deleted in step 8.
    #
    # MUST run before step 6: the join reaches these rows through
    # applicants.user_id, which step 6 sets to NULL.
    task_submissions_withdrawn_result = await db.execute(
        text(
            "UPDATE task_submissions SET status = 'withdrawn', token_hash = NULL,"
            " updated_at = now()"
            " WHERE status IN ('assigned', 'in_progress') AND redacted_at IS NULL"
            "   AND applicant_id IN (SELECT id FROM applicants WHERE user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    task_submissions_withdrawn: int = (
        getattr(task_submissions_withdrawn_result, "rowcount", 0) or 0
    )
    task_responses_redacted_result = await db.execute(
        text(
            "UPDATE task_responses SET text_value = NULL, link_url = NULL, title = NULL,"
            " description = NULL, storage_key = NULL, original_name = NULL, redacted_at = now(),"
            " updated_at = now()"
            " WHERE redacted_at IS NULL AND submission_id IN ("
            "   SELECT t.id FROM task_submissions t"
            "     JOIN applicants a ON a.id = t.applicant_id WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    task_responses_redacted: int = getattr(task_responses_redacted_result, "rowcount", 0) or 0
    task_submissions_redacted_result = await db.execute(
        text(
            "UPDATE task_submissions SET token_hash = NULL, redacted_at = now(), updated_at = now()"
            " WHERE redacted_at IS NULL"
            "   AND applicant_id IN (SELECT id FROM applicants WHERE user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    task_submissions_redacted: int = getattr(task_submissions_redacted_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.task_submissions_redacted",
        user_id=uid_str,
        request_id=str(request.request_id),
        withdrawn=task_submissions_withdrawn,
        submissions_redacted=task_submissions_redacted,
        responses_redacted=task_responses_redacted,
    )

    # ------------------------------------------------------------------
    # Step 5j: 90-day hire check-ins (PH5-D5-2)
    # ------------------------------------------------------------------
    # Unlike every other row in this executor, hire_checkins is DELETED
    # outright, not redacted: the row carries no free text and no
    # applicant_id (the DPDP review that shaped this table decided a
    # structured employment/left_reason/performance record naming no one is
    # not worth keeping once the applicant it concerns is gone — there is
    # nothing left to anonymise it against). Ordinary 24-month retention
    # deletes the rest (app.hire_checkins.purge); this step deletes sooner,
    # on request.
    #
    # MUST run before step 6: the join reaches these rows through
    # enrolments.applicant_id -> applicants.user_id, and hire_checkins has no
    # applicant_id of its own to be reached through once user_id is cleared.
    hire_checkins_result = await db.execute(
        text(
            "DELETE FROM hire_checkins"
            " WHERE enrolment_id IN ("
            "   SELECT e.id FROM enrolments e"
            "     JOIN applicants a ON a.id = e.applicant_id"
            "    WHERE a.user_id = :uid)"
        ),
        {"uid": uid_str},
    )
    hire_checkins_deleted: int = getattr(hire_checkins_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.hire_checkins_deleted",
        user_id=uid_str,
        request_id=str(request.request_id),
        count=hire_checkins_deleted,
    )

    # ------------------------------------------------------------------
    # Step 6: Anonymise applicant rows linked to this user_id
    # ------------------------------------------------------------------
    # embedding is NOT decoration on this list. applicants.embedding is a
    # halfvec(3072) computed by hr_applicants._embed_applicant directly FROM
    # resume_text, so nulling the text while keeping the vector leaves a dense
    # representation of the candidate's CV behind — and keeps the erased
    # applicant semantically searchable through GET /hr/applicants?q=.
    # Embedding-inversion research makes "a vector is not personal data" a
    # position we would have to defend with evidence, not assert. Cheaper to
    # null it.
    applicants_result = await db.execute(
        text(
            "UPDATE applicants "
            "SET full_name = '[redacted]', "
            "    email = NULL, "
            "    resume_text = NULL, "
            "    resume_s3_key = NULL, "
            "    embedding = NULL, "
            "    user_id = NULL, "
            # Added by migration d5f7b9c1e3a6 (the multi-step application)
            # and never erased until now: a candidate's phone, employer and
            # profile links, and the name read out of their CV, all survived
            # an erasure. Plus parsed_email (d1f3a5b7c9e2), the address read
            # out of a CV that matched someone else.
            "    phone = NULL, "
            "    years_experience = NULL, "
            "    current_company = NULL, "
            "    current_title = NULL, "
            "    linkedin_url = NULL, "
            "    github_url = NULL, "
            "    parsed_full_name = NULL, "
            "    parsed_email = NULL, "
            "    updated_at = :now "
            "WHERE user_id = :uid"
        ),
        {"uid": uid_str, "now": datetime.now(UTC)},
    )
    applicants_anonymised: int = getattr(applicants_result, "rowcount", 0) or 0
    log.info(
        "erasure.executor.applicants_anonymised",
        user_id=uid_str,
        request_id=str(request.request_id),
        count=applicants_anonymised,
    )

    # ------------------------------------------------------------------
    # Step 7: Anonymise the users row in-place (email replaced with opaque
    #         sentinel so the UNIQUE constraint remains satisfied and the
    #         FK from erasure_requests does not dangle).
    # ------------------------------------------------------------------
    erased_email_sentinel = f"erased_{uid_str}@deleted.invalid"
    await db.execute(
        text(
            "UPDATE users SET "
            "  email = :sentinel, "
            "  full_name = '[redacted]', "
            "  phone = NULL, "
            "  password_hash = NULL, "
            "  naipunyam_id = NULL, "
            "  linkedin_url = NULL, "
            "  github_url = NULL, "
            "  avatar_url = NULL, "
            "  headline = NULL, "
            "  bio = NULL, "
            "  official_email = NULL, "
            "  resume_text = NULL, "
            "  resume_s3_key = NULL, "
            # Profile and onboarding fields added after this step was written,
            # and never erased until now: where the person lives, their
            # employment situation, and the roles and goals they described.
            "  location = NULL, "
            "  employment_status = NULL, "
            "  desired_roles = NULL, "
            "  onboarding_goal = NULL, "
            "  target_role = NULL, "
            "  updated_at = :now "
            "WHERE id = :uid"
        ),
        {
            "sentinel": erased_email_sentinel,
            "uid": uid_str,
            "now": datetime.now(UTC),
        },
    )
    log.info(
        "erasure.executor.user_anonymised",
        user_id=uid_str,
        request_id=str(request.request_id),
    )

    # ------------------------------------------------------------------
    # Step 8: DELETE every collected S3 / R2 object key from object
    #         storage BEFORE stamping status='completed'.
    #
    # This is the critical step that makes the erasure claim honest under
    # DPDP §12.  If any delete call fails this function raises an exception
    # so the caller rolls back the entire DB transaction and leaves the
    # erasure_request in 'pending' for the next poll cycle to retry.
    #
    # Key catalogue:
    #   scorecard bucket → report_pdf_key, transcript_key (scorecards)
    #                    → audio_s3_key (turns; NULL until the voice pipeline)
    #   uploads bucket   → resume_s3_key from users, EVERY resumes version row,
    #                      and every applicant row
    # ------------------------------------------------------------------
    # Built unconditionally so the artifacts record below can report the true
    # counts whether or not object storage was configured.
    scorecard_bucket_keys: list[str] = []
    for sc_key in scorecard_keys:
        if sc_key.get("pdf"):
            scorecard_bucket_keys.append(sc_key["pdf"])  # type: ignore[arg-type]
        if sc_key.get("transcript"):
            scorecard_bucket_keys.append(sc_key["transcript"])  # type: ignore[arg-type]
    scorecard_bucket_keys.extend(turn_audio_keys)

    # dedupe: users.resume_s3_key normally duplicates the is_current row in
    # resumes, and two applicant rows can point at one upload. Deleting the same
    # key twice is harmless (delete_objects tolerates absent keys) but it
    # inflates the artifacts count, which is the number an auditor reads.
    resume_bucket_keys: list[str] = list(
        dict.fromkeys(
            ([user_resume_s3_key] if user_resume_s3_key else [])
            + resume_version_keys
            + applicant_resume_keys
        )
    )

    total_s3_keys = len(scorecard_bucket_keys) + len(resume_bucket_keys)

    # s3_objects_deleted is what an auditor reads, so it is the count
    # delete_objects REPORTS, never the count we hoped for. It used to be
    # len(collected keys) guarded only by `settings is not None`, and
    # delete_objects returned None both when it had deleted everything and when
    # it had silently skipped the whole phase for want of credentials. A
    # real-but-unconfigured Settings therefore took the "deleted" branch and
    # stamped status='completed' with a non-zero count over objects that were
    # all still in the bucket — the exact false completion this module's
    # docstring claims to prevent.
    s3_objects_deleted = 0

    if total_s3_keys == 0:
        # Nothing was ever stored for this user. Completing is honest here even
        # with no storage configured, and it keeps local dev / CI usable.
        log.info(
            "erasure.executor.s3_delete_not_needed",
            user_id=uid_str,
            request_id=str(request.request_id),
        )
    elif settings is None:
        # No Settings at all: we cannot delete, so we cannot claim completion.
        # Raising leaves the request 'pending' for the next poll cycle, which is
        # the retryable state — a wrong 'completed' is not retryable at all.
        log.error(
            "erasure.executor.s3_delete_unconfigured",
            user_id=uid_str,
            request_id=str(request.request_id),
            total_keys=total_s3_keys,
            reason="settings=None but object keys were collected — refusing to "
                   "stamp 'completed'. The request stays pending for retry.",
        )
        raise StorageNotConfiguredError(
            "Erasure collected object keys but no Settings were supplied; "
            "refusing to claim DPDP §12 completion."
        )
    else:
        # Imported per call, not at module scope: the tests patch
        # ``app.s3_client.delete_objects``, and a module-scope `from … import`
        # would capture the real function before any patch could reach it.
        # (The typed errors above import fine at module scope — there is no
        # cycle; app.s3_client imports nothing from here.)
        from app.s3_client import delete_objects

        keys_by_bucket: dict[str, list[str]] = {}
        if scorecard_bucket_keys:
            keys_by_bucket[settings.s3_scorecard_bucket] = scorecard_bucket_keys
        if resume_bucket_keys:
            keys_by_bucket[settings.s3_bucket_name] = resume_bucket_keys

        log.info(
            "erasure.executor.s3_delete_start",
            user_id=uid_str,
            request_id=str(request.request_id),
            total_keys=total_s3_keys,
        )

        # Raises on any non-absent S3 error, and on unconfigured storage —
        # caller rolls back either way.
        s3_objects_deleted = await delete_objects(keys_by_bucket, settings=settings)

        if s3_objects_deleted < total_s3_keys:
            # Belt-and-braces: delete_objects raises rather than under-deleting,
            # so reaching here means its contract changed. Fail loudly instead
            # of writing the shortfall into the DPDP artifacts record.
            log.error(
                "erasure.executor.s3_delete_shortfall",
                user_id=uid_str,
                request_id=str(request.request_id),
                total_keys=total_s3_keys,
                deleted=s3_objects_deleted,
            )
            raise ErasureIncompleteError(
                f"S3 reported {s3_objects_deleted} of {total_s3_keys} objects "
                "deleted; refusing to claim DPDP §12 completion."
            )

        log.info(
            "erasure.executor.s3_delete_complete",
            user_id=uid_str,
            request_id=str(request.request_id),
            total_keys=total_s3_keys,
            deleted=s3_objects_deleted,
        )

    # ------------------------------------------------------------------
    # Step 9: Stamp the erasure_request as completed
    #         (only reached when ALL S3 deletes succeeded or were no-ops)
    # ------------------------------------------------------------------
    now_utc = datetime.now(UTC)
    artifacts: dict[str, Any] = {
        # Bumped 1.1 → 1.2 when step 5b (notifications) joined the erasure, and
        # 1.2 → 1.3 when step 5f (human interview evidence, PH4-A1/A5) did, and
        # 1.3 → 1.4 when 5f took in stage exceptions (PH4-O1), 1.4 → 1.5
        # when it took in interview loops and sessions (PH4-A2), 1.5 → 1.6
        # when it took in offers and preboarding documents (PH4-A3/A4), and
        # 1.6 → 1.7 when step 5g took in candidate accommodations (PH4-D2),
        # 1.7 → 1.8 when step 5h took in coding-round source and program
        # output (PH4-D3), 1.8 → 1.9 when step 5i took in job simulation
        # / portfolio submissions (PH4-D4), and 1.9 → 1.10 when step 5j took
        # in 90-day hire check-ins (PH5-D5-2): the artifacts record is what an
        # auditor reads to know WHAT a given completion covered, so two
        # records with different coverage must not claim the same version.
        "executor_version": "1.10",
        "completed_at": now_utc.isoformat(),
        "turns_deleted": turns_deleted,
        "resumes_deleted": resumes_deleted,
        "scorecards_deleted": scorecards_deleted,
        "sessions_deleted": sessions_deleted,
        "notifications_deleted": notifications_deleted,
        "applicants_anonymised": applicants_anonymised,
        "interview_assignments_withdrawn": interview_assignments_withdrawn,
        "interview_evidence_redacted": interview_evidence_redacted,
        "interview_scorecards_redacted": interview_scorecards_redacted,
        "interviewer_notes_deleted": interviewer_notes_deleted,
        "stage_exceptions_redacted": stage_exceptions_redacted,
        "interview_sessions_cancelled": interview_sessions_cancelled,
        "interview_loops_redacted": interview_loops_redacted,
        "offers_redacted": offers_redacted,
        "preboarding_documents_redacted": preboarding_documents_redacted,
        "accommodations_revoked": accommodations_revoked,
        "accommodations_redacted": accommodations_redacted,
        "code_attempts_redacted": code_attempts_redacted,
        "code_reports_deleted": code_reports_deleted,
        "code_fingerprints_deleted": code_fingerprints_deleted,
        "code_signals_deleted": code_signals_deleted,
        "code_findings_redacted": code_findings_redacted,
        "task_submissions_withdrawn": task_submissions_withdrawn,
        "task_submissions_redacted": task_submissions_redacted,
        "task_responses_redacted": task_responses_redacted,
        "hire_checkins_deleted": hire_checkins_deleted,
        "scorecard_s3_keys": scorecard_keys,
        # Count what we actually deleted, not what we assumed. The old
        # expression was `len(scorecard_keys) * 2 + (1 if user_resume_s3_key)`,
        # which double-counted scorecards with a NULL transcript_key and
        # ignored resume versions entirely — so the number written to the DPDP
        # artifacts record did not describe the erasure it claimed to.
        "s3_objects_deleted": s3_objects_deleted,
        # The per-category counts are safe as collected lengths only because we
        # never get here unless every collected key was deleted (or there were
        # none): step 8 raises otherwise.
        "resume_objects_deleted": len(resume_bucket_keys),
        "turn_audio_objects_deleted": len(turn_audio_keys),
    }
    await db.execute(
        update(ErasureRequest)
        .where(ErasureRequest.request_id == request.request_id)
        .values(
            status="completed",
            completed_at=now_utc,
            artifacts=artifacts,
        )
    )

    # ------------------------------------------------------------------
    # Step 10: Write audit_log entry (action only, zero PII)
    # ------------------------------------------------------------------
    audit_row = AuditLog(
        actor_id=system_actor_id,
        actor_type="system",
        action="dpdp_erasure_completed",
        resource_type="user",
        resource_id=user_id,
        details={
            "request_id": str(request.request_id),
            "turns_deleted": turns_deleted,
            "resumes_deleted": resumes_deleted,
            "scorecards_deleted": scorecards_deleted,
            "sessions_deleted": sessions_deleted,
            "notifications_deleted": notifications_deleted,
            "applicants_anonymised": applicants_anonymised,
            "interview_assignments_withdrawn": interview_assignments_withdrawn,
            "interview_evidence_redacted": interview_evidence_redacted,
            "interview_scorecards_redacted": interview_scorecards_redacted,
            "interviewer_notes_deleted": interviewer_notes_deleted,
            "stage_exceptions_redacted": stage_exceptions_redacted,
            "interview_sessions_cancelled": interview_sessions_cancelled,
            "interview_loops_redacted": interview_loops_redacted,
            "offers_redacted": offers_redacted,
            "preboarding_documents_redacted": preboarding_documents_redacted,
            "accommodations_revoked": accommodations_revoked,
            "accommodations_redacted": accommodations_redacted,
        },
        ip_address=None,
        user_agent=None,
        event_ts=now_utc,
    )
    db.add(audit_row)

    return artifacts


# ---------------------------------------------------------------------------
# Poll + claim loop — processes ALL due requests in one poll cycle
# ---------------------------------------------------------------------------


async def run_erasure_poll(
    session_factory: async_sessionmaker[AsyncSession],
    system_actor_id: uuid.UUID,
    settings: Settings | None = None,
) -> int:
    """Claim and execute all due erasure requests.

    Uses ``SELECT … FOR UPDATE SKIP LOCKED`` so multiple running instances
    never process the same row.  Each request is processed in its own
    transaction so a failure on request N does not roll back request N-1.

    Args:
        session_factory:  The admin_ops async session factory.
        system_actor_id:  UUID used as actor_id in audit_log entries.
        settings:         Admin-ops Settings — passed through to
                          ``_execute_one_erasure`` for S3 deletion.  When None,
                          a request with object keys is left pending rather
                          than falsely completed.

    Returns:
        The number of requests successfully completed in this poll cycle.
    """
    completed_count = 0

    # First pass: discover IDs of due requests.  We do a lightweight
    # non-locking query so the discovery read is cheap and does not hold
    # locks across the loop.
    async with session_factory() as discovery_session:
        result = await discovery_session.execute(
            text(
                "SELECT request_id FROM erasure_requests "
                "WHERE status = 'pending' AND scheduled_for <= :now "
                "ORDER BY scheduled_for "
                "LIMIT 100"
            ),
            {"now": datetime.now(UTC)},
        )
        candidate_ids: list[str] = [str(row[0]) for row in result.fetchall()]

    if not candidate_ids:
        return 0

    log.info(
        "erasure.executor.poll_found",
        candidate_count=len(candidate_ids),
    )

    for rid_str in candidate_ids:
        # Each request gets its own transaction with FOR UPDATE SKIP LOCKED
        # so two instances do not race on the same row.
        async with session_factory() as db:
            try:
                # Claim the row atomically — skip if already locked by a
                # sibling instance.
                claim_result = await db.execute(
                    text(
                        "SELECT request_id, user_id, status "
                        "FROM erasure_requests "
                        "WHERE request_id = :rid "
                        "  AND status = 'pending' "
                        "  AND scheduled_for <= :now "
                        "FOR UPDATE SKIP LOCKED"
                    ),
                    {"rid": rid_str, "now": datetime.now(UTC)},
                )
                row = claim_result.fetchone()
                if row is None:
                    # Already claimed by another instance or no longer pending.
                    log.info(
                        "erasure.executor.row_skipped",
                        request_id=rid_str,
                        reason="locked_or_stale",
                    )
                    continue

                # Reload the full ORM object (we have the lock now).
                req_result = await db.execute(
                    text(
                        "SELECT request_id, user_id, requested_by, reason, "
                        "status, scheduled_for, completed_at, artifacts, created_at "
                        "FROM erasure_requests WHERE request_id = :rid"
                    ),
                    {"rid": rid_str},
                )
                req_row = req_result.fetchone()
                if req_row is None:
                    continue

                # Build a lightweight ErasureRequest-like object.
                er = ErasureRequest(
                    request_id=uuid.UUID(str(req_row[0])),
                    user_id=uuid.UUID(str(req_row[1])),
                    requested_by=uuid.UUID(str(req_row[2])),
                    reason=req_row[3],
                    status=req_row[4],
                    scheduled_for=req_row[5],
                    completed_at=req_row[6],
                    artifacts=req_row[7],
                    created_at=req_row[8],
                )

                await _execute_one_erasure(
                    db=db,
                    request=er,
                    system_actor_id=system_actor_id,
                    settings=settings,
                )
                await db.commit()
                completed_count += 1
                log.info(
                    "erasure.executor.request_completed",
                    request_id=rid_str,
                    user_id=str(er.user_id),
                )

            except (StorageNotConfiguredError, ErasureIncompleteError) as exc:
                # Named separately from the catch-all below so the log says
                # "this deployment cannot delete objects" — an operator fix —
                # rather than burying it in unexpected_error. The row stays
                # 'pending' and is retried every cycle until storage works.
                await db.rollback()
                log.error(
                    "erasure.executor.storage_refusal",
                    request_id=rid_str,
                    exc_type=type(exc).__name__,
                    exc_msg=str(exc),
                )
            except SQLAlchemyError as exc:
                await db.rollback()
                log.error(
                    "erasure.executor.request_failed",
                    request_id=rid_str,
                    exc_type=type(exc).__name__,
                    exc_msg=str(exc),
                )
            except Exception as exc:  # noqa: BLE001 — broad catch to never kill the loop
                await db.rollback()
                log.error(
                    "erasure.executor.unexpected_error",
                    request_id=rid_str,
                    exc_type=type(exc).__name__,
                    exc_msg=str(exc),
                )

    return completed_count


# ---------------------------------------------------------------------------
# Background task — runs forever, sleeping between poll cycles
# ---------------------------------------------------------------------------


async def erasure_executor_task(
    session_factory: async_sessionmaker[AsyncSession],
    poll_interval_seconds: int = ERASURE_POLL_INTERVAL_SECONDS,
    system_actor_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> None:
    """Async background task suitable for ``asyncio.create_task()``.

    Runs indefinitely until cancelled (e.g. on app shutdown).
    Sleeps between poll cycles — does NOT busy-wait.

    Args:
        session_factory:        The admin_ops async session factory.
        poll_interval_seconds:  Seconds to sleep between poll cycles.
        system_actor_id:        UUID used as actor_id in audit_log entries.
                                Defaults to a stable nil-adjacent sentinel UUID.
        settings:               Admin-ops Settings — passed through to
                                ``run_erasure_poll`` for S3 deletion.
    """
    actor = system_actor_id or uuid.UUID("00000000-0000-0000-0000-000000000001")
    log.info(
        "erasure.executor.started",
        poll_interval_seconds=poll_interval_seconds,
        system_actor_id=str(actor),
    )
    while True:
        try:
            completed = await run_erasure_poll(
                session_factory=session_factory,
                system_actor_id=actor,
                settings=settings,
            )
            if completed:
                log.info(
                    "erasure.executor.cycle_complete",
                    completed=completed,
                )
        except asyncio.CancelledError:
            log.info("erasure.executor.cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 — polling errors must not kill the task
            log.error(
                "erasure.executor.poll_error",
                exc_type=type(exc).__name__,
                exc_msg=str(exc),
            )
        await asyncio.sleep(poll_interval_seconds)
