"""Job simulations and portfolio rounds — PH4-D4.

Four routers, one per audience:

* ``hr_router`` (``/hr``, HR managers): the round's brief and items, its
  reference materials, the submissions for one application or one opening,
  re-issue and withdraw.
* ``iv_router`` (``/interviewer``, ``InterviewerCtxDep``): a reviewer reads
  the submission for a scorecard they own — through
  ``interviewer_scorecards``'s own ownership check, so anyone else's read is
  a 404.
* ``public_router`` (``/task``, no login): the candidate's own submission,
  reached ONLY with the ``X-Task-Token`` from their link. Every failure reads
  the same, and every route is rate-limited.
* ``me_router`` (``/users/me``): a signed-in candidate rotates their own
  link.

Nothing here advances, holds or scores anyone (see ``app/job_tasks.py``).
"""

from __future__ import annotations

import uuid
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

from app import job_tasks as svc
from app.config import settings
from app.database import DbSessionDep, get_db_session
from app.dependencies import HrCtxDep, InterviewerCtxDep, get_current_user
from app.interviewer_scorecards import RequestMeta, ScorecardError
from app.job_tasks import TaskError
from app.rate_limit import rate_limit_task
from app.utils.request_ip import extract_client_ip, extract_user_agent

hr_router = APIRouter(prefix="/hr", tags=["job-tasks"])
iv_router = APIRouter(prefix="/interviewer", tags=["job-tasks"])
public_router = APIRouter(prefix="/task", tags=["job-tasks-public"])
me_router = APIRouter(prefix="/users/me", tags=["job-tasks"])

CurrentUserDep = Annotated[User, Depends(get_current_user)]
CandidateDbDep = Annotated[AsyncSession, Depends(get_db_session)]


async def _task_token(
    token: Annotated[str | None, Header(alias="X-Task-Token")] = None,
) -> str | None:
    """The candidate's link credential — a header, never a URL part, so both
    Caddyfiles can redact it from access logs the way ``X-Offer-Token`` is."""
    return token


TaskTokenDep = Annotated[str | None, Depends(_task_token)]


def _meta(request: Request) -> RequestMeta:
    return RequestMeta(ip_address=extract_client_ip(request), user_agent=extract_user_agent(request))


async def _fail(db: AsyncSession, exc: TaskError | ScorecardError) -> HTTPException:
    await db.rollback()
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------
class TaskItemIn(BaseModel):
    key: str
    prompt: str
    response_type: str
    required: bool = True
    max_chars: int | None = None


class TaskConfigIn(BaseModel):
    brief: str = Field(max_length=20000)
    brief_translations: dict[str, str] | None = None
    items: list[TaskItemIn] = Field(default_factory=list)
    min_artifacts: int | None = None
    max_artifacts: int | None = None
    allow_files: bool = True
    allow_links: bool = True
    allowed_link_domains: list[str] | None = None

    def as_cfg(self) -> dict[str, Any]:
        return self.model_dump()


class ReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class SaveResponseIn(BaseModel):
    text_value: str | None = Field(default=None, max_length=20000)
    link_url: str | None = Field(default=None, max_length=2000)


class StartIn(BaseModel):
    """Consent moved here (H2, security review PH4-D4): nothing is ever
    autosaved or uploaded before ``start`` succeeds, so consent has to exist
    before it, not at submit."""

    consent: bool = False


class SubmitIn(BaseModel):
    # Accepted and ignored (H2) — kept only so the field the UI still sends
    # (publicTask.ts::submitTask, for compatibility) never becomes an
    # unrecognised-field error. Consent itself was taken at `start`.
    consent: bool = False


# ---------------------------------------------------------------------------
# HR — configuration and materials
# ---------------------------------------------------------------------------
@hr_router.get("/rounds/{round_id}/task")
async def get_round_task(round_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep) -> dict[str, Any] | None:
    _uid, company_id = ctx
    try:
        return await svc.get_config(db, company_id=company_id, round_id=round_id)
    except TaskError as exc:
        raise await _fail(db, exc) from exc


