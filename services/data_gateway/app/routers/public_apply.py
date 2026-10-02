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
the applicant row.

ONE WINDOW IS NOT COVERED BY THAT, deliberately. The CV is uploaded before the
reapplication gate is consulted, so that the work this endpoint does cannot be
timed to learn whether an address has applied here before (see
``submit_application``). On a REFUSED submission no ledger row is ever written
— so for the length of the gate, the notice and the commit, an object sits in
storage with no consent record, and permanently if the delete that follows it
fails. One invariant traded against a disclosure channel. This paragraph used
to claim there was no such window; the trade is recorded in
docs/ACCEPTED-RISKS.md instead of being left here for a reader to find.

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

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import (
    APIRouter,
    Depends,
    Form,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app import application_drafts as draft_store
from app import rediscovery
from app.application_questions import (
    AnswerError,
    list_questions,
    store_answers,
    validate_answers,
)
from app.application_source import DIRECT, normalise_source
from app.apply_activation import (
    REAPPLY_TOKEN_KIND,
    ActivationError,
    activate,
    activation_target,
    redeem_reapply_token,
    stage_activation_email,
    stage_reapply_confirmation,
)
from app.auth_tokens import hash_token, mint_token
from app.config import settings
from app.database import DbSessionDep
from app.local_storage import LocalStorageError
from app.mailer import enqueue_email
from app.models import Applicant
from app.publishing import visible_sql
from app.rate_limit import rate_limit, rate_limit_window
from app.reapplication import CooldownVerdict, StageResult
from app.reapplication import clear_staged as reapplication_clear_staged
from app.reapplication import confirm as reapplication_confirm
from app.reapplication import gate as reapplication_gate
from app.reapplication import stage as reapplication_stage
from app.reapplication import staged_for_token as reapplication_staged_for_token
from app.resume_details import extract_contact_details
from app.routers.consent import _hash_value
from app.routers.resume import _delete_from_s3, _extract_pdf_text, _upload_to_s3
from app.utils.request_ip import extract_client_ip, extract_user_agent
from app.workflow_runner import enrol_applicant

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/apply", tags=["public-apply"])

_MAX_RESUME_BYTES = 5 * 1024 * 1024  # 5 MB — same ceiling as the HR upload path.

# How long the CV detail parser may take before we give up and show the
# candidate empty fields instead. Generous: the parser is linear and capped,
# so reaching this means something is wrong rather than merely large.
_PARSE_TIMEOUT_SECONDS = 5.0

# One uniform refusal. Closed, paused, not-public, deleted, another tenant's,
# or simply not a real id all answer identically: an opening that is not taking
# applications must be indistinguishable from one that does not exist, or the
# 404-vs-403 split becomes an enumeration oracle for a company's private roles.
_NOT_AVAILABLE = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="This opening is not accepting applications.",
)

# Expired, submitted, deleted, never-existed, and no header at all: one answer.
_NO_SUCH_DRAFT = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="That link has expired or is no longer valid. Please start again.",
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
    # The acquisition channel this view was tracked under (PH3-B1), already
    # normalised. The client sends it back with the submission; it is echoed
    # rather than left to the client to re-derive so there is one implementation
    # of the vocabulary and it is the server's. Not personal data and not
    # company data — it is what the reader's own link said.
    source: str = "direct"
    source_detail: str | None = None


class ApplicationOut(BaseModel):
    """The ONE reply every submission to an anonymous door receives.

    There used to be an `already_applied: bool` here, and before that an
    `awaiting_confirmation`. Both are gone, and nothing may take their place:
    this model must not carry a field whose value depends on what is stored
    about the address that was typed in.

    WHY, because this keeps getting re-added in good faith.
    These doors are anonymous and identify a person by an address typed into a
    form. Anyone holding the public link can therefore submit any address they
    like. If the reply differs by what we already know about that address, then
    two requests confirm that a named person applied to a named opening and was
    turned down — somebody's employment history, handed to a stranger.

    Four rounds of review found that difference four times, each time somewhere
    adjacent to where the last one was closed: a 409 with the date on it, then
    this model's own fields, then the draft row left readable, then — with every
    single reply identical — *what the second submission read back*, because the
    reply still depended on stored state and the branches were simply reachable
    at different repetition counts.

    So the rule is stronger than "make the replies match": the reply is a pure
    function of what the caller sent. Everything a real candidate needs to be
    told that depends on what we know — you already applied, you were turned
    down, you may apply again on this date — goes to the ADDRESS, which is the
    only place it is theirs to read. `tests/integration/
    test_ph3_cooldown_indistinguishable.py` holds this down across the full
    four-case matrix, on both doors, over repeated submissions.
    """

    applicant_id: str
    enrolment_id: str | None
    full_name: str
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
    """The opening if it is genuinely taking applications, else 404.

    The gate is ``app.publishing.visible_sql`` and nothing else (PH3-B0). It
    used to be written out here, including the closing-date check in Python
    below the query, and the careers board carried a hand-copy that had already
    lost the published-workflow clause.
    """
    row = (
        await db.execute(
            text(
                # SAFE: the only interpolation below is visible_sql("r"), which
                # returns a predicate assembled from module-level literals in
                # app/publishing.py. Every value here is a bound parameter.
                # bandit reports the first fragment of the concatenation, so
                # the directive lives on this line rather than on the f-string.
                "SELECT r.id, r.company_id, r.title, r.level, r.jd_text, r.closes_at,"  # nosec B608
                "       r.owner_user_id, r.created_by_user_id, c.name AS company_name,"
                "       r.department, r.location, r.employment_type,"
                "       r.experience_min_years, r.experience_max_years,"
                "       r.responsibilities, r.required_skills, r.nice_to_have_skills,"
                "       r.salary_min, r.salary_max, r.salary_currency, r.salary_visible,"
                # PH3-B4b. Read here rather than in a second query: the apply
                # path already has this row, and a separate SELECT would be a
                # round trip for one smallint.
                "       r.reapply_cooldown_days"
                "  FROM job_requisitions r"
                "  JOIN companies c ON c.id = r.company_id AND c.is_active"
                " WHERE r.id = :i"
                f"   AND {visible_sql('r')}"
            ),
            {"i": requisition_id, "now": datetime.now(tz=UTC)},
        )
    ).mappings().first()
    if row is None:
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


# ---------------------------------------------------------------------------
# DECLARED BEFORE THE PARAMETERISED ROUTES, and that is not stylistic.
#
# FastAPI matches in declaration order and `/apply/{requisition_id}` happily
# matches the literal string "draft". With these below it, `GET /apply/draft`
# was being served by get_posting with requisition_id="draft" — verified, it
# returned a 500 and fired the wrong rate-limit bucket. The module docstring
# above already warned about exactly this for /apply/activate; this block
# ignored it. Anything else added under /apply with a literal first segment
# goes here too.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Save & resume, and the confirmation step — PH3-B4c and PH3-B5
#
# CONSENT COMES FIRST HERE, NOT AT SUBMIT.
# A draft holds a name, an email, a phone number and a CV. That is exactly the
# personal data CLAUDE.md forbids storing without a dpdp_consent_ledger entry,
# and the submitted-application path has always honoured that by writing both in
# one transaction. A draft saved before the consent checkbox would break it, so
# the checkbox moves to the first save: POST /apply/{id}/draft refuses without
# consent and writes the ledger row in the same transaction as the draft.
#
# The ledger entry is the same one the submitted application uses and is
# idempotent per user, so drafting and then submitting neither asks twice nor
# records twice.
#
# THE TOKEN IS THE ONLY CREDENTIAL. There is no login. The candidate keeps a
# resume link; the database stores only the token's hash. Every route below is
# rate-limited for the same reason interview links are — an unthrottled endpoint
# that reports valid-or-invalid is a free oracle even against 256 random bits.
# ---------------------------------------------------------------------------
class DraftStartIn(BaseModel):
    """Opening a draft. The email identifies the person; consent permits us to
    remember it."""

    email: EmailStr
    # Not defaulted to True and not inferred from the request reaching us.
    # DPDP consent has to be an act the person took.
    consent_granted: bool = False
    # PH5-E3 (D5-1), code review FIX 2 — a SECOND, INDEPENDENT opt-in, default
    # false, on exactly the same terms as `submit_application`'s Form field of
    # the same name: not defaulted to True and not inferred from the request
    # reaching us, because DPDP consent has to be an act the person took, and
    # doubly so for an optional one nothing else requires. Stored on the draft
    # by `start_draft`; `submit_draft` is what actually records it, since a
    # draft is not an application and this consent is about being considered
    # for a FUTURE opening. A false value writes nothing at all.
    rediscovery_opt_in: bool = False
    language: Literal["en", "hi", "te"] = "en"
    src: str | None = Field(default=None, max_length=200)


