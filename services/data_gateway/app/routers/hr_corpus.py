"""HR/super-admin document library routes — PH5-E2.

Mounted at ``/hr/library`` — NOT ``/hr/documents`` (LOW-8, security review /
auditor's call). ``/hr/documents/{id}`` was already a route in ``offers.py``
for candidate preboarding documents (candidate PII); this corpus is an
unrelated, company-authored entity, and the two would have shared a URL
namespace distinguished only by HTTP verb. Making the corpus download a POST
(to dodge the collision the other way) was rejected because it would create a
genuine one, not just a confusing coincidence — so the corpus moved instead.

``hr_manager`` and ``super_admin`` share every route (Q3 in the design doc is
enforced in ``app/corpus.py``, not by keeping a super_admin off this router
entirely): both may see and manage the company's document library, but a
``super_admin`` may never create or read back an ``hr_only`` document — the
service layer refuses that with a 422, and the audience predicate in
``search_corpus``/``get_document``/``download_url``/``update_document``/
``delete_document`` (``app/corpus.py::_may_read``) makes it unreachable even
if the router ever forgot to check.

No route here is reachable by a candidate, an interviewer, or a platform role
— ``require_role_password_ok`` is the gate, exactly like every other HR
router.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from shared.auth.base import User
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from app import corpus as svc
from app.config import settings
from app.corpus import CorpusError
from app.database import get_db_session
from app.dependencies import require_role_password_ok
from app.interviewer_scorecards import RequestMeta
from app.rate_limit import rate_limit_context
from app.utils.request_ip import extract_client_ip, extract_user_agent

router = APIRouter(prefix="/hr/library", tags=["corpus"])

DbSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


# ---------------------------------------------------------------------------
# Tenant + role context — hr_manager and super_admin share this router, and
# CORPUS_AUDIENCE_ROLES (app/corpus.py) needs the ROLE, not only company_id.
# ---------------------------------------------------------------------------
async def _corpus_ctx(
    user: Annotated[User, Depends(require_role_password_ok("hr_manager", "super_admin"))],
    db: DbSessionDep,
) -> tuple[uuid.UUID, uuid.UUID, str]:
    """(actor_user_id, company_id, role). 403 if not assigned to a company."""
    try:
        uid = uuid.UUID(user.user_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user identity."
        ) from exc
    row = (
        await db.execute(
            sa_text(
                "SELECT u.company_id FROM users u"
                "  JOIN companies c ON c.id = u.company_id AND c.deleted_at IS NULL"
                " WHERE u.id = :uid AND u.deleted_at IS NULL"
            ),
            {"uid": uid},
        )
    ).first()
    if row is None or row[0] is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is not assigned to an active company.",
        )
    role = "hr_manager" if "hr_manager" in user.roles else "super_admin"
    return uid, row[0], role


CorpusCtxDep = Annotated[tuple[uuid.UUID, uuid.UUID, str], Depends(_corpus_ctx)]


def _meta(request: Request) -> RequestMeta:
    return RequestMeta(ip_address=extract_client_ip(request), user_agent=extract_user_agent(request))


async def _fail(db: AsyncSession, exc: CorpusError) -> HTTPException:
    await db.rollback()
    return HTTPException(
        status_code=exc.status_code, detail={"failure_code": exc.code, "message": exc.message}
    )


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------
class DocumentEditIn(BaseModel):
    audience: str = Field(pattern="^(all_staff|hr_only)$")
    expires_on: date | None = None


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=4, ge=1, le=6)


# ---------------------------------------------------------------------------
# Routes — final list (all under /hr/library):
#   GET    /hr/library
#   GET    /hr/library/{document_id}
#   POST   /hr/library                          (rate-limited)
#   POST   /hr/library/{document_id}/versions   (rate-limited)
#   PATCH  /hr/library/{document_id}
#   DELETE /hr/library/{document_id}
#   POST   /hr/library/{document_id}/reindex
#   GET    /hr/library/{document_id}/download
#   POST   /hr/library/search
# ---------------------------------------------------------------------------
_UPLOAD_RATE_LIMIT = [rate_limit_context(
    "corpus_upload", settings.corpus_upload_per_minute, _corpus_ctx,
)]


@router.get("")
async def list_documents(ctx: CorpusCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    _uid, company_id, role = ctx
    return await svc.list_documents(db, company_id=company_id, role=role)


@router.get("/{document_id}")
async def get_document(document_id: uuid.UUID, ctx: CorpusCtxDep, db: DbSessionDep) -> dict[str, Any]:
    _uid, company_id, role = ctx
    row = await svc.get_document(db, company_id=company_id, role=role, document_id=document_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Document not found.")
    return row


@router.post("", status_code=201, dependencies=_UPLOAD_RATE_LIMIT)
async def upload_document(
    request: Request, ctx: CorpusCtxDep, db: DbSessionDep,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form()],
    audience: Annotated[str, Form()],
    doc_kind: Annotated[str, Form()] = "other",
    expires_on: Annotated[date | None, Form()] = None,
    attested: Annotated[bool, Form()] = False,
) -> dict[str, Any]:
    actor, company_id, role = ctx
    data = await file.read(settings.corpus_document_max_bytes + 1)
    try:
        out = await svc.ingest_document(
            db, company_id=company_id, actor=actor, actor_role=role, title=title,
            audience=audience, doc_kind=doc_kind, expires_on=expires_on, attested=attested,
            data=data, filename=file.filename, meta=_meta(request),
        )
    except CorpusError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@router.post("/{document_id}/versions", status_code=201, dependencies=_UPLOAD_RATE_LIMIT)
async def replace_document(
    document_id: uuid.UUID, request: Request, ctx: CorpusCtxDep, db: DbSessionDep,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    actor, company_id, role = ctx
    data = await file.read(settings.corpus_document_max_bytes + 1)
    try:
        out = await svc.add_version(
            db, company_id=company_id, actor=actor, actor_role=role, document_id=document_id,
            data=data, filename=file.filename, meta=_meta(request),
        )
    except CorpusError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@router.patch("/{document_id}")
async def edit_document(
    document_id: uuid.UUID, body: DocumentEditIn, request: Request, ctx: CorpusCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id, role = ctx
    try:
        out = await svc.update_document(
            db, company_id=company_id, actor=actor, actor_role=role, document_id=document_id,
            audience=body.audience, expires_on=body.expires_on, meta=_meta(request),
        )
    except CorpusError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID, request: Request, ctx: CorpusCtxDep, db: DbSessionDep,
) -> Response:
    actor, company_id, role = ctx
    try:
        await svc.delete_document(db, company_id=company_id, actor=actor, actor_role=role,
                                  document_id=document_id, meta=_meta(request))
    except CorpusError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return Response(status_code=204)


@router.post("/{document_id}/reindex")
async def reindex_document(
    document_id: uuid.UUID, request: Request, ctx: CorpusCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    """Retry a version parked at 'failed' — the reconciler otherwise never
    picks one back up (see ``app/corpus.py::reindex_document``)."""
    actor, company_id, role = ctx
    try:
        out = await svc.reindex_document(
            db, company_id=company_id, actor=actor, actor_role=role, document_id=document_id,
            meta=_meta(request),
        )
    except CorpusError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@router.get("/{document_id}/download")
async def download_document(
    document_id: uuid.UUID, request: Request, ctx: CorpusCtxDep, db: DbSessionDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    actor, company_id, role = ctx
    try:
        out = await svc.download_url(
            db, company_id=company_id, actor=actor, role=role, document_id=document_id,
            version=version, meta=_meta(request),
        )
    except CorpusError as exc:
        raise await _fail(db, exc) from exc
    # MEDIUM-4 (security review): download_url writes the audit row via
    # db.add(); this GET must commit for it to persist, since nothing else on
    # this path does.
    await db.commit()
    return out


@router.post("/search")
async def search_documents(body: SearchIn, ctx: CorpusCtxDep, db: DbSessionDep) -> dict[str, Any]:
    """Manual search, mainly for the library screen's own search box. The
    copilot tool ``search_company_documents`` (``app/agents/tools.py``, the
    E1 wave) reaches the same ``search_corpus`` function this route calls —
    it is the only path from the corpus to a model."""
    _uid, company_id, role = ctx
    return await svc.search_corpus(db, company_id=company_id, role=role, query=body.query,
                                   limit=body.limit)
