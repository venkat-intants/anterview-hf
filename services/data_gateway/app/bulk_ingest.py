"""Bulk resume uploads, processed in the background — Group E, E5.

WHAT CHANGED
The bulk upload endpoint used to read every PDF and write every applicant inside
the request. Scoring had already moved out, but a cohort of a few hundred CVs
was still a few hundred extractions and inserts on one connection, and once the
response was gone nothing recorded which files had failed or why.

Now the request only STORES: each file's bytes go to object storage, the batch
and one row per file are recorded (``upload_batches`` / ``upload_items``), and
the response comes back with a batch id. Everything else happens here, in the
reconciler's loop:

    stored  →  claimed (row-locked)  →  read  →  applicant + enrolment  →  created
                                   ↘ unreadable PDF          →  failed (no retry)
                                   ↘ storage or database error →  retried with
                                      the reconciler's backoff, then failed

Scoring, embedding and pulling the real name out of the CV are the reconciler's
existing passes, which pick the new applicants up by the same absence of data
they always have. One notification tells the uploader when the whole batch —
including the files that failed — is done.

WHY THE DATABASE IS THE QUEUE
One container, one process. A broker would be a second process and a second
bill for work the reconciler already schedules, retries and records, and its
queue could drift from the tables; a row that says ``stored`` cannot.

ISOLATION
A batch belongs to one company's opening (composite foreign key), every query
here filters on the company, and applicants are created in the batch's company
only. Nothing here matches a file to an applicant in another company.

LAWFUL BASIS
HR-uploaded CVs arrive without the candidate ticking anything. Each applicant
created here gets a ledger entry recording that HR collected the data for
recruitment (``consent_type='hr_collected_application'``), against the person
who uploaded it. Whether that is a sufficient basis under the DPDP Act is a
legal call; the entry makes the basis explicit and auditable rather than absent.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

KIND_UPLOAD_ITEM = "upload_item"
# Files claimed per pass. Reading a PDF is fast; the bound keeps one pass from
# holding locks on a whole batch.
INGEST_BATCH = 25
# A storage or database error is retried this many times, with the reconciler's
# backoff, before the file is reported as failed.
MAX_INGEST_ATTEMPTS = 5

UNREADABLE = "Could not read the PDF — it may be encrypted, a scan, or not a PDF."
GAVE_UP = "Could not be processed after repeated attempts — try uploading it again."
OPENING_GONE = "The opening was removed before this file was processed."

HR_COLLECTED_CONSENT = "hr_collected_application"


@dataclass
class StagedFile:
    """One file as the request left it: stored, or refused with a reason."""

    item_id: uuid.UUID
    filename: str
    s3_key: str | None
    size_bytes: int
    error: str | None = None


# ---------------------------------------------------------------------------
# Recording an upload (called by the endpoint)
# ---------------------------------------------------------------------------
async def create_batch(
    db: AsyncSession,
    *,
    batch_id: uuid.UUID,
    company_id: uuid.UUID,
    requisition_id: uuid.UUID,
    uploaded_by: uuid.UUID,
    files: list[StagedFile],
    source: str | None = None,
) -> None:
    """Record the batch and its files. Caller commits.

    A file refused at upload (not a PDF, empty, too large, not stored) is
    recorded as ``failed`` straight away, so the batch's progress shows it with
    the rest rather than only in a response that is gone once the page closes.

    ``source`` (PH5-C1) is the channel this WHOLE batch came through — one
    bulk upload is already scoped to one opening, and in practice one channel
    too. Validated by the caller (``app.application_source.validate_hr_source``)
    before it reaches here; ``_create_applicant`` falls back to ``internal``
    when it is NULL, the same default HR add has always used.
    """
    now = datetime.now(tz=UTC)
    waiting = any(f.error is None for f in files)
    await db.execute(
        text(
            "INSERT INTO upload_batches (id, company_id, requisition_id, uploaded_by_user_id,"
            " total_files, status, source, created_at, finished_at)"
            " VALUES (:i, :c, :r, :u, :n, :s, :src, :t, :f)"
        ),
        {"i": batch_id, "c": company_id, "r": requisition_id, "u": uploaded_by,
         "n": len(files), "s": "processing" if waiting else "finished", "src": source,
         "t": now, "f": None if waiting else now},
    )
    for f in files:
        await db.execute(
            text(
                "INSERT INTO upload_items (id, batch_id, company_id, filename, s3_key,"
                " size_bytes, status, error, created_at, updated_at)"
                " VALUES (:i, :b, :c, :fn, :k, :sz, :s, :e, :t, :t)"
            ),
            {"i": f.item_id, "b": batch_id, "c": company_id, "fn": f.filename[:300],
             "k": f.s3_key, "sz": f.size_bytes, "s": "failed" if f.error else "stored",
             "e": f.error, "t": now},
        )


# ---------------------------------------------------------------------------
# The background pass
# ---------------------------------------------------------------------------
# Claim the oldest waiting files that are not backing off. SKIP LOCKED: a pass
# running alongside another (a second instance, a manual run) takes different
# rows instead of the same ones.
_CLAIM_SQL = """
UPDATE upload_items SET status = 'processing', updated_at = :now
 WHERE id IN (
       SELECT i.id FROM upload_items i
        WHERE i.status = 'stored'
          AND NOT EXISTS (SELECT 1 FROM reconciliation_state rs
                           WHERE rs.kind = :kind AND rs.ref_id = i.id
                             AND rs.next_attempt_at IS NOT NULL
                             AND rs.next_attempt_at > :now)
        ORDER BY i.created_at
        LIMIT :lim
        FOR UPDATE SKIP LOCKED)