class DraftFieldsIn(BaseModel):
    """Progress. Every field optional — a draft is allowed to be incomplete."""

    full_name: str | None = Field(default=None, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    years_experience: int | None = Field(default=None, ge=0, le=60)
    current_company: str | None = Field(default=None, max_length=200)
    current_title: str | None = Field(default=None, max_length=200)
    linkedin_url: str | None = Field(default=None, max_length=500)
    github_url: str | None = Field(default=None, max_length=500)
    language: Literal["en", "hi", "te"] | None = None
    answers: dict[str, Any] | None = None


class ParsedDetails(BaseModel):
    """What the CV parser read, for the candidate to check — PH3-B5.

    Only fields the parser actually produces. The story lists education and
    skills as examples; the resume scorer does not return them today, and
    showing an empty box labelled "Education" that can never fill in would be
    worse than not asking. PH3-B5b extends the scorer; this shape grows with it.

    ALL FIVE, not just the name. extract_contact_details has always read a
    phone number and the two profile links as well, and they were stored and
    then dropped here — so a screen headed "we read these from your CV" showed
    one of the four things it had read, and the candidate retyped a number the
    parser already had. Criterion 2 is "extracted information is presented for
    review"; presenting a fifth of it is not that.
    """

    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None


class DraftOut(BaseModel):
    """A draft, as the candidate's own browser sees it.

    Carries no ids belonging to anyone else and no company data beyond the
    posting they are already looking at.
    """

    requisition_id: str
    title: str
    company_name: str
    email: str
    full_name: str | None = None
    phone: str | None = None
    years_experience: int | None = None
    current_company: str | None = None
    current_title: str | None = None
    linkedin_url: str | None = None
    github_url: str | None = None
    language: str = "en"
    answers: dict[str, Any] = Field(default_factory=dict)
    resume_filename: str | None = None
    has_resume: bool = False
    # PH3-B5. What we read off the CV, and whether they have said it is right.
    parsed: ParsedDetails = Field(default_factory=ParsedDetails)
    confirmed: bool = False
    expires_at: str


class DraftStartOut(BaseModel):
    """The draft plus the one credential that reopens it."""

    resume_token: str
    draft: DraftOut


# THE reply. Not "the reply for this case" — there are no cases any more.
#
# Every submission that is not an outright input error (unreadable PDF, closed
# opening, rate limit) is answered with this exact object. A live application, a
# rejection inside its waiting period, a rejection past it, an address that has
# never been seen here, a lost race: all the same status, all the same bytes.
#
# Four review rounds shrank the difference between these cases and never
# removed it, because each round made the replies MATCH rather than making the
# reply not depend on stored state. Matching is not enough. When the reply is
# still computed from what we know, the branches stay reachable — the last
# round found that a rejected address is answered "accepted" on every
# submission for ever, while an address that never applied is answered
# "accepted" once and "already applied" from the second time on. Two anonymous
# requests, and a stranger knows a named person was turned down for a named
# job.
#
# Hence one constant, built from nothing. What genuinely differs between the
# cases — you already have an application with us, you were turned down, you
# may apply again on this date, confirm this really is you — travels by EMAIL,
# to the address, which is the only place any of it is the reader's to know.
# The ids are blank for the same reason: the reply echoes what this request
# sent and not one thing more.
#
# Do not add a branch here. Do not add a field. See ApplicationOut.
_RECEIVED = (
    "Thanks — we have your application. Please check your email; we have sent "
    "you a message about it."
)


def _received(name: str) -> ApplicationOut:
    """The single reply, so no call site can drift into having an opinion."""
    return ApplicationOut(
        applicant_id="", enrolment_id=None, full_name=name, message=_RECEIVED
    )


async def _reply(name: str, *, floor_from: float) -> ApplicationOut:
    """The one reply, held until a common deadline.

    THE REPLY IS A CONSTANT; THE WORK IS NOT. Every state answers with the
    same bytes, but they do not cost the same: a live application is two
    SELECTs and a return, an address free to apply is a dozen writes, two
    commits and an email staged. Identical replies at measurably different
    times still say which branch was taken, and the branch is the answer to
    "has this named person applied here and been turned down".

    Five rounds closed differences in what these doors SAID. The sixth found
    the difference had simply moved into how long they took — and that making
    the CV upload common to every state, which was the previous attempt, had
    handed the caller control of the NOISE FLOOR rather than removing the
    signal: a 600-byte PDF shrinks the shared term to nothing and leaves the
    write differential as the whole variance. That attempt made the channel
    cleaner to exploit, not harder.

    So the reply waits. `floor_from` is taken after the shared work — for the
    one-shot door, after the upload the caller sized — so the deadline covers
    only the state-dependent tail. A branch that finishes early sleeps; a
    branch that overruns the floor is reported, because a floor quietly being
    exceeded is the control silently switching itself off.

    This does NOT make every failure mode uniform. A partial outage that lets
    reads through and refuses writes still answers 201 for the states that
    write nothing and 503 for the states that do. That is a narrower channel
    than this one and it is recorded rather than claimed closed — see the
    module docstring of the indistinguishability test.
    """
    floor = settings.apply_reply_floor_ms / 1000
    if floor > 0:
        remaining = floor - (time.monotonic() - floor_from)
        if remaining > 0:
            await asyncio.sleep(remaining)
        else:
            # Not fatal, and not something to hide: past the floor the timing
            # of this branch is visible again.
            log.warning(
                "public_apply.reply_floor_exceeded",
                over_ms=round(-remaining * 1000),
            )
    return _received(name)


async def _stage_accepted_mail(
    db: DbSessionDep,
    *,
    user_id: uuid.UUID,
    address: str,
    applicant_name: str,
    job_title: str | None,
    company_id: uuid.UUID,
    company_name: str | None,
    now: datetime,
    reapplying: bool,
    staged: bool,
    reapply_raw: str | None,
) -> None:
    """Which mail an accepted submission earns, on EITHER door. Caller commits.

    Three outcomes, and the first of them is the one that had to be learned
    twice. This was two hand-written copies, one per door, and round 5 found
    the "send nothing" case fixed on one of them only — the same
    one-door-of-two drift that `_refuse` exists to stop.

    * REAPPLYING, and staging was REFUSED because an attempt is already
      pending: nothing at all. "First link wins" means this submission
      recorded nothing, so there is no confirmation to send — and
      `stage_activation_email` carries no dedupe key, so falling through to
      it let anyone who knows a rejected candidate's address drive "we have
      your application" at that inbox at 6/min for the whole confirmation
      window, from the tenant's own authenticated sending domain, minting a
      fresh auth token each time. The mail would also be false: nothing is
      with the hiring team.
    * REAPPLYING and staged: the link that CONFIRMS it, not "your application
      is in" — which would be untrue while it waits, and which mints no token
      for somebody who has already claimed their account, leaving them nothing
      to confirm with.
    * Otherwise: the activation mail. It goes to the address ON FILE, so the
      real owner hears about an application they did not make.

    `applicant_name` is the caller's business: both doors pass
    `_email_name(existing, name)`, which prefers the STORED name so an
    anonymous request cannot write a line of its own choosing into a third
    party's inbox.
    """
    if reapplying and not staged:
        log.info("apply.reapply_pending_no_mail")
        return
    if reapplying:
        await stage_reapply_confirmation(
            db,
            user_id=user_id,
            applicant_email=address,
            applicant_name=applicant_name,
            job_title=job_title,
            company_id=company_id,
            company_name=company_name,
            now=now,
            raw=reapply_raw or "",
        )
        return
    await stage_activation_email(
        db,
        user_id=user_id,
        applicant_email=address,
        applicant_name=applicant_name,
        job_title=job_title,
        company_id=company_id,
        company_name=company_name,
        now=now,
    )


@dataclass(frozen=True)
class _CooldownNotice:
    """What the cooldown branch needs to mail, as a checked shape.

    A dict would do the job and `**kwargs` would be shorter, but both erase
    the types — and a field that was declared and then silently never bound is
    one of the defects this feature has already shipped. mypy checks this.
    """

    requisition_id: uuid.UUID
    company_id: uuid.UUID
    applicant_id: uuid.UUID | None
    address: str
    job_title: str | None
    company_name: str | None
    verdict: CooldownVerdict


async def _refuse(
    db: DbSessionDep,
    *,
    name: str,
    floor_from: float,
    cv_key: str | None,
    draft_id: uuid.UUID | None,
    cooldown: _CooldownNotice | None,
) -> ApplicationOut:
    """Answer a submission the gate will not act on, on EITHER door.

    WHY THIS IS ONE FUNCTION. `submit_application` and `submit_draft` are
    near-duplicates, and every one of the six review rounds on this feature
    found its defect in the gap between them — a fix applied to one door and
    not the other, five separate times. The refusal branches were the worst of
    it: four copies (two doors x already-applied/cooldown) of the same four
    steps, which is how one of them ended up clearing a draft's CV pointer
    without deleting the object, stranding a file no erasure could reach.

    The steps, in this order on both doors:

    1. consume the draft, if this door has one. A draft left readable is the
       round-3 channel: `GET /apply/draft` answered 404 for a live application
       and 200 for a rejected one, which is the same disclosure one step later.
       `release_resume=True` clears the pointer inside the transaction.
    2. mail the reason, if this is the cooldown branch. To the ADDRESS, which
       is the only place the date is the reader's to know; the reply itself
       says nothing. Owns a savepoint, never this transaction.
    3. commit.
    4. delete the object nothing names any more. AFTER the commit, so a failed
       delete leaves a findable orphan rather than a row pointing at a file
       that is gone.
    5. return the one reply, held to the common deadline.

    The doors differ only in what they pass: the one-shot door has no draft
    and its `cv_key` is the object it uploaded above the gate; the draft door
    passes its draft and that draft's key. Those are data, not code.
    """
    if draft_id is not None:
        await draft_store.mark_submitted(db, draft_id=draft_id, release_resume=True)
    if cooldown is not None:
        log.info(
            "public_apply.cooldown_blocked",
            requisition_id=str(cooldown.requisition_id),
            until=(
                cooldown.verdict.until.isoformat() if cooldown.verdict.until else None
            ),
        )
        await _mail_cooldown_reason(
            db,
            requisition_id=cooldown.requisition_id,
            company_id=cooldown.company_id,
            applicant_id=cooldown.applicant_id,
            address=cooldown.address,
            job_title=cooldown.job_title,
            company_name=cooldown.company_name,
            verdict=cooldown.verdict,
        )
    # GUARDED. This commit had no handler at any level, while the accepting
    # path's commit sits inside a try that turns any failure into a 503. So a
    # commit-time failure — serialization failure, statement timeout, a
    # connection reset, pgbouncer eviction — gave an unhandled 500 on the
    # REFUSING states and a 503 on the accepting ones: the states told apart
    # on the status line again, with no statistics needed.
    #
    # Worse on the draft door, where `mark_submitted` has already run: the
    # 500 unwinds the session, the draft stays `status='draft'`, and
    # `GET /apply/draft` then answers 200 for a refused state and 404 for an
    # accepted one — the round-3 channel, live on an error path.
    try:
        await db.commit()
    except Exception as exc:  # noqa: BLE001 — same answer the accept path gives
        await db.rollback()
        log.warning(
            "public_apply.refusal_commit_failed", error_type=type(exc).__name__
        )
        if cv_key:
            await _release_unadopted(str(cv_key))
        raise HTTPException(
            status_code=503,
            detail="We could not save your application. Please try again.",
        ) from exc
    if cv_key:
        await _release_unadopted(str(cv_key))
    return await _reply(name, floor_from=floor_from)


async def _require_write_capability(db: DbSessionDep) -> None:
    """Refuse every submission equally when the database cannot be written to.

    THE LAST STATUS-CODE ORACLE, and the one a reply floor cannot close.

    Under a partial outage that serves reads and refuses writes — failover to
    a read-only standby, `default_transaction_read_only`, a maintenance
    window — the states diverge on the status line, which no amount of
    latency-shaping hides:

      * a live application performs only SELECTs and commits nothing -> 201
      * a cooldown's only write is the notice, which is swallowed inside its
        own savepoint, so its commit commits nothing -> 201
      * every state that is free to apply raises on the insert -> 503

    Two anonymous requests during any read-only window therefore separate
    "this address already has an application here" from "this address is free
    to apply", which is the disclosure this whole feature exists to prevent.

    The alternative fix was to give every state the same write set. That means
    storing a row naming an address for somebody who never applied — PII about
    a non-applicant, with no consent-ledger entry to justify it and no
    erasure anchor to reach it. Hiding a status code is not a lawful basis.

    So instead this asks the question directly, before anything branches: can
    this transaction write? A zero-row UPDATE answers it. Postgres rejects DML
    in a read-only transaction at executor start, BEFORE evaluating the
    predicate — verified, not assumed: the statement below raises
    `cannot execute UPDATE in a read-only transaction` while reporting
    `UPDATE 0` on a healthy connection. So it costs an indexed probe that
    touches nothing, and it fails for every state alike.

    The 503 is the honest answer in that window: the service genuinely cannot
    accept an application from anybody. The alternative — answering 201
    everywhere — would tell a real candidate their application had landed when
    it had not, which is a worse thing to do than leak the distinction.

    SAVEPOINT, because an error aborts the surrounding transaction in
    Postgres unless one is held, and the caller has work in flight.

    WHAT THIS DOES NOT CLOSE: a disk-full primary, where reads and a zero-row
    DML both succeed and only real writes fail. That residue is narrower than
    what this closes and it is recorded rather than pretended away.
    """
    try:
        async with db.begin_nested():
            await db.execute(
                text(
                    "UPDATE enrolments SET updated_at = updated_at"
                    " WHERE id = '00000000-0000-0000-0000-000000000000'"
                )
            )
    except Exception as exc:  # noqa: BLE001 — any write refusal is the answer
        log.warning(
            "public_apply.write_unavailable", error_type=type(exc).__name__
        )
        raise HTTPException(
            # The SAME sentence a storage failure gives, so the two degraded
            # modes are not distinguishable from each other either.
            status_code=503,
            detail="We could not save your application. Please try again.",
        ) from exc


async def _release_unadopted(s3_key: str) -> None:
    """Remove a CV that was uploaded before the gate and then not kept.

    The upload happens before we know whether this submission will be acted
    on, so that the WORK the endpoint does cannot be read as an answer about
    the address — see `submit_application`. The branches that keep nothing
    call this.

    Best-effort and silent: the reply is already decided, and a failed delete
    must cost an orphan rather than change what a caller is told.

    HOW RECOVERABLE AN ORPHAN IS DEPENDS ON THE CALLER, and this docstring
    used to claim otherwise — "the key is under `applicants/{company}/
    {applicant}`, which the erasure sweep covers". That holds for one caller
    of three:

    * one-shot door, an address we already hold: `applicants/{company}/{id}-…`
      and the erasure sweep derives its prefixes from the subject's applicant
      rows, so this one IS reachable;
    * one-shot door, an address with no record: the key embeds a uuid4 that no
      applicant row ever used, because nothing was created. The sweep has no
      row to derive that prefix from. PERMANENT;
    * draft door: `drafts/{company}/{draft}.pdf`, outside the applicant prefix
      entirely, and `_refuse` has already NULLed the draft's pointer while
      `purge_expired` keeps objects only for rows still `status='draft'`.
      PERMANENT.

    So the key is LOGGED. The two permanent cases are the ones nothing else
    can name, and an object nobody can name is one a DPDP erasure reports
    success over; a key in the log is at least recoverable by hand. A bucket
    path with an opaque id is not personal data on its own, and the
    alternative is a file that cannot be found at all.
    """
    try:
        await _delete_from_s3(s3_key)
    except Exception:  # noqa: BLE001 — nothing points at it either way
        log.warning("public_apply.unadopted_cv_orphaned", s3_key=s3_key)


def _email_name(existing: Any | None, submitted: str) -> str:
    """The name to put in a mail — the STORED one whenever we have one.

    `_mail_cooldown_reason` already reasons this out for the notice it sends,
    and then the two mails staged beside it on the same route took the
    submitted name anyway. The threat is the same for all three.

    These doors are anonymous. `full_name` is up to 200 characters of whoever
    typed the form, and for a RETURNING applicant it reaches an inbox belonging
    to somebody we already know applied here — so an attacker who knows an
    address can post a line of their own choosing and have it delivered above a
    genuine call-to-action, from this company's authenticated sending domain,
    wearing its sender reputation. The person's name is already on file;
    nothing the sender typed needs to reach their inbox.

    For an address with no record here there is nothing stored to prefer, and
    the submitted name is what creates the record. `_clean` (applied where
    `name` is bound) is what keeps that case to a single line.
    """
    if existing is not None:
        stored = (existing["full_name"] or "").strip()
        if stored:
            return stored
    return submitted


async def _mail_cooldown_reason(
    db: DbSessionDep,
    *,
    requisition_id: uuid.UUID,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID | None,
    address: str,
    job_title: str | None,
    company_name: str | None,
    verdict: CooldownVerdict,
) -> None:
    """Tell the ADDRESS why the application was not taken, and when to return.

    The endpoint's own reply cannot say this. It is anonymous and accepts any
    address, so a refusal naming a date told whoever typed it that a real
    person had applied for this role, been rejected, and roughly when. Email is
    the only channel where that sentence reaches the person it is about and
    nobody else.

    Best-effort, and silent on failure: the reply has already been decided and
    is about to be returned. A failure costs the candidate an explanation, not
    an application.

    "Not an application" is the whole reason for the SAVEPOINT below. This used
    to `commit()` on success and `rollback()` on failure — the CALLER's
    transaction, which on the draft route already holds the `mark_submitted`
    that consumes the draft. A failure in here therefore threw that away, the
    caller's following `commit()` committed nothing, and the CV object was
    deleted regardless: the draft stayed readable at `GET /apply/draft` for a
    rejected candidate while a live one's returns 404, pointing at an object
    that no longer existed. That readable-draft difference is exactly the
    channel a previous round closed, re-opened by the fix for the round before
    it. A best-effort extra must never be able to undo the work of the request
    that called it, so it gets a savepoint of its own and commits nothing.

    Only sent when we already hold this person: with no applicant row there is
    nothing to be inside a cooldown for, and mailing an address we do not know
    would turn this into a way to send mail to strangers.
    """
    if applicant_id is None or verdict.until is None:
        return
    try:
        user_id = await db.scalar(
            text("SELECT user_id FROM applicants WHERE id = :a"), {"a": applicant_id}
        )
        if user_id is None:
            return
        lang = await db.scalar(
            text("SELECT preferred_language FROM users WHERE id = :u"), {"u": user_id}
        )
        # The STORED name, never the one on this request. The caller is
        # anonymous and `full_name` is 200 characters of their choosing, which
        # would otherwise land verbatim in the plain-text part of a mail sent
        # from the company's own domain to somebody they know applied there —
        # a phishing line with a live URL, wearing the company's sender
        # reputation. The person's own name is already on file; nothing the
        # sender typed needs to reach their inbox.
        stored_name = await db.scalar(
            text("SELECT full_name FROM applicants WHERE id = :a"),
            {"a": applicant_id},
        )
        # The savepoint. `enqueue_email` dedupes with a SELECT-then-skip while
        # `email_events.dedupe_key` is UNIQUE, so two probes for the same
        # address inside the same window both pass the check and the second
        # raises on flush. That is a reachable, caller-influenced failure, and
        # it must cost this notice and nothing else.
        async with db.begin_nested():
            await enqueue_email(
                db,
                to=address,
                template="generic",
                lang=(lang or "en"),
                ctx={
                    "name": stored_name or None,
                    "title": f"About your application for {job_title or 'this role'}",
                    "body": verdict.message(),
                    "brand": company_name,
                },
                to_user_id=uuid.UUID(str(user_id)),
                company_id=company_id,
                related_kind="reapply_cooldown_notice",
                # ONE notice per window, not one per probe. The route allows
                # 6/min per IP and fails open when Redis is down, so without
                # this anyone who knows the address can drive mail at a real
                # person's inbox indefinitely. It is also simply the right
                # behaviour: the answer does not change until the date does.
                dedupe_key=(
                    f"reapply-cooldown:{applicant_id}:{requisition_id}"
                    f":{verdict.until.date().isoformat()}"
                ),
            )
    except Exception:  # noqa: BLE001 — the reply is already decided
        # No rollback: the savepoint already undid whatever this attempted, and
        # rolling back here would discard the CALLER's work.
        log.warning(
            "public_apply.cooldown_notice_failed",
            requisition_id=str(requisition_id),
        )


def _draft_out(row: dict[str, Any]) -> DraftOut:
    parsed = dict(row.get("parsed") or {})
    return DraftOut(
        requisition_id=str(row["requisition_id"]),
        title=str(row.get("title") or ""),
        company_name=str(row.get("company_name") or ""),
        email=row["email"],
        full_name=row.get("full_name"),
        phone=row.get("phone"),
        years_experience=row.get("years_experience"),
        current_company=row.get("current_company"),
        current_title=row.get("current_title"),
        linkedin_url=row.get("linkedin_url"),
        github_url=row.get("github_url"),
        language=str(row.get("language") or "en"),
        answers=dict(row.get("answers") or {}),
        resume_filename=row.get("resume_filename"),
        has_resume=bool(row.get("resume_s3_key")),
        parsed=ParsedDetails(
            full_name=parsed.get("full_name"),
            email=parsed.get("email"),
            phone=parsed.get("phone"),
            linkedin_url=parsed.get("linkedin_url"),
            github_url=parsed.get("github_url"),
        ),
        confirmed=row.get("confirmed_at") is not None,
        expires_at=row["expires_at"].isoformat(),
    )


async def _draft_token(
    x_draft_token: Annotated[str | None, Header(alias="X-Draft-Token")] = None,
) -> str:
    """The resume token, from a HEADER — never the URL.

    This is the pattern ``exam_take`` and ``interview_take`` already use
    (``X-Exam-Token``, ``X-Interview-Token``), and exam_take's own docstring
    spells out why: "never the URL path/query". The token was briefly in the
    path here, which put a live credential to somebody's name, phone, employer,
    screening answers and CV into uvicorn's access log, the Space and Railway
    edge logs, browser history and any cross-origin Referer. CWE-598, CWE-532.

    The emailed/shared link still carries it — in the URL FRAGMENT, which
    browsers do not send to servers, exactly as the exam and interview links do.
    The SPA reads the fragment and puts it in this header.
    """
    if not x_draft_token:
        # Same uniform refusal as a bad token: whether the header was missing or
        # simply wrong is not information anyone needs.
        raise _NO_SUCH_DRAFT
    return x_draft_token


DraftTokenDep = Annotated[str, Depends(_draft_token)]


async def _draft_or_404(db: DbSessionDep, token: str) -> dict[str, Any]:
    """The draft this token opens, or a uniform 404.

    Expired, submitted, deleted and never-existed all answer identically — the
    same reasoning as ``_NOT_AVAILABLE`` above.
    """
    row = await draft_store.load(db, raw_token=token)
    if row is None:
        raise _NO_SUCH_DRAFT
    return row


@router.post(
    "/{requisition_id}/draft",
    response_model=DraftStartOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[rate_limit("public_apply_draft_start", 10)],
)
async def start_draft(
    requisition_id: uuid.UUID,
    body: DraftStartIn,
    request: Request,
    db: DbSessionDep,
) -> DraftStartOut:
    """Begin an application you can come back to.

    CONSENT IS TAKEN HERE. This is the first moment a person's email is stored,
    so it is the moment their permission is recorded — in the same transaction,
    exactly as the submitted-application path does it.

    THIS ROUTE CREATES. IT NEVER RESUMES, AND THAT IS A SECURITY PROPERTY.

    An earlier version resolved the email in the request body to an existing
    identity and, if that person already had a live draft, rotated its token
    and returned its contents. The email was therefore an authenticator — and
    it is not one. Anyone holding the apply link (which the module docstring
    above is explicit is not a secret) plus a candidate's address could read
    their name, phone, employer, job title, profile links, every screening
    answer and their CV filename, receive a working token for their draft,
    alter it, replace their CV and submit an application in their name — in one
    unauthenticated request, with the victim's own link silently killed.

    So identity is no longer derived from anything the caller says. Every call
    mints a FRESH draft with a FRESH guest identity, and the returned token is
    the only way back to it. Two saves make two drafts, each reachable only by
    its own link; that is the cost, and it is much cheaper than the alternative.

    The consequence worth naming: somebody who loses their link cannot recover
    it here, by design — recovering it would mean proving ownership of the
    address, and this endpoint cannot do that. The draft expires in
    DRAFT_TTL_DAYS and they start again.
    """
    req = await _open_posting(db, requisition_id)
    company_id = req["company_id"]

    if not body.consent_granted:
        # 422 rather than 403: nothing is wrong with the caller's authority,
        # the form is incomplete. The frontend renders this by the checkbox.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "We need your permission to store your details before we can save "
                "your progress."
            ),
        )

    address = str(body.email).strip().lower()[:320]
    now = datetime.now(tz=UTC)

    try:
        # A FRESH identity, every time. Deliberately no lookup by the address
        # the caller supplied: see the docstring. No applicant row is created
        # either — a draft is not an application, and putting somebody in HR's
        # pipeline before they have applied would be both wrong and visible.
        user_id = await _draft_only_guest_user(
            db, company_id=company_id, now=now, language=body.language,
        )

        draft, raw_token = await draft_store.start(
            db,
            requisition_id=requisition_id,
            company_id=company_id,
            email=address,
            consent_granted=body.consent_granted,
            user_id=user_id,
            source=normalise_source(body.src, default=DIRECT),
            now=now,
            rediscovery_opt_in=body.rediscovery_opt_in,
        )
        # SAME TRANSACTION as the draft row. This is the invariant that
        # CLAUDE.md hard constraint 3 requires, and it is why the consent
        # checkbox moved to the first save rather than to submission.
        #
        # The ledger row hangs off the fresh guest identity. When this draft is
        # submitted, submit_draft records consent against the identity that
        # ends up OWNING the application too, so an audit that looks the person
        # up by their real user id finds it.
        await _record_apply_consent(
            db, request=request, user_id=user_id, applicant_id=uuid.uuid4(),
            company_id=company_id, requisition_id=requisition_id, now=now,
        )
        await db.commit()
    except draft_store.ConsentRequiredError as exc:  # pragma: no cover - checked above
        await db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        await db.rollback()
        log.exception("public_apply.draft_start_failed", requisition_id=str(requisition_id))
        raise HTTPException(
            status_code=503, detail="We could not save your progress just now."
        ) from None

    return DraftStartOut(
        resume_token=raw_token,
        draft=_draft_out({**draft, "title": req["title"],
                          "company_name": req.get("company_name")}),
    )


