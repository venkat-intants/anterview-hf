"""Reconciliation loop — A1.

What this replaces
------------------
Three enrichment steps in this service are written as "best-effort": ATS scoring
on applicant upload, resume embedding, and the same two inside a bulk upload.
Each catches its own failure, logs a warning, and moves on — which is right, an
outage must not lose the applicant. What was missing is the other half: nothing
ever came back for the row afterwards.

The observable consequences today are a manual **Reindex** button that processes
96 applicants per click, and applicants that sit unscored indefinitely with a
code comment that says "HR can POST /rescore later". Both are a human doing a
retry loop by hand.

How it finds work
-----------------
By the *absence* of data, not by a queue:

* ``ats_overall IS NULL`` and there is resume text -> needs scoring
* ``embedding IS NULL`` and there is resume text  -> needs embedding
* a completed interview with no scorecard          -> needs scoring (from ``turns``)
* a scorecard with ``report_pdf_key IS NULL``      -> needs its PDF

This is deliberate. A queue can drift from reality — a lost message means a row
is never retried and nothing notices. The absence query cannot drift: if the
column is null, the work is outstanding, whatever happened before. It also means
this loop correctly picks up rows that failed *before* it was deployed.

``reconciliation_state`` exists only to stop a permanently broken row consuming
the batch on every cycle. It is an exception list, never the work list; deleting
every row in it simply causes the next pass to retry everything at once.

Why an asyncio loop rather than APScheduler
-------------------------------------------
This is interval work, not calendar work, so it follows the house pattern for
interval work already used by the email outbox worker and the erasure executor:
a task started in ``lifespan`` that sleeps between passes. That also sidesteps
A6 entirely — an interval loop resumes correctly after the Space wakes, whereas
a fixed-hour cron silently skips the window it slept through.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.applicant_enrichment import (
    apply_ats_score,
    apply_extracted_identity,
    store_embedding,
)
from app.config import settings
from app.embedding_client import embed_texts_remote
from app.models import Applicant
from app.notifications_util import create_notification
from app.scheduling import record_loop_pass
from app.scoring_client import (
    ScoringServiceUnavailableError,
    render_scorecard_pdf_remote,
    score_interview_remote,
    score_resume_remote,
)

log = structlog.get_logger(__name__)

# This loop's row in scheduled_job_runs (A6).
LOOP_JOB_ID = "reconciliation_loop"

# How many rows one pass will attempt per check. Small on purpose: a pass should
# finish well inside its interval even when every call is slow, so a backlog
# drains steadily instead of one pass running for an hour and overlapping.
SCORE_BATCH = 12
EMBED_BATCH = 32
# The embedder accepts up to 64 per call; 16 matches what the upload path uses.
EMBED_CHUNK = 16

# Backoff between attempts on one row: ~5 min, 20 min, 80 min, 5.3 h, capped.
_BACKOFF_BASE_SECONDS = 300
_BACKOFF_CAP_SECONDS = 6 * 3600
# After this many attempts a row is parked and reported rather than retried
# forever. A resume that cannot be scored after eight tries is not a transient
# outage; it is a bad row that wants a human.
MAX_ATTEMPTS = 8

KIND_ATS = "applicant_ats"
KIND_EMBED = "applicant_embedding"
KIND_SCORECARD = "session_scorecard"
KIND_PDF = "scorecard_pdf"

# Interviews are re-scored a few at a time: each is a paid LLM call that can
# take a minute, and a pass must finish inside its ten-minute interval.
SCORECARD_BATCH = 3
PDF_BATCH = 10
# Fewer attempts than a resume: every attempt that reaches Gemini is spend
# against the per-session budget, and a transcript the scorer rejected four
# times over a day is a case for a person, not for a ninth try.
SCORECARD_MAX_ATTEMPTS = 4

# Must equal interview_core's worker/constants.py MIN_ANSWERS_TO_SCORE — the
# worker's own "was this interview long enough to score" rule. Mirrored rather
# than imported because the services do not share code; a test pins the two.
MIN_ANSWERS_TO_SCORE = 2

# Only interviews finished within this window are re-scored. The results email
# fires when a scorecard appears, and a candidate should not be told about an
# interview from last quarter because a deploy found it unscored.
_SCORECARD_LOOKBACK = timedelta(days=7)
# The live worker's own scoring call can still land for a while after the
# session is marked completed (the scorer outlives the worker's 15s timeout),
# so the reconciler leaves recent sessions — and recent PDFs — to it.
_SCORECARD_GRACE = timedelta(minutes=15)
_PDF_GRACE = timedelta(minutes=10)


@dataclass
class PassResult:
    """What one pass did — returned for logging, tests and the ops endpoint."""

    scored: int = 0
    embedded: int = 0
    # Interviews whose scorecard was written by this pass, and scorecards whose
    # PDF was rendered by it.
    interviews_scored: int = 0
    pdfs_rendered: int = 0
    # Rows whose real name (and sometimes email) were read out of the PDF and
    # written over the filename-derived placeholder a deferred ingest left.
    named: int = 0
    # Bulk uploads whose last outstanding row finished during this pass, and
    # which therefore produced one "upload complete" notification (A4).
    batches_finished: int = 0
    failed: int = 0
    gave_up: int = 0
    outstanding: dict[str, int] = field(default_factory=dict)
    # "pass: ErrorType: message" for each pass that raised. The others still
    # ran; this is how the failure reaches the job record (A6).
    failed_passes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scored": self.scored,
            "embedded": self.embedded,
            "interviews_scored": self.interviews_scored,
            "pdfs_rendered": self.pdfs_rendered,
            "named": self.named,
            "batches_finished": self.batches_finished,
            "failed": self.failed,
            "gave_up": self.gave_up,
            "outstanding": self.outstanding,
            "failed_passes": self.failed_passes,
        }


def _backoff(attempts: int) -> timedelta:
    return timedelta(
        seconds=min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2 ** max(0, attempts - 1)))
    )


async def _record_failure(
    db: AsyncSession,
    kind: str,
    ref_id: uuid.UUID,
    error: str,
    *,
    max_attempts: int = MAX_ATTEMPTS,
) -> bool:
    """Note a failed attempt and schedule the retry. Returns True if it gave up.

    The row is upserted so the first failure creates it; ``attempts`` drives both
    the backoff and the give-up decision.
    """
    now = datetime.now(tz=UTC)
    row = (
        await db.execute(
            text(
                """
                INSERT INTO reconciliation_state
                       (kind, ref_id, attempts, last_error, last_attempt_at, next_attempt_at)
                VALUES (:kind, :ref, 1, :err, :now, :now)
                ON CONFLICT (kind, ref_id) DO UPDATE
                   SET attempts        = reconciliation_state.attempts + 1,
                       last_error      = :err,
                       last_attempt_at = :now
                RETURNING attempts
                """
            ),
            {"kind": kind, "ref": ref_id, "err": error[:1000], "now": now},
        )
    ).first()
    attempts = int(row[0]) if row else 1

    if attempts >= max_attempts:
        await db.execute(
            text(
                "UPDATE reconciliation_state SET gave_up_at = :now, next_attempt_at = NULL "
                "WHERE kind = :kind AND ref_id = :ref"
            ),
            {"kind": kind, "ref": ref_id, "now": now},
        )
        await db.commit()
        log.warning(
            "reconcile.gave_up", kind=kind, ref_id=str(ref_id), attempts=attempts, error=error[:200]
        )
        return True

    await db.execute(
        text(
            "UPDATE reconciliation_state SET next_attempt_at = :nxt "
            "WHERE kind = :kind AND ref_id = :ref"
        ),
        {"kind": kind, "ref": ref_id, "nxt": now + _backoff(attempts)},
    )
    await db.commit()
    return False


async def _clear_state(db: AsyncSession, kind: str, ref_id: uuid.UUID) -> None:
    """Forget a row that has now succeeded, so a later failure starts fresh."""
    await db.execute(
        text("DELETE FROM reconciliation_state WHERE kind = :kind AND ref_id = :ref"),
        {"kind": kind, "ref": ref_id},
    )


# The shared tail of both selects: outstanding work that is not currently
# backing off and has not been given up on. Written once because the two checks
# differ only in the column they test for absence.
_DUE_PREDICATE = """
      AND a.deleted_at IS NULL
      AND a.resume_text IS NOT NULL
      AND length(trim(a.resume_text)) > 0
      AND NOT EXISTS (
            SELECT 1 FROM reconciliation_state rs
             WHERE rs.kind = :kind AND rs.ref_id = a.id
               AND (rs.gave_up_at IS NOT NULL
                    OR (rs.next_attempt_at IS NOT NULL AND rs.next_attempt_at > :now))
          )