@hr_router.put("/rounds/{round_id}/task")
async def put_round_task(
    round_id: uuid.UUID, body: TaskConfigIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await svc.put_config(
            db, company_id=company_id, round_id=round_id, cfg=body.as_cfg(), actor=actor,
            meta=_meta(request),
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@hr_router.get("/rounds/{round_id}/task/materials")
async def list_round_task_materials(
    round_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep,
) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    try:
        return await svc.list_materials(db, company_id=company_id, round_id=round_id)
    except TaskError as exc:
        raise await _fail(db, exc) from exc


@hr_router.post("/rounds/{round_id}/task/materials", status_code=201)
async def add_round_task_material(
    round_id: uuid.UUID, request: Request, ctx: HrCtxDep, db: DbSessionDep,
    file: Annotated[UploadFile, File()], title: Annotated[str, Form()],
) -> dict[str, Any]:
    actor, company_id = ctx
    data = await file.read(settings.task_material_max_bytes + 1)
    try:
        out = await svc.add_material(
            db, company_id=company_id, round_id=round_id, title=title, data=data,
            filename=file.filename, actor=actor, meta=_meta(request),
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    key = out.pop("_storage_key", None)
    try:
        await db.commit()
    except Exception:
        if key:
            from app.document_storage import remove as _remove  # noqa: PLC0415

            await _remove(settings, [key])
        raise
    return out


@hr_router.delete("/rounds/{round_id}/task/materials/{material_id}", status_code=204)
async def remove_round_task_material(
    round_id: uuid.UUID, material_id: uuid.UUID, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> Response:
    actor, company_id = ctx
    try:
        await svc.remove_material(
            db, company_id=company_id, round_id=round_id, material_id=material_id, actor=actor,
            meta=_meta(request),
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return Response(status_code=204)


@hr_router.get("/rounds/{round_id}/task/materials/{material_id}/download")
async def download_round_task_material(
    round_id: uuid.UUID, material_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    _uid, company_id = ctx
    try:
        return await svc.material_download(
            db, company_id=company_id, round_id=round_id, material_id=material_id,
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc


# ---------------------------------------------------------------------------
# HR — submissions
# ---------------------------------------------------------------------------
@hr_router.get("/enrolments/{enrolment_id}/tasks")
async def list_enrolment_tasks(
    enrolment_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep,
) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await svc.for_enrolment(db, company_id=company_id, enrolment_id=enrolment_id)


@hr_router.get("/requisitions/{requisition_id}/task-submissions")
async def list_requisition_task_submissions(
    requisition_id: uuid.UUID, ctx: HrCtxDep, db: DbSessionDep,
) -> list[dict[str, Any]]:
    _uid, company_id = ctx
    return await svc.for_requisition(db, company_id=company_id, requisition_id=requisition_id)


@hr_router.get("/task-submissions/{submission_id}/artifacts/{response_id}/download")
async def download_task_artifact(
    submission_id: uuid.UUID, response_id: uuid.UUID, request: Request, ctx: HrCtxDep,
    db: DbSessionDep,
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await svc.artifact_download(
            db, company_id=company_id, submission_id=submission_id, response_id=response_id,
            actor=uid, actor_type="user", meta=_meta(request),
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@hr_router.post("/task-submissions/{submission_id}/reissue")
async def reissue_task_submission(
    submission_id: uuid.UUID, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await svc.reissue(
            db, company_id=company_id, submission_id=submission_id, actor=actor, meta=_meta(request),
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@hr_router.post("/task-submissions/{submission_id}/withdraw")
async def withdraw_task_submission(
    submission_id: uuid.UUID, body: ReasonIn, request: Request, ctx: HrCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    actor, company_id = ctx
    try:
        out = await svc.withdraw(
            db, company_id=company_id, submission_id=submission_id, reason=body.reason, actor=actor,
            meta=_meta(request),
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


# ---------------------------------------------------------------------------
# Interviewer — through the existing scorecard ownership check
# ---------------------------------------------------------------------------
@iv_router.get("/scorecards/{scorecard_id}/submission")
async def get_scorecard_submission(
    scorecard_id: uuid.UUID, ctx: InterviewerCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await svc.submission_for_reviewer(
            db, company_id=company_id, scorecard_id=scorecard_id, interviewer_user_id=uid,
        )
    except (TaskError, ScorecardError) as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@iv_router.get("/scorecards/{scorecard_id}/submission/artifacts/{response_id}/download")
async def download_scorecard_artifact(
    scorecard_id: uuid.UUID, response_id: uuid.UUID, request: Request, ctx: InterviewerCtxDep,
    db: DbSessionDep,
) -> dict[str, Any]:
    uid, company_id = ctx
    try:
        out = await svc.reviewer_artifact_download(
            db, company_id=company_id, scorecard_id=scorecard_id, interviewer_user_id=uid,
            response_id=response_id, meta=_meta(request),
        )
    except (TaskError, ScorecardError) as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@iv_router.get("/scorecards/{scorecard_id}/submission/materials/{material_id}/download")
async def download_scorecard_material(
    scorecard_id: uuid.UUID, material_id: uuid.UUID, ctx: InterviewerCtxDep, db: DbSessionDep,
) -> dict[str, Any]:
    """Gap 3: a reviewer previously had no way at all to read a round's
    reference materials."""
    uid, company_id = ctx
    try:
        out = await svc.reviewer_material_download(
            db, company_id=company_id, scorecard_id=scorecard_id, interviewer_user_id=uid,
            material_id=material_id,
        )
    except (TaskError, ScorecardError) as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


# ---------------------------------------------------------------------------
# The candidate, by link
#
# M3: each route below has its OWN bucket name (previously four of them
# shared "task_answer" with four different caps, so autosaves could exhaust
# the budget a submit needed) and is keyed on the candidate's own link, not
# client IP, with a looser per-IP ceiling behind it — see
# app.rate_limit.rate_limit_task's docstring for why (a college computer lab,
# the primary market, puts many candidates behind one NAT address).
# ---------------------------------------------------------------------------
@public_router.get("", dependencies=[rate_limit_task("task_view", per_token=30, per_ip=300)])
async def view_task(token: TaskTokenDep, request: Request, db: CandidateDbDep) -> dict[str, Any]:
    try:
        out = await svc.open_submission(db, raw=token, meta=_meta(request))
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.post("/start", dependencies=[rate_limit_task("task_start", per_token=20, per_ip=200)])
async def start_task(
    body: StartIn, token: TaskTokenDep, request: Request, db: CandidateDbDep,
) -> dict[str, Any]:
    try:
        out = await svc.start(db, raw=token, consent=body.consent, meta=_meta(request))
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.post(
    "/consent/withdraw", dependencies=[rate_limit_task("task_consent_withdraw", per_token=10, per_ip=100)],
)
async def withdraw_task_consent(
    token: TaskTokenDep, request: Request, db: CandidateDbDep,
) -> dict[str, Any]:
    """Withdraw consent to send this task's work to the hiring team (DPDP
    §11) — the preboarding-documents precedent
    (``POST /offer/documents/consent/withdraw``). Nothing already stored is
    deleted; save, upload and submit all refuse afterwards."""
    try:
        out = await svc.withdraw_task_consent(db, raw=token, meta=_meta(request))
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.put(
    "/responses/{item_key}", dependencies=[rate_limit_task("task_save", per_token=60, per_ip=600)],
)
async def save_task_response(
    item_key: str, body: SaveResponseIn, token: TaskTokenDep, request: Request, db: CandidateDbDep,
) -> dict[str, Any]:
    try:
        out = await svc.save_response(
            db, raw=token, item_key=item_key, text_value=body.text_value, link_url=body.link_url,
            meta=_meta(request),
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.post(
    "/artifacts", status_code=201,
    dependencies=[rate_limit_task("task_upload", per_token=20, per_ip=200)],
)
async def add_task_artifact(
    token: TaskTokenDep, request: Request, db: CandidateDbDep,
    file: Annotated[UploadFile | None, File()] = None,
    link_url: Annotated[str | None, Form()] = None,
    link_kind: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
    item_key: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    data: bytes | None = None
    if file is not None:
        data = await file.read(settings.task_response_max_bytes + 1)
    kind = "file" if file is not None else "link"
    try:
        out = await svc.add_artifact(
            db, raw=token, kind=kind, data=data, filename=file.filename if file else None,
            link_url=link_url, link_kind=link_kind, title=title, description=description,
            item_key=item_key, meta=_meta(request),
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    key = out.pop("_storage_key", None)
    try:
        await db.commit()
    except Exception:
        if key:
            from app.document_storage import remove as _remove  # noqa: PLC0415

            await _remove(settings, [key])
        raise
    return out


@public_router.delete(
    "/artifacts/{response_id}", status_code=204,
    dependencies=[rate_limit_task("task_delete", per_token=30, per_ip=300)],
)
async def remove_task_artifact(
    response_id: uuid.UUID, token: TaskTokenDep, request: Request, db: CandidateDbDep,
) -> Response:
    try:
        key = await svc.remove_artifact(db, raw=token, response_id=response_id, meta=_meta(request))
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    if key:
        from app.document_storage import remove as _remove  # noqa: PLC0415

        await _remove(settings, [key])
    return Response(status_code=204)


@public_router.post("/submit", dependencies=[rate_limit_task("task_submit", per_token=10, per_ip=100)])
async def submit_task(
    body: SubmitIn, token: TaskTokenDep, request: Request, db: CandidateDbDep,
) -> dict[str, Any]:
    try:
        out = await svc.submit(db, raw=token, consent=body.consent, meta=_meta(request))
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out


@public_router.get(
    "/materials/{material_id}/download",
    dependencies=[rate_limit_task("task_material_download", per_token=30, per_ip=300)],
)
async def download_task_material(
    material_id: uuid.UUID, token: TaskTokenDep, db: CandidateDbDep,
) -> dict[str, Any]:
    try:
        sub = await svc.by_token(db, token)
        if svc.materials_withheld(sub):
            # M4 (before a timed task starts) / security review wave 5 item 2
            # (after submission): the same NOT_AVAILABLE-shaped refusal a bad
            # token gets, so this is not an oracle for "is this timed" either.
            raise TaskError(404, svc.NOT_AVAILABLE)
        out = await svc.material_download(
            db, company_id=sub["company_id"], round_id=sub["round_id"], material_id=material_id,
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.rollback()
    return out


# ---------------------------------------------------------------------------
# The candidate, signed in
# ---------------------------------------------------------------------------
@me_router.post("/tasks/{submission_id}/link")
async def my_task_link(
    submission_id: uuid.UUID, request: Request, user: CurrentUserDep, db: CandidateDbDep,
) -> dict[str, Any]:
    try:
        out = await svc.candidate_link(
            db, user_id=uuid.UUID(user.user_id), submission_id=submission_id, meta=_meta(request),
        )
    except TaskError as exc:
        raise await _fail(db, exc) from exc
    await db.commit()
    return out
