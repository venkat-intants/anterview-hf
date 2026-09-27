"""HR talent-pool routes — PH5-E3. ``hr_manager`` ONLY.

  GET    /hr/pools                                          list this company's pools
  POST   /hr/pools                                          create a pool
  GET    /hr/pools/{pool_id}                                a pool and its members
  PATCH  /hr/pools/{pool_id}                                rename / describe / archive / restore
  DELETE /hr/pools/{pool_id}                                delete a pool and its members
  POST   /hr/pools/{pool_id}/members                        add member(s)
  DELETE /hr/pools/{pool_id}/members/{member_id}            remove a member (soft)
  POST   /hr/pools/{pool_id}/members/{member_id}/evidence-reviewed
  POST   /hr/pools/{pool_id}/members/{member_id}/invite

A NARROWER ROUTER THAN ``hr_corpus.py``, on purpose, and for the SAME reason
``hr_rediscovery.py`` states: a pool names candidates, so this is
``candidate_pii`` (``shared/agents/schema.py::DATA_CLASS_ROLES``), and that
class is ``{hr_manager}`` alone. ``HrCtxDep`` is the whole gate — a company
``super_admin`` gets 403 here even though it manages the company, because a
company super admin is deliberately NOT a superset of HR (``CLAUDE.md``).

A pool id, member id or requisition id from ANOTHER company 404s, never 403 —
the ``/hr`` convention (``routers/evidence_graph.py``): the response must not
disclose that a record exists at all to a caller who cannot see it.

NOTHING HERE CAN DECIDE A HIRING OUTCOME. The invite route creates an
``enrolments`` row through the same ``enrol_applicant`` every other enrolment
in this service goes through, and does nothing else — no stage past ``new``,
no round result, no status, no decision.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Query, Request, status

from app import talent_pools as svc
from app.database import DbSessionDep
from app.dependencies import HrCtxDep
from app.interviewer_scorecards import RequestMeta
from app.schemas.pools import (
    MemberAddIn,
    MemberAddOut,
    MemberInviteIn,
    MemberInviteOut,
    MemberOut,
    MemberRemoveIn,
    MemberRemoveOut,
    PoolCreateIn,
    PoolDeleteOut,
    PoolListOut,
    PoolOut,
    PoolUpdateIn,
    PoolWithMembersOut,
)
from app.utils.request_ip import extract_client_ip, extract_user_agent

router = APIRouter(prefix="/hr/pools", tags=["talent-pools"])


def _meta(request: Request) -> RequestMeta:
    return RequestMeta(ip_address=extract_client_ip(request), user_agent=extract_user_agent(request))


def _fail(exc: svc.PoolError) -> HTTPException:
    # The wire shape the lead confirmed against the two routers that already
    # exist: `app/routers/hr_corpus.py` and `app/routers/hr_rediscovery.py`
    # both emit `detail={"failure_code": ..., "message": ...}`, and
    # `web/src/api/corpus.ts` already parses `detail.failure_code`.
    return HTTPException(
        status_code=exc.status_code, detail={"failure_code": exc.code, "message": exc.message}
    )


def _uuid_or_404(raw: str, *, what: str) -> uuid.UUID:
    """A malformed id names nothing — 404, never 422, so a guess cannot be
    told apart from a well-formed id belonging to another company."""
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"{what} not found.") from exc


@router.get("", response_model=PoolListOut)
async def list_pools(
    ctx: HrCtxDep, db: DbSessionDep,
    include_archived: Annotated[bool, Query()] = False,
) -> PoolListOut:
    _uid, company_id = ctx
    out = await svc.list_pools(db, company_id=company_id, include_archived=include_archived)
    return PoolListOut(**out)


@router.post("", response_model=PoolOut, status_code=status.HTTP_201_CREATED)
async def create_pool(
    body: PoolCreateIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> PoolOut:
    actor, company_id = ctx
    try:
        out = await svc.create_pool(
            db, company_id=company_id, actor=actor, name=body.name,
            description=body.description, meta=_meta(request),
        )
    except svc.PoolError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return PoolOut(**out)


@router.get("/{pool_id}", response_model=PoolWithMembersOut)
async def get_pool(pool_id: str, ctx: HrCtxDep, db: DbSessionDep) -> PoolWithMembersOut:
    _uid, company_id = ctx
    pid = _uuid_or_404(pool_id, what="Pool")
    out = await svc.get_pool_with_members(db, company_id=company_id, pool_id=pid)
    if out is None:
        raise HTTPException(status_code=404, detail="Pool not found.")
    return PoolWithMembersOut(pool=PoolOut(**out["pool"]), members=[MemberOut(**m) for m in out["members"]])


@router.patch("/{pool_id}", response_model=PoolOut)
async def update_pool(
    pool_id: str, body: PoolUpdateIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> PoolOut:
    actor, company_id = ctx
    pid = _uuid_or_404(pool_id, what="Pool")
    fields = body.model_fields_set
    try:
        out = await svc.update_pool(
            db, company_id=company_id, actor=actor, pool_id=pid,
            name=body.name, description=body.description, archived=body.archived,
            name_given="name" in fields, description_given="description" in fields,
            meta=_meta(request),
        )
    except svc.PoolError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return PoolOut(**out)


@router.delete("/{pool_id}", response_model=PoolDeleteOut)
async def delete_pool(
    pool_id: str, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> PoolDeleteOut:
    actor, company_id = ctx
    pid = _uuid_or_404(pool_id, what="Pool")
    try:
        out = await svc.delete_pool(db, company_id=company_id, actor=actor, pool_id=pid,
                                    meta=_meta(request))
    except svc.PoolError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return PoolDeleteOut(**out)


@router.post("/{pool_id}/members", response_model=MemberAddOut, status_code=status.HTTP_201_CREATED)
async def add_members(
    pool_id: str, body: MemberAddIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> MemberAddOut:
    actor, company_id = ctx
    pid = _uuid_or_404(pool_id, what="Pool")
    applicant_ids: list[uuid.UUID] = []
    for raw in body.applicant_ids:
        try:
            applicant_ids.append(uuid.UUID(raw))
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail={"failure_code": "invalid_name", "message": "Bad applicant id."},
            ) from exc
    try:
        out = await svc.add_members(
            db, company_id=company_id, actor=actor, pool_id=pid, applicant_ids=applicant_ids,
            source=body.source, note=body.note, match_reason=body.match_reason,
            meta=_meta(request),
        )
    except svc.PoolError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return MemberAddOut(**out)


@router.delete("/{pool_id}/members/{member_id}", response_model=MemberRemoveOut)
async def remove_member(
    pool_id: str, member_id: str, request: Request, ctx: HrCtxDep, db: DbSessionDep,
    body: Annotated[MemberRemoveIn | None, Body()] = None,
) -> MemberRemoveOut:
    actor, company_id = ctx
    pid = _uuid_or_404(pool_id, what="Pool")
    mid = _uuid_or_404(member_id, what="Pool member")
    reason = (body or MemberRemoveIn()).reason
    try:
        out = await svc.remove_member(
            db, company_id=company_id, actor=actor, pool_id=pid, member_id=mid, reason=reason,
            meta=_meta(request),
        )
    except svc.PoolError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return MemberRemoveOut(**out)


@router.post("/{pool_id}/members/{member_id}/evidence-reviewed", response_model=MemberOut)
async def mark_evidence_reviewed(
    pool_id: str, member_id: str, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> MemberOut:
    actor, company_id = ctx
    pid = _uuid_or_404(pool_id, what="Pool")
    mid = _uuid_or_404(member_id, what="Pool member")
    try:
        out = await svc.mark_evidence_reviewed(
            db, company_id=company_id, actor=actor, pool_id=pid, member_id=mid,
            meta=_meta(request),
        )
    except svc.PoolError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return MemberOut(**out)


@router.post("/{pool_id}/members/{member_id}/invite", response_model=MemberInviteOut)
async def invite_member(
    pool_id: str, member_id: str, body: MemberInviteIn, request: Request,
    ctx: HrCtxDep, db: DbSessionDep,
) -> MemberInviteOut:
    actor, company_id = ctx
    pid = _uuid_or_404(pool_id, what="Pool")
    mid = _uuid_or_404(member_id, what="Pool member")
    rid = _uuid_or_404(body.requisition_id, what="Opening")
    try:
        out = await svc.invite_member(
            db, company_id=company_id, actor=actor, pool_id=pid, member_id=mid,
            requisition_id=rid, acknowledged_stale=body.acknowledged_stale,
            meta=_meta(request),
        )
    except svc.PoolError as exc:
        raise _fail(exc) from exc
    await db.commit()
    return MemberInviteOut(**out)