"""


# A pending row is "stuck" when this loop will never finish it: it has no text
# to score, it has been parked after MAX_ATTEMPTS, or it was scored some other
# way (HR's manual rescore writes the score but does not clear the flag). Stuck
# rows must not hold the batch open — they used to, and one unreadable PDF out
# of twenty-five meant the upload was never reported finished at all.
_STUCK = f"""(
       a.ats_overall IS NOT NULL
    OR a.resume_text IS NULL OR length(trim(a.resume_text)) = 0
    OR EXISTS (SELECT 1 FROM reconciliation_state rs
                WHERE rs.kind = '{KIND_ATS}' AND rs.ref_id = a.id
                  AND rs.gave_up_at IS NOT NULL)
)"""

_BATCH_COUNTS_SQL = f"""
SELECT count(*) AS total,
       count(*) FILTER (WHERE a.pending_enrichment AND a.deleted_at IS NULL
                          AND NOT {_STUCK}) AS outstanding,
       count(*) FILTER (WHERE a.pending_enrichment AND a.deleted_at IS NULL
                          AND a.ats_overall IS NULL AND {_STUCK}) AS unreadable
  FROM applicants a
 WHERE a.upload_batch_id = :b
"""


async def _notify_batch_done(
    db: AsyncSession, batch_id: uuid.UUID | None, uploader_id: uuid.UUID | None
) -> bool:
    """Tell the uploader when the LAST row of their bulk upload finishes (A4).

    Called after a row has been scored and committed, or after one has been
    given up on, so the counts below already reflect it. Nothing outstanding
    means this row was the last one the loop is going to finish.

    One notification per batch, not per applicant: the whole point of the batch
    id is that twenty-five separate reconciler passes are one event to the
    person who started them. The dedupe key makes that hold even when two
    passes finish a batch's last two rows at the same moment and both count
    zero outstanding.

    Takes ids rather than the Applicant because the give-up path calls it after
    a rollback, which expires the loaded row — touching its attributes then
    would be a lazy load from async code.

    Returns True when a notification was staged, purely so the pass can count
    it. Failure here must never fail the enrichment that succeeded: the score is
    written and committed before this runs.
    """
    if batch_id is None or uploader_id is None:
        return False
    counts = (
        await db.execute(text(_BATCH_COUNTS_SQL), {"b": batch_id})
    ).mappings().first()
    if counts is None or counts["outstanding"]:
        return False
    total, unreadable = int(counts["total"] or 0), int(counts["unreadable"] or 0)
    body = f"{total} resume{'' if total == 1 else 's'} have been read and scored."
    if unreadable:
        body = (
            f"{total - unreadable} of {total} resumes have been read and scored. "
            f"{unreadable} could not be read — open them to check the file."
        )
    staged = await create_notification(
        db,
        user_id=uploader_id,
        kind="bulk_upload",
        title="Bulk upload finished",
        body=body,
        link="/hr/applicants",
        dedupe_key=f"bulk_upload:{batch_id}",
    )
    await db.commit()
    return staged


async def _score_pass(db: AsyncSession, result: PassResult) -> None:
    """Retry ATS scoring for applicants that have none."""
    now = datetime.now(tz=UTC)
    ids = [
        r[0]
        for r in (
            await db.execute(
                text(
                    "SELECT a.id FROM applicants a WHERE a.ats_overall IS NULL"
                    + _DUE_PREDICATE
                    + " ORDER BY a.created_at LIMIT :lim"
                ),
                {"kind": KIND_ATS, "now": now, "lim": SCORE_BATCH},
            )
        ).all()
    ]
    if not ids:
        return

    for aid in ids:
        applicant = await db.get(Applicant, aid)
        if applicant is None:  # deleted between the select and here
            continue
        # Read now: the failure path rolls back, which expires `applicant`.
        batch_id, uploader_id = applicant.upload_batch_id, applicant.created_by_user_id
        try:
            score = await score_resume_remote(
                resume_text=applicant.resume_text or "",
                job_title=applicant.target_job_title,
                level=applicant.target_level,
                jd_text=applicant.target_jd_text,
                # Attributed to the account that created the applicant so the
                # internal-token audit trail names a real actor rather than a
                # synthetic "system" principal that has no company scope.
                acting_user_id=str(applicant.created_by_user_id or applicant.id),
            )
        except Exception as exc:  # noqa: BLE001 — one bad row must not stop the pass
            await db.rollback()
            result.failed += 1
            if await _record_failure(db, KIND_ATS, aid, f"{type(exc).__name__}: {exc}"):
                result.gave_up += 1
                # A parked row is finished as far as this loop is concerned, so
                # it can be the one that completes its batch.
                await _announce_batch(db, result, aid, batch_id, uploader_id)
            continue

        apply_ats_score(applicant, score)
        # A deferred ingest stored this row without reading it, so its name is
        # derived from the filename and its email is missing. The scorer just
        # read both out of the PDF — this is the only moment they are available,
        # so it is the moment they get written.
        if apply_extracted_identity(applicant, score):
            result.named += 1
        await _clear_state(db, KIND_ATS, aid)
        await db.commit()
        result.scored += 1
        # After the commit: the batch is only finished once this row's own
        # pending flag is durably cleared, and a notification is never worth
        # rolling back a score for.
        await _announce_batch(db, result, aid, batch_id, uploader_id)


async def _announce_batch(
    db: AsyncSession,
    result: PassResult,
    aid: uuid.UUID,
    batch_id: uuid.UUID | None,
    uploader_id: uuid.UUID | None,
) -> None:
    try:
        if await _notify_batch_done(db, batch_id, uploader_id):
            result.batches_finished += 1
    except Exception as exc:  # noqa: BLE001 — notification is not the work
        await db.rollback()
        log.warning(
            "reconcile.batch_notify_failed",
            applicant_id=str(aid),
            error_type=type(exc).__name__,
        )


async def _embed_pass(db: AsyncSession, result: PassResult) -> None:
    """Backfill resume embeddings — what the manual Reindex button did by hand."""
    now = datetime.now(tz=UTC)
    rows = (
        await db.execute(
            text(
                "SELECT a.id, a.company_id, a.resume_text FROM applicants a "
                "WHERE a.embedding IS NULL"
                + _DUE_PREDICATE
                + " ORDER BY a.created_at LIMIT :lim"
            ),
            {"kind": KIND_EMBED, "now": now, "lim": EMBED_BATCH},
        )
    ).all()
    if not rows:
        return

    for i in range(0, len(rows), EMBED_CHUNK):
        chunk = rows[i : i + EMBED_CHUNK]
        try:
            vecs = await embed_texts_remote(
                texts=[r[2] for r in chunk],
                task_type="document",
                acting_user_id=str(chunk[0][0]),
            )
        except Exception as exc:  # noqa: BLE001 — one bad chunk must not stop the pass
            await db.rollback()
            # A chunk failure is almost always the embedder being down rather
            # than these particular rows being bad, so every row in the chunk
            # backs off together and the next pass tries a different slice.
            for r in chunk:
                if await _record_failure(db, KIND_EMBED, r[0], f"{type(exc).__name__}: {exc}"):
                    result.gave_up += 1
                result.failed += 1
            continue

        for r, vec in zip(chunk, vecs, strict=False):
            if not vec:
                continue
            await store_embedding(db, r[1], r[0], vec)
            await _clear_state(db, KIND_EMBED, r[0])
            result.embedded += 1
        await db.commit()


# ---------------------------------------------------------------------------
# Interviews without a scorecard, scorecards without a PDF
# ---------------------------------------------------------------------------
# The same "found by absence" rule as the applicant passes, one layer later in
# the funnel. The live worker scores once (one retry, two seconds apart) and the
# scorer renders the PDF once, fire-and-forget; after that, nothing came back
# for either. A candidate whose scorecard call hit a Gemini outage finished
# their interview and was never assessed — and HR, whose invite now sat at
# 'consumed' forever, was never told they had finished.

def _not_backing_off(alias: str, kind_param: str) -> str:
    """The reconciliation_state exclusion, for a row whose id is ``alias``."""
    return f"""
       AND NOT EXISTS (
             SELECT 1 FROM reconciliation_state rs
              WHERE rs.kind = :{kind_param} AND rs.ref_id = {alias}
                AND (rs.gave_up_at IS NOT NULL
                     OR (rs.next_attempt_at IS NOT NULL AND rs.next_attempt_at > :now))
           )"""


# Which finished interviews are owed a scorecard. Every clause is a reason the
# live worker would, or would not, have scored it:
#
# * status 'completed' — the worker writes 'completed' only when the candidate
#   gave enough answers (InterviewState.final_status). 'abandoned' covers both a
#   short interview and a crash the startup reaper finalised, and neither is
#   ever scored automatically: a scorecard from a truncated transcript reads as
#   a complete assessment, and the reaper's docstring is explicit that the
#   platform must not manufacture one.
# * enough candidate turns on record — the transcript is what gets scored, so a
#   session whose turns never persisted cannot be retried and is not selected.
# * an active interview consent — the same gate that let the interview start.
#   A candidate who has since withdrawn it is not processed further.
# * neither the session nor the user is pending erasure.
_UNSCORED_SQL = """
SELECT s.id, s.language, j.title AS job_title, j.level, j.description AS jd_text,
       j.department, j.company_name, j.interview_type,
       e.current_round_id
  FROM sessions s
  JOIN users u ON u.id = s.user_id AND u.deleted_at IS NULL
  JOIN jobs  j ON j.id = s.job_id
  LEFT JOIN interview_invites ii ON ii.session_id = s.id AND ii.deleted_at IS NULL
  LEFT JOIN enrolments e ON e.id = ii.enrolment_id AND e.deleted_at IS NULL
 WHERE s.status = 'completed'
   AND s.deleted_at IS NULL
   AND s.completed_at > :floor
   AND s.completed_at <= :settled
   AND NOT EXISTS (SELECT 1 FROM scorecards sc WHERE sc.session_id = s.id)
   AND (SELECT count(*) FROM turns t
         WHERE t.session_id = s.id AND t.speaker = 'candidate'
           AND length(trim(t.text_content)) > 0) >= :min_answers
   AND EXISTS (SELECT 1 FROM dpdp_consent_ledger c
                WHERE c.user_id = s.user_id
                  AND c.consent_type = 'interview_voice_recording'
                  AND c.granted AND c.revoked_at IS NULL)
