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
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from shared.auth.base import User
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.database import get_db_session
from app.dependencies import require_role
from app.embedding_client import (
    EmbeddingError,
    embed_one_remote,
    embed_texts_remote,
    to_pgvector_literal,
    why_match_remote,
)
from app.mailer import enqueue_email
from app.models import Applicant
from app.routers.resume import _delete_from_s3, _extract_pdf_text, _upload_to_s3
from app.scoring_client import ResumeScoreError, score_resume_remote

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr", tags=["hr-applicants"])

_MAX_RESUME_BYTES = 5 * 1024 * 1024  # 5 MB
_MAX_BULK_FILES = 25  # per batch; large batches should move to an async queue (Phase 5)
_VALID_STATUSES = {"new", "shortlisted", "rejected", "interviewed", "hired"}
# Status transitions that warrant a decision email to the candidate.
_DECISION_EMAIL_STATUSES = {"shortlisted", "rejected", "hired"}

DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


async def email_applicant_decision(
    db: AsyncSession,
    *,
    applicant: Applicant,
    decision: str,
    company_id: uuid.UUID,
) -> None:
    """Stage a branded shortlist/hire/reject email to the candidate (caller commits).

    No-op when the applicant has no email on file or the decision isn't one we
    notify on. Best-effort: enqueue never raises on a bad recipient. Shared by the
    applicant status PATCH and the pipeline hire/reject decision endpoint.
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
            "job_title": applicant.target_job_title,
            "decision": decision,
        },
        company_id=company_id,
        related_kind="applicant_decision",
        related_id=applicant.id,
    )


# ---------------------------------------------------------------------------
# Tenant context — resolve the caller's company_id (the isolation boundary)
# ---------------------------------------------------------------------------
async def get_hr_company(
    user: Annotated[User, Depends(require_role("hr_manager"))],
    db: DbSessionDep,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Return (hr_user_id, company_id). 403 if the HR is not assigned a company."""
    try:
        uid = uuid.UUID(user.user_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user identity."
        ) from exc
    company_id = await db.scalar(
        text("SELECT company_id FROM users WHERE id = :uid AND deleted_at IS NULL"),
        {"uid": uid},
    )
    if company_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is not assigned to a company.",
        )
    return uid, company_id


HrCtxDep = Annotated[tuple[uuid.UUID, uuid.UUID], Depends(get_hr_company)]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ApplicantOut(BaseModel):
    id: str
    full_name: str
    email: str | None
    target_job_title: str
    target_level: str
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


class WhyMatchOut(BaseModel):
    reason: str


class ReindexResult(BaseModel):
    reindexed: int
    failed: int
    remaining: int


class StatusUpdate(BaseModel):
    status: str


class BulkUploadResult(BaseModel):
    created: list[ApplicantOut]
    failed: list[dict[str, str]]
    created_count: int
    failed_count: int


def _to_out(a: Applicant) -> ApplicantOut:
    # resume_text / s3_key are PII — never returned in the list/detail payload.
    return ApplicantOut(
        id=str(a.id),
        full_name=a.full_name,
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
    )


def _apply_score(a: Applicant, score: dict[str, Any]) -> None:
    a.ats_overall = int(score.get("overall", 0))
    a.ats_breakdown = score.get("breakdown")
    a.ats_strengths = score.get("strengths")
    a.ats_concerns = score.get("concerns")
    a.ats_recommendation = score.get("recommendation")
    a.ats_summary = score.get("summary")
    a.updated_at = datetime.now(tz=UTC)