@router.get(
    "/draft",
    response_model=DraftOut,
    dependencies=[rate_limit("public_apply_draft_read", 30)],
)
async def resume_draft(db: DbSessionDep, token: DraftTokenDep) -> DraftOut:
    """Pick up where you left off."""
    return _draft_out(await _draft_or_404(db, token))


@router.patch(
    "/draft",
    response_model=DraftOut,
    dependencies=[rate_limit("public_apply_draft", 30)],
)
async def update_draft(
    body: DraftFieldsIn, db: DbSessionDep, token: DraftTokenDep
) -> DraftOut:
    """Save progress. Nothing here is required and nothing is validated against
    the opening's rules — those apply at submission, because a draft is allowed
    to be incomplete."""
    row = await _draft_or_404(db, token)
    fields = body.model_dump(exclude_unset=True)
    answers = fields.pop("answers", None)
    await draft_store.save(db, draft_id=row["id"], fields=fields, answers=answers)
    await db.commit()
    return _draft_out(await _draft_or_404(db, token))


@router.post(
    "/draft/confirm",
    response_model=DraftOut,
    dependencies=[rate_limit("public_apply_draft", 30)],
)
async def confirm_draft(
    body: DraftFieldsIn, db: DbSessionDep, token: DraftTokenDep
) -> DraftOut:
    """Confirm the details we read off your CV — PH3-B5.

    The corrections in the body win over whatever the parser produced, and they
    become the values the application is submitted with. That is the entire
    point: a name read out of a PDF is a guess and a person's own answer is not.

    A parser that produced nothing does not block this. PH3-B5 Task 4 is
    explicit — an optional field that could not be extracted must not stop
    somebody applying — so the only requirement is the one the application
    itself has: a name and an email.
    """
    row = await _draft_or_404(db, token)
    corrections = body.model_dump(exclude_unset=True)
    corrections.pop("answers", None)

    # The one check worth making here rather than at submit: confirming means
    # "these details are right", and confirming an empty name is not meaningful.
    name = corrections.get("full_name") or row.get("full_name")
    if not (name or "").strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please tell us your name before confirming.",
        )

    await draft_store.confirm(db, draft_id=row["id"], corrections=corrections)
    await db.commit()
    return _draft_out(await _draft_or_404(db, token))