""" + _not_backing_off("s.id", "kind") + """
 ORDER BY s.completed_at
 LIMIT :lim
"""

_TURNS_SQL = """
SELECT speaker, text_content FROM turns
 WHERE session_id = :sid AND length(trim(text_content)) > 0
 ORDER BY turn_number
"""


async def _role_profile_for(db: AsyncSession, row: Any) -> dict[str, Any] | None:
    """The rubric to score against, as the live worker would have chosen it.

    A workflow round's frozen rubric when the session belongs to one — the same
    lookup the worker does (invite -> enrolment -> current round), so the retry
    grades against exactly what the round promised. Otherwise the deterministic
    taxonomy baseline: the profile the worker itself falls back to when Gemini
    is unavailable. The live interview may have had an LLM-refined profile that
    was never stored, so for a practice interview this is the closest faithful
    rubric that costs nothing extra. Never raises — a missing rubric means the
    scorer's legacy fixed axes, which is still a scorecard.
    """
    from shared.intelligence import derive_role_profile  # noqa: PLC0415

    from app.workflow_runner import frozen_rubric_for_round  # noqa: PLC0415

    try:
        if row["current_round_id"] is not None:
            frozen = await frozen_rubric_for_round(
                db, round_id=row["current_round_id"], job_title=row["job_title"]
            )
            if frozen is not None:
                return frozen.model_dump(mode="json")
        profile = await derive_role_profile(
            job_title=row["job_title"],
            jd_text=row["jd_text"] or "",
            department=row["department"] or "",
            company_name=row["company_name"] or "",
            experience_level=_level(row["level"]),
            interview_type=row["interview_type"] or "screening",
            llm=None,
        )
        return profile.model_dump(mode="json")
    except Exception as exc:  # noqa: BLE001 — degrade to the legacy rubric
        log.warning("reconcile.role_profile_failed", session_id=str(row["id"]),
                    error_type=type(exc).__name__)
        return None


def _level(level: str | None) -> str:
    # Same normalisation as the worker's _lookup_session.
    return level if level in ("entry", "mid", "senior") else "entry"


async def _scorecard_pass(db: AsyncSession, result: PassResult) -> None:
    """Score finished interviews whose scorecard was never written."""
    now = datetime.now(tz=UTC)
    rows = (
        await db.execute(
            text(_UNSCORED_SQL),
            {
                "kind": KIND_SCORECARD, "now": now,
                "floor": now - _SCORECARD_LOOKBACK, "settled": now - _SCORECARD_GRACE,
                "min_answers": MIN_ANSWERS_TO_SCORE, "lim": SCORECARD_BATCH,
            },
        )
    ).mappings().all()

    for row in rows:
        sid = row["id"]
        turns = [
            # Back to the worker's vocabulary — the scorer accepts both, but
            # the retry should send exactly what the live call would have.
            {"role": "user" if t[0] == "candidate" else "ai", "text": t[1]}
            for t in (await db.execute(text(_TURNS_SQL), {"sid": sid})).all()
        ]
        lang = str(row["language"] or "en").lower()
        payload: dict[str, Any] = {
            "session_id": str(sid),
            "job_title": row["job_title"],
            "experience_level": _level(row["level"]),
            "language": lang if lang in ("en", "hi", "te") else "en",
            "jd_text": row["jd_text"] or "",
            "turns": turns,
        }
        profile = await _role_profile_for(db, row)
        if profile is not None:
            payload["role_profile"] = profile
        # Release the connection before a call that can take minutes.
        await db.rollback()

        try:
            outcome = await score_interview_remote(payload)
        except ScoringServiceUnavailableError as exc:
            # The scorer is down or refusing every call. Nothing about this
            # session failed, so nothing is charged to it — the next pass
            # simply tries again. See ScoringServiceUnavailableError.
            await db.rollback()
            log.warning("reconcile.scorer_unavailable", error=str(exc)[:200])
            return
        except Exception as exc:  # noqa: BLE001 — one bad session must not stop the pass
            await db.rollback()
            result.failed += 1
            if await _record_failure(
                db, KIND_SCORECARD, sid, f"{type(exc).__name__}: {exc}",
                max_attempts=SCORECARD_MAX_ATTEMPTS,
            ):
                result.gave_up += 1
            continue

        await _clear_state(db, KIND_SCORECARD, sid)
        await db.commit()
        if outcome == "created":
            result.interviews_scored += 1
            # Deliberately nothing else. The reminder sweep finds a new
            # scorecard by the same absence rule and sends the candidate's
            # results email and HR's "interview completed" notice — exactly as
            # it does for a scorecard the live path wrote.
            log.info("reconcile.interview_scored", session_id=str(sid))


_MISSING_PDF_SQL = """
SELECT sc.scorecard_id
  FROM scorecards sc
 WHERE sc.report_pdf_key IS NULL
   AND sc.created_at <= :settled