async def _store_embedding(
    db: AsyncSession, company_id: uuid.UUID, applicant_id: uuid.UUID, vec: list[float]
) -> None:
    """Persist a resume embedding on the applicant row (ORM does not map it).

    company_id is in the predicate as defence-in-depth: every applicant write in
    this module stays tenant-scoped, even though applicant_id is already owned.
    """
    if not vec:
        return
    await db.execute(
        text(
            "UPDATE applicants SET embedding = CAST(:emb AS halfvec) "
            "WHERE id = :id AND company_id = :cid"
        ),
        {"emb": to_pgvector_literal(vec), "id": applicant_id, "cid": company_id},
    )
    await db.commit()


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
    a = await db.scalar(
        select(Applicant).where(
            Applicant.id == applicant_id,
            Applicant.company_id == company_id,
            Applicant.deleted_at.is_(None),
        )
    )
    if a is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Applicant not found.")
    return a


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
) -> Applicant:
    """Extract → store → score → persist ONE resume.

    The candidate's name + email are auto-extracted from the resume by the scorer
    (so the score happens *before* insert and the extracted name lands on the
    initial row). Falls back to ``fallback_name`` if extraction yields nothing or
    the scorer is unavailable. Raises ``ValueError`` (bad PDF / DB) or a botocore
    error (storage) so the caller can record it as a per-file failure.
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
            email = str(score["candidate_email"]).strip()[:320] or None
    except ResumeScoreError as exc:
        log.warning("hr.applicant.bulk.score_unavailable", error=str(exc))

    now = datetime.now(tz=UTC)
    applicant = Applicant(
        id=applicant_id,
        company_id=company_id,
        created_by_user_id=hr_uid,
        full_name=full_name,
        email=email,
        target_job_title=job_title,
        target_level=level,
        target_jd_text=jd_text,
        resume_text=resume_text,
        resume_s3_key=s3_key,
        status="new",
        created_at=now,
        updated_at=now,
    )
    if score is not None:
        _apply_score(applicant, score)
    db.add(applicant)
    try:
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
    email: Annotated[str | None, Form()] = None,
    target_level: Annotated[str, Form()] = "mid",
    target_jd_text: Annotated[str | None, Form()] = None,
) -> ApplicantOut:
    """Upload an applicant's resume, store it, and ATS-score it (best-effort)."""
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

    applicant_id = uuid.uuid4()
    s3_key = f"applicants/{company_id}/{applicant_id}.pdf"
    try:
        await _upload_to_s3(raw, s3_key)
    except (BotoCoreError, ClientError) as exc:
        log.error("hr.applicant.storage_failed", error_type=type(exc).__name__, error=str(exc))
        raise HTTPException(
            status_code=502, detail="Resume storage is currently unavailable. Please try again."
        ) from exc

    now = datetime.now(tz=UTC)
    applicant = Applicant(
        id=applicant_id,
        company_id=company_id,
        created_by_user_id=hr_uid,
        full_name=full_name.strip(),
        email=(email.strip() if email and email.strip() else None),
        target_job_title=target_job_title.strip(),
        target_level=target_level.strip() or "mid",
        target_jd_text=target_jd_text,
        resume_text=resume_text,
        resume_s3_key=s3_key,
        status="new",
        created_at=now,
        updated_at=now,
    )
    db.add(applicant)
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001
        log.error("hr.applicant.db_write_failed", error_type=type(exc).__name__)
        await _delete_from_s3(s3_key)
        raise HTTPException(
            status_code=503, detail="Could not save the applicant. Please try again."
        ) from exc

    # ATS scoring is best-effort: a scorer outage must NOT lose the applicant.
    try:
        score = await score_resume_remote(
            resume_text=resume_text,
            job_title=applicant.target_job_title,
            level=applicant.target_level,
            jd_text=applicant.target_jd_text,
            acting_user_id=str(hr_uid),
        )
        _apply_score(applicant, score)
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
) -> BulkUploadResult:
    """Upload many resumes at once for a SINGLE role.

    For each resume the candidate's name + email are auto-extracted from the
    resume (no manual entry), then it is stored and ATS-scored. A bad/empty/oversized
    file is reported in ``failed`` without aborting the rest of the batch. Processing
    is sequential per file (the scorer is the slow step) — large batches should move
    to an async queue (Phase 5).
    """
    hr_uid, company_id = ctx
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")
    if len(files) > _MAX_BULK_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Up to {_MAX_BULK_FILES} resumes per batch — you sent {len(files)}.",
        )

    job_title = target_job_title.strip() or "General Role"
    level = target_level.strip() or "mid"

    created: list[ApplicantOut] = []
    failed: list[dict[str, str]] = []
    embed_ids: list[uuid.UUID] = []
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

    # Embed the whole batch in one or two calls (best-effort — never fails upload).
    await _embed_applicants_batch(db, company_id, hr_uid, embed_ids)

    log.info(
        "hr.applicant.bulk.complete",
        company_id=str(company_id),
        created=len(created),
        failed=len(failed),
    )
    return BulkUploadResult(
        created=created,
        failed=failed,
        created_count=len(created),
        failed_count=len(failed),
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
    if status_filter:
        where.append("a.status = :status")
        params["status"] = status_filter
    if job:
        where.append("a.target_job_title ILIKE :job")
        params["job"] = f"%{job}%"

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
    if status_filter:
        stmt = stmt.where(Applicant.status == status_filter)
    if job:
        stmt = stmt.where(Applicant.target_job_title.ilike(f"%{job}%"))
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
    prev_status = a.status
    a.status = body.status
    a.updated_at = datetime.now(tz=UTC)
    # Email the candidate on a real shortlist/hire/reject transition (not a re-save).
    if body.status != prev_status:
        await email_applicant_decision(
            db, applicant=a, decision=body.status, company_id=company_id
        )
    await db.commit()
    return _to_out(a)


@router.post("/applicants/{applicant_id}/rescore", response_model=ApplicantOut)
async def rescore_applicant(
    applicant_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep
) -> ApplicantOut:
    hr_uid, company_id = ctx
    a = await _get_owned(db, company_id, applicant_id)
    if not a.resume_text:
        raise HTTPException(status_code=400, detail="No resume text on file to score.")
    try:
        score = await score_resume_remote(
            resume_text=a.resume_text,
            job_title=a.target_job_title,
            level=a.target_level,
            jd_text=a.target_jd_text,
            acting_user_id=str(hr_uid),
        )
    except ResumeScoreError as exc:
        raise HTTPException(status_code=502, detail=f"Resume scoring failed: {exc}") from exc
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