RETURNING id
"""

_ITEMS_SQL = """
SELECT i.id, i.batch_id, i.company_id, i.filename, i.s3_key,
       b.requisition_id, b.uploaded_by_user_id, b.source,
       r.title, r.level, r.jd_text, r.deleted_at AS requisition_deleted_at
  FROM upload_items i
  JOIN upload_batches b ON b.id = i.batch_id AND b.company_id = i.company_id
  JOIN job_requisitions r ON r.id = b.requisition_id AND r.company_id = b.company_id
 WHERE i.id = ANY(:ids)
 ORDER BY i.created_at
"""

_MARK_SQL = """
UPDATE upload_items
   SET status = :s, error = :e, applicant_id = :a, enrolment_id = :en, updated_at = :now
 WHERE id = :i
"""


async def _mark(
    db: AsyncSession, item_id: uuid.UUID, status: str, *, error: str | None = None,
    applicant_id: uuid.UUID | None = None, enrolment_id: uuid.UUID | None = None,
) -> None:
    await db.execute(
        text(_MARK_SQL),
        {"s": status, "e": error, "a": applicant_id, "en": enrolment_id,
         "now": datetime.now(tz=UTC), "i": item_id},
    )


async def ingest_pass(db: AsyncSession, result: Any) -> None:
    """Turn stored files into applicants, a bounded number per pass."""
    from app.reconciliation import (  # noqa: PLC0415 — the reconciler imports this module
        _announce_batch,
        _clear_state,
        _record_failure,
    )
    from app.routers.resume import (  # noqa: PLC0415 — keeps the router off this import path
        _download_from_s3,
        _extract_pdf_text,
    )

    now = datetime.now(tz=UTC)
    claimed = [
        r[0] for r in (
            await db.execute(
                text(_CLAIM_SQL),
                {"now": now, "kind": KIND_UPLOAD_ITEM, "lim": INGEST_BATCH},
            )
        ).all()
    ]
    await db.commit()
    if not claimed:
        return

    items = [dict(r) for r in (await db.execute(text(_ITEMS_SQL), {"ids": claimed})).mappings()]
    for it in items:
        item_id, batch_id, uploader = it["id"], it["batch_id"], it["uploaded_by_user_id"]

        async def finish_failed(reason: str, _it: dict[str, Any] = it) -> None:
            await _mark(db, _it["id"], "failed", error=reason)
            await _clear_state(db, KIND_UPLOAD_ITEM, _it["id"])
            await db.commit()
            result.failed += 1
            await _announce_batch(db, result, _it["id"], _it["batch_id"],
                                  _it["uploaded_by_user_id"])

        if it["requisition_deleted_at"] is not None or not it["s3_key"]:
            await finish_failed(OPENING_GONE if it["s3_key"] else GAVE_UP)
            continue

        try:
            raw = await _download_from_s3(it["s3_key"])
        except Exception as exc:  # noqa: BLE001 — storage hiccup: retry, do not lose the file
            await db.rollback()
            await _retry_or_fail(db, it, f"storage: {type(exc).__name__}: {exc}", result,
                                 _record_failure, finish_failed)
            continue

        try:
            resume_text = await _extract_pdf_text(raw)
            if not resume_text.strip():
                raise ValueError("no text in the PDF")
        except Exception:  # noqa: BLE001 — an unreadable file will not read on a retry either
            await db.rollback()
            await finish_failed(UNREADABLE)
            continue

        try:
            applicant_id, enrolment_id = await _create_applicant(db, it, resume_text)
            await _mark(db, item_id, "created", applicant_id=applicant_id,
                        enrolment_id=enrolment_id)
            await _clear_state(db, KIND_UPLOAD_ITEM, item_id)
            await db.commit()
            result.ingested += 1
        except Exception as exc:  # noqa: BLE001 — one bad file must not stop the pass
            await db.rollback()
            log.warning("bulk_ingest.create_failed", item_id=str(item_id),
                        batch_id=str(batch_id), error_type=type(exc).__name__)
            await _retry_or_fail(db, it, f"{type(exc).__name__}: {exc}", result,
                                 _record_failure, finish_failed)
            continue
        log.info("bulk_ingest.created", item_id=str(item_id), batch_id=str(batch_id),
                 uploader=str(uploader) if uploader else None)


async def _retry_or_fail(
    db: AsyncSession, it: dict[str, Any], error: str, result: Any,
    record_failure: Any, finish_failed: Any,
) -> None:
    """Back to the queue with the reconciler's backoff, or failed for good."""
    gave_up = await record_failure(db, KIND_UPLOAD_ITEM, it["id"], error,
                                   max_attempts=MAX_INGEST_ATTEMPTS)
    if gave_up:
        result.gave_up += 1
        await finish_failed(GAVE_UP)
        return
    await _mark(db, it["id"], "stored", error=None)
    await db.commit()