""" + _not_backing_off("sc.scorecard_id", "kind") + """
 ORDER BY sc.created_at
 LIMIT :lim
"""


async def _park(db: AsyncSession, kind: str, ref_id: uuid.UUID, reason: str) -> None:
    """Record work that will never succeed, so it stops being selected.

    Distinct from a failure: nothing is retried and no backoff applies. The row
    stays visible in the "parked" count, with its reason.
    """
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            """
            INSERT INTO reconciliation_state
                   (kind, ref_id, attempts, last_error, last_attempt_at, gave_up_at)
            VALUES (:kind, :ref, 1, :err, :now, :now)
            ON CONFLICT (kind, ref_id) DO UPDATE
               SET last_error = :err, last_attempt_at = :now,
                   gave_up_at = :now, next_attempt_at = NULL
            """
        ),
        {"kind": kind, "ref": ref_id, "err": reason[:1000], "now": now},
    )
    await db.commit()


async def _pdf_pass(db: AsyncSession, result: PassResult) -> None:
    """Render PDFs for scorecards whose fire-and-forget render never landed."""
    now = datetime.now(tz=UTC)
    ids = [
        r[0]
        for r in (
            await db.execute(
                text(_MISSING_PDF_SQL),
                {"kind": KIND_PDF, "now": now, "settled": now - _PDF_GRACE,
                 "lim": PDF_BATCH},
            )
        ).all()
    ]
    await db.rollback()

    for scid in ids:
        try:
            body = await render_scorecard_pdf_remote(str(scid))
        except ScoringServiceUnavailableError as exc:
            # Includes "storage not configured", which is how every local
            # environment without S3 answers. Charging each scorecard an
            # attempt would park them all, and they would stay parked after
            # storage was configured.
            await db.rollback()
            log.info("reconcile.pdf_service_unavailable", error=str(exc)[:200])
            return
        except Exception as exc:  # noqa: BLE001 — one bad render must not stop the pass
            await db.rollback()
            result.failed += 1
            if await _record_failure(db, KIND_PDF, scid, f"{type(exc).__name__}: {exc}"):
                result.gave_up += 1
            continue

        if body.get("status") == "not_applicable":
            # No candidate name, or pending erasure: a retry would get the
            # same answer, so it is parked rather than retried.
            await _park(db, KIND_PDF, scid, f"not_applicable: {body.get('reason')}")
            continue
        await _clear_state(db, KIND_PDF, scid)
        await db.commit()
        if body.get("status") == "rendered":
            result.pdfs_rendered += 1


async def _outstanding(db: AsyncSession) -> dict[str, int]:
    """How much work is still queued — the number an operator actually wants."""
    row = (
        await db.execute(
            text(
                """
                SELECT
                  count(*) FILTER (WHERE ats_overall IS NULL)  AS unscored,
                  count(*) FILTER (WHERE embedding   IS NULL)  AS unembedded
                FROM applicants
                WHERE deleted_at IS NULL
                  AND resume_text IS NOT NULL
                  AND length(trim(resume_text)) > 0
                """
            )
        )
    ).first()
    parked = await db.scalar(
        text("SELECT count(*) FROM reconciliation_state WHERE gave_up_at IS NOT NULL")
    )
    later = (
        await db.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM sessions s
                    WHERE s.status = 'completed' AND s.deleted_at IS NULL
                      AND s.completed_at > :floor
                      AND NOT EXISTS (SELECT 1 FROM scorecards sc
                                       WHERE sc.session_id = s.id)) AS unscored_interviews,
                  (SELECT count(*) FROM scorecards sc
                    WHERE sc.report_pdf_key IS NULL
                      AND NOT EXISTS (SELECT 1 FROM reconciliation_state rs
                                       WHERE rs.kind = :pdf AND rs.ref_id = sc.scorecard_id
                                         AND rs.gave_up_at IS NOT NULL)) AS missing_pdfs
                """
            ),
            {"floor": datetime.now(tz=UTC) - _SCORECARD_LOOKBACK, "pdf": KIND_PDF},
        )
    ).first()
    return {
        "unscored": int(row[0] or 0) if row else 0,
        "unembedded": int(row[1] or 0) if row else 0,
        "unscored_interviews": int(later[0] or 0) if later else 0,
        "missing_pdfs": int(later[1] or 0) if later else 0,
        "parked": int(parked or 0),
    }