async def _draft_only_guest_user(
    db: DbSessionDep,
    *,
    company_id: uuid.UUID,
    now: datetime,
    language: str,
) -> uuid.UUID:
    """A fresh ``guest_candidate`` user to anchor one draft's consent record.

    Needed because ``dpdp_consent_ledger.user_id`` is NOT NULL and the consent
    is recorded at the first save.

    TAKES NO EMAIL, AND LOOKS NOTHING UP. An earlier version reused an existing
    identity found by matching the address the caller supplied, which is what
    let an anonymous request reach somebody else's draft. Resolving identity
    from unverified input is the vulnerability; not doing it is the fix.

    THE ADDRESS STORED HERE IS SYNTHETIC. ``users.email`` is UNIQUE across the
    whole platform, not per company, so writing a candidate's real address here
    would collide with any account that already held it — which was a separate
    bug, fixed separately. The real address lives on
    ``application_drafts.email``, and moves to ``applicants.email`` if and when
    the person actually applies.

    Deliberately does NOT create an applicant row: a draft is not an
    application.
    """
    user_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO users (id, email, password_hash, full_name, company_id,"
            " preferred_language, is_active, notify_login_email, created_at, updated_at)"
            " VALUES (:i,:e,NULL,'',:c,:lang,true,false,:n,:n)"
        ),
        {"i": user_id, "e": f"guest+{user_id}@applicants.invalid",
         "c": company_id, "lang": language, "n": now},
    )
    await db.execute(
        text(
            "INSERT INTO user_roles (user_id, role_id, assigned_at)"
            " SELECT :u, id, :n FROM roles WHERE name = 'guest_candidate'"
            " ON CONFLICT DO NOTHING"
        ),
        {"u": user_id, "n": now},
    )
    return user_id