async def _create_applicant(
    db: AsyncSession, it: dict[str, Any], resume_text: str
) -> tuple[uuid.UUID, uuid.UUID | None]:
    """The applicant, their enrolment under the batch's opening, and the basis.

    The row is stored unread, exactly as the synchronous path stored it: a
    filename-derived name and no score, ``pending_enrichment`` true, so the
    reconciler scores it, embeds it and replaces the placeholder name with the
    one in the CV. The CV key is pinned on the enrolment (E-foundations).
    """
    from app.application_source import INTERNAL  # noqa: PLC0415
    from app.models import Applicant  # noqa: PLC0415
    from app.routers.hr_applicants import _name_from_filename  # noqa: PLC0415
    from app.workflow_runner import enrol_applicant  # noqa: PLC0415

    now = datetime.now(tz=UTC)
    applicant_id = uuid.uuid4()
    company_id, uploader = it["company_id"], it["uploaded_by_user_id"]
    title, level = str(it["title"]), str(it["level"] or "mid")
    db.add(
        Applicant(
            id=applicant_id,
            company_id=company_id,
            created_by_user_id=uploader,
            full_name=_name_from_filename(it["filename"]),
            full_name_source="filename",
            email=None,
            target_job_title=title,
            target_level=level,
            target_jd_text=it["jd_text"],
            resume_text=resume_text,
            resume_s3_key=it["s3_key"],
            status="new",
            pending_enrichment=True,
            upload_batch_id=it["batch_id"],
            created_at=now,
            updated_at=now,
        )
    )
    await db.flush()
    outcome = await enrol_applicant(
        db,
        company_id=company_id,
        applicant_id=applicant_id,
        requisition_id=it["requisition_id"],
        target_job_title=title,
        target_level=level,
        target_jd_text=it["jd_text"],
        actor_user_id=uploader,
        reason="added by HR bulk upload",
        resume_s3_key=it["s3_key"],
        # PH5-C1: the batch's own channel (validated at upload time), falling
        # back to 'internal' — the default before this existed — for a batch
        # that did not set one.
        source=it["source"] or INTERNAL,
    )
    if uploader is not None:
        await record_hr_collected_basis(
            db, uploader=uploader, applicant_id=applicant_id, company_id=company_id,
            requisition_id=it["requisition_id"], batch_id=it["batch_id"], now=now,
        )
    return applicant_id, uuid.UUID(outcome.enrolment_id) if outcome.enrolment_id else None


