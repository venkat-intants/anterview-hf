"""HR applicant screening — HR workflow Phase 1.

An HR manager uploads applicant resumes; each is text-extracted, stored in S3,
and ATS-scored (via feedback_billing). HR then sees a ranked list and can
shortlist/reject.

MULTI-TENANT: every endpoint is scoped to the caller's company_id (resolved from
the HR's user row). An HR can NEVER see or touch another company's applicants —
all reads/writes filter by company_id, so a cross-company id returns 404.

  POST   /hr/applicants                 — upload + auto-score an applicant
  GET    /hr/applicants[?status=]       — ranked list (by ATS score), paged
  GET    /hr/applicants/{id}            — detail
  PATCH  /hr/applicants/{id}            — set status (new|shortlisted|rejected)
  POST   /hr/applicants/{id}/rescore    — re-run ATS scoring
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, BeforeValidator, EmailStr, Field
from sqlalchemy import column, exists, or_, select, table, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.applicant_enrichment import (
    apply_ats_score,
    apply_ats_to_enrolment,
    store_embedding,
    valid_email_or_none,
)
from app.database import DbSessionDep
from app.dependencies import HrCtxDep, get_hr_company
from app.embedding_client import (
    EmbeddingError,
    embed_one_remote,
    embed_texts_remote,
    to_pgvector_literal,
    why_match_remote,
)
from app.mailer import enqueue_email
from app.models import Applicant
from app.requisitions import (
    TERMINAL_STATUSES,
    ambiguous_decision_detail,
    applicant_by_email,
    choose_application,
    record_transition,
    requisition_for_title,
)
from app.routers.resume import _delete_from_s3, _extract_pdf_text, _upload_to_s3
from app.scoring_client import ResumeScoreError, score_resume_remote
from app.utils.ownership import get_owned
from app.utils.sql_like import LIKE_ESCAPE, like_literal
from app.workflow_runner import enrol_applicant, on_shortlisted

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-applicants"])

_MAX_RESUME_BYTES = 5 * 1024 * 1024  # 5 MB
# Per batch. E5 asks for "the present batch limit" to go, and the 25 that stood
# here was the old time-based one: scoring ran inside the request at roughly ten
# seconds a file, so 25 was already four minutes on one connection. That reason
# died when Group E moved scoring to the reconciler — a file now costs one PDF
# extraction and one upload.
#
# What is left is a REQUEST-SIZE bound, so it is expressed as one: a byte budget
# for the batch, plus a file count high enough that a real cohort never meets it
# and low enough that a runaway client cannot open ten thousand file handles.
# An HR manager uploading a graduate intake of 200 CVs is the case E5 exists
# for, and it now succeeds.
_MAX_BULK_FILES = 500
# 250 MB. Above this the multipart body itself is the problem — the proxy in
# front of the service will drop it long before we finish reading, and a limit
# that produces a readable error beats one that produces a truncated upload.
_MAX_BULK_TOTAL_BYTES = 250 * 1024 * 1024
_VALID_STATUSES = {"new", "shortlisted", "rejected", "interviewed", "hired"}
# Status transitions that warrant a decision email to the candidate.
_DECISION_EMAIL_STATUSES = {"shortlisted", "rejected", "hired"}

# DbSessionDep, get_hr_company and HrCtxDep are imported above and re-exported
# from this module (DG-1): eight sibling routers still import them from here, and
# the tests override FastAPI dependencies keyed on get_hr_company. They now live
# beside what they wrap — the session alias in app/database.py, the tenant
# resolver in app/dependencies.py — so a candidate-facing router no longer drags
# the applicant module's S3, embedding and scoring clients into its import graph.
__all__ = [
    "DbSessionDep",
    "HrCtxDep",
    "email_applicant_decision",
    "get_hr_company",
    "router",
]


async def email_applicant_decision(
    db: AsyncSession,
    *,
    applicant: Applicant,
    decision: str,
    company_id: uuid.UUID,
    job_title: str | None = None,
) -> None:
    """Stage a branded shortlist/hire/reject email to the candidate (caller commits).

    No-op when the applicant has no email on file or the decision isn't one we
    notify on. Best-effort: enqueue never raises on a bad recipient. Shared by the
    applicant status PATCH and the pipeline hire/reject decision endpoint.

    ``job_title`` names the opening decided on. Pass it whenever the decision
    is about one application: ``applicant.target_job_title`` describes only the
    person's LATEST application, so a rejection for an older one would
    otherwise name a job they are still being considered for.
    """
    if not applicant.email or decision not in _DECISION_EMAIL_STATUSES:
        return
    await enqueue_email(
        db,
        to=applicant.email,
        template="decision",
        lang="en",
        ctx={
            "name": applicant.full_name,
            "job_title": job_title or applicant.target_job_title,
            "decision": decision,
        },
        company_id=company_id,
        related_kind="applicant_decision",
        related_id=applicant.id,
    )


# ---------------------------------------------------------------------------
# Email validation at the boundary
#
# A malformed address used to be stored verbatim and only surfaced hours later
# in the mailer worker, where the failure looks like an email-system problem
# rather than like the upload that caused it. Validate where it enters.
#
# The two entry points are deliberately validated DIFFERENTLY:
#   * the HR-typed `email` form field — EmailStr, so a typo is a 422 the HR
#     manager sees and can correct on the spot;
#   * the scorer-extracted address — same rule, but a failure DROPS the address
#     instead of raising, because that value is LLM output read off a PDF, not
#     something a human typed. Failing a bulk upload over an OCR artefact would
#     lose the candidate; a missing email is recoverable, a lost applicant is not.
# ---------------------------------------------------------------------------
def _blank_to_none(value: object) -> object:
    """Treat an empty/whitespace field as "not supplied" rather than invalid.

    Browsers submit an untouched optional input as ``""``, and the previous
    ``str | None`` parameter accepted that. Without this, adding EmailStr would
    turn every blank email box into a 422.
    """
    if isinstance(value, str):
        return value.strip() or None
    return value


OptionalEmail = Annotated[EmailStr | None, BeforeValidator(_blank_to_none)]

# Aliased, not reimplemented. Reconciliation validates the same
# machine-extracted addresses hours after upload does, and two copies of this
# rule would drift into a mailer failure rather than into anything visibly
# wrong here. Same reasoning as the sql_like alias below.
_valid_email_or_none = valid_email_or_none


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ApplicantOut(BaseModel):
    # Response-side email stays a plain str: rows created before the checks
    # above existed can hold an address the scorer hallucinated, and a response
    # model that refuses to serialise them would turn one bad legacy row into a
    # 500 on the whole applicant LIST. Tightening happens on the way in.
    id: str
    full_name: str
    # What the CV said, when it disagrees with the name on file. Surfaced so a
    # recruiter can see the discrepancy rather than only ever seeing one of the
    # two names — the reconciler no longer replaces a name a person typed, so
    # without this the parsed one would be invisible.
    parsed_full_name: str | None = None
    # 'candidate' | 'hr' | 'filename' | 'resume'. Tells the console whether the
    # name is somebody's answer or a machine's reading of a PDF.
    full_name_source: str | None = None
    email: str | None
    target_job_title: str
    target_level: str
    # Details the multi-step application collects. All optional — the older
    # single-field form supplies none of them, and neither does bulk upload.
    phone: str | None = None
    years_experience: int | None = None
    current_company: str | None = None
    current_title: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    status: str
    ats_overall: int | None
    ats_breakdown: dict[str, int] | None
    ats_strengths: list[str] | None
    ats_concerns: list[str] | None
    ats_recommendation: str | None
    ats_summary: str | None
    created_at: str
    # The linked candidate user (set once the applicant redeems an interview
    # invite) — lets HR open the candidate's full profile. None until provisioned.
    user_id: str | None = None
    # Relevance to the current semantic search query (0-100), set only on a
    # ?q= search response. A RELATIVE ranking signal — higher = better match.
    match_score: int | None = None
    # True while this resume is stored but not yet read. The name is derived
    # from the filename and there is no score yet, so the console must show it
    # as in-progress rather than as a weak candidate — those look identical in
    # a list and mean opposite things.
    pending_enrichment: bool = False


class WhyMatchOut(BaseModel):
    reason: str


class ReindexResult(BaseModel):
    reindexed: int
    failed: int
    remaining: int


class StatusUpdate(BaseModel):
    status: str
    # The application this change is about (B5). Optional: with one
    # application it is implied.
    enrolment_id: uuid.UUID | None = None


class BulkUploadResult(BaseModel):
    created: list[ApplicantOut]
    failed: list[dict[str, str]]
    created_count: int
    failed_count: int
    # How many of `created` are still waiting to be read. The console needs
    # this to say "12 still being scored" instead of presenting filename-derived
    # names and empty scores as if they were the finished answer.
    pending_enrichment: int = 0
    # The opening every file in the batch was filed under (B1/B4).
    requisition_id: str | None = None


# How an HR upload says which opening it is for (B1/B4). Every applicant is
# filed under one: an explicitly chosen requisition when the form sends one,
# otherwise the opening its typed title resolves to (created, and flagged for
# review, when there is none yet).
async def _resolve_opening(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    hr_uid: uuid.UUID,
    requisition_id: uuid.UUID | None,
    title: str,
    level: str,
    jd_text: str | None,
) -> dict[str, Any]:
    if requisition_id is not None:
        row = (
            await db.execute(
                text(
                    "SELECT id, title, level, jd_text FROM job_requisitions"
                    " WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
                ),
                {"i": requisition_id, "c": company_id},
            )
        ).mappings().first()
        if row is None:
            # Uniform with every other cross-tenant miss.
            raise HTTPException(status_code=404, detail="Opening not found.")
        return {**dict(row), "created": False}
    try:
        return await requisition_for_title(
            db, company_id=company_id, title=title, level=level, jd_text=jd_text,
            actor_user_id=hr_uid,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _file_under(
    db: AsyncSession,
    *,
    applicant: Applicant,
    opening: dict[str, Any],
    hr_uid: uuid.UUID,
) -> uuid.UUID | None:
    """Create the applicant's enrolment in ``opening``. Caller commits.

    The applicant row must already be flushed: the enrolment's composite FK
    points at it. Marked as HR's doing in the ledger, not the system's.
    """
    await db.flush()
    outcome = await enrol_applicant(
        db,
        company_id=applicant.company_id,
        applicant_id=applicant.id,
        requisition_id=uuid.UUID(str(opening["id"])),
        target_job_title=str(opening["title"]),
        target_level=str(opening["level"] or "mid"),
        target_jd_text=opening["jd_text"],
        actor_user_id=hr_uid,
        reason="added by HR upload",
    )
    return uuid.UUID(outcome.enrolment_id) if outcome.enrolment_id else None


def _to_out(a: Applicant) -> ApplicantOut:
    # resume_text / s3_key are PII — never returned in the list/detail payload.
    return ApplicantOut(
        id=str(a.id),
        full_name=a.full_name,
        parsed_full_name=(
            a.parsed_full_name if a.parsed_full_name != a.full_name else None
        ),
        full_name_source=a.full_name_source,
        phone=a.phone,
        years_experience=a.years_experience,
        current_company=a.current_company,
        current_title=a.current_title,
        linkedin_url=a.linkedin_url,
        github_url=a.github_url,
        email=a.email,
        target_job_title=a.target_job_title,
        target_level=a.target_level,
        status=a.status,
        ats_overall=a.ats_overall,
        ats_breakdown=a.ats_breakdown,
        ats_strengths=a.ats_strengths,
        ats_concerns=a.ats_concerns,
        ats_recommendation=a.ats_recommendation,
        ats_summary=a.ats_summary,
        created_at=a.created_at.isoformat(),
        user_id=str(a.user_id) if a.user_id else None,
        pending_enrichment=a.pending_enrichment,
    )


# _apply_score / _store_embedding now live in app.applicant_enrichment so the
# reconciliation loop can share the ATS field mapping rather than copy it.
# Aliased to their original private names to leave this module's call sites
# (and the unit test that imports _apply_score from here) unchanged.
_apply_score = apply_ats_score
_store_embedding = store_embedding


async def _embed_applicant(db: AsyncSession, applicant: Applicant, hr_uid: uuid.UUID) -> None:
    """Best-effort: embed the resume so it is searchable. Never raises.

    An embedding outage must NOT fail an upload — the applicant still persists and
    remains findable via the full-text (exact-keyword) leg until reindexed.
    """
    if not applicant.resume_text or not applicant.resume_text.strip():
        return
    try:
        vec = await embed_one_remote(
            text=applicant.resume_text, task_type="document", acting_user_id=str(hr_uid)
        )
        await _store_embedding(db, applicant.company_id, applicant.id, vec)
    except EmbeddingError as exc:
        log.warning("hr.applicant.embed_unavailable", error=str(exc))
    except Exception as exc:  # noqa: BLE001 — embedding must never break ingest
        # The applicant is already committed; this only rolls back the (uncommitted)
        # embedding UPDATE and clears any aborted-transaction state so the next
        # bulk file is unaffected.
        await db.rollback()
        log.warning("hr.applicant.embed_failed", error_type=type(exc).__name__)


# Embeds requested in one /internal/embed call (feedback_billing caps at 64).
_EMBED_BATCH = 16


async def _embed_applicants_batch(
    db: AsyncSession,
    company_id: uuid.UUID,
    hr_uid: uuid.UUID,
    applicant_ids: list[uuid.UUID],
) -> None:
    """Best-effort batch embed for a just-uploaded batch (≈1 HTTP call / 16 resumes)."""
    if not applicant_ids:
        return
    rows = (
        await db.execute(
            select(Applicant.id, Applicant.resume_text).where(
                Applicant.company_id == company_id,
                Applicant.id.in_(applicant_ids),
                Applicant.resume_text.isnot(None),
            )
        )
    ).all()
    items = [(r.id, r.resume_text) for r in rows if r.resume_text and r.resume_text.strip()]
    for i in range(0, len(items), _EMBED_BATCH):
        chunk = items[i : i + _EMBED_BATCH]
        try:
            vecs = await embed_texts_remote(
                texts=[t for _, t in chunk], task_type="document", acting_user_id=str(hr_uid)
            )
        except EmbeddingError as exc:
            log.warning("hr.applicant.bulk.embed_unavailable", error=str(exc))
            return
        for (aid, _), vec in zip(chunk, vecs, strict=True):
            await _store_embedding(db, company_id, aid, vec)


async def _get_owned(db: AsyncSession, company_id: uuid.UUID, applicant_id: uuid.UUID) -> Applicant:
    """Fetch an applicant scoped to the company, or 404 (tenant isolation)."""
    return await get_owned(db, Applicant, company_id, applicant_id, noun="Applicant")


def _name_from_filename(filename: str) -> str:
    """Best-effort readable name from a filename (used only if the scorer cannot
    extract a name from the resume itself)."""
    base = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    if base.lower().endswith(".pdf"):
        base = base[:-4]
    base = base.replace("_", " ").replace("-", " ").strip()
    return base[:200] or "Unnamed candidate"


async def _ingest_resume(
    *,
    db: AsyncSession,
    company_id: uuid.UUID,
    hr_uid: uuid.UUID,
    raw: bytes,
    fallback_name: str,
    job_title: str,
    level: str,
    jd_text: str | None,
    score_now: bool = True,
    upload_batch_id: uuid.UUID | None = None,
    opening: dict[str, Any] | None = None,
) -> Applicant:
    """Extract → store → (optionally score) → persist ONE resume.

    The candidate's name + email are auto-extracted from the resume by the
    scorer, so with ``score_now`` the score happens *before* insert and the
    extracted name lands on the initial row. Falls back to ``fallback_name`` if
    extraction yields nothing or the scorer is unavailable. Raises
    ``ValueError`` (bad PDF / DB) or a botocore error (storage) so the caller
    can record it as a per-file failure.

    ``score_now=False`` stores the resume and returns immediately, flagging the
    row ``pending_enrichment`` for the Group A reconciler to score, embed and
    name later. That is not a shortcut — it is the difference between an HTTP
    request that holds a connection open for four minutes and one that answers
    in seconds. The trade is that the row carries a filename-derived
    placeholder until the reconciler catches up, which is why the flag exists
    and why the UI has to say so rather than showing a name it made up.
    """
    try:
        resume_text = await _extract_pdf_text(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("could not read the PDF (encrypted or not text-based)") from exc

    applicant_id = uuid.uuid4()
    s3_key = f"applicants/{company_id}/{applicant_id}.pdf"
    await _upload_to_s3(raw, s3_key)  # BotoCoreError / ClientError propagate to caller

    full_name = fallback_name
    email: str | None = None
    score: dict[str, Any] | None = None
    # Both the deferred path and the scorer-unavailable path fall through with
    # score=None and a placeholder name — deliberately the same row, so the
    # reconciler has exactly one shape to finish rather than two.
    if score_now:
        try:
            score = await score_resume_remote(
                resume_text=resume_text,
                job_title=job_title,
                level=level,
                jd_text=jd_text,
                acting_user_id=str(hr_uid),
            )
            if score.get("candidate_name"):
                full_name = str(score["candidate_name"]).strip()[:200] or fallback_name
            if score.get("candidate_email"):
                # Dropped rather than stored when malformed: this address was
                # read out of a PDF by the scorer, so "Jane Doe | jane@" is a
                # plausible extraction and must not become a permanently
                # un-emailable row.
                email = _valid_email_or_none(str(score["candidate_email"])[:320])
                if email is None:
                    log.info("hr.applicant.extracted_email_rejected")
        except ResumeScoreError as exc:
            log.warning("hr.applicant.bulk.score_unavailable", error=str(exc))

    # B4: an address read off the CV that someone on file already has. Not
    # merged automatically — the extraction is a guess, and folding two people
    # together on a guess is not the system's call — and not stored as
    # ``email``, which would be a second applicant for one person (and, once the
    # unique index is on, a failed save that loses the CV). Kept aside instead,
    # where the review screen proposes the merge.
    parsed_email: str | None = None
    if email is not None and await applicant_by_email(db, company_id=company_id, email=email):
        email, parsed_email = None, email

    now = datetime.now(tz=UTC)
    applicant = Applicant(
        id=applicant_id,
        company_id=company_id,
        created_by_user_id=hr_uid,
        full_name=full_name,
        # Derived from the uploaded file's name — a placeholder, and the one
        # case where the scorer replacing it is an improvement rather than a
        # correction nobody asked for.
        full_name_source="filename",
        email=email,
        parsed_email=parsed_email,
        target_job_title=job_title,
        target_level=level,
        target_jd_text=jd_text,
        resume_text=resume_text,
        resume_s3_key=s3_key,
        status="new",
        # Set whenever the row was stored without being read — which is exactly
        # when full_name is a placeholder the reconciler may replace.
        # True whenever nothing read this PDF — the deferred path, and also a
        # synchronous upload whose scorer was down. Both leave a placeholder
        # name, and both want the reconciler to come back for it.
        pending_enrichment=score is None,
        # Set here rather than by the caller: this helper commits, so a field
        # assigned after it returns would not be part of that transaction.
        upload_batch_id=upload_batch_id,
        created_at=now,
        updated_at=now,
    )
    if score is not None:
        _apply_score(applicant, score)
    db.add(applicant)
    try:
        # Filed under its opening in the same transaction, so there is never an
        # applicant row the requisition dashboard cannot see (B1/B4).
        if opening is not None:
            enrolment_id = await _file_under(db, applicant=applicant, opening=opening,
                                             hr_uid=hr_uid)
            if score is not None and enrolment_id is not None:
                await apply_ats_to_enrolment(db, enrolment_id=enrolment_id, score=score,
                                             resume_key=s3_key)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        await _delete_from_s3(s3_key)
        raise ValueError("could not save the applicant") from exc
    # NOTE: embedding is deferred — bulk_upload_applicants batch-embeds the whole
    # batch in one shot afterwards (far fewer HTTP calls than one per file).
    return applicant


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/applicants", status_code=status.HTTP_201_CREATED, response_model=ApplicantOut)
async def create_applicant(
    file: UploadFile,
    full_name: Annotated[str, Form()],
    target_job_title: Annotated[str, Form()],
    ctx: HrCtxDep,
    db: DbSessionDep,
    # EmailStr, not str: a typo here used to persist and fail in the mailer.
    # Blank is still "not supplied" (see _blank_to_none), so the field stays
    # genuinely optional.
    email: Annotated[OptionalEmail, Form()] = None,
    target_level: Annotated[str, Form()] = "mid",
    target_jd_text: Annotated[str | None, Form()] = None,
    # The opening to file them under. When absent, the typed title decides
    # (see _resolve_opening). When present it wins, and the applicant's target
    # role is the opening's, not whatever was typed alongside it.
    requisition_id: Annotated[uuid.UUID | None, Form()] = None,
) -> ApplicantOut:
    """Upload an applicant's resume, store it, file them under an opening, and
    ATS-score the application (best-effort)."""
    hr_uid, company_id = ctx

    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only PDF resumes are accepted.")
    try:
        raw: bytes = await file.read()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Could not read the uploaded file.") from exc
    if len(raw) > _MAX_RESUME_BYTES:
        raise HTTPException(status_code=400, detail="Resume must be under 5 MB.")
    try:
        resume_text = await _extract_pdf_text(raw)
    except Exception as exc:  # noqa: BLE001
        log.warning("hr.applicant.pdf_parse_failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=400,
            detail="Could not read the PDF. Please upload a valid, unencrypted PDF.",
        ) from exc

    # After the file checks, so a bad upload can never mint an opening.
    opening = await _resolve_opening(
        db, company_id=company_id, hr_uid=hr_uid, requisition_id=requisition_id,
        title=target_job_title, level=target_level.strip() or "mid", jd_text=target_jd_text,
    )

    # B4 / D-06: one person is one applicant per company. Someone HR uploads
    # whose email is already on file is that person applying again — a new
    # application (enrolment) on the same applicant, not a second copy of them
    # with their history split between the rows.
    existing_id = await applicant_by_email(db, company_id=company_id, email=email)
    if existing_id is not None:
        already = await db.scalar(
            text(
                "SELECT a.full_name FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
                " WHERE e.applicant_id = :a AND e.requisition_id = :r AND e.deleted_at IS NULL"
            ),
            {"a": existing_id, "r": opening["id"]},
        )
        if already is not None:
            raise HTTPException(
                status_code=409,
                detail=f"{already} is already in this opening — open their record instead.",
            )

    applicant_id = existing_id or uuid.uuid4()
    # A returning person's CV gets a key of its own: the old one may be the CV
    # an earlier application was scored against (enrolments.scored_resume_s3_key).
    s3_key = (
        f"applicants/{company_id}/{applicant_id}.pdf" if existing_id is None
        else f"applicants/{company_id}/{applicant_id}-{uuid.uuid4().hex[:12]}.pdf"
    )
    try:
        await _upload_to_s3(raw, s3_key)
    except (BotoCoreError, ClientError) as exc:
        log.error("hr.applicant.storage_failed", error_type=type(exc).__name__, error=str(exc))
        raise HTTPException(
            status_code=502, detail="Resume storage is currently unavailable. Please try again."
        ) from exc

    now = datetime.now(tz=UTC)
    target_jd = opening["jd_text"] if requisition_id is not None else target_jd_text
    previous_key: str | None = None
    found = await db.get(Applicant, existing_id) if existing_id is not None else None
    if found is not None:
        applicant = found
        previous_key = applicant.resume_s3_key
        # The person keeps their name (typed once, by them or by HR); what
        # changes is the CV on file and the application it is now scored for.
        applicant.resume_text = resume_text
        applicant.resume_s3_key = s3_key
        applicant.target_job_title = str(opening["title"]).strip()
        applicant.target_level = str(opening["level"] or "mid")
        applicant.target_jd_text = target_jd
        # Cleared: the applicant row mirrors the LATEST application, which is
        # this one, and its score lands below (or from the reconciler).
        applicant.ats_overall = None
        applicant.ats_breakdown = None
        applicant.ats_strengths = None
        applicant.ats_concerns = None
        applicant.ats_recommendation = None
        applicant.ats_summary = None
        applicant.updated_at = now
    else:
        applicant = Applicant(
            id=applicant_id,
            company_id=company_id,
            created_by_user_id=hr_uid,
            full_name=full_name.strip(),
            # A recruiter typed this, so the reconciler may not replace it —
            # same rule as a candidate typing their own name on the public form.
            full_name_source="hr",
            # Already stripped and validated by OptionalEmail on the way in.
            email=email,
            target_job_title=str(opening["title"]).strip(),
            target_level=str(opening["level"] or "mid"),
            target_jd_text=target_jd,
            resume_text=resume_text,
            resume_s3_key=s3_key,
            status="new",
            created_at=now,
            updated_at=now,
        )
        db.add(applicant)
    try:
        enrolment_id = await _file_under(db, applicant=applicant, opening=opening,
                                         hr_uid=hr_uid)
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        log.error("hr.applicant.db_write_failed", error_type=type(exc).__name__)
        await _delete_from_s3(s3_key)
        raise HTTPException(
            status_code=503, detail="Could not save the applicant. Please try again."
        ) from exc

    # The CV this one replaced is kept only while an application still points at
    # it as the CV it was scored against; otherwise it is PII nothing refers to.
    if previous_key and previous_key != s3_key:
        still_used = await db.scalar(
            text("SELECT 1 FROM enrolments WHERE scored_resume_s3_key = :k LIMIT 1"),
            {"k": previous_key},
        )
        if still_used is None:
            await _delete_from_s3(previous_key)

    # ATS scoring is best-effort: a scorer outage must NOT lose the applicant.
    # Unscored, the enrolment is picked up by the reconciler.
    try:
        score = await score_resume_remote(
            resume_text=resume_text,
            job_title=applicant.target_job_title,
            level=applicant.target_level,
            jd_text=applicant.target_jd_text,
            acting_user_id=str(hr_uid),
        )
        _apply_score(applicant, score)
        if enrolment_id is not None:
            await apply_ats_to_enrolment(db, enrolment_id=enrolment_id, score=score,
                                         resume_key=s3_key)
        await db.commit()
    except ResumeScoreError as exc:
        log.warning("hr.applicant.score_unavailable", error=str(exc))
        # Applicant persists unscored; HR can POST /rescore later.

    # Make the resume semantically searchable (best-effort — never fails upload).
    await _embed_applicant(db, applicant, hr_uid)

    log.info(
        "hr.applicant.created",
        applicant_id=str(applicant_id),
        company_id=str(company_id),
        scored=applicant.ats_overall is not None,
    )
    return _to_out(applicant)


@router.post(
    "/applicants/bulk",
    status_code=status.HTTP_201_CREATED,
    response_model=BulkUploadResult,
    summary="Bulk-upload many resumes for one role (names auto-extracted)",
)
async def bulk_upload_applicants(
    files: Annotated[list[UploadFile], File(description="One or more PDF resumes")],
    target_job_title: Annotated[str, Form()],
    ctx: HrCtxDep,
    db: DbSessionDep,
    target_level: Annotated[str, Form()] = "mid",
    target_jd_text: Annotated[str | None, Form()] = None,
    requisition_id: Annotated[uuid.UUID | None, Form()] = None,
) -> BulkUploadResult:
    """Upload many resumes at once for a SINGLE role.

    Each PDF is read and stored; scoring, embedding and name extraction happen
    afterwards in the background (Group A's reconciler). A bad, empty or
    oversized file is reported in ``failed`` without aborting the rest.

    Scoring used to run inside this request, sequentially, at roughly ten
    seconds a file — so a full twenty-five-resume batch held one connection
    open for four minutes, and a dropped connection lost the tail of it. That
    is why every row comes back with ``pending_enrichment`` true and no ATS
    score: the work is queued, not skipped, and the response says which rows
    are still being read so the console can say so too rather than showing
    filename-derived names as though they were real.
    """
    hr_uid, company_id = ctx
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    if len(files) > _MAX_BULK_FILES:
        raise HTTPException(
            status_code=413,
            detail=f"Up to {_MAX_BULK_FILES} resumes per batch — you sent {len(files)}.",
        )

    level = target_level.strip() or "mid"
    opening = await _resolve_opening(
        db, company_id=company_id, hr_uid=hr_uid, requisition_id=requisition_id,
        title=target_job_title.strip() or "General Role", level=level, jd_text=target_jd_text,
    )
    job_title = str(opening["title"])
    if requisition_id is not None:
        level = str(opening["level"] or level)
        target_jd_text = opening["jd_text"]
    # Committed before the loop: each file commits (or rolls back) on its own,
    # and a rollback after one bad file must not take the opening with it.
    await db.commit()

    # One id for the whole batch, so the reconciler can tell when the last row
    # of THIS upload has finished being read and emit a single notification
    # (A4) rather than one per applicant.
    batch_id = uuid.uuid4()

    created: list[ApplicantOut] = []
    failed: list[dict[str, str]] = []
    embed_ids: list[uuid.UUID] = []
    batch_bytes = 0
    for f in files:
        fname = f.filename or "resume.pdf"
        # Browsers usually send application/pdf; some send octet-stream — allow both
        # and let _extract_pdf_text reject anything that is not actually a PDF.
        if f.content_type not in ("application/pdf", "application/octet-stream"):
            failed.append({"filename": fname, "error": "not a PDF"})
            continue
        try:
            raw = await f.read()
        except Exception:  # noqa: BLE001
            failed.append({"filename": fname, "error": "could not read upload"})
            continue
        if not raw:
            failed.append({"filename": fname, "error": "empty file"})
            continue
        if len(raw) > _MAX_RESUME_BYTES:
            failed.append({"filename": fname, "error": "over 5 MB"})
            continue
        batch_bytes += len(raw)
        if batch_bytes > _MAX_BULK_TOTAL_BYTES:
            # Reported per file rather than raised, so the files already stored
            # stay stored. Aborting here would discard work that succeeded and
            # give the operator nothing to retry from.
            failed.append({"filename": fname, "error": "batch size limit reached"})
            continue
        try:
            applicant = await _ingest_resume(
                db=db,
                company_id=company_id,
                hr_uid=hr_uid,
                raw=raw,
                fallback_name=_name_from_filename(fname),
                job_title=job_title,
                level=level,
                jd_text=target_jd_text,
                score_now=False,
                upload_batch_id=batch_id,
                opening=opening,
            )
            created.append(_to_out(applicant))
            embed_ids.append(applicant.id)
        except (ValueError, BotoCoreError, ClientError) as exc:
            await db.rollback()
            failed.append({"filename": fname, "error": str(exc)[:140]})
        except Exception as exc:  # noqa: BLE001 — one bad file must not kill the batch
            await db.rollback()
            # Full traceback — an "unexpected error" with only the type name is
            # undiagnosable from production logs (learned the hard way on the Space).
            log.exception(
                "hr.applicant.bulk.unexpected",
                error_type=type(exc).__name__,
                error=str(exc)[:300],
            )
            failed.append({"filename": fname, "error": "unexpected error"})

    # Embedding is left to the reconciler along with the scoring. Doing it here
    # would put a network round-trip back into a request whose whole purpose is
    # now to return quickly, and the reconciler already finds unembedded rows by
    # the same absence it uses for unscored ones.

    # An opening this upload minted, that ended up with nobody in it, is noise
    # in HR's review queue. Removed rather than left empty — nothing refers to
    # it, and it was never a decision anyone made.
    if opening["created"] and not created:
        await db.execute(
            text("DELETE FROM job_requisitions WHERE id = :i AND company_id = :c"
                 " AND NOT EXISTS (SELECT 1 FROM enrolments WHERE requisition_id = :i)"),
            {"i": opening["id"], "c": company_id},
        )
        await db.commit()

    log.info(
        "hr.applicant.bulk.complete",
        company_id=str(company_id),
        created=len(created),
        failed=len(failed),
        deferred=len(embed_ids),
        requisition_id=str(opening["id"]),
    )
    return BulkUploadResult(
        created=created,
        failed=failed,
        created_count=len(created),
        failed_count=len(failed),
        requisition_id=str(opening["id"]) if created else None,
        pending_enrichment=len(created),
    )


# Hybrid weighting: semantic meaning dominates, exact-keyword presence boosts.
_SEMANTIC_WEIGHT = 0.7
_LEXICAL_WEIGHT = 0.3
_SEARCH_LIMIT = 200
# Default AND maximum page size for the plain list. Deliberately the same
# ceiling as the search leg so both modes of GET /hr/applicants bound the scan
# identically, and so the console — which does not send page params yet — keeps
# showing what it always showed for any company under that size.
_LIST_PAGE_SIZE = _SEARCH_LIMIT

# Upper bound on `page`. OFFSET paging makes Postgres sort every matching row
# before discarding the skipped ones, so an unbounded page number costs the
# same full sort the LIMIT was added to avoid. 500 pages x 200 rows = 100k
# applicants, far past any real company on this platform; a tenant that ever
# needs more should get keyset pagination on (ats_overall, created_at, id)
# rather than a bigger ceiling.
_MAX_PAGE = 500

# The LIKE escaping moved to app/utils/sql_like.py (DG-4) when the copilot tool
# layer needed the same guard on its own ILIKE filter. Aliased, not copied — a
# second implementation is exactly how the first ILIKE gap survived review.
_LIKE_ESCAPE = LIKE_ESCAPE

# The application_progress VIEW (migration e2a4c6b8d0f1), as far as the list
# filters need it. A lightweight table construct rather than an ORM model: it
# is read-only, and a mapped class would put it in the schema inventories.
_PROGRESS = table(
    "application_progress",
    column("applicant_id"),
    column("stored_status"),
    column("opening_title"),
    column("target_job_title"),
)
_like_literal = like_literal


async def _semantic_search(
    db: AsyncSession,
    hr_uid: uuid.UUID,
    company_id: uuid.UUID,
    q: str,
    status_filter: str | None,
    job: str | None,
) -> list[ApplicantOut]:
    """Hybrid (semantic + exact-keyword) ranked search, scoped to the company.

    semantic leg : cosine similarity of the resume embedding to the query vector.
    lexical leg  : Postgres full-text rank of the query terms over the resume.
    If the embedding service is down we degrade gracefully to pure full-text.
    """
    qvec: list[float] = []
    try:
        qvec = await embed_one_remote(text=q, task_type="query", acting_user_id=str(hr_uid))
    except EmbeddingError as exc:
        log.warning("hr.applicant.search.embed_unavailable", error=str(exc))

    params: dict[str, Any] = {"company_id": company_id, "q": q, "limit": _SEARCH_LIMIT}
    where = ["a.company_id = :company_id", "a.deleted_at IS NULL"]
    # B5: by application, the same as the plain list (see list_applicants).
    if status_filter:
        where.append(
            "EXISTS (SELECT 1 FROM application_progress p"
            " WHERE p.applicant_id = a.id AND p.stored_status = :status)"
        )
        params["status"] = status_filter
    if job:
        where.append(
            "EXISTS (SELECT 1 FROM application_progress p WHERE p.applicant_id = a.id"
            " AND (p.opening_title ILIKE :job ESCAPE '\\'"
            "      OR p.target_job_title ILIKE :job ESCAPE '\\'))"
        )
        params["job"] = f"%{_like_literal(job)}%"

    lexical = (
        "ts_rank_cd(to_tsvector('english', coalesce(a.resume_text, '')), "
        "plainto_tsquery('english', :q))"
    )
    if qvec:
        params["qvec"] = to_pgvector_literal(qvec)
        semantic = (
            "CASE WHEN a.embedding IS NULL THEN 0 "
            "ELSE 1 - (a.embedding <=> CAST(:qvec AS halfvec)) END"
        )
        # Keep only rows with some signal: an embedding OR a keyword hit.
        where.append(f"(a.embedding IS NOT NULL OR ({lexical}) > 0)")
    else:
        semantic = "0"
        where.append(f"({lexical}) > 0")  # pure full-text fallback

    score_expr = f"({_SEMANTIC_WEIGHT} * ({semantic}) + {_LEXICAL_WEIGHT} * LEAST(({lexical}), 1.0))"
    sql = text(
        f"SELECT a.id AS id, GREATEST(0, ROUND(100 * {score_expr}))::int AS match_score "
        f"FROM applicants a "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY {score_expr} DESC, a.ats_overall DESC NULLS LAST, a.created_at DESC "
        f"LIMIT :limit"
    )
    ranked = (await db.execute(sql, params)).all()
    if not ranked:
        return []

    score_by_id: dict[uuid.UUID, int] = {r.id: int(r.match_score) for r in ranked}
    id_order = [r.id for r in ranked]
    rows = (
        await db.execute(
            select(Applicant)
            .where(
                Applicant.id.in_(id_order),
                Applicant.company_id == company_id,  # defence-in-depth: never cross tenants
            )
            .options(defer(Applicant.resume_text), defer(Applicant.target_jd_text))
        )
    ).scalars().all()
    by_id = {a.id: a for a in rows}

    out: list[ApplicantOut] = []
    for aid in id_order:  # preserve relevance order
        a = by_id.get(aid)
        if a is None:
            continue
        item = _to_out(a)
        item.match_score = score_by_id.get(aid)
        out.append(item)
    return out


@router.get("/applicants", response_model=list[ApplicantOut])
async def list_applicants(
    ctx: HrCtxDep,
    db: DbSessionDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    q: Annotated[
        str | None, Query(max_length=500, description="Semantic + keyword search phrase")
    ] = None,
    job: Annotated[
        str | None, Query(max_length=200, description="Filter by target job title (contains)")
    ] = None,
    # `le` as well as `ge` — without an upper bound this re-arms the very scan
    # the LIMIT was added to prevent. The ORDER BY is
    # `ats_overall DESC NULLS LAST, created_at DESC`, so Postgres must sort
    # every matching row before it can discard the OFFSET; ?page=999999 costs
    # the same full sort as the old unbounded query. The Python-side
    # materialisation stays bounded, so it is a partial reversion rather than a
    # total one, but it is still an authenticated HR user re-arming a DoS.
    page: Annotated[
        int,
        Query(ge=1, le=_MAX_PAGE, description=f"Page number (1-based, max {_MAX_PAGE})"),
    ] = 1,
    per_page: Annotated[
        int,
        Query(ge=1, le=_LIST_PAGE_SIZE, description=f"Items per page (max {_LIST_PAGE_SIZE})"),
    ] = _LIST_PAGE_SIZE,
) -> list[ApplicantOut]:
    """Applicant list for the caller's company, one page at a time.

    Default: ranked by ATS score. With ``?q=`` it becomes a hybrid semantic +
    exact-keyword search (each result carries a 0-100 ``match_score``). The
    ``status`` and ``job`` filters stack with either mode, as does paging.
    """
    hr_uid, company_id = ctx
    offset = (page - 1) * per_page

    if q and q.strip():
        ranked = await _semantic_search(db, hr_uid, company_id, q.strip(), status_filter, job)
        # The ranked window is already capped at _SEARCH_LIMIT; slice it so a
        # paging client gets a real second page rather than page one again.
        return ranked[offset : offset + per_page]

    stmt = (
        select(Applicant)
        .where(Applicant.company_id == company_id, Applicant.deleted_at.is_(None))
        # resume_text / target_jd_text are large PII columns _to_out never
        # returns; without this every listed applicant's full CV is pulled into
        # memory for nothing.
        .options(defer(Applicant.resume_text), defer(Applicant.target_jd_text))
    )
    # B5: filters match APPLICATIONS. "Shortlisted" lists everyone with a
    # shortlisted application, not only people whose latest one happens to be;
    # a job filter finds everyone who applied for it, not only people for whom
    # it was the last thing they applied to.
    if status_filter:
        stmt = stmt.where(
            exists().where(
                _PROGRESS.c.applicant_id == Applicant.id,
                _PROGRESS.c.stored_status == status_filter,
            )
        )
    if job:
        pattern = f"%{_like_literal(job)}%"
        stmt = stmt.where(
            exists().where(
                _PROGRESS.c.applicant_id == Applicant.id,
                or_(
                    _PROGRESS.c.opening_title.ilike(pattern, escape=_LIKE_ESCAPE),
                    _PROGRESS.c.target_job_title.ilike(pattern, escape=_LIKE_ESCAPE),
                ),
            )
        )
    stmt = (
        stmt.order_by(Applicant.ats_overall.desc().nullslast(), Applicant.created_at.desc())
        .limit(per_page)
        .offset(offset)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [_to_out(a) for a in rows]


@router.get("/applicants/reindex-status", response_model=ReindexResult)
async def reindex_status(ctx: HrCtxDep, db: DbSessionDep) -> ReindexResult:
    """How many of this company's applicants still lack a search embedding."""
    _hr_uid, company_id = ctx
    remaining = await db.scalar(
        text(
            "SELECT count(*) FROM applicants WHERE company_id = :c AND deleted_at IS NULL "
            "AND embedding IS NULL AND resume_text IS NOT NULL AND length(trim(resume_text)) > 0"
        ),
        {"c": company_id},
    )
    return ReindexResult(reindexed=0, failed=0, remaining=int(remaining or 0))


@router.post("/applicants/reindex", response_model=ReindexResult)
async def reindex_applicants(ctx: HrCtxDep, db: DbSessionDep) -> ReindexResult:
    """Backfill embeddings for existing applicants that have none (one click).

    Processes in batches; safe to call repeatedly until ``remaining`` is 0.
    """
    hr_uid, company_id = ctx
    rows = (
        await db.execute(
            text(
                "SELECT id, resume_text FROM applicants WHERE company_id = :c "
                "AND deleted_at IS NULL AND embedding IS NULL "
                "AND resume_text IS NOT NULL AND length(trim(resume_text)) > 0 "
                "ORDER BY created_at DESC LIMIT 96"
            ),
            {"c": company_id},
        )
    ).all()
    if not rows:
        return ReindexResult(reindexed=0, failed=0, remaining=0)

    batch = 16  # matches feedback_billing's /internal/embed cap (max 64)
    done = 0
    failed = 0
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        try:
            vecs = await embed_texts_remote(
                texts=[r.resume_text for r in chunk],
                task_type="document",
                acting_user_id=str(hr_uid),
            )
        except EmbeddingError as exc:
            log.warning("hr.applicant.reindex.embed_unavailable", error=str(exc))
            failed += len(chunk)
            continue
        for r, vec in zip(chunk, vecs, strict=True):
            if vec:
                await db.execute(
                    text(
                        "UPDATE applicants SET embedding = CAST(:e AS halfvec) "
                        "WHERE id = :id AND company_id = :cid"
                    ),
                    {"e": to_pgvector_literal(vec), "id": r.id, "cid": company_id},
                )
                done += 1
        await db.commit()

    remaining = await db.scalar(
        text(
            "SELECT count(*) FROM applicants WHERE company_id = :c AND deleted_at IS NULL "
            "AND embedding IS NULL AND resume_text IS NOT NULL AND length(trim(resume_text)) > 0"
        ),
        {"c": company_id},
    )
    log.info("hr.applicant.reindex.complete", company_id=str(company_id), done=done, failed=failed)
    return ReindexResult(reindexed=done, failed=failed, remaining=int(remaining or 0))


@router.get("/applicants/{applicant_id}", response_model=ApplicantOut)
async def get_applicant(applicant_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> ApplicantOut:
    _hr_uid, company_id = ctx
    return _to_out(await _get_owned(db, company_id, applicant_id))


class CriterionScore(BaseModel):
    """One competency's score inside a round, with the evidence for it."""

    competency_id: str
    name: str
    score: float | None = None
    evidence: str | None = None


class RoundResultOut(BaseModel):
    """One round a candidate has sat, in both layers (C8).

    ``criteria`` is the evaluation that decided progression; ``axes`` is the
    frozen four-axis comparison. Interview rounds carry both, deterministic
    rounds only the first.
    """

    round_id: str
    round_title: str
    position: int
    kind: str
    percent: float | None = None
    passed: bool | None = None
    graded_by: str
    evidence: str | None = None
    criteria: list[CriterionScore] = Field(default_factory=list)
    axes: dict[str, float] = Field(default_factory=dict)
    created_at: datetime


def _criteria_list(raw: Any) -> list[CriterionScore]:
    """Normalise ``round_results.criterion_scores`` into a stable list.

    The column is JSONB written by the scorer, so its shape is a contract with
    another service rather than something the database enforces. Two shapes are
    accepted because both have been written: a list of objects, and a mapping
    of competency id to score. Anything else yields an empty list rather than a
    500 — a malformed score must not make a candidate unopenable.
    """
    if isinstance(raw, list):
        out: list[CriterionScore] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("competency_id") or item.get("id") or "").strip()
            if not cid:
                continue
            score = item.get("score")
            out.append(
                CriterionScore(
                    competency_id=cid,
                    name=str(item.get("name") or item.get("competency_name") or cid),
                    score=float(score) if isinstance(score, int | float) else None,
                    evidence=(str(item["evidence"])[:800] if item.get("evidence") else None),
                )
            )
        return out
    if isinstance(raw, dict):
        return [
            CriterionScore(
                competency_id=str(k),
                name=str(k),
                score=float(v) if isinstance(v, int | float) else None,
            )
            for k, v in raw.items()
        ]
    return []


@router.get(
    "/applicants/{applicant_id}/round-results",
    response_model=list[RoundResultOut],
    summary="Per-round scores for one candidate, criterion by criterion",
)
async def list_applicant_round_results(
    applicant_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> list[RoundResultOut]:
    """Why a candidate scored what they scored.

    The scores existed already — C8 has been writing per-criterion results with
    evidence since the workflow engine shipped — but nothing read them back, so
    the console showed a composite and no way to ask what produced it. That is
    the gap the UI/UX specification's §7 describes: a score presented as an
    unexplained truth.

    Superseded attempts are excluded. A retake supersedes rather than
    overwrites, and showing both without saying which counted would make the
    drawer harder to read than the composite it explains.

    Tenant-scoped through the applicant, which ``_get_owned`` has already
    checked — the join then constrains on the same company_id rather than
    trusting the enrolment chain.
    """
    _hr_uid, company_id = ctx
    await _get_owned(db, company_id, applicant_id)

    rows = (
        await db.execute(
            text(
                "SELECT rr.round_id, rr.percent, rr.passed, rr.graded_by,"
                "       rr.evidence, rr.criterion_scores, rr.axes, rr.created_at,"
                "       wr.title AS round_title, wr.position, wr.kind"
                "  FROM round_results rr"
                "  JOIN enrolments e ON e.id = rr.enrolment_id"
                "  JOIN workflow_rounds wr ON wr.id = rr.round_id"
                " WHERE e.applicant_id = :a AND rr.company_id = :c"
                "   AND rr.superseded_at IS NULL AND e.deleted_at IS NULL"
                " ORDER BY wr.position, rr.created_at"
            ),
            {"a": applicant_id, "c": company_id},
        )
    ).mappings().all()

    return [
        RoundResultOut(
            round_id=str(r["round_id"]),
            round_title=r["round_title"],
            position=int(r["position"]),
            kind=r["kind"],
            percent=float(r["percent"]) if r["percent"] is not None else None,
            passed=r["passed"],
            graded_by=r["graded_by"],
            evidence=r["evidence"],
            criteria=_criteria_list(r["criterion_scores"]),
            axes={
                k: float(v)
                for k, v in (r["axes"] or {}).items()
                if isinstance(v, int | float)
            },
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.patch("/applicants/{applicant_id}", response_model=ApplicantOut)
async def update_applicant_status(
    applicant_id: uuid.UUID, body: StatusUpdate, ctx: HrCtxDep, db: DbSessionDep
) -> ApplicantOut:
    _hr_uid, company_id = ctx
    if body.status not in _VALID_STATUSES:
        raise HTTPException(
            status_code=400, detail=f"status must be one of {sorted(_VALID_STATUSES)}"
        )
    a = await _get_owned(db, company_id, applicant_id)
    # B2/B5: the ledger is application-shaped. The change goes to the
    # application named in the body, or to the only one there is. With several
    # and none named, a hire or reject cannot be attributed to an opening
    # without guessing, and a terminal decision is the last thing to guess
    # (D-05), so it is refused and pointed at the opening.
    try:
        app_ = await choose_application(
            db, applicant_id=a.id, company_id=company_id, enrolment_id=body.enrolment_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Application not found.") from exc
    if app_.ambiguous and body.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409, detail=ambiguous_decision_detail(a.full_name, len(app_.live))
        )
    only = app_.enrolment_id

    prev_status = app_.status if only is not None else a.status
    # The person-level mirror describes their latest application; an older
    # one's change is recorded on it (the ledger) and leaves the mirror alone.
    if only is None or app_.is_latest:
        a.status = body.status
        a.updated_at = datetime.now(tz=UTC)
    # Email the candidate on a real shortlist/hire/reject transition (not a re-save).
    if body.status != prev_status:
        await email_applicant_decision(
            db, applicant=a, decision=body.status, company_id=company_id,
            job_title=app_.title,
        )
        # Start the workflow on a shortlist, when there is exactly one
        # application to start. Somebody with three live applications who gets
        # shortlisted here has not been shortlisted for all three, and sending
        # three exam links because a recruiter clicked once would be worse than
        # doing nothing — the per-opening action on the requisition dashboard
        # names the application it starts.
        if body.status == "shortlisted" and only is not None:
            outcome = await on_shortlisted(db, enrolment_id=only, actor_user_id=_hr_uid)
            log.info(
                "hr.applicant.shortlisted",
                applicant_id=str(a.id),
                enrolment_id=str(only),
                action=outcome.action,
                to_round=outcome.to_round,
                reason=outcome.reason,
            )
        elif body.status == "shortlisted":
            log.info(
                "hr.applicant.shortlist.not_started",
                applicant_id=str(a.id),
                reason="no single live application to start",
            )
    if only is not None:
        # Every change, not just a shortlist. No-op (and no ledger entry) when
        # the application is already in that status.
        await record_transition(
            db,
            enrolment_id=only,
            company_id=company_id,
            to_status=body.status,
            actor_user_id=_hr_uid,
            automated=False,
            reason=f"{body.status} from the applicant board",
        )
    await db.commit()
    return _to_out(a)


@router.post("/applicants/{applicant_id}/rescore", response_model=ApplicantOut)
async def rescore_applicant(
    applicant_id: uuid.UUID,
    ctx: HrCtxDep,
    db: DbSessionDep,
    enrolment_id: Annotated[uuid.UUID | None, Query()] = None,
) -> ApplicantOut:
    """Re-run ATS scoring for one of this person's applications (D-06a).

    A score belongs to an application, so this scores an ENROLMENT: the one
    named, or by default the applicant's most recent. It is scored against that
    enrolment's own target role — a person who applied to two openings is two
    different scores — and the applicant row is updated only when it is the
    latest application (see apply_ats_to_enrolment). An applicant with no
    enrolment at all (a row predating openings) is scored as before.
    """
    hr_uid, company_id = ctx
    a = await _get_owned(db, company_id, applicant_id)
    if not a.resume_text:
        raise HTTPException(status_code=400, detail="No resume text on file to score.")
    enr = (
        await db.execute(
            text(
                "SELECT id, target_job_title, target_level, target_jd_text FROM enrolments"
                " WHERE applicant_id = :a AND company_id = :c AND deleted_at IS NULL"
                "   AND (CAST(:e AS uuid) IS NULL OR id = CAST(:e AS uuid))"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"a": applicant_id, "c": company_id, "e": enrolment_id},
        )
    ).mappings().first()
    if enrolment_id is not None and enr is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    try:
        score = await score_resume_remote(
            resume_text=a.resume_text,
            job_title=enr["target_job_title"] if enr else a.target_job_title,
            level=enr["target_level"] if enr else a.target_level,
            jd_text=enr["target_jd_text"] if enr else a.target_jd_text,
            acting_user_id=str(hr_uid),
        )
    except ResumeScoreError as exc:
        raise HTTPException(status_code=502, detail=f"Resume scoring failed: {exc}") from exc
    latest = True
    if enr is not None:
        latest = await apply_ats_to_enrolment(
            db, enrolment_id=enr["id"], score=score, resume_key=a.resume_s3_key
        )
    if latest:
        _apply_score(a, score)
    await db.commit()
    # Refresh the search embedding too (also backfills it if it was missing).
    await _embed_applicant(db, a, hr_uid)
    return _to_out(a)


@router.get("/applicants/{applicant_id}/why-match", response_model=WhyMatchOut)
async def why_match_applicant(
    applicant_id: uuid.UUID,
    ctx: HrCtxDep,
    db: DbSessionDep,
    q: Annotated[str, Query(min_length=1, description="The search phrase to explain against")],
) -> WhyMatchOut:
    """One-sentence explanation of why this candidate matches the query (lazy).

    Computed on demand (when HR opens a candidate during a search) so the LLM cost
    is paid per-look, not per-result on every keystroke.
    """
    hr_uid, company_id = ctx
    a = await _get_owned(db, company_id, applicant_id)
    if not a.resume_text:
        raise HTTPException(status_code=400, detail="No resume text on file to explain.")
    try:
        reason = await why_match_remote(
            resume_text=a.resume_text, query=q, acting_user_id=str(hr_uid)
        )
    except EmbeddingError as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not generate explanation: {exc}"
        ) from exc
    return WhyMatchOut(reason=reason)