@router.delete(
    "/draft",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[rate_limit("public_apply_draft", 30)],
)
async def delete_draft(db: DbSessionDep, token: DraftTokenDep) -> Response:
    """Throw this saved application away, now.

    DPDP gives a data principal the right to ACT, not merely to be forgotten on
    a schedule. Without this, somebody who started a draft and changed their
    mind had no route at all: the guest account has no password, so there is
    nothing to sign in to and nothing to ask from. The 30-day expiry is a
    backstop, not an answer.

    Token-authenticated, so it is neither an oracle nor available to anyone but
    the person holding the link. The CV object goes with the row — deleting the
    record and leaving the file is the failure mode this whole area keeps
    having, so it is done here explicitly rather than left to the cron.
    """
    row = await _draft_or_404(db, token)
    key = row.get("resume_s3_key")

    # THE OBJECT GOES FIRST, AND ITS FAILURE IS FATAL TO THE REQUEST.
    #
    # This used to delete the rows, commit, and THEN try the object, swallowing
    # any error into a warning nothing consumes. On a storage outage the
    # candidate was told — in three languages, on a screen headed "Your saved
    # application has been deleted" — that their CV had been removed, while the
    # object was still sitting in the bucket. And the row that pointed at it was
    # already gone, so no sweeper could ever find it again: not a delayed
    # deletion, a permanent orphan plus a false statement to a data principal.
    #
    # Ordering it this way makes the claim true or makes the request fail. The
    # remaining window is object-deleted-then-commit-fails, which leaves a draft
    # row pointing at a key that no longer exists — visible, recoverable, and
    # harmless to retry, because S3/R2 DELETE of an absent key is a no-op. That
    # is strictly the better failure to have.
    if key:
        try:
            await _delete_from_s3(str(key))
        except Exception as exc:  # noqa: BLE001 — surfaced, not swallowed
            log.error(
                "public_apply.draft_delete_storage_failed",
                draft_id=str(row["id"]), exc_type=type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "We could not delete your CV just now, so nothing has been "
                    "removed. Please try again in a few minutes."
                ),
            ) from exc

    await db.execute(
        text("DELETE FROM application_drafts WHERE id = :i"), {"i": row["id"]}
    )
    # And the guest identity it anchored, on exactly the terms purge_expired
    # uses. Without this, the self-serve erasure path — added for the DPDP
    # right to ACT — left behind the users row, its role grant and its consent
    # ledger entry, which is the unbounded growth the retention pass exists to
    # stop. Scoped to the synthetic address, and guarded on everything that
    # could still need the row, so a real account can never be caught.
    await db.execute(
        text(
            "DELETE FROM users u"
            " WHERE u.id = :uid"
            "   AND u.email LIKE 'guest+%@applicants.invalid'"
            "   AND NOT EXISTS (SELECT 1 FROM applicants a WHERE a.user_id = u.id)"
            "   AND NOT EXISTS ("
            "         SELECT 1 FROM application_drafts d WHERE d.user_id = u.id)"
            "   AND NOT EXISTS ("
            "         SELECT 1 FROM erasure_requests e WHERE e.user_id = u.id)"
        ),
        {"uid": row["user_id"]},
    )
    await db.commit()
    log.info("application_draft.deleted_by_candidate", draft_id=str(row["id"]))
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/draft/resume-upload",
    response_model=DraftOut,
    dependencies=[
        rate_limit("public_apply_draft_upload", 6),
        # The other anonymous door that writes an object. Same terms.
        rate_limit_window("public_apply_draft_upload_hourly", 60, 3600),
    ],
)
async def upload_draft_resume(
    db: DbSessionDep, resume: UploadFile, token: DraftTokenDep
) -> DraftOut:
    """Attach a CV to a draft, and read it so PH3-B5 has something to confirm.

    The same size and type limits as the submitted path, enforced here too
    rather than deferred: a candidate should be told their 30 MB scan is no good
    while they are still on the upload step, not at the end.
    """
    row = await _draft_or_404(db, token)

    if resume.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Please upload your CV as a PDF.")
    raw = await resume.read(_MAX_RESUME_BYTES + 1)
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

    s3_key = f"drafts/{row['company_id']}/{row['id']}.pdf"
    try:
        await _upload_to_s3(raw, s3_key)
    except (BotoCoreError, ClientError, LocalStorageError) as exc:
        log.warning("public_apply.draft_storage_failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="We could not store your CV just now. Please try again."
        ) from exc

    # What the candidate will be asked to confirm (PH3-B5).
    #
    # OFF THE EVENT LOOP, with a wall-clock bound — the same treatment
    # _extract_pdf_text gets two calls up, and for the same reason. The
    # patterns are bounded and the input is truncated, so this is linear and
    # capped; running it inline would still put a stranger's upload on the
    # thread that serves every other request, health probe included.
    #
    # A timeout here is not a failure worth refusing the upload over: the
    # candidate is about to be shown these fields to correct anyway, so an
    # empty set of suggestions is a slightly worse form, not a lost applicant.
    try:
        parsed = await asyncio.wait_for(
            asyncio.to_thread(extract_contact_details, resume_text),
            timeout=_PARSE_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        log.warning("public_apply.draft_parse_timeout", draft_id=str(row["id"]))
        parsed = {}
    await draft_store.attach_resume(
        db, draft_id=row["id"], s3_key=s3_key,
        filename=resume.filename, parsed=parsed,
    )
    await db.commit()
    return _draft_out(await _draft_or_404(db, token))


@router.post(
    "/draft/submit",
    response_model=ApplicationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        rate_limit("public_apply_submit", 6),
        # A SECOND cap, over an hour. This route stores the CV before
        # the gate is consulted (see the body for why), so a caller can
        # make us write an object we immediately delete. Six a minute
        # bounds a burst and is still 8,640 uploads a day from one
        # address; 60 an hour is far above anyone filling in a form and
        # far below anything worth calling storage abuse.
        rate_limit_window("public_apply_submit_hourly", 60, 3600),
    ],
)
async def submit_draft(
    request: Request, db: DbSessionDep, token: DraftTokenDep
) -> ApplicationOut:
    """Turn a confirmed draft into an application.

    Everything the one-shot endpoint enforces is enforced here too — the opening
    must still be taking applications, the required questions must be answered,
    the cooldown must have passed — because a draft started last week says
    nothing about whether any of those is still true today.

    Consent is NOT re-taken: it was recorded when the draft was created, in the
    same transaction as the first row that held this person's email, and the
    ledger entry is idempotent per person. Asking again would imply the first
    answer had not counted.
    """
    row = await _draft_or_404(db, token)
    requisition_id = uuid.UUID(str(row["requisition_id"]))

    # Re-checked, not trusted: the opening may have closed since the draft
    # started, and a draft is not a reservation.
    req = await _open_posting(db, requisition_id)
    company_id = req["company_id"]

    # `_clean`, not `.strip()`, for the reason the one-shot door gives: this
    # lands in the plain-text part of a mail, and `.strip()` trims only the
    # ENDS. `start_draft` accepts any address without verifying it, so an
    # attacker can open a draft against a victim's inbox and PATCH a
    # multi-line `full_name` — the collapse is what stops a paragraph of their
    # choosing arriving above a genuine call-to-action. The draft store
    # sanitises on write; this is the value that reaches the mail.
    name = _clean(row.get("full_name"), 200) or ""
    address = str(row["email"]).strip().lower()[:320]
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please tell us your name before submitting.",
        )
    if not row.get("resume_s3_key"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please upload your CV before submitting.",
        )
    if row.get("confirmed_at") is None:
        # PH3-B5. The confirmed details are what gets stored, so submitting
        # without confirming would mean storing what a parser guessed.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please review and confirm your details before submitting.",
        )

    # The opening's own required questions, validated against the draft's
    # answers. A draft may be incomplete; an application may not.
    questions = await list_questions(db, requisition_id=requisition_id)
    try:
        checked_answers = validate_answers(questions, dict(row.get("answers") or {}))
    except AnswerError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # The same question this door's twin asks, for the same reason. Nothing
    # was uploaded in this handler — the CV arrived at
    # /apply/draft/resume-upload and the draft row still names it — so there
    # is nothing to release if the answer is no.
    await _require_write_capability(db)

    # THE FLOOR STARTS HERE. This door does no upload of its own — the CV
    # arrived at /apply/draft/resume-upload — so the shared work is behind
    # us and everything below this line is state-dependent.
    floor_from = time.monotonic()

    existing = (
        await db.execute(
            text(
                # full_name: for `_email_name`, so a mail to a returning
                # applicant carries the name on file rather than the one this
                # anonymous request typed.
                "SELECT a.id, a.full_name, e.id AS enrolment_id,"
                "       e.status AS enrolment_status"
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

    # The SAME gate the one-shot form uses. This route had its own copy of the
    # decision and only the other copy was fixed, so a rejected candidate who
    # had used "Save and finish later" was still told "we have your
    # application": the cooldown never ran on this route, an override let
    # nobody through, and the branch below was dead code for exactly the people
    # it was written for. Two copies of one predicate is how it drifted.
    gate = await reapplication_gate(
        db,
        requisition_id=requisition_id,
        cooldown_days=req.get("reapply_cooldown_days"),
        applicant_id=uuid.UUID(str(existing["id"])) if existing is not None else None,
        enrolment_id=existing["enrolment_id"] if existing is not None else None,
        enrolment_status=existing["enrolment_status"] if existing is not None else None,
    )

    # BOTH refusal branches go through `_refuse`, which is the same function
    # the one-shot door uses. These were four hand-written copies of four
    # steps, and the drift between them is where five of six review rounds
    # found their defect. What differs between the doors is now only what is
    # passed in.
    #
    # `cv_key` is the draft's own object. Nothing adopts it on either branch:
    # no applicant is created and no enrolment is made, so neither
    # `applicants.resume_s3_key` nor `enrolments.applied_resume_s3_key` ever
    # references it — and `purge_expired` rightly refuses to delete a
    # submitted draft's object, so without this it would have no deletion path
    # at all and sit in the bucket past its purpose.
    #
    # `draft_id` is what consumes the draft. The cooldown branch used to
    # return without touching it, so `load` (which filters `status='draft'`)
    # answered 404 afterwards for a live application and 200 for a rejected
    # one — the same disclosure one step later.
    if gate.already_applied or not gate.verdict.allowed:
        return await _refuse(
            db,
            name=name,
            floor_from=floor_from,
            cv_key=row.get("resume_s3_key"),
            draft_id=row["id"],
            cooldown=(
                None
                if gate.already_applied
                else _CooldownNotice(
                    requisition_id=requisition_id,
                    company_id=company_id,
                    applicant_id=(
                        uuid.UUID(str(existing["id"])) if existing is not None else None
                    ),
                    address=address,
                    job_title=req["title"],
                    company_name=req.get("company_name"),
                    verdict=gate.verdict,
                )
            ),
        )

    applicant_id = uuid.UUID(str(existing["id"])) if existing is not None else uuid.uuid4()
    is_new_person = existing is None
    now = datetime.now(tz=UTC)
    actor = req["owner_user_id"] or req["created_by_user_id"]
    source = str(row.get("source") or DIRECT)
    source_detail = row.get("source_detail")

    try:
        if is_new_person:
            db.add(
                Applicant(
                    id=applicant_id,
                    company_id=company_id,
                    created_by_user_id=actor,
                    user_id=uuid.UUID(str(row["user_id"])),
                    full_name=name,
                    email=address,
                    target_job_title=req["title"],
                    target_level=req["level"],
                    target_jd_text=req["jd_text"],
                    resume_s3_key=row["resume_s3_key"],
                    # The candidate CONFIRMED this name (PH3-B5), so it is
                    # authored rather than parsed — the reconciler must not
                    # overwrite it with whatever the CV says.
                    full_name_source="candidate",
                    details_confirmed_at=row["confirmed_at"],
                    pending_enrichment=True,
                    phone=row.get("phone"),
                    years_experience=row.get("years_experience"),
                    current_company=row.get("current_company"),
                    current_title=row.get("current_title"),
                    linkedin_url=row.get("linkedin_url"),
                    github_url=row.get("github_url"),
                    status="new",
                    created_at=now,
                    updated_at=now,
                )
            )
        # A RETURNING applicant's record is NOT touched, exactly as the one-shot
        # path refuses to touch it, and for the same reason its comment gives:
        # "this form replaced their CV, target role, contact details and scores
        # for anyone who typed their email address, with no proof of who they
        # were."
        #
        # This path had re-introduced precisely that. It overwrote `full_name`
        # unconditionally and stamped `full_name_source='candidate'` plus
        # `details_confirmed_at` — which assert to the reconciler and to HR that
        # the real person personally confirmed those values. An anonymous caller
        # with a public apply link and somebody's address could therefore rename
        # them in a company's ATS, back-fill empty fields with chosen values and
        # give it all false provenance.
        #
        # The draft's details are not lost: they live on the draft row, the CV
        # belongs to this application alone via applied_resume_s3_key below, and
        # the confirmation email goes to the address on file — so the real owner
        # hears about an application they did not make. The record itself may
        # change only after activation has proved the address.
        await db.flush()

        outcome = await enrol_applicant(
            db,
            company_id=company_id,
            applicant_id=applicant_id,
            requisition_id=requisition_id,
            target_job_title=req["title"],
            target_level=req["level"],
            target_jd_text=req["jd_text"],
            resume_s3_key=row["resume_s3_key"],
            source=source,
            source_detail=source_detail,
        )
        # Not on a reapplication — see the one-shot route for why.
        if checked_answers and outcome.enrolment_id and not gate.reapplying:
            await store_answers(
                db,
                company_id=company_id,
                enrolment_id=uuid.UUID(outcome.enrolment_id),
                answers=checked_answers,
            )
        # Staged, not applied. Same door, same reason.
        superseded_cv: str | None = None
        # Clears the draft's own pointer in the same transaction, so the row
        # never outlives the object it names. See where it is set below.
        release_staged_draft_cv = False
        # Bound here too: `gate.reapplying` can be true while
        # enrol_applicant returned no enrolment id, and the email
        # check below reads it.
        staged = StageResult(staged=False)
        if gate.reapplying and outcome.enrolment_id:
            reapply_raw = mint_token()
            staged = await reapplication_stage(
                db,
                enrolment_id=uuid.UUID(outcome.enrolment_id),
                company_id=company_id,
                resume_s3_key=row["resume_s3_key"],
                answers=dict(checked_answers) if checked_answers else None,
                token_hash=hash_token(reapply_raw, REAPPLY_TOKEN_KIND),
            )
            superseded_cv = staged.superseded_key
            if not staged.staged:
                # THE SAME RELEASE THE ONE-SHOT DOOR DOES, and it was missing
                # here — which mattered more on this door, not less.
                #
                # An attempt was already pending, so this submission recorded
                # nothing: no applicant row is created for a returning person,
                # `enrol_applicant` no-ops, and `stage` stored no key. The
                # draft's object is left named by the draft row alone — and
                # `purge_expired` deletes a submitted draft's ROW after its
                # retention window while deliberately keeping the object,
                # because it assumes `applied_resume_s3_key` names it. Nothing
                # does. The key is `drafts/{company}/{draft}.pdf`, outside the
                # applicant-prefix sweep erasure runs, so after that window a
                # CV sits in the bucket that no collector and no sweep can
                # name — and an erasure request completes over it.
                superseded_cv = row["resume_s3_key"]
                release_staged_draft_cv = True
        # The draft's consent row hangs off the throwaway guest identity that
        # created it. Record it against the identity that owns the application
        # too, so an audit that looks this person up by their real user id
        # finds their consent rather than missing it. Idempotent per user, so
        # a returning applicant is not double-recorded.
        owner_user_id = await db.scalar(
            text("SELECT user_id FROM applicants WHERE id = :a"), {"a": applicant_id}
        )
        if owner_user_id is not None:
            await _record_apply_consent(
                db, request=request, user_id=uuid.UUID(str(owner_user_id)),
                applicant_id=applicant_id, company_id=company_id,
                requisition_id=requisition_id, now=now,
            )
            # PH5-E3, code review FIX 2. A FALSE value writes nothing at all —
            # `record_opt_in` is only ever called when the draft actually
            # stored a true flag. Same terms as the single-shot path
            # (submit_application): `source="public_apply_form"`, so a
            # withdrawn candidate is not silently re-granted through this
            # door either — this one is no more authenticated than that one.
            # NOT on a reapplication. A talent-pool opt-in is its own DPDP
            # §6 consent, and on this path nobody has proved they own the
            # address — so an unverified request could create a first-ever
            # rediscovery consent for a candidate who never gave one, with the
            # sender's own IP stored as the evidence for it. It waits for the
            # same confirmation the reopen does.
            if row.get("rediscovery_opt_in") and not gate.reapplying:
                await rediscovery.record_opt_in(
                    db, user_id=uuid.UUID(str(owner_user_id)), company_id=company_id,
                    applicant_id=applicant_id, requisition_id=requisition_id,
                    source="public_apply_form",
                    meta=rediscovery.OptInMeta(
                        ip_address=extract_client_ip(request),
                        user_agent=extract_user_agent(request),
                    ),
                    now=now,
                )
        await draft_store.mark_submitted(
            db, draft_id=row["id"], now=now,
            release_resume=release_staged_draft_cv,
        )
        await db.commit()
    except IntegrityError:
        # Two submissions racing for the same (requisition, applicant); the
        # partial unique index is the arbiter and this one lost.
        await db.rollback()
        # THE DRAFT IS STILL CONSUMED. This was the one exit of the eight that
        # left it readable, and the difference is state-correlated rather than
        # random: this error can only be raised while an enrolment is being
        # CREATED, which never happens for an address that already has one
        # (`enrol_applicant` no-ops). So a still-readable draft after a
        # concurrent submit meant "this address had no live application and no
        # cooldown" — the round-3 channel, surviving on the one exit the
        # matrix test cannot reach because it never issues concurrent
        # requests.
        #
        # In its own transaction, after the rollback: the work above is gone,
        # and this has to land on its own.
        #
        # AND THE OBJECT GOES WITH THE POINTER. `release_resume=True` nulls
        # `application_drafts.resume_s3_key`, and on this door that column is
        # the only thing naming the file: the transaction rolled back, so no
        # applicant or enrolment adopted it; the winner adopted its OWN
        # draft's key; `purge_expired` only queues objects for rows still in
        # `status='draft'`, and this row is now 'submitted' with a NULL
        # pointer; and `drafts/{company}/{draft}.pdf` sits outside the
        # `applicants/{company}/{applicant}` prefix the erasure sweep walks.
        # Clearing the pointer without deleting the object therefore makes a
        # CV that a completed DPDP erasure reports success over — which is the
        # exact failure the commit that added this handler said it was fixing,
        # two branches away. The two sibling exits on this door already do it
        # this way; this one was the odd one out.
        orphaned_race_cv = row.get("resume_s3_key")
        try:
            await draft_store.mark_submitted(
                db, draft_id=row["id"], now=now, release_resume=True
            )
            await db.commit()
        except Exception:  # noqa: BLE001 — the reply is already decided
            await db.rollback()
            log.warning("public_apply.draft_not_consumed_on_race", draft_id=str(row["id"]))
        else:
            if orphaned_race_cv:
                try:
                    await _delete_from_s3(str(orphaned_race_cv))
                except Exception:  # noqa: BLE001 — the pointer is already cleared
                    log.warning(
                        "public_apply.draft_object_orphaned", draft_id=str(row["id"])
                    )
        return await _reply(name, floor_from=floor_from)
    except Exception:
        await db.rollback()
        log.exception("public_apply.draft_submit_failed", requisition_id=str(requisition_id))
        raise HTTPException(
            status_code=503, detail="We could not submit your application just now."
        ) from None

    # Confirmation email, with a link to activate the account this application
    # just created. The one-shot path has always sent this and its own comment
    # names why it matters: "the confirmation email goes to the address on file
    # — so the real owner of that address hears about an application they did
    # not make." The draft path shipped without it, which removed that control
    # from the very flow this branch adds, and left draft-path applicants with
    # no way to claim their account.
    #
    # AFTER the commit and in its own transaction, for the reason spelled out
    # on the one-shot path: a SQL failure while staging the email would abort
    # the transaction and take the application down with it. The application is
    # already safe; only the email is at risk.
    guest_user_id = await db.scalar(
        text("SELECT user_id FROM applicants WHERE id = :a"), {"a": applicant_id}
    )
    if guest_user_id is not None:
        try:
            # A staged reapplication gets the link that CONFIRMS it, not the
            # "your application is in" email — which would be untrue (it is
            # waiting), and which mints no token at all for somebody who has
            # already claimed their account, leaving them nothing to confirm
            # with.
            # NOTHING AT ALL when a reapplication was refused because one
            # was already pending. "First link wins" means this submission
            # recorded nothing, so there is no confirmation to send — and
            # `stage_activation_email` carries no dedupe key, so falling
            # through to it let anyone who knows a rejected candidate's
            # address drive "your application has been received" at that
            # inbox at 6/min for the whole 168-hour window, from the tenant's
            # own authenticated sending domain, minting a fresh auth token
            # each time. The mail would also be false: nothing is with the
            # hiring team.
            await _stage_accepted_mail(
                db,
                user_id=uuid.UUID(str(guest_user_id)),
                address=address,
                applicant_name=_email_name(existing, name),
                job_title=req["title"],
                company_id=company_id,
                company_name=req.get("company_name"),
                now=now,
                reapplying=gate.reapplying,
                staged=staged.staged,
                reapply_raw=reapply_raw if gate.reapplying else None,
            )
            await db.commit()
        except Exception:  # noqa: BLE001 — see above
            await db.rollback()
            log.warning(
                "public.apply.draft_activation_email_failed",
                requisition_id=str(requisition_id),
                applicant_id=str(applicant_id),
            )

    # OUTSIDE the email block, deliberately. The pointer to this object was
    # cleared in a transaction that has already committed, so this delete is
    # the only thing left that can reach it — and it used to sit inside the
    # `try` above, after the commit, so a failure while staging the mail
    # skipped it and stranded the file. `drafts/` is outside the applicant
    # prefix erasure sweeps, which makes that strand permanent.
    if superseded_cv:
        try:
            await _delete_from_s3(superseded_cv)
        except Exception:  # noqa: BLE001 — the pointer is already gone
            log.warning("public_apply.superseded_reapply_cv_orphaned")

    log.info(
        "public.apply.received_from_draft",
        company_id=str(company_id), requisition_id=str(requisition_id),
        applicant_id=str(applicant_id), returning=not is_new_person,
    )
    return await _reply(name, floor_from=floor_from)



class ReapplyConfirmIn(BaseModel):
    token: str = Field(min_length=16, max_length=256)


class ReapplyConfirmOut(BaseModel):
    #: How many staged reapplications this link applied. Normally one; zero
    #: when the link has already been followed, which is not an error.
    applied: int
    message: str


@router.post(
    "/reapply/confirm",
    response_model=ReapplyConfirmOut,
    summary="Confirm a reapplication from the link emailed to the address",
    dependencies=[rate_limit("apply_reapply_confirm", settings.rate_limit_login_per_minute)],
)
async def confirm_reapplication(
    body: ReapplyConfirmIn, db: DbSessionDep
) -> ReapplyConfirmOut:
    """Apply a second attempt that has been waiting for proof of the address.

    Everything the anonymous submission deliberately did not do happens here:
    the application moves back to `new`, the CV that attempt was submitted
    with becomes the application's, its answers are written, and an override —
    if one is what let it past the cooldown — is spent.

    Following the link twice applies nothing the second time and says so
    calmly. The token is single-use, so the usual answer to a stale link is
    "invalid or expired"; `applied: 0` is for the case where the token was
    good but the work was already done.
    """
    try:
        await redeem_reapply_token(db, body.token)
    except ActivationError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    # THE ONE attempt this link was minted for, found by its own hash. Keyed
    # on the person instead, a link for company A also applied whatever was
    # staged at company B — and an attacker who knew the address could
    # re-stage until the victim's own confirmation authenticated the
    # attacker's CV.
    staged = await reapplication_staged_for_token(
        db, token_hash=hash_token(body.token, REAPPLY_TOKEN_KIND)
    )
    applied = 0
    orphaned: str | None = None
    try:
        if staged is not None:
            req_row = (
                await db.execute(
                    text(
                        # SAFE, on the same terms as `_open_posting` above:
                        # the only interpolation is visible_sql("r"), a
                        # predicate assembled from module-level literals in
                        # app/publishing.py, and every value is bound. bandit
                        # reports the first fragment of the concatenation, so
                        # the directive sits here rather than on the f-string.
                        "SELECT r.id, r.reapply_cooldown_days FROM enrolments e"  # nosec B608
                        "  JOIN job_requisitions r ON r.id = e.requisition_id"
                        " WHERE e.id = :e"
                        # The opening has to still be taking applications. The
                        # link is good for days, and a requisition closed or
                        # unpublished in the meantime must not have somebody
                        # walked back into it by a link minted while it was
                        # open. The SAME predicate the apply routes use, rather
                        # than a second opinion about what "open" means.
                        f"   AND {visible_sql('r')}"
                    ),
                    {"e": staged["id"], "now": datetime.now(tz=UTC)},
                )
            ).mappings().first()
            if req_row is None:
                # Nothing to reopen into. Clear the attempt so the data does
                # not sit there until the retention sweep, and delete the CV it
                # was the only name for.
                orphaned = await reapplication_clear_staged(
                    db,
                    enrolment_id=uuid.UUID(str(staged["id"])),
                    company_id=uuid.UUID(str(staged["company_id"])),
                )
                await db.commit()
                if orphaned:
                    try:
                        await _delete_from_s3(orphaned)
                    except Exception:  # noqa: BLE001
                        log.warning("apply.reapply_confirm.object_orphaned")
                log.info("apply.reapply_confirm.opening_closed")
                # The same sentence a second click gets. This one is not the
                # candidate's fault and not theirs to debug, and "that job has
                # closed" is a fact about the opening we are happy to tell the
                # holder of a link we minted for them — but it is told by the
                # hiring team, not by a confirmation screen.
                return ReapplyConfirmOut(
                    applied=0, message="This application has already been confirmed."
                )
            done = await reapplication_confirm(
                db,
                enrolment_id=uuid.UUID(str(staged["id"])),
                company_id=uuid.UUID(str(staged["company_id"])),
                applicant_id=uuid.UUID(str(staged["applicant_id"])),
                requisition_id=uuid.UUID(str(req_row["id"])) if req_row else None,
                cooldown_days=req_row["reapply_cooldown_days"] if req_row else None,
            )
            if done.retry_after is not None:
                # NOT YET, and the attempt is still staged. Roll back so the
                # token this router consumed a few lines up goes back to
                # unconsumed — committing here would burn the candidate's only
                # link over an attempt `confirm` deliberately preserved, and
                # "first link wins" would refuse to mint another for the rest
                # of the window. The date is safe to name: this link was
                # emailed to the address and nowhere else, which is the same
                # standard the cooldown notice already meets.
                await db.rollback()
                log.info("apply.reapply_confirm.not_yet")
                return ReapplyConfirmOut(
                    applied=0,
                    message=(
                        "Not yet — you can confirm this application from "
                        f"{done.retry_after.date().isoformat()}. "
                        "Keep this email; the link still works."
                    ),
                )
            applied = 1 if done.applied else 0
            orphaned = done.orphaned_key
        await db.commit()
        if orphaned:
            # The CV of an attempt that was overtaken while the link sat in an
            # inbox. Its columns are cleared, so from here nothing names it —
            # deleted after the commit, as the submit routes do for a
            # superseded upload.
            try:
                await _delete_from_s3(orphaned)
            except Exception:  # noqa: BLE001 — the pointer is already cleared
                log.warning("apply.reapply_confirm.object_orphaned")
    except Exception as exc:  # noqa: BLE001
        await db.rollback()
        log.exception("apply.reapply_confirm.failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="We could not confirm that just now. Please try the link again.",
        ) from exc

    return ReapplyConfirmOut(
        applied=applied,
        message=(
            "Thanks — your application is with the hiring team again."
            if applied
            else "This application has already been confirmed."
        ),
    )


@router.get(
    "/{requisition_id}",
    response_model=PostingOut,
    dependencies=[rate_limit("public_apply_view", 60)],
)
async def get_posting(
    requisition_id: uuid.UUID,
    db: DbSessionDep,
    # The tracked link is this GET: a recruiter shares /apply/<id>?src=linkedin.
    # Echoed back normalised (PH3-B1) so the client can carry it to the POST
    # without having to know the vocabulary, and so a tracked link that arrives
    # with a channel we do not recognise still tells the client what we will
    # record. Nothing is stored here — a page view is not an application.
    src: Annotated[str | None, Query(max_length=200)] = None,
) -> PostingOut:
    """The public posting. Never reveals anything about who else applied."""
    req = await _open_posting(db, requisition_id)
    source, source_detail = normalise_source(src, default=DIRECT)
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
        source=source,
        source_detail=source_detail,
    )


@router.post(
    "/{requisition_id}",
    response_model=ApplicationOut,
    status_code=status.HTTP_201_CREATED,
    # Tighter than the read: this one writes a row and uploads a file. Six a
    # minute is generous for a person filling in a form and useless for a
    # script trying to fill a funnel with noise.
    dependencies=[
        rate_limit("public_apply_submit", 6),
        # A SECOND cap, over an hour. This route stores the CV before
        # the gate is consulted (see the body for why), so a caller can
        # make us write an object we immediately delete. Six a minute
        # bounds a burst and is still 8,640 uploads a day from one
        # address; 60 an hour is far above anyone filling in a form and
        # far below anything worth calling storage abuse.
        rate_limit_window("public_apply_submit_hourly", 60, 3600),
    ],
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
    # PH5-E3 (D5-1) — a SECOND, INDEPENDENT opt-in, default false. Deliberately
    # not folded into `consent_granted`, and not required for anything: DPDP
    # §6(1) requires consent to be granular, and D5-1 calls this one "opt-in"
    # specifically, so bundling it with the application consent would make it
    # non-optional in substance even with its own checkbox. A false value
    # writes NOTHING at all — record_opt_in is only ever called when true.
    rediscovery_opt_in: Annotated[bool, Form()] = False,
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
    # The language of the emails this application sends (E4). Applied to the
    # placeholder account created for a NEW applicant; someone already on file
    # keeps the language they chose before.
    language: Annotated[str, Form(pattern="^(en|hi|te)$")] = "en",
    # Where this application came from (PH3-B1). A form field rather than a
    # query parameter because the tracked link is the GET; by the time the
    # candidate submits, the ?src= is several screens back and the client
    # carries it forward. Unvalidated by FastAPI on purpose — normalise_source
    # never refuses, and a 422 over a mistyped campaign tag would lose a real
    # applicant to a recruiter's typo.
    src: Annotated[str | None, Form(max_length=200)] = None,
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
    source, source_detail = normalise_source(src, default=DIRECT)

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

    # `_clean`, not `.strip()`. This lands in the plain-text part of an email,
    # and `.strip()` only takes whitespace off the ENDS — newlines in the
    # middle survive, which is enough to write a paragraph of somebody else's
    # choosing into a mail sent from this company's domain. Every other
    # free-text field on this route is already cleaned; this one was missed.
    name = _clean(full_name, 200) or ""
    address = str(email).strip().lower()[:320]

    # THE FLOOR STARTS HERE, before the identity lookup below.
    #
    # It used to start after the upload, on the argument that the upload is
    # caller-sized and common to every state and so is noise rather than
    # signal. That is true of the upload and it left the IDENTITY LOOKUP
    # outside the window — a LEFT JOIN that returns one row for four states
    # and none for the fifth, additive and measurable, and the caller shrinks
    # the upload burying it to nothing with a 600-byte PDF. The lookup cannot
    # move below the floor instead: `applicant_id` comes out of it and the
    # object key embeds `applicant_id`, so the upload depends on it.
    #
    # Starting the clock here is strictly better than starting it later. With
    # a small PDF — the attacker's own preference, because it is the quiet
    # regime — the floor dominates and the reply is a constant. With a large
    # one the floor may be exceeded, but then the term burying the difference
    # is the one the caller chose to make large. There is no PDF size that
    # both exposes the lookup and keeps the floor from covering it.
    floor_from = time.monotonic()

    # ── Who this email already is ───────────────────────────────────────────
    # Looked up here, but NOT answered until the CV has been read (below).
    existing = (
        await db.execute(
            text(
                "SELECT a.id, a.full_name, a.resume_s3_key, e.id AS enrolment_id,"
                "       e.status AS enrolment_status"
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
    # Bounded: one byte past the limit is enough to know it is over, and a 250 MB
    # body should not be read into memory to be told no.
    raw = await resume.read(_MAX_RESUME_BYTES + 1)
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

    # ── Already applied? ────────────────────────────────────────────────────
    # Answered only now, after a readable CV. The check used to run first and
    # return the STORED name and ids for whatever address was typed, so anyone
    # holding a live link could learn, for free, whether someone had applied and
    # what their name was. The reply now echoes only what this request sent.
    # A REJECTED application is not a live one, so this branch must not answer
    # for it. It used to: any enrolment at all, whatever its status, was told
    # "we have your application" — which shadowed the whole reapplication rule
    # below. The cooldown could never refuse anybody through this form, an
    # override could never let anybody through, and the candidate was told
    # their CV was with the hiring team when in fact they had been turned down.
    #
    # It went unseen because the PH3-B4b smoke soft-DELETES the enrolment
    # before reapplying, which no real rejection does; with the row gone this
    # branch missed and the cooldown ran. Found on 2026-09-27 by the first
    # browser test of this rule.
    #
    # Decided by reapplication.gate, which BOTH doors into an application share
    # — this one and the saved-draft route below. They used to hold two
    # hand-written copies of it and only one was fixed.
    # ── The CV is stored BEFORE the gate is consulted ───────────────────────
    # This ordering is the whole point, and it is the opposite of what reads
    # naturally.
    #
    # The obvious order — decide, then store only if we are keeping it — makes
    # the ENDPOINT's work depend on what we already know about the address,
    # and that is observable even when every byte of the reply is identical.
    # A live application and a cooldown returned here, before a 5 MB upload
    # and ten writes; a first-time application and a reapplication did all of
    # it. Two requests with a large PDF and a short client timeout separated
    # the four states on latency alone, with the caller choosing the file size
    # and so the size of the gap. Worse, it was not only timing: with object
    # storage unavailable the states that upload answered 503 while the states
    # that returned early answered 201 — a clean, non-statistical oracle from
    # two requests.
    #
    # So every submission that gets this far does the same work in the same
    # order, and the branches below delete what they do not keep. The cost is
    # that an anonymous caller can make us write an object we immediately
    # remove; `_MAX_RESUME_BYTES` bounds each one and the route carries both a
    # burst and a sustained rate limit to bound the rest.
    applicant_id = uuid.UUID(str(existing["id"])) if existing is not None else uuid.uuid4()
    is_new_person = existing is None
    # A returning candidate's CV gets a key of its own and belongs to THIS
    # application (enrolments.applied_resume_s3_key). It does not replace the
    # CV on their record — see the returning branch below.
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
        #
        # Reached by EVERY state now, which is the point: this 503 used to be
        # unreachable for a live application and a cooldown, and that made a
        # storage outage a way to ask whether an address had applied.
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
            status_code=503, detail="We could not save your application. Please try again."
        ) from exc

    # Can we write at all? Asked before anything branches, so a read-only
    # database refuses every state with the same 503 instead of answering 201
    # for the states that write nothing. The object just uploaded is released
    # first, or a degraded window would fill the bucket with orphans.
    try:
        await _require_write_capability(db)
    except HTTPException:
        await _release_unadopted(s3_key)
        raise

    gate = await reapplication_gate(
        db,
        requisition_id=requisition_id,
        cooldown_days=req.get("reapply_cooldown_days"),
        applicant_id=uuid.UUID(str(existing["id"])) if existing is not None else None,
        enrolment_id=existing["enrolment_id"] if existing is not None else None,
        enrolment_status=existing["enrolment_status"] if existing is not None else None,
    )
    # BOTH refusal branches go through `_refuse`, the same function the draft
    # door uses. `cv_key` is the object uploaded above the gate — uploaded so
    # this branch costs what the others cost, and released because nothing
    # here adopts it. `draft_id` is None: this door has no draft to consume.
    #
    # The cooldown branch is AFTER the already-applied one, deliberately: a
    # live application is answered with "we have it", which is not a refusal,
    # and a person whose application is still open must never be told to wait.
    # Both are after the CV has been read, because answering earlier would let
    # anyone holding the link discover, by typing addresses, who had been
    # turned down for this role.
    #
    # Neither says anything about the refusal. A 409 carrying the date was an
    # oracle: anyone with the public link could type an address and learn that
    # person had been rejected, and when. The date reaches the candidate by
    # email to the address, which is the only place it is theirs to read.
    if gate.already_applied or not gate.verdict.allowed:
        return await _refuse(
            db,
            name=name,
            floor_from=floor_from,
            cv_key=s3_key,
            draft_id=None,
            cooldown=(
                None
                if gate.already_applied
                else _CooldownNotice(
                    requisition_id=requisition_id,
                    company_id=company_id,
                    applicant_id=(
                        uuid.UUID(str(existing["id"])) if existing is not None else None
                    ),
                    address=address,
                    job_title=req["title"],
                    company_name=req.get("company_name"),
                    verdict=gate.verdict,
                )
            ),
        )

    # ── Store ───────────────────────────────────────────────────────────────
    # An applicant already exists for this email (they applied to a DIFFERENT
    # opening) — reuse the person and add an enrolment. D-06: one applicant per
    # company, many enrolments. `applicant_id`, `is_new_person` and `s3_key`
    # were all bound above the gate, with the upload.

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
        # A RETURNING applicant's record is not touched. It used to be: this
        # form replaced their CV, target role, contact details and scores for
        # anyone who typed their email address, with no proof of who they were.
        # Their new CV now belongs to this application alone
        # (applied_resume_s3_key), the reconciler scores the application against
        # it, and the confirmation email goes to the address on file — so the
        # real owner of that address hears about an application they did not
        # make.

        guest_user_id = await _ensure_guest_user(
            db, applicant_id=applicant_id, company_id=company_id, name=name,
            email=address, resume_text=resume_text, now=now, language=language,
        )
        await _record_apply_consent(
            db, request=request, user_id=guest_user_id, applicant_id=applicant_id,
            company_id=company_id, requisition_id=requisition_id, now=now,
        )
        # PH5-E3. A FALSE value writes nothing at all — this is the only
        # writer of this consent type reached from this route, and it is
        # never called except when the candidate actually ticked the box.
        # Not on a reapplication — see the draft route for why.
        if rediscovery_opt_in and not gate.reapplying:
            await rediscovery.record_opt_in(
                db, user_id=guest_user_id, company_id=company_id,
                applicant_id=applicant_id, requisition_id=requisition_id,
                source="public_apply_form",
                meta=rediscovery.OptInMeta(
                    ip_address=extract_client_ip(request),
                    user_agent=extract_user_agent(request),
                ),
                now=now,
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
            resume_s3_key=s3_key,
            # No ?src= means direct, not untracked: this arrival came through
            # the public apply link, which is itself a fact worth keeping apart
            # from the historical rows nobody was tracking at all.
            source=source,
            source_detail=source_detail,
        )
        # Same transaction as the enrolment they belong to: an application
        # whose answers did not land is not a complete application, and the
        # required ones were a condition of accepting it at all.
        # NOT on a reapplication. store_answers upserts on
        # (enrolment_id, question_id), so writing here would let an anonymous
        # request overwrite the answers the real candidate had already given
        # on an application that already exists. A second attempt's answers
        # are staged below and written when the address has been proven.
        if checked_answers and outcome.enrolment_id and not gate.reapplying:
            await store_answers(
                db,
                company_id=company_id,
                enrolment_id=uuid.UUID(outcome.enrolment_id),
                answers=checked_answers,
            )

        # A reapplication is STAGED, not applied. Both doors here are
        # anonymous and identify a person by an address typed into a form, so
        # acting on this request would let a stranger move a real person's
        # status, replace their CV and spend an override granted to them. It
        # waits on the enrolment until a link emailed to the address is
        # followed — see reapplication.stage.
        superseded_cv: str | None = None
        # Bound here too: `gate.reapplying` can be true while
        # enrol_applicant returned no enrolment id, and the email
        # check below reads it.
        staged = StageResult(staged=False)
        if gate.reapplying and outcome.enrolment_id:
            # Minted HERE so its hash can be bound to this one attempt before
            # the link goes out. A token that merely proves the address, and
            # not which submission it belongs to, applies whatever is staged.
            reapply_raw = mint_token()
            staged = await reapplication_stage(
                db,
                enrolment_id=uuid.UUID(outcome.enrolment_id),
                company_id=company_id,
                resume_s3_key=s3_key,
                answers=dict(checked_answers) if checked_answers else None,
                token_hash=hash_token(reapply_raw, REAPPLY_TOKEN_KIND),
            )
            superseded_cv = staged.superseded_key
            if not staged.staged:
                # An attempt is already pending, so this submission recorded
                # nothing — and the object uploaded for it a moment ago is
                # therefore named by no column at all. Released here rather
                # than left for a sweep that has nothing to find it by.
                superseded_cv = s3_key

        await db.commit()
    except IntegrityError:
        # Two submissions racing. The partial unique index on
        # (requisition_id, applicant_id) is the arbiter; the loser reports
        # success, because from the candidate's side their application landed.
        await db.rollback()
        # The winning submission stored its own CV; this one's object has no
        # row. (For a new person it never did: the applicant insert rolled back.)
        await _release_unadopted(s3_key)
        log.info("public.apply.race_lost", requisition_id=str(requisition_id))
        return await _reply(name, floor_from=floor_from)
    except rediscovery.RediscoveryError as exc:
        # Unreachable in practice — `source` above is the fixed literal
        # "public_apply_form", never caller input — but rendered with the
        # same `failure_code` shape as every other refusal in this service
        # rather than falling through to the generic 503 below, in case that
        # ever stops being true.
        await db.rollback()
        await _release_unadopted(s3_key)
        raise HTTPException(
            status_code=exc.status_code,
            detail={"failure_code": exc.code, "message": exc.message},
        ) from exc
    except Exception as exc:  # noqa: BLE001 — the upload must not outlive the row
        await db.rollback()
        # Orphaned object otherwise: a CV in storage belonging to nobody is PII
        # with no consent record and no erasure path. A returning candidate's
        # upload has its own key now, so it is removed too; their previous CV
        # is untouched.
        await _release_unadopted(s3_key)
        log.exception("public.apply.failed", error_type=type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="We could not save your application. Please try again."
        ) from exc

    # Confirmation email, with a link to activate the account this application
    # just created. AFTER the commit and in its own transaction, deliberately:
    # inside the application's transaction, a SQL-level failure while staging
    # the email would leave that transaction aborted and take the application
    # down with it — a caught exception is not enough, because every later
    # statement on an aborted transaction fails too. The application is already
    # safe by this point; what is at risk is only the email.
    try:
        # A staged reapplication gets the link that confirms it — see the
        # draft route for why it is not the activation email.
        #
        # And NOTHING when staging was refused because an attempt was already
        # pending. Same reasoning as the draft door: "first link wins" means
        # this submission recorded nothing, so there is no confirmation to
        # send, and `stage_activation_email` carries no dedupe key — falling
        # through to it let anyone who knows a rejected candidate's address
        # drive "we have your application" at that inbox at 6/min for the
        # whole confirmation window (fail-open when Redis is down), minting a
        # fresh auth token each time. Only this state amplifies that way: a
        # live application sends nothing, the cooldown notice is deduped, and
        # an unknown address becomes a live application after one request.
        await _stage_accepted_mail(
            db,
            user_id=uuid.UUID(str(guest_user_id)),
            address=address,
            applicant_name=_email_name(existing, name),
            job_title=req["title"],
            company_id=company_id,
            company_name=req.get("company_name"),
            now=now,
            reapplying=gate.reapplying,
            staged=staged.staged,
            reapply_raw=reapply_raw if gate.reapplying else None,
        )
        await db.commit()
    except Exception:  # noqa: BLE001 — see above
        await db.rollback()
        log.warning(
            "public.apply.activation_email_failed",
            requisition_id=str(requisition_id),
            applicant_id=str(applicant_id),
        )

    # OUTSIDE the email block. See the draft route: the pointer to this object
    # is already committed as cleared, so this delete is the only thing that
    # can still reach it, and it must not be skipped because staging a mail
    # failed.
    if superseded_cv:
        try:
            await _delete_from_s3(superseded_cv)
        except Exception:  # noqa: BLE001 — the pointer is already gone
            log.warning("public_apply.superseded_reapply_cv_orphaned")

    # Scoring happens in the reconciler. Wake it, as a bulk upload does, rather
    # than leaving this application for the next scheduled pass (up to ten
    # minutes).
    from app.reconciliation import wake as wake_reconciler  # noqa: PLC0415

    wake_reconciler()

    log.info(
        "public.apply.received",
        company_id=str(company_id),
        requisition_id=str(requisition_id),
        applicant_id=str(applicant_id),
        returning=not is_new_person,
        # NEVER log the name, email or resume text.
    )
    return await _reply(name, floor_from=floor_from)


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
    language: str = "en",
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
            " VALUES (:id, :em, NULL, :fn, :cid, :rt, :lang, true, false, false, :n, :n)"
        ),
        {"id": guest_user_id, "em": f"guest+{guest_user_id}@applicants.invalid",
         "fn": name, "cid": company_id, "rt": resume_text, "n": now, "lang": language},
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
    # Any row at all, granted or REVOKED. The revoked case is the one that
    # matters: this function is reachable from an unauthenticated submission
    # carrying an address anybody can type, and the previous predicate filtered
    # `revoked_at IS NULL` — so a stranger could mint a fresh `granted = TRUE`
    # row for somebody who had explicitly withdrawn, and reconciliation.py gates
    # processing on exactly `granted AND revoked_at IS NULL`. A withdrawal has
    # to be sticky against anyone but its owner, or it is not a withdrawal.
    #
    # Be precise about what that costs, because this comment used to claim a
    # remedy that does not exist: "it goes through the consent router, which is
    # authenticated". It does not. consent.py accepts only
    # consent_type in {interview_voice_recording, video_capture} with
    # _VALID_PURPOSES == {"interview"}, so NO endpoint anywhere can re-grant
    # application_data/recruitment. This function is its only writer.
    #
    # It is still not a lock-out, for a different reason: the only path that
    # revokes this row is a completed DPDP erasure, and the executor also NULLs
    # applicants.email and applicants.user_id — so a later application finds no
    # existing person, mints a fresh guest identity, and consents against that.
    # The withdrawn row belongs to an identity nobody can reach again.
    #
    # If a self-serve withdrawal is ever added WITHOUT erasure, that stops being
    # true and this becomes a real lock-out. Add the re-grant route in the same
    # change.
    existing = await db.scalar(
        text(
            "SELECT revoked_at IS NOT NULL AS was_revoked"
            "  FROM dpdp_consent_ledger WHERE user_id = :uid"
            " AND consent_type = 'application_data' AND purpose = 'recruitment'"
            " ORDER BY granted_at DESC LIMIT 1"
        ),
        {"uid": user_id},
    )
    if existing is not None:
        if existing:
            log.info(
                "consent.not_regranted_after_withdrawal",
                user_id=str(user_id), requisition_id=str(requisition_id),
            )
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


