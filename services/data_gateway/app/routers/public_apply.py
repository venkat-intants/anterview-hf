"""Public job applications — Group E, E4. NO LOGIN.

Everything Phase 2 built assumed candidates were already in the system, and the
only way they got there was an HR manager uploading a resume. This is the front
door: a candidate opens a link, uploads a CV, and lands in the opening's funnel
with an enrolment, ready for the workflow to pick them up.

    GET  /apply/{requisition_id}   the posting — title, level, JD
    POST /apply/{requisition_id}   apply, with a resume and explicit consent

WHY AN OPT-IN FLAG. The route is addressed by requisition id. A UUID is
unguessable, but it is not a secret — it turns up in forwarded emails, browser
histories and screenshots — so it must not by itself open an application
channel. ``job_requisitions.public_apply_enabled`` defaults to false and HR
turns it on per opening. An opening that is closed, paused, or simply never
published to the web answers exactly as one that does not exist.

CONSENT IS NOT OPTIONAL. This endpoint stores a person's name, email and
resume, which is precisely what CLAUDE.md forbids without a
``dpdp_consent_ledger`` entry. The applicant ticks a box; the request is
refused without it; and the ledger entry is written in the SAME transaction as
the applicant row, so there is no window in which the PII exists un-consented.
The ledger needs a user row (its FK is NOT NULL), so a ``guest_candidate`` user
is minted for the applicant here — the same lazy provisioning
``interview_take`` already does when a candidate redeems an invite.

NOTHING IS SCORED IN THIS REQUEST. The resume is stored and flagged
``pending_enrichment``; Group A's reconciler scores, embeds and names it within
the minute. A candidate pressing Submit should not wait on a language model,
and an applicant lost because the model was down would be the worst possible
failure for this particular endpoint.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.application_questions import (
    AnswerError,
    list_questions,
    store_answers,
    validate_answers,
)
from app.apply_activation import (
    ActivationError,
    activate,
    activation_target,
    stage_activation_email,
)
from app.config import settings
from app.database import DbSessionDep
from app.local_storage import LocalStorageError
from app.models import Applicant
from app.rate_limit import rate_limit
from app.routers.consent import _hash_value
from app.routers.resume import _delete_from_s3, _extract_pdf_text, _upload_to_s3
from app.utils.request_ip import extract_client_ip, extract_user_agent
from app.workflow_runner import enrol_applicant

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/apply", tags=["public-apply"])

_MAX_RESUME_BYTES = 5 * 1024 * 1024  # 5 MB — same ceiling as the HR upload path.

# One uniform refusal. Closed, paused, not-public, deleted, another tenant's,
# or simply not a real id all answer identically: an opening that is not taking
# applications must be indistinguishable from one that does not exist, or the
# 404-vs-403 split becomes an enumeration oracle for a company's private roles.
_NOT_AVAILABLE = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="This opening is not accepting applications.",
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class PostingQuestion(BaseModel):
    """A question as the candidate sees it.

    No ``position`` — the list is already in order, and a client that sorts by
    a field it was given is a client that can sort by it wrongly. No
    ``answer_count`` either: how many other people have applied is the
    company's information, and it leaks out of a question count as readily as
    out of an applicant count.
    """

    id: str
    prompt: str
    kind: str
    help_text: str | None = None
    required: bool
    options: list[str] = Field(default_factory=list)


class PostingOut(BaseModel):
    """What a candidate sees before applying. Deliberately thin.

    No counts, no funnel, no hiring-team names: this is served to anyone with
    the link, and "37 people have applied" is the company's information, not
    the applicant's.
    """

    requisition_id: str
    title: str
    level: str
    company_name: str
    jd_text: str | None
    closes_at: str | None
    # The advert. Everything here is candidate-facing by definition — it is
    # what HR wrote in order to be read by strangers.
    department: str | None = None
    location: str | None = None
    employment_type: str | None = None
    experience_min_years: int | None = None
    experience_max_years: int | None = None
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    nice_to_have_skills: list[str] = Field(default_factory=list)
    # Salary is the one field that is NOT candidate-facing by default. A band
    # can be recorded for internal planning and never advertised, so the flag
    # gates it and the fields are omitted entirely rather than sent as null —
    # "not disclosed" and "we forgot to fill it in" should not be the same
    # payload to anyone reading the API.
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    # What this opening asks, in the order HR put them in. Empty for most
    # openings — the form renders the step only when there is something on it.
    questions: list[PostingQuestion] = Field(default_factory=list)


class ApplicationOut(BaseModel):
    applicant_id: str
    enrolment_id: str | None
    full_name: str
    # True when this email had already applied to this opening. Reported rather
    # than treated as an error: re-submitting is a normal thing an anxious
    # candidate does, and a red failure would suggest their first attempt was
    # lost.
    already_applied: bool
    message: str


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------
def _clean(value: str | None, limit: int) -> str | None:
    """Trim, collapse whitespace, and treat an empty field as unanswered.

    A browser submits an untouched optional input as "", and storing that would
    make "left blank" indistinguishable from "answered with nothing" — which
    matters on a returning application, where the update below must not wipe a
    detail the person gave last time by not repeating it.
    """
    if value is None:
        return None
    cleaned = " ".join(value.split())[:limit]
    return cleaned or None


async def _open_posting(db: DbSessionDep, requisition_id: uuid.UUID) -> dict[str, Any]:
    """The opening if it is genuinely taking applications, else 404."""
    row = (
        await db.execute(
            text(
                "SELECT r.id, r.company_id, r.title, r.level, r.jd_text, r.closes_at,"
                "       r.owner_user_id, r.created_by_user_id, c.name AS company_name,"
                "       r.department, r.location, r.employment_type,"
                "       r.experience_min_years, r.experience_max_years,"
                "       r.responsibilities, r.required_skills, r.nice_to_have_skills,"
                "       r.salary_min, r.salary_max, r.salary_currency, r.salary_visible"
                "  FROM job_requisitions r"
                "  JOIN companies c ON c.id = r.company_id AND c.is_active"
                " WHERE r.id = :i"
                "   AND r.deleted_at IS NULL"
                "   AND r.status = 'open'"
                "   AND r.public_apply_enabled"
            ),
            {"i": requisition_id},
        )
    ).mappings().first()
    if row is None:
        raise _NOT_AVAILABLE
    # A closing date that has passed stops applications without HR having to
    # remember to flip the status.
    closes_at = row["closes_at"]
    if closes_at is not None and closes_at <= datetime.now(tz=UTC):
        raise _NOT_AVAILABLE
    return dict(row)


# ---------------------------------------------------------------------------
# Routes
#
# ORDER MATTERS HERE. FastAPI matches routes in declaration order, and
# ``/apply/{requisition_id}`` happily matches the literal string
# "activate" — which it then fails to parse as a UUID, so an activation
# POST came back as a 422 complaining about requisition_id. The fixed-path
# routes are therefore declared FIRST. Anything else added under /apply
# with a literal first segment must go above the parameterised pair too.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Account activation — the applicant claims the row their application created
# ---------------------------------------------------------------------------
# Both routes are public and authenticated only by the emailed token, so both
# are rate-limited: the token is 256 bits of opaque randomness, but an
# unthrottled endpoint that reports "valid" or "invalid" is still a free
# oracle, and these sit on the open internet next to the apply form.
class ActivationTargetOut(BaseModel):
    """Who the link belongs to, so the page can greet them before they type.

    The address is echoed because the person needs to confirm it is the one
    they applied with — they may have several. Nothing else about the
    application is included: this is served to whoever holds the link.
    """

    full_name: str
    email: str
    job_title: str
    company_name: str


class ActivateIn(BaseModel):
    token: str = Field(min_length=16, max_length=256)
    # Same floor as registration. Enforced here rather than left to the
    # frontend: this endpoint creates a credential, and a client is not where a
    # password policy can live.
    new_password: str = Field(min_length=8, max_length=128)


class ActivateOut(BaseModel):
    email: str
    # "existing" when the address already had an account and this application
    # was attached to it, "new" when the applicant's own row became the
    # account. The page says different things in the two cases — "sign in with
    # your existing password" versus "you are all set".
    linked: str
    message: str


@router.get(
    "/activate/target",
    response_model=ActivationTargetOut,
    summary="Whose account an activation link belongs to",
    dependencies=[rate_limit("apply_activate", settings.rate_limit_login_per_minute)],
)
async def read_activation_target(
    db: DbSessionDep, token: Annotated[str, Query(min_length=16, max_length=256)]
) -> ActivationTargetOut:
    """Check a link and report who it is for, without consuming it.

    Separate from the POST so an expired link can be reported on arrival rather
    than after someone has chosen a password.
    """
    try:
        target = await activation_target(db, token)
    except ActivationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return ActivationTargetOut(**target)


@router.post(
    "/activate",
    response_model=ActivateOut,
    summary="Set a password and claim the account an application created",
    dependencies=[rate_limit("apply_activate", settings.rate_limit_login_per_minute)],
)
async def activate_account(body: ActivateIn, db: DbSessionDep) -> ActivateOut:
    """Consume the link and give the applicant an account they can sign in to.

    Idempotent only in the sense that matters: the token is single-use, so a
    double-submitted form gets the same "invalid or expired" answer as a stale
    link rather than a second account.
    """
    try:
        result = await activate(db, raw_token=body.token, new_password=body.new_password)
        await db.commit()
    except ActivationError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        log.exception("apply.activation.failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We could not set up your account. Please try again.",
        ) from exc

    if result["linked"] == "existing":
        message = (
            "This email already had an account, so we have added the application "
            "to it. Sign in with your existing password."
        )
    else:
        message = "Your account is ready. Sign in to track your application."
    return ActivateOut(email=result["email"], linked=result["linked"], message=message)


@router.get(
    "/{requisition_id}",
    response_model=PostingOut,
    dependencies=[rate_limit("public_apply_view", 60)],
)
async def get_posting(requisition_id: uuid.UUID, db: DbSessionDep) -> PostingOut:
    """The public posting. Never reveals anything about who else applied."""
    req = await _open_posting(db, requisition_id)
    shows_salary = bool(req.get("salary_visible"))
    questions = await list_questions(db, requisition_id=requisition_id)
    return PostingOut(
        requisition_id=str(req["id"]),
        title=req["title"],
        level=req["level"],
        company_name=req["company_name"],
        jd_text=req["jd_text"],
        closes_at=req["closes_at"].isoformat() if req["closes_at"] else None,
        department=req.get("department"),
        location=req.get("location"),
        employment_type=req.get("employment_type"),
        experience_min_years=req.get("experience_min_years"),
        experience_max_years=req.get("experience_max_years"),
        responsibilities=req.get("responsibilities") or [],
        required_skills=req.get("required_skills") or [],
        nice_to_have_skills=req.get("nice_to_have_skills") or [],
        salary_min=req.get("salary_min") if shows_salary else None,
        salary_max=req.get("salary_max") if shows_salary else None,
        salary_currency=req.get("salary_currency") if shows_salary else None,
        questions=[
            PostingQuestion(
                id=str(q["id"]),
                prompt=q["prompt"],
                kind=q["kind"],
                help_text=q["help_text"],
                required=q["required"],
                options=list(q["options"] or []),
            )
            for q in questions
        ],
    )


@router.post(
    "/{requisition_id}",
    response_model=ApplicationOut,
    status_code=status.HTTP_201_CREATED,
    # Tighter than the read: this one writes a row and uploads a file. Six a
    # minute is generous for a person filling in a form and useless for a
    # script trying to fill a funnel with noise.
    dependencies=[rate_limit("public_apply_submit", 6)],
)
async def submit_application(
    requisition_id: uuid.UUID,
    request: Request,
    db: DbSessionDep,
    resume: UploadFile,
    full_name: Annotated[str, Form(min_length=2, max_length=200)],
    email: Annotated[EmailStr, Form()],
    # Not a default of True, and not inferred from the request reaching us.
    # DPDP consent has to be an act the person took.
    consent_granted: Annotated[bool, Form()] = False,
    # The rest of the multi-step form. Every one optional, and that is not
    # laziness — a candidate who abandons the application at step two has told
    # us nothing, and a required field here would turn "I would rather not say
    # what I earn now" into "you may not apply". The opening decides what it
    # actually needs; this endpoint decides what it will accept.
    phone: Annotated[str | None, Form(max_length=40)] = None,
    years_experience: Annotated[int | None, Form(ge=0, le=60)] = None,
    current_company: Annotated[str | None, Form(max_length=200)] = None,
    current_title: Annotated[str | None, Form(max_length=200)] = None,
    linkedin_url: Annotated[str | None, Form(max_length=500)] = None,
    github_url: Annotated[str | None, Form(max_length=500)] = None,
    # A JSON object keyed by question id. JSON inside a form field rather than
    # a nested body because the request is multipart — it carries a file — and
    # multipart has no way to express a nested object.
    answers: Annotated[str | None, Form(max_length=20_000)] = None,
) -> ApplicationOut:
    """Apply to an opening. Stores name, email and resume — with consent.

    Idempotent per (opening, email): a second submission returns the first
    application rather than creating a duplicate. That is not only politeness
    to a candidate who double-clicked — one enrolment per opening is a Group B
    database invariant, and a duplicate applicant row would fragment one
    person's assessment history across two records.
    """
    req = await _open_posting(db, requisition_id)
    company_id = req["company_id"]

    if not consent_granted:
        # 422, not 403: nothing is wrong with the caller's authority, the form
        # is incomplete. The frontend renders this next to the checkbox.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "We need your permission to store your CV and contact details "
                "before we can accept an application."
            ),
        )

    name = full_name.strip()[:200]
    address = str(email).strip().lower()[:320]

    # ── Already applied? ────────────────────────────────────────────────────
    # Checked before reading the upload, so a repeat submission costs nothing.
    existing = (
        await db.execute(
            text(
                "SELECT a.id, a.full_name, a.resume_s3_key, e.id AS enrolment_id"
                "  FROM applicants a"
                "  LEFT JOIN enrolments e ON e.applicant_id = a.id"
                "   AND e.requisition_id = :r AND e.deleted_at IS NULL"
                " WHERE a.company_id = :c AND a.deleted_at IS NULL"
                "   AND lower(btrim(a.email)) = :em"
                " ORDER BY a.created_at LIMIT 1"
            ),
            {"c": company_id, "r": requisition_id, "em": address},
        )
    ).mappings().first()

    if existing is not None and existing["enrolment_id"] is not None:
        return ApplicationOut(
            applicant_id=str(existing["id"]),
            enrolment_id=str(existing["enrolment_id"]),
            full_name=existing["full_name"],
            already_applied=True,
            message="You have already applied for this role. We have your application.",
        )

    # ── The opening's own questions ─────────────────────────────────────────
    # Validated here, before the CV is read or anything is stored. A required
    # answer that is missing must refuse the application in the same breath as
    # a missing consent — after the upload it would mean deleting a file we had
    # just written, and a partial application nobody asked for.
    questions = await list_questions(db, requisition_id=requisition_id)
    try:
        submitted = json.loads(answers) if answers else {}
        if not isinstance(submitted, dict):
            raise ValueError("answers must be an object keyed by question id")
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=422, detail="Could not read your answers. Please try again."
        ) from exc
    try:
        checked_answers = validate_answers(questions, submitted)
    except AnswerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # ── Read the resume ─────────────────────────────────────────────────────
    if resume.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Please upload your CV as a PDF.")
    raw = await resume.read()
    if not raw:
        raise HTTPException(status_code=400, detail="That file was empty.")
    if len(raw) > _MAX_RESUME_BYTES:
        raise HTTPException(status_code=413, detail="Your CV must be under 5 MB.")
    try:
        resume_text = await _extract_pdf_text(raw)
    except Exception as exc:  # noqa: BLE001 — encrypted or image-only PDF
        raise HTTPException(
            status_code=422,
            detail=(
                "We could not read that PDF. If it is a scan, please upload a "
                "text-based version."
            ),
        ) from exc

    # ── Store ───────────────────────────────────────────────────────────────
    # An applicant already exists for this email (they applied to a DIFFERENT
    # opening) — reuse the person and add an enrolment. D-06: one applicant per
    # company, many enrolments.
    applicant_id = uuid.UUID(str(existing["id"])) if existing is not None else uuid.uuid4()
    is_new_person = existing is None

    # A returning candidate's new CV gets a key of its own. It used to be
    # written over the old one, and the old one is what their earlier
    # application was scored against (enrolments.scored_resume_s3_key) — so
    # that score silently stopped being reproducible (D-06a). The previous
    # object is removed after the commit if no application points at it.
    previous_key: str | None = None if is_new_person else existing["resume_s3_key"]
    s3_key = (
        f"applicants/{company_id}/{applicant_id}.pdf" if is_new_person
        else f"applicants/{company_id}/{applicant_id}-{uuid.uuid4().hex[:12]}.pdf"
    )
    try:
        await _upload_to_s3(raw, s3_key)
    except (BotoCoreError, ClientError, LocalStorageError) as exc:
        # The candidate is told something they can act on ("try again"); the
        # operator is told what to actually fix. On a laptop this is almost
        # always "no bucket, no fallback", which is a config problem the
        # candidate-facing message must not try to explain.
        log.warning(
            "public.apply.storage_failed",
            error_type=type(exc).__name__,
            hint=(
                "no object storage configured — set S3_* for a real bucket, or "
                "STORAGE_LOCAL_DIR to write to disk in development"
                if not settings.s3_access_key_id and not settings.storage_local_dir
                else None
            ),
        )
        raise HTTPException(
            status_code=503, detail="We could not store your CV just now. Please try again."
        ) from exc

    now = datetime.now(tz=UTC)
    # Attributed to whoever owns the opening so the reconciler's later scoring
    # call names a real person in its audit trail rather than a synthetic
    # principal with no company scope.
    actor = req["owner_user_id"] or req["created_by_user_id"]

    try:
        if is_new_person:
            db.add(
                Applicant(
                    id=applicant_id,
                    company_id=company_id,
                    created_by_user_id=actor,
                    full_name=name,
                    email=address,
                    target_job_title=req["title"],
                    target_level=req["level"],
                    target_jd_text=req["jd_text"],
                    resume_text=resume_text,
                    resume_s3_key=s3_key,
                    status="new",
                    # The candidate typed this name, so it is authoritative and
                    # the reconciler may not replace it.
                    #
                    # This used to say the opposite — that overwriting with the
                    # PDF's name was "the right way round, because the CV is the
                    # document the employer will read". That reasoning holds for
                    # HR's bulk upload, where the name is derived from a
                    # filename. It does not hold here: this form asks a person
                    # for their name, and silently replacing their answer with a
                    # parser's guess is wrong even when the guess is better.
                    # The parsed name is kept in parsed_full_name so it can be
                    # offered back for confirmation instead.
                    full_name_source="candidate",
                    phone=_clean(phone, 40),
                    years_experience=years_experience,
                    current_company=_clean(current_company, 200),
                    current_title=_clean(current_title, 200),
                    linkedin_url=_clean(linkedin_url, 500),
                    github_url=_clean(github_url, 500),
                    # The scorer has not seen this resume yet.
                    pending_enrichment=True,
                    created_at=now,
                    updated_at=now,
                )
            )
            # Flushed before the guest user is provisioned, because that step
            # reads and updates this row through raw SQL. Without the flush the
            # INSERT is still pending, the SELECT finds nothing, the UPDATE
            # matches nothing — and the applicant ends up with user_id NULL
            # while an orphaned guest and a consent row it can never be linked
            # to are written anyway. Every application then minted another
            # pair, which is how the consent ledger ends up with duplicates
            # nobody can trace back to a person.
            await db.flush()
        else:
            # Returning applicant: refresh the CV on file, and queue a re-score
            # against THIS opening's title rather than the one they applied to
            # before — the same resume scores differently for a different role.
            #
            # The detail fields use COALESCE(:new, existing) rather than
            # overwriting: this form is optional past the first step, so a
            # returning candidate who skips "current company" has not told us
            # they left their job. Silently blanking what they gave us last
            # time would be the same class of mistake as replacing a typed name
            # with a parsed one.
            #
            # full_name is deliberately absent. They typed it the first time
            # and the source is already 'candidate'; a second application to
            # another opening should not rename the person.
            await db.execute(
                text(
                    "UPDATE applicants SET resume_text = :rt, resume_s3_key = :k,"
                    " target_job_title = :ti, target_level = :lv, target_jd_text = :jd,"
                    " phone = COALESCE(:ph, phone),"
                    " years_experience = COALESCE(:yx, years_experience),"
                    " current_company = COALESCE(:cc, current_company),"
                    " current_title = COALESCE(:ct, current_title),"
                    " linkedin_url = COALESCE(:li, linkedin_url),"
                    " github_url = COALESCE(:gh, github_url),"
                    " ats_overall = NULL, ats_breakdown = NULL, ats_strengths = NULL,"
                    " ats_concerns = NULL, ats_recommendation = NULL, ats_summary = NULL,"
                    " pending_enrichment = true, updated_at = :n"
                    " WHERE id = :i AND company_id = :c"
                ),
                {"rt": resume_text, "k": s3_key, "ti": req["title"], "lv": req["level"],
                 "jd": req["jd_text"], "n": now, "i": applicant_id, "c": company_id,
                 "ph": _clean(phone, 40), "yx": years_experience,
                 "cc": _clean(current_company, 200), "ct": _clean(current_title, 200),
                 "li": _clean(linkedin_url, 500), "gh": _clean(github_url, 500)},
            )

        guest_user_id = await _ensure_guest_user(
            db, applicant_id=applicant_id, company_id=company_id, name=name,
            email=address, resume_text=resume_text, now=now,
        )
        await _record_apply_consent(
            db, request=request, user_id=guest_user_id, applicant_id=applicant_id,
            company_id=company_id, requisition_id=requisition_id, now=now,
        )
        await db.flush()

        outcome = await enrol_applicant(
            db,
            company_id=company_id,
            applicant_id=applicant_id,
            requisition_id=requisition_id,
            target_job_title=req["title"],
            target_level=req["level"],
            target_jd_text=req["jd_text"],
        )
        # Same transaction as the enrolment they belong to: an application
        # whose answers did not land is not a complete application, and the
        # required ones were a condition of accepting it at all.
        if checked_answers and outcome.enrolment_id:
            await store_answers(
                db,
                company_id=company_id,
                enrolment_id=uuid.UUID(outcome.enrolment_id),
                answers=checked_answers,
            )

        await db.commit()
    except IntegrityError:
        # Two submissions racing. The partial unique index on
        # (requisition_id, applicant_id) is the arbiter; the loser reports
        # success, because from the candidate's side their application landed.
        await db.rollback()
        # The winning submission stored its own CV; this one's object has no
        # row. (For a new person it never did: the applicant insert rolled back.)
        await _delete_from_s3(s3_key)
        log.info("public.apply.race_lost", requisition_id=str(requisition_id))
        return ApplicationOut(
            applicant_id=str(applicant_id),
            enrolment_id=None,
            full_name=name,
            already_applied=True,
            message="You have already applied for this role. We have your application.",
        )
    except Exception as exc:  # noqa: BLE001 — the upload must not outlive the row
        await db.rollback()
        # Orphaned object otherwise: a CV in storage belonging to nobody is PII
        # with no consent record and no erasure path. A returning candidate's
        # upload has its own key now, so it is removed too; their previous CV
        # is untouched.
        await _delete_from_s3(s3_key)
        log.exception("public.apply.failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="We could not save your application. Please try again."
        ) from exc

    # The CV this one replaced, kept only while an application still points at
    # it as the CV it was scored against. Otherwise it is PII nothing refers to
    # — no erasure path would ever find it — so it goes. Best-effort, after the
    # commit: a failed delete leaves an unreferenced object, never a broken
    # application.
    if previous_key and previous_key != s3_key:
        try:
            still_used = await db.scalar(
                text("SELECT 1 FROM enrolments WHERE scored_resume_s3_key = :k LIMIT 1"),
                {"k": previous_key},
            )
            await db.rollback()
            if still_used is None:
                await _delete_from_s3(previous_key)
        except Exception:  # noqa: BLE001
            await db.rollback()
            log.warning("public.apply.previous_cv_cleanup_failed")

    # Confirmation email, with a link to activate the account this application
    # just created. AFTER the commit and in its own transaction, deliberately:
    # inside the application's transaction, a SQL-level failure while staging
    # the email would leave that transaction aborted and take the application
    # down with it — a caught exception is not enough, because every later
    # statement on an aborted transaction fails too. The application is already
    # safe by this point; what is at risk is only the email.
    try:
        await stage_activation_email(
            db,
            user_id=guest_user_id,
            applicant_email=address,
            applicant_name=name,
            job_title=req["title"],
            company_id=company_id,
            company_name=req.get("company_name"),
            now=now,
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — see above
        await db.rollback()
        log.warning(
            "public.apply.activation_email_failed",
            requisition_id=str(requisition_id),
            applicant_id=str(applicant_id),
        )

    log.info(
        "public.apply.received",
        company_id=str(company_id),
        requisition_id=str(requisition_id),
        applicant_id=str(applicant_id),
        returning=not is_new_person,
        # NEVER log the name, email or resume text.
    )
    return ApplicationOut(
        applicant_id=str(applicant_id),
        enrolment_id=outcome.enrolment_id,
        full_name=name,
        already_applied=False,
        message="Thanks — your application is in. We will be in touch by email.",
    )


# ---------------------------------------------------------------------------
# Guest provisioning + consent
# ---------------------------------------------------------------------------
async def _ensure_guest_user(
    db: DbSessionDep,
    *,
    applicant_id: uuid.UUID,
    company_id: uuid.UUID,
    name: str,
    email: str,
    resume_text: str,
    now: datetime,
) -> uuid.UUID:
    """The applicant's ``guest_candidate`` user row, created if absent.

    Exists because ``dpdp_consent_ledger.user_id`` is NOT NULL — consent has to
    hang off a user. The same lazy provisioning ``interview_take`` does on
    invite redemption, moved earlier: a candidate who applies through this door
    already has one by the time they are invited.

    The row carries no password and holds only the ``guest_candidate`` role,
    which every candidate and HR route rejects.
    """
    linked = await db.scalar(
        text("SELECT user_id FROM applicants WHERE id = :a"), {"a": applicant_id}
    )
    if linked is not None:
        return uuid.UUID(str(linked))

    guest_user_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO users (id, email, password_hash, full_name, company_id,"
            " resume_text, preferred_language, is_active, notify_login_email,"
            " must_change_password, created_at, updated_at)"
            " VALUES (:id, :em, NULL, :fn, :cid, :rt, 'en', true, false, false, :n, :n)"
        ),
        {"id": guest_user_id, "em": f"guest+{guest_user_id}@applicants.invalid",
         "fn": name, "cid": company_id, "rt": resume_text, "n": now},
    )
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id, assigned_at) VALUES "
            "(:uid, (SELECT id FROM roles WHERE name = 'guest_candidate'), :n)"
        ),
        {"uid": guest_user_id, "n": now},
    )
    await db.execute(
        text("UPDATE applicants SET user_id = :uid, updated_at = :n WHERE id = :a"),
        {"uid": guest_user_id, "a": applicant_id, "n": now},
    )
    return guest_user_id


async def _record_apply_consent(
    db: DbSessionDep,
    *,
    request: Request,
    user_id: uuid.UUID,
    applicant_id: uuid.UUID,
    company_id: uuid.UUID,
    requisition_id: uuid.UUID,
    now: datetime,
) -> None:
    """Write the DPDP ledger entry for storing this person's CV. Idempotent.

    In the same transaction as the applicant row, so the PII and its lawful
    basis are committed together or not at all — there is no moment at which
    the CV exists without the record of permission to hold it.

    ``evidence`` carries hashed request metadata and ids only. Never raw PII:
    the ledger is read during audits by people who have no business seeing the
    applicant's address.
    """
    already = await db.scalar(
        text(
            "SELECT 1 FROM dpdp_consent_ledger WHERE user_id = :uid"
            " AND consent_type = 'application_data' AND purpose = 'recruitment'"
            " AND granted = TRUE AND revoked_at IS NULL LIMIT 1"
        ),
        {"uid": user_id},
    )
    if already:
        return
    await db.execute(
        text(
            "INSERT INTO dpdp_consent_ledger"
            " (id, user_id, consent_type, granted, granted_at, purpose, evidence)"
            " VALUES (:id, :uid, 'application_data', true, :n, 'recruitment',"
            " CAST(:ev AS jsonb))"
        ),
        {
            "id": uuid.uuid4(),
            "uid": user_id,
            "n": now,
            "ev": json.dumps(
                {
                    "source": "public_apply_form",
                    "applicant_id": str(applicant_id),
                    "company_id": str(company_id),
                    "requisition_id": str(requisition_id),
                    # HASHED, not raw. dpdp_consent_ledger.evidence is read
                    # during audits by people with no business seeing an
                    # applicant's address, and the column's contract
                    # (models.DpdpConsent) says it never carries raw PII.
                    "ip_hash": _hash_value(extract_client_ip(request) or ""),
                    "ua_hash": _hash_value(extract_user_agent(request) or ""),
                    "consented_at_iso": now.isoformat(),
                }
            ),
        },
    )
