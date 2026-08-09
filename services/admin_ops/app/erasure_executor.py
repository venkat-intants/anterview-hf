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
     resume_text, resume_s3_key, embedding → redacted / NULL; user_id NULL).
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
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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
}

#: Tables deliberately left standing, each with the reason it is defensible.
EXCLUDED_TABLES: dict[str, str] = {
    # --- Records that exist to prove the erasure / prior consent -----------
    "erasure_requests": "the §12 record of this very erasure. Deleting it would "
                        "destroy the evidence that the request was honoured.",
    "audit_log": "immutable compliance trail. Carries user UUIDs and action "
                 "names only — never email, name or phone (see PII safety above).",
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
    # --- Candidate-DERIVED, but reached through applicants ------------------
    # These four are the judgement call in this list, so the reasoning is
    # written out rather than asserted: they hang off `applicants`, which step 6
    # anonymises rather than deletes. Once full_name is '[redacted]', email is
    # NULL and user_id is NULL, an attempt and its proctoring events belong to
    # an applicant that identifies nobody — they are the COMPANY's assessment
    # record, not the erased user's. Deleting them would destroy a fiduciary's
    # own hiring evidence to no privacy gain. This is why they differ from
    # `integrity_events` above, which hangs off `sessions` — a session is the
    # user's own practice run and is hard-deleted, so its events go with it.
    "exam_assignments": "keys off applicants (anonymised in step 6); holds a "
                        "token hash and a schedule, no personal data.",
    "exam_attempts": "the company's graded assessment record, attached to the "
                     "anonymised applicant rather than to the erased user.",
    "exam_integrity_events": "proctoring events for an exam_attempts row — see "
                             "exam_attempts. Event type + timestamp only; raw "
                             "camera/keystroke input never leaves the browser.",
    "interview_invites": "keys off applicants/company. guest_user_id and "
                         "created_by_user_id resolve to anonymised users rows, "
                         "and session_id nulls itself (ON DELETE SET NULL) when "
                         "step 5 deletes the session.",
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
        # Bumped 1.1 → 1.2 when step 5b (notifications) joined the erasure: the
        # artifacts record is what an auditor reads to know WHAT a given
        # completion covered, so two records with different coverage must not
        # claim the same version.
        "executor_version": "1.2",
        "completed_at": now_utc.isoformat(),
        "turns_deleted": turns_deleted,
        "resumes_deleted": resumes_deleted,
        "scorecards_deleted": scorecards_deleted,
        "sessions_deleted": sessions_deleted,
        "notifications_deleted": notifications_deleted,
        "applicants_anonymised": applicants_anonymised,
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