async def run_once(factory: async_sessionmaker[AsyncSession]) -> PassResult:
    """One reconciliation pass. Safe to call concurrently and from tests."""
    result = PassResult()
    async with factory() as db:
        # Each pass is isolated: a failure in one (a bad query, a service
        # down) must not cost the others their turn.
        for name, pass_ in (
            ("score", _score_pass),
            ("embed", _embed_pass),
            ("scorecard", _scorecard_pass),
            ("pdf", _pdf_pass),
        ):
            try:
                await pass_(db, result)
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                result.failed_passes.append(f"{name}: {type(exc).__name__}: {exc}"[:300])
                log.error("reconcile.pass_failed", stage=name,
                          error_type=type(exc).__name__, error=str(exc)[:300])
        result.outstanding = await _outstanding(db)
    if (result.scored or result.embedded or result.interviews_scored
            or result.pdfs_rendered or result.failed):
        log.info("reconcile.pass", **result.as_dict())
    return result


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------
_task: asyncio.Task[None] | None = None


async def _loop(factory: async_sessionmaker[AsyncSession]) -> None:
    interval = max(60, settings.reconciliation_interval_seconds)
    # A short delay before the first pass so boot is not competing with
    # migrations and the first requests for the connection pool.
    await asyncio.sleep(min(30, interval))
    while True:
        started = datetime.now(tz=UTC)
        error: str | None = None
        try:
            result = await run_once(factory)
            error = "; ".join(result.failed_passes) or None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the loop must outlive any one pass
            error = f"{type(exc).__name__}: {exc}"
            log.error(
                "reconcile.pass_failed", exc_type=type(exc).__name__, exc_msg=str(exc)
            )
        # A6: every pass leaves a record — see scheduling.record_loop_pass.
        await record_loop_pass(factory, LOOP_JOB_ID, started_at=started, error=error)
        await asyncio.sleep(interval)


def start(factory: async_sessionmaker[AsyncSession]) -> None:
    """Start the loop. No-op when disabled or already running."""
    global _task
    if not settings.reconciliation_enabled:
        log.info("reconcile.disabled")
        return
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_loop(factory), name="reconciliation-worker")
    log.info("reconcile.started", interval_s=settings.reconciliation_interval_seconds)


async def stop() -> None:
    """Cancel the loop and wait for it — called from lifespan shutdown."""
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    except Exception:  # noqa: BLE001 — shutdown must not raise
        pass
    _task = None
    log.info("reconcile.stopped")
