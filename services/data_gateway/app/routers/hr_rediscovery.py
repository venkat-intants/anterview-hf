"""HR rediscovery routes — PH5-E3. ``hr_manager`` ONLY.

  POST /hr/rediscovery/search    rank this company's opted-in candidates
  GET  /hr/rediscovery/universe  how many can be searched at all

A NARROWER ROUTER THAN ``hr_corpus.py``, AND THAT IS THE POINT. A result names
a candidate, so this is ``candidate_pii``, and
``shared/agents/schema.py::DATA_CLASS_ROLES`` gives that class to
``{hr_manager}`` alone. A company ``super_admin`` is deliberately NOT a
superset of HR (``CLAUDE.md``) — it runs hiring operations and has no route to
a named candidate — so it gets 403 here even though it manages the company and
shares every route in the document library. ``HrCtxDep`` is the whole gate,
exactly like every other ``/hr`` router.

WHY SEARCH IS A POST. Not the body size: HR will type candidate names into that
box, a query string in a URL is recorded by every proxy and access log in the
path, and the audit row this route writes deliberately carries only the query's
LENGTH. A GET would undo that decision in infrastructure this service does not
control.

NOTHING HERE CAN CHANGE A HIRING OUTCOME. Both routes are reads. The only row
either writes is the audit row for the read itself. There is no path from this
router to a status, an enrolment stage, a round result or a scorecard — the
actions a result offers (add to pool, invite) live in the pools router, behind
their own checks.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, HTTPException, Request

from app import rediscovery as svc
from app.config import settings
from app.database import DbSessionDep
from app.dependencies import HrCtxDep, get_hr_company
from app.rate_limit import rate_limit_context
from app.schemas.rediscovery import (
    RediscoverySearchIn,
    RediscoverySearchOut,
    Universe,
    UniverseOut,
    as_search_out,
)
from app.utils.request_ip import extract_client_ip, extract_user_agent

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/hr/rediscovery", tags=["rediscovery"])

# Per COMPANY, not per IP: one search costs one query embedding, and the seats
# of one tenant share that cost. The rate_limit_context precedent
# (app/routers/hr_corpus.py).
_SEARCH_RATE_LIMIT = [
    rate_limit_context(
        "rediscovery_search", settings.rediscovery_search_per_minute, get_hr_company,
    )
]


def _fail(exc: svc.RediscoveryError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code, detail={"failure_code": exc.code, "message": exc.message}
    )


@router.get("/universe", response_model=UniverseOut)
async def get_universe(ctx: HrCtxDep, db: DbSessionDep) -> UniverseOut:
    """How many of this company's applicants a rediscovery search can see.

    The screen's empty state and its always-on counter are built from this, and
    both are load-bearing: on day one the answer is zero, and for candidates who
    applied before this feature and never activated an account it stays zero.
    A search that silently returned nothing would read as a broken feature
    rather than as an opt-in one.
    """
    _uid, company_id = ctx
    counts = await svc.universe_counts(db, company_id=company_id)
    return UniverseOut(
        universe=Universe(**counts),
        consent_months=int(settings.rediscovery_consent_months),
    )


@router.post(
    "/search",
    response_model=RediscoverySearchOut,
    response_model_exclude_none=True,
    dependencies=_SEARCH_RATE_LIMIT,
)
async def search_rediscovery(
    body: RediscoverySearchIn,
    ctx: HrCtxDep,
    db: DbSessionDep,
    request: Request,
) -> RediscoverySearchOut:
    """Rank this company's opted-in candidates against a query.

    Every result carries its own ``breakdown`` and a ``why`` array in which
    each contribution is either explained with a citation or LABELLED as
    unexplainable. A row whose only contribution is CV similarity comes back
    with ``explained: false`` and ``unexplained_note`` set, sorts below
    explained rows at equal score, and is flagged ``requires_review`` — there is
    nothing there to review, which is exactly why it cannot be invited without
    an acknowledgement.

    ``semantic: false`` means the embedding service was unreachable and these
    results came from full text alone.

    404 (never 403) if ``requisition_id`` is not this company's opening.
    """
    hr_uid, company_id = ctx
    requisition_id: uuid.UUID | None = None
    if body.requisition_id:
        try:
            requisition_id = uuid.UUID(body.requisition_id)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"failure_code": "bad_requisition_id",
                        "message": "That opening id is not valid."},
            ) from exc
    try:
        payload = await svc.search(
            db, company_id=company_id, hr_user_id=hr_uid, query=body.query,
            requisition_id=requisition_id, limit=body.limit,
        )
    except svc.RediscoveryError as exc:
        raise _fail(exc) from exc

    # Facts only: the query's LENGTH, never the query. See record_search_audit.
    svc.record_search_audit(
        db, actor_id=hr_uid, company_id=company_id, payload=payload,
        query_length=len(body.query), ip_address=extract_client_ip(request),
        user_agent=extract_user_agent(request),
    )
    await db.commit()
    log.info(
        "rediscovery.searched", company_id=str(company_id), query_length=len(body.query),
        results=payload["returned"], eligible=payload["universe"]["eligible"],
        semantic=payload["semantic"],
    )
    return as_search_out(payload)
