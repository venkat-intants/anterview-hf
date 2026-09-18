"""Offers, preboarding documents and the HRMS handoff — PH4 Wave 4 (A3, A4).

Four routers, one per audience, each scoped by the session or the link:

* ``hr_router`` (``/hr``, HR managers): offer templates; write, submit, send,
  re-send and withdraw offers; the documents an opening requires; review a
  candidate's documents; complete preboarding; prepare the signed HRMS payload.
* ``admin_router`` (``/admin``, the company super admin): the offers awaiting
  approval; approve or send back (decision D4-2).
* ``public_router`` (``/offer``, no login): the candidate's own offer and
  documents, reached ONLY with the ``X-Offer-Token`` from their link. Every
  failure reads the same. Answering needs an emailed one-time code.
* ``me_router`` (``/users/me``): a signed-in candidate's offers, and a fresh
  link to open one.

Nothing here changes an application's status. Compensation is never returned
to anyone but the company's HR managers and super admins, and the candidate.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field
from shared.auth.base import User
from sqlalchemy.ext.asyncio import AsyncSession

from app import offers as svc
from app import preboarding as docs
from app.config import settings
from app.database import DbSessionDep, get_db_session
from app.dependencies import HrCtxDep, SuperAdminCtxDep, get_current_user
from app.interviewer_scorecards import RequestMeta
from app.offers import OfferError
from app.rate_limit import rate_limit
from app.utils.request_ip import extract_client_ip, extract_user_agent

hr_router = APIRouter(prefix="/hr", tags=["offers"])
admin_router = APIRouter(prefix="/admin", tags=["offers"])
public_router = APIRouter(prefix="/offer", tags=["offer-public"])
me_router = APIRouter(prefix="/users/me", tags=["offers"])

CurrentUserDep = Annotated[User, Depends(get_current_user)]
CandidateDbDep = Annotated[AsyncSession, Depends(get_db_session)]


async def _offer_token(
    token: Annotated[str | None, Header(alias="X-Offer-Token")] = None,
) -> str | None:
    """The candidate's link credential. Read ONLY as this header — never a URL
    part — so it stays out of access logs (both Caddyfiles delete it from the
    log line). Optional here so that a missing header reaches the service and
    reads exactly like a wrong one, rather than as a validation error."""
    return token


OfferTokenDep = Annotated[str | None, Depends(_offer_token)]


async def _offer_session(
    session: Annotated[str | None, Header(alias="X-Offer-Session")] = None,
) -> str | None:
    """The hour-long preboarding session opened with an emailed code — what the
    document routes need besides the link (security review H1). A credential:
    redacted from access logs like the link."""
    return session


OfferSessionDep = Annotated[str | None, Depends(_offer_session)]


def _meta(request: Request) -> RequestMeta:
    return RequestMeta(ip_address=extract_client_ip(request), user_agent=extract_user_agent(request))


async def _fail(db: AsyncSession, exc: OfferError) -> HTTPException:
    """Refusals write nothing — except the few whose own writes must stand (a
    wrong code's attempt, an offer found expired): those are committed."""
    if exc.keep:
        await db.commit()
    else:
        await db.rollback()
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------
class OfferFieldsIn(BaseModel):
    job_title: str | None = Field(default=None, max_length=200)
    employment_type: str | None = None
    start_date: date | None = None
    location: str | None = Field(default=None, max_length=200)
    # Money arrives as a decimal (a JSON number or string), never a float.
    base_salary: Decimal | None = Field(default=None, gt=0, max_digits=14, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    pay_period: str | None = None
    bonus: str | None = Field(default=None, max_length=1000)
    equity: str | None = Field(default=None, max_length=1000)
    benefits: str | None = Field(default=None, max_length=4000)
    terms: str | None = Field(default=None, max_length=20000)
    probation_months: int | None = Field(default=None, ge=0, le=24)
    notice_period_days: int | None = Field(default=None, ge=0, le=365)
    valid_days: int | None = Field(default=None, ge=1, le=60)

    def given(self) -> dict[str, Any]:
        return self.model_dump(exclude_unset=True)


class OfferCreateIn(OfferFieldsIn):
    template_id: uuid.UUID | None = None


class TemplateIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    employment_type: str | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    pay_period: str | None = None
    probation_months: int | None = Field(default=None, ge=0, le=24)
    notice_period_days: int | None = Field(default=None, ge=0, le=365)
    benefits: str | None = Field(default=None, max_length=4000)
    terms: str | None = Field(default=None, max_length=20000)
    valid_days: int | None = Field(default=None, ge=1, le=60)


class NoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=1000)


class RequirementIn(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    doc_type: str | None = None
    description: str | None = Field(default=None, max_length=1000)
    mandatory: bool | None = None
    requires_expiry: bool | None = None
    position: int | None = Field(default=None, ge=0, le=100)


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(verify|reject|request_replacement)$")
    note: str | None = Field(default=None, max_length=1000)


class CodeIn(BaseModel):
    purpose: str = Field(pattern="^(accept|decline)$")


class SessionIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)


class AcceptIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    full_name: str = Field(min_length=2, max_length=200)


class DeclineIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)
    reason: str | None = Field(default=None, max_length=1000)