async def record_hr_collected_basis(
    db: AsyncSession, *, uploader: uuid.UUID, applicant_id: uuid.UUID,
    company_id: uuid.UUID, requisition_id: uuid.UUID, batch_id: uuid.UUID, now: datetime,
) -> None:
    """Ledger entry: HR collected this applicant's CV for recruitment. Caller commits.

    Against the uploader, because the candidate took no action. The purpose
    names the applicant so each entry is its own row under the ledger's
    one-active-row-per-(user, type, purpose) rule. Evidence carries ids only,
    never PII.
    """
    await db.execute(
        text(
            "INSERT INTO dpdp_consent_ledger"
            " (id, user_id, consent_type, granted, granted_at, purpose, evidence)"
            " VALUES (:id, :uid, :ct, true, :n, :p, CAST(:ev AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "uid": uploader,
            "ct": HR_COLLECTED_CONSENT,
            "n": now,
            "p": f"recruitment:applicant:{applicant_id}",
            "ev": json.dumps({
                "source": "hr_bulk_upload",
                "basis": "collected by HR for recruitment; the candidate did not consent "
                         "through the product",
                "applicant_id": str(applicant_id),
                "company_id": str(company_id),
                "requisition_id": str(requisition_id),
                "batch_id": str(batch_id),
                "recorded_at_iso": now.isoformat(),
            }),
        },
    )


# ---------------------------------------------------------------------------
# Progress (read by the console)
# ---------------------------------------------------------------------------
# One literal for one batch and for a company's recent batches. "Being scored"
# is an applicant this batch created that the reconciler has not read yet;
# a batch is finished when nothing is queued and nothing is being scored.
_PROGRESS_SQL = """
SELECT b.id, b.requisition_id, r.title AS requisition_title, b.total_files, b.status,
       b.created_at, b.finished_at, u.full_name AS uploaded_by,
       count(i.id) FILTER (WHERE i.status IN ('stored', 'processing')) AS queued,
       count(i.id) FILTER (WHERE i.status = 'created') AS created,
       count(i.id) FILTER (WHERE i.status = 'failed') AS failed,
       count(a.id) FILTER (WHERE i.status = 'created' AND a.pending_enrichment
                             AND a.deleted_at IS NULL) AS being_scored
  FROM upload_batches b
  JOIN job_requisitions r ON r.id = b.requisition_id AND r.company_id = b.company_id
  LEFT JOIN users u ON u.id = b.uploaded_by_user_id
  LEFT JOIN upload_items i ON i.batch_id = b.id AND i.company_id = b.company_id
  LEFT JOIN applicants a ON a.id = i.applicant_id AND a.company_id = b.company_id
 WHERE b.company_id = :c
   AND (CAST(:b AS uuid) IS NULL OR b.id = CAST(:b AS uuid))
   AND (CAST(:r AS uuid) IS NULL OR b.requisition_id = CAST(:r AS uuid))
 GROUP BY b.id, r.title, u.full_name
 ORDER BY b.created_at DESC
 LIMIT :lim
"""

_FAILURES_SQL = """
SELECT filename, error FROM upload_items
 WHERE batch_id = :b AND company_id = :c AND status = 'failed'
 ORDER BY created_at
 LIMIT 500
"""


def _progress(row: dict[str, Any]) -> dict[str, Any]:
    queued, being_scored = int(row["queued"] or 0), int(row["being_scored"] or 0)
    return {
        "batch_id": str(row["id"]),
        "requisition_id": str(row["requisition_id"]),
        "requisition_title": row["requisition_title"],
        "uploaded_by": row["uploaded_by"],
        "total_files": int(row["total_files"]),
        "queued": queued,
        "created": int(row["created"] or 0),
        "failed": int(row["failed"] or 0),
        "being_scored": being_scored,
        "finished": queued == 0 and being_scored == 0,
        "created_at": row["created_at"].isoformat(),
        "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
    }


async def batch_progress(
    db: AsyncSession, *, company_id: uuid.UUID, batch_id: uuid.UUID
) -> dict[str, Any] | None:
    """One batch, with its failures. None when it is not this company's."""
    row = (
        await db.execute(
            text(_PROGRESS_SQL),
            {"c": company_id, "b": batch_id, "r": None, "lim": 1},
        )
    ).mappings().first()
    if row is None:
        return None
    failures = (
        await db.execute(text(_FAILURES_SQL), {"b": batch_id, "c": company_id})
    ).mappings().all()
    return {**_progress(dict(row)),
            "failures": [{"filename": f["filename"], "error": f["error"]} for f in failures]}


async def recent_batches(
    db: AsyncSession, *, company_id: uuid.UUID, requisition_id: uuid.UUID | None, limit: int
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(_PROGRESS_SQL),
            {"c": company_id, "b": None, "r": requisition_id, "lim": limit},
        )
    ).mappings().all()
    return [_progress(dict(r)) for r in rows]