# ---------------------------------------------------------------------------
# HR — templates
# ---------------------------------------------------------------------------
@hr_router.get("/offer-templates")
async def list_offer_templates(ctx: HrCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await svc.list_templates(db, company_id=company_id)


@hr_router.post("/offer-templates", status_code=201)
async def create_offer_template(body: TemplateIn, request: Request, ctx: HrCtxDep,
                                db: DbSessionDep) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await svc.save_template(db, company_id=company_id, template_id=None,
                                      fields=body.model_dump(exclude_unset=True), actor=actor,
                                      meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@hr_router.patch("/offer-templates/{template_id}")
async def update_offer_template(template_id: uuid.UUID, body: TemplateIn, request: Request,
                                ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await svc.save_template(db, company_id=company_id, template_id=template_id,
                                      fields=body.model_dump(exclude_unset=True), actor=actor,
                                      meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@hr_router.delete("/offer-templates/{template_id}", status_code=204)
async def delete_offer_template(template_id: uuid.UUID, request: Request, ctx: HrCtxDep,
                                db: DbSessionDep) -> Response:
    actor, company_id = ctx
    try:
        await svc.delete_template(db, company_id=company_id, template_id=template_id,
                                  actor=actor, meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# HR — offers
# ---------------------------------------------------------------------------
@hr_router.get("/enrolments/{enrolment_id}/offers")
async def list_enrolment_offers(enrolment_id: uuid.UUID, ctx: HrCtxDep,
                                db: DbSessionDep) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await svc.offers_for_enrolment(db, company_id=company_id, enrolment_id=enrolment_id)


@hr_router.post("/enrolments/{enrolment_id}/offers", status_code=201)
async def create_enrolment_offer(enrolment_id: uuid.UUID, body: OfferCreateIn, request: Request,
                                 ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    actor, company_id = ctx
    fields = body.given()
    template_id = fields.pop("template_id", None)
    try:
        out = await svc.create_offer(db, company_id=company_id, enrolment_id=enrolment_id,
                                     template_id=template_id, fields=fields, actor=actor,
                                     meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@hr_router.get("/offers/{offer_id}")
async def get_offer(offer_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    _uid, company_id = ctx
    try:
        out = await svc.get_offer(db, company_id=company_id, offer_id=offer_id)
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    out["history"] = await svc.history(db, company_id=company_id, offer_id=offer_id)
    return out


@hr_router.patch("/offers/{offer_id}")
async def update_offer(offer_id: uuid.UUID, body: OfferFieldsIn, request: Request, ctx: HrCtxDep,
                       db: DbSessionDep) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await svc.update_offer(db, company_id=company_id, offer_id=offer_id,
                                     fields=body.given(), actor=actor, meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


async def _hr_step(fn: Any, offer_id: uuid.UUID, request: Request, ctx: Any, db: AsyncSession,
                   **kw: Any) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out: dict[str, Any] = await fn(db, company_id=company_id, offer_id=offer_id, actor=actor,
                                       meta=_meta(request), **kw)
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@hr_router.post("/offers/{offer_id}/submit")
async def submit_offer(offer_id: uuid.UUID, request: Request, ctx: HrCtxDep,
                       db: DbSessionDep) -> dict[str, Any]:
    return await _hr_step(svc.submit, offer_id, request, ctx, db)


@hr_router.post("/offers/{offer_id}/recall")
async def recall_offer(offer_id: uuid.UUID, request: Request, ctx: HrCtxDep,
                       db: DbSessionDep) -> dict[str, Any]:
    return await _hr_step(svc.recall, offer_id, request, ctx, db)


@hr_router.post("/offers/{offer_id}/reopen")
async def reopen_offer(offer_id: uuid.UUID, request: Request, ctx: HrCtxDep,
                       db: DbSessionDep) -> dict[str, Any]:
    return await _hr_step(svc.reopen, offer_id, request, ctx, db)


@hr_router.post("/offers/{offer_id}/send")
async def send_offer(offer_id: uuid.UUID, request: Request, ctx: HrCtxDep,
                     db: DbSessionDep) -> dict[str, Any]:
    return await _hr_step(svc.send, offer_id, request, ctx, db)


@hr_router.post("/offers/{offer_id}/resend")
async def resend_offer(offer_id: uuid.UUID, request: Request, ctx: HrCtxDep,
                       db: DbSessionDep) -> dict[str, Any]:
    return await _hr_step(svc.send, offer_id, request, ctx, db, again=True)


@hr_router.post("/offers/{offer_id}/withdraw")
async def withdraw_offer(offer_id: uuid.UUID, body: NoteIn, request: Request, ctx: HrCtxDep,
                         db: DbSessionDep) -> dict[str, Any]:
    return await _hr_step(svc.withdraw, offer_id, request, ctx, db, reason=body.note)


# ---------------------------------------------------------------------------
# HR — documents and preboarding
# ---------------------------------------------------------------------------
@hr_router.get("/requisitions/{requisition_id}/document-requirements")
async def list_document_requirements(requisition_id: uuid.UUID, ctx: HrCtxDep,
                                     db: DbSessionDep) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await docs.list_requirements(db, company_id=company_id, requisition_id=requisition_id)


@hr_router.post("/requisitions/{requisition_id}/document-requirements", status_code=201)
async def add_document_requirement(requisition_id: uuid.UUID, body: RequirementIn,
                                   request: Request, ctx: HrCtxDep,
                                   db: DbSessionDep) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await docs.add_requirement(db, company_id=company_id, requisition_id=requisition_id,
                                         fields=body.model_dump(exclude_unset=True), actor=actor,
                                         meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@hr_router.patch("/document-requirements/{requirement_id}")
async def update_document_requirement(requirement_id: uuid.UUID, body: RequirementIn,
                                      request: Request, ctx: HrCtxDep,
                                      db: DbSessionDep) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await docs.update_requirement(db, company_id=company_id,
                                            requirement_id=requirement_id,
                                            fields=body.model_dump(exclude_unset=True),
                                            actor=actor, meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out or {}


@hr_router.delete("/document-requirements/{requirement_id}", status_code=204)
async def remove_document_requirement(requirement_id: uuid.UUID, request: Request, ctx: HrCtxDep,
                                      db: DbSessionDep) -> Response:
    actor, company_id = ctx
    try:
        await docs.update_requirement(db, company_id=company_id, requirement_id=requirement_id,
                                      fields={}, actor=actor, meta=_meta(request), remove=True)
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return Response(status_code=204)


@hr_router.get("/offers/{offer_id}/documents")
async def offer_documents(offer_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    _uid, company_id = ctx
    try:
        out = await docs.hr_checklist(db, company_id=company_id, offer_id=offer_id)
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.rollback()  # the offer row was read FOR UPDATE; release it
    return out


@hr_router.post("/documents/{document_id}/review")
async def review_document(document_id: uuid.UUID, body: ReviewIn, request: Request,
                          ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await docs.review(db, company_id=company_id, document_id=document_id,
                                action=body.action, note=body.note, actor=actor,
                                meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@hr_router.post("/documents/{document_id}/download")
async def download_document(document_id: uuid.UUID, request: Request, ctx: HrCtxDep,
                            db: DbSessionDep) -> dict[str, Any]:
    """POST, not GET: it writes the record that this person opened it."""
    actor, company_id = ctx
    try:
        out = await docs.hr_download(db, company_id=company_id, document_id=document_id,
                                     actor=actor, meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@hr_router.post("/offers/{offer_id}/preboarding-complete")
async def complete_preboarding(offer_id: uuid.UUID, request: Request, ctx: HrCtxDep,
                               db: DbSessionDep) -> dict[str, Any]:
    return await _hr_step(docs.complete, offer_id, request, ctx, db)


@hr_router.post("/offers/{offer_id}/hrms-export")
async def hrms_export(offer_id: uuid.UUID, request: Request, ctx: HrCtxDep,
                      db: DbSessionDep) -> dict[str, Any]:
    return await _hr_step(docs.export_to_hrms, offer_id, request, ctx, db)


# ---------------------------------------------------------------------------
# Super admin — approval (D4-2)
# ---------------------------------------------------------------------------
@admin_router.get("/offer-approvals")
async def offer_approvals(ctx: SuperAdminCtxDep, db: DbSessionDep) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await svc.pending_for_company(db, company_id=company_id)


@admin_router.get("/offers/{offer_id}")
async def admin_offer(offer_id: uuid.UUID, ctx: SuperAdminCtxDep,
                      db: DbSessionDep) -> dict[str, Any]:
    _uid, company_id = ctx
    try:
        out = await svc.get_offer(db, company_id=company_id, offer_id=offer_id)
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    out["history"] = await svc.history(db, company_id=company_id, offer_id=offer_id)
    return out


async def _decide(offer_id: uuid.UUID, approve: bool, body: NoteIn, request: Request,
                  ctx: Any, db: AsyncSession) -> dict[str, Any]:
    reviewer, company_id = ctx
    try:
        out = await svc.decide(db, company_id=company_id, offer_id=offer_id, approve=approve,
                               note=body.note, reviewer=reviewer, meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@admin_router.post("/offers/{offer_id}/approve")
async def approve_offer(offer_id: uuid.UUID, body: NoteIn, request: Request,
                        ctx: SuperAdminCtxDep, db: DbSessionDep) -> dict[str, Any]:
    return await _decide(offer_id, True, body, request, ctx, db)


@admin_router.post("/offers/{offer_id}/reject")
async def reject_offer(offer_id: uuid.UUID, body: NoteIn, request: Request,
                       ctx: SuperAdminCtxDep, db: DbSessionDep) -> dict[str, Any]:
    return await _decide(offer_id, False, body, request, ctx, db)


# ---------------------------------------------------------------------------
# The candidate, by link
# ---------------------------------------------------------------------------
@public_router.get("", dependencies=[rate_limit("offer_view", 30)])
async def view_offer(token: OfferTokenDep, request: Request, db: CandidateDbDep) -> dict[str, Any]:
    try:
        out = await svc.candidate_view(db, raw=token, meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.post("/code", dependencies=[rate_limit("offer_code", 5)])
async def request_offer_code(body: CodeIn, token: OfferTokenDep, request: Request,
                             db: CandidateDbDep) -> dict[str, Any]:
    try:
        out = await svc.request_code(db, raw=token, purpose=body.purpose, meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.post("/accept", dependencies=[rate_limit("offer_answer", 10)])
async def accept_offer(body: AcceptIn, token: OfferTokenDep, request: Request,
                       db: CandidateDbDep) -> dict[str, Any]:
    try:
        out = await svc.answer(db, raw=token, accept=True, code=body.code, name=body.full_name,
                               reason=None, meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.post("/decline", dependencies=[rate_limit("offer_answer", 10)])
async def decline_offer(body: DeclineIn, token: OfferTokenDep, request: Request,
                        db: CandidateDbDep) -> dict[str, Any]:
    try:
        out = await svc.answer(db, raw=token, accept=False, code=body.code, name=None,
                               reason=body.reason, meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.post("/documents/code", dependencies=[rate_limit("offer_code", 5)])
async def request_documents_code(token: OfferTokenDep, request: Request,
                                 db: CandidateDbDep) -> dict[str, Any]:
    """Email a code that opens the documents for an hour."""
    try:
        out = await svc.request_code(db, raw=token, purpose="documents", meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.post("/documents/session", dependencies=[rate_limit("offer_answer", 10)])
async def open_documents_session(body: SessionIn, token: OfferTokenDep, request: Request,
                                 db: CandidateDbDep) -> dict[str, Any]:
    try:
        out = await svc.open_documents_session(db, raw=token, code=body.code, meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.get("/documents", dependencies=[rate_limit("offer_view", 30)])
async def my_documents(token: OfferTokenDep, session: OfferSessionDep,
                       db: CandidateDbDep) -> dict[str, Any]:
    try:
        out = await docs.candidate_checklist(db, raw=token, session=session)
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.rollback()
    return out


@public_router.post("/documents/{requirement_id}", status_code=201,
                   dependencies=[rate_limit("offer_upload", 10)])
async def upload_my_document(
    requirement_id: uuid.UUID, token: OfferTokenDep, session: OfferSessionDep,
    request: Request, db: CandidateDbDep,
    file: Annotated[UploadFile, File()],
    expires_on: Annotated[date | None, Form()] = None,
) -> dict[str, Any]:
    # Read one byte past the limit: enough to say "too large" without buffering
    # an arbitrarily large body (the edge also caps /offer* bodies at 11 MB).
    data = await file.read(settings.preboarding_document_max_bytes + 1)
    try:
        out = await docs.upload(db, raw=token, session=session, requirement_id=requirement_id,
                                data=data, filename=file.filename, expires_on=expires_on,
                                meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    key = out.pop("_storage_key")
    try:
        await db.commit()
    except Exception:
        # The rows are gone; so must the bytes be (security review L4).
        from app.document_storage import remove as _remove  # noqa: PLC0415

        await _remove(settings, [key])
        raise
    return out
# No candidate download: the candidate already has the file, and a link that
# could fetch every document back was the exposure (security review H1).


# ---------------------------------------------------------------------------
# The candidate, signed in
# ---------------------------------------------------------------------------
@me_router.get("/offers")
async def my_offers(user: CurrentUserDep, db: CandidateDbDep) -> list[dict[str, Any]]:
    return await svc.candidate_offers(db, user_id=uuid.UUID(user.user_id))


@me_router.post("/offers/{offer_id}/link")
async def my_offer_link(offer_id: uuid.UUID, request: Request, user: CurrentUserDep,
                        db: CandidateDbDep) -> dict[str, Any]:
    try:
        out = await svc.candidate_link(db, user_id=uuid.UUID(user.user_id), offer_id=offer_id,
                                       meta=_meta(request))
    except OfferError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out
