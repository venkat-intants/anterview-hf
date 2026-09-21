"""Candidate accommodations — PH4-D2.

WHO DOES WHAT
An HR manager records an adjustment for an applicant: extra time, a deadline
extension, relaxed auto-submit on proctoring flags, or a free-text "other"
adjustment — scoped to the whole applicant, one application (``enrolment_id``),
one workflow round, or one hand-assigned exam round. HR revises it (a new row
supersedes the old one — an attempt already taken keeps pointing at what
applied when it was taken) or revokes it. There is no candidate route and no
super-admin, interviewer or agent route: only ``hr_manager`` ever records,
reads or revokes one (``app/routers/accommodations.py``).

WHO SEES WHAT
``interviewer_note`` is the ONLY accommodation text an assigned interviewer
ever sees (``interviewer_scorecards.get_for_interviewer``, as
``adjustments_note``). ``internal_note`` is HR-only and never leaves this
module. Neither note is ever written into an ``accommodation_events`` row, an
audit row, or the candidate email — those carry facts (scope, which
parameters are present, the target) and, for the email, the parameter values
that tell a candidate what changed, never prose.

THE SCORING PATH IS NOT TOUCHED
``pick``/``extra_seconds``/``scaled`` are pure. The only place an accommodation
reaches grading is ``exam_take._deadline``, which decides whether an attempt
is on time — never how it is scored. ``exam_grading.py`` and
``coding_grader.py`` do not import this module and never will (AST-tested).

Callers commit — every function here does at most ``db.flush()``.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviewer_scorecards import RequestMeta
from app.mailer import candidate_language, enqueue_email
from app.models import AccommodationEvent, Applicant, AuditLog

log = structlog.get_logger(__name__)

BASIS: tuple[str, ...] = ("candidate_request", "hr_initiated")
CONSENT_TYPE = "assessment_accommodation"
PURGE_BATCH = 2000


class AccommodationError(Exception):
    """Refused. Carries the HTTP status and a sentence for a person."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class AccommodationRow:
    """The fields ``pick`` needs to resolve which row, if any, applies."""

    id: uuid.UUID
    enrolment_id: uuid.UUID | None
    round_id: uuid.UUID | None
    exam_round_id: uuid.UUID | None
    extra_time_percent: int | None
    deadline_extension_days: int | None
    relax_auto_submit: bool
    effective_from: datetime
    effective_until: datetime | None
    interviewer_note: str | None


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------
def pick(
    rows: list[AccommodationRow],
    *,
    enrolment_id: uuid.UUID | None,
    workflow_round_id: uuid.UUID | None,
    exam_round_id: uuid.UUID | None,
    at: datetime,
) -> AccommodationRow | None:
    """The one row that applies here and now, or ``None``.

    The most specific row wins: a row scoped to THIS round or exam round, then
    a row scoped to the application, then a row scoped to the whole applicant.
    Within the same level, the latest ``effective_from`` wins. Rows are never
    added together — a candidate has at most one effective adjustment at a
    time for a given round.

    ``rows`` is expected to already be active, not superseded and not
    redacted; this function only applies the time window and the scope match.
    """
    by_level: dict[int, list[AccommodationRow]] = {}
    for row in rows:
        if row.effective_from > at:
            continue
        if row.effective_until is not None and row.effective_until < at:
            continue
        if row.round_id is not None:
            if workflow_round_id is None or row.round_id != workflow_round_id:
                continue
            if row.enrolment_id is not None and row.enrolment_id != enrolment_id:
                continue
            level = 3
        elif row.exam_round_id is not None:
            if exam_round_id is None or row.exam_round_id != exam_round_id:
                continue
            # The same exam round can back more than one application -- the
            # same exam used for two openings, or a retake under a second
            # enrolment. Without this an adjustment HR scoped to ONE
            # application would follow the candidate into another, which is
            # both wrong and invisible. The round_id branch above has always
            # checked this; the omission here was an asymmetry, not a rule.
            if row.enrolment_id is not None and row.enrolment_id != enrolment_id:
                continue
            level = 3
        elif row.enrolment_id is not None:
            if enrolment_id is None or row.enrolment_id != enrolment_id:
                continue
            level = 2
        else:
            level = 1
        by_level.setdefault(level, []).append(row)

    if not by_level:
        return None
    candidates = by_level[max(by_level)]
    # Ties are broken by id so the rule is total. effective_from is a date HR
    # types, so two active rows at the same scope can share one; before this,
    # which of them applied depended on the order the rows came back in, which
    # nothing guarantees. The id is arbitrary but stable, which is the point:
    # the same inputs pick the same row every time.
    return max(candidates, key=lambda r: (r.effective_from, r.id))


def extra_seconds(base: int | None, pct: int | None) -> int:
    """The additional seconds an ``extra_time_percent`` adds to ``base``,
    rounded up. Zero when there is no base or no percentage — no adjustment
    means nothing changes."""
    if not base or not pct:
        return 0
    return math.ceil(base * pct / 100)


def scaled(limit: int | None, pct: int | None) -> int | None:
    """``limit`` plus its ``extra_seconds`` — the scaled section/round limit a
    candidate sees. ``None`` stays ``None`` (an untimed limit stays untimed)."""
    if limit is None:
        return None
    return limit + extra_seconds(limit, pct)


# ---------------------------------------------------------------------------
# Facts-only records — never note text, never parameter values
# ---------------------------------------------------------------------------
def _scope_facts(
    enrolment_id: uuid.UUID | None, round_id: uuid.UUID | None, exam_round_id: uuid.UUID | None,
) -> dict[str, Any]:
    return {
        "enrolment_id": str(enrolment_id) if enrolment_id else None,
        "round_id": str(round_id) if round_id else None,
        "exam_round_id": str(exam_round_id) if exam_round_id else None,
    }


def _field_facts(
    *, extra_time_percent: int | None, deadline_extension_days: int | None,
    relax_auto_submit: bool, other_adjustment: str | None, interviewer_note: str | None,
    internal_note: str | None,
) -> dict[str, Any]:
    """Which parameters are present — never their values, never note text."""
    return {
        "has_extra_time_percent": extra_time_percent is not None,
        "has_deadline_extension_days": deadline_extension_days is not None,
        "relax_auto_submit": bool(relax_auto_submit),
        "has_other_adjustment": bool(other_adjustment),
        "has_interviewer_note": bool(interviewer_note),
        "has_internal_note": bool(internal_note),
    }


def _reason_facts(reason: str | None) -> dict[str, Any]:
    return {"has_reason": bool(reason), "reason_chars": len(reason or "")}


def _event(
    db: AsyncSession, *, company_id: uuid.UUID, accommodation_id: uuid.UUID, action: str,
    actor: uuid.UUID | None, details: dict[str, Any] | None = None,
) -> None:
    db.add(AccommodationEvent(
        id=uuid.uuid4(), company_id=company_id, accommodation_id=accommodation_id,
        action=action, actor_user_id=actor, details=details or {}, created_at=datetime.now(tz=UTC),
    ))


def _audit(
    db: AsyncSession, *, actor: uuid.UUID | None, action: str, resource_id: uuid.UUID,
    details: dict[str, Any], meta: RequestMeta,
) -> None:
    db.add(AuditLog(
        actor_id=actor, actor_type="user", action=action, resource_type="candidate_accommodation",
        resource_id=resource_id, details=details, ip_address=meta.ip_address,
        user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC),
    ))


_BASIS_NOTE: dict[str, str] = {
    "candidate_request": (
        "the candidate asked HR for this adjustment; the candidate did not consent "
        "through the product"
    ),
    "hr_initiated": (
        "HR recorded this adjustment on its own initiative, without a candidate "
        "request; the candidate did not consent through the product"
    ),
}


async def _record_basis(
    db: AsyncSession, *, accommodation_id: uuid.UUID, applicant_id: uuid.UUID,
    company_id: uuid.UUID, actor: uuid.UUID, now: datetime, action: str, basis: str,
    granted: bool,
) -> None:
    """Ledger entry: the basis for this accommodation state change.

    Booked against the RECORDER (``actor``), never the applicant --
    ``bulk_ingest.record_hr_collected_basis`` precedent. There is no candidate
    route on this feature (module docstring): whatever ``basis`` says, the
    candidate never consented through the product for ``record``, ``revise``
    or ``revoke``. Booking a ``granted=true`` row against the applicant's own
    linked ``user_id`` -- the previous behaviour -- asserted a consent that was
    never given, and the platform-owner DPDP audit feed reads any
    ``granted=true`` row as exactly that (``kind='consent_granted',
    actor='candidate'``).

    ``granted`` is True for ``record``/``revise`` (this state change is what
    establishes the basis for holding the new PII, same as
    ``record_hr_collected_basis``) and False for ``revoke`` (nothing new is
    granted here -- an existing adjustment is being ended, and a
    ``granted=true`` row for that would be dishonest in the other direction).

    Evidence carries ids, the accommodation's own ``basis`` field and a
    sentence naming what it means -- never the parameters or either note.
    """
    await db.execute(
        text(
            "INSERT INTO dpdp_consent_ledger"
            " (id, user_id, consent_type, granted, granted_at, purpose, evidence)"
            " VALUES (:id, :uid, :ct, :granted, :n, :p, CAST(:ev AS jsonb))"
        ),
        {
            "id": uuid.uuid4(), "uid": actor, "ct": CONSENT_TYPE, "granted": granted, "n": now,
            "p": f"accommodation:{accommodation_id}:{action}",
            "ev": json.dumps({
                "accommodation_id": str(accommodation_id), "applicant_id": str(applicant_id),
                "company_id": str(company_id), "action": action, "basis": basis,
                "basis_note": _BASIS_NOTE.get(basis, _BASIS_NOTE["hr_initiated"]),
                "recorded_by_user_id": str(actor), "recorded_at_iso": now.isoformat(),
            }),
        },
    )


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _ActiveRow:
    id: uuid.UUID
    applicant_id: uuid.UUID
    enrolment_id: uuid.UUID | None
    round_id: uuid.UUID | None
    exam_round_id: uuid.UUID | None
    basis: str
    requested_on: date | None


async def _get_active(
    db: AsyncSession, company_id: uuid.UUID, accommodation_id: uuid.UUID,
) -> _ActiveRow | None:
    row = (
        await db.execute(
            text(
                "SELECT id, applicant_id, enrolment_id, round_id, exam_round_id, basis, requested_on"
                "  FROM candidate_accommodations"
                " WHERE id = :i AND company_id = :c AND status = 'active'"
                "   AND superseded_at IS NULL AND redacted_at IS NULL"
            ),
            {"i": accommodation_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return _ActiveRow(**row)


async def _active_rows(
    db: AsyncSession, *, company_id: uuid.UUID, applicant_id: uuid.UUID,
) -> list[AccommodationRow]:
    rows = (
        await db.execute(
            text(
                "SELECT id, enrolment_id, round_id, exam_round_id, extra_time_percent,"
                "       deadline_extension_days, relax_auto_submit, effective_from,"
                "       effective_until, interviewer_note"
                "  FROM candidate_accommodations"
                " WHERE company_id = :c AND applicant_id = :a AND status = 'active'"
                "   AND superseded_at IS NULL AND redacted_at IS NULL"
                " ORDER BY effective_from, id"
            ),
            {"c": company_id, "a": applicant_id},
        )
    ).mappings().all()
    return [AccommodationRow(**r) for r in rows]


async def effective_for(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID,
    enrolment_id: uuid.UUID | None = None,
    workflow_round_id: uuid.UUID | None = None,
    exam_round_id: uuid.UUID | None = None,
    at: datetime | None = None,
) -> AccommodationRow | None:
    """The accommodation that applies for this applicant, in this scope, right
    now (or at ``at``). ``None`` when there is none — no adjustment means
    nothing changes."""
    rows = await _active_rows(db, company_id=company_id, applicant_id=applicant_id)
    return pick(
        rows, enrolment_id=enrolment_id, workflow_round_id=workflow_round_id,
        exam_round_id=exam_round_id, at=at or datetime.now(tz=UTC),
    )


async def interviewer_note_for(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, round_id: uuid.UUID,
) -> str | None:
    """The one thing an assigned interviewer ever sees about an accommodation.

    Never ``internal_note``, never the parameters — just this candidate's
    ``interviewer_note`` for this round, if any adjustment is effective here.
    """
    applicant_id = await db.scalar(
        text("SELECT applicant_id FROM enrolments WHERE id = :e AND company_id = :c"),
        {"e": enrolment_id, "c": company_id},
    )
    if applicant_id is None:
        return None
    row = await effective_for(
        db, company_id=company_id, applicant_id=applicant_id, enrolment_id=enrolment_id,
        workflow_round_id=round_id,
    )
    return row.interviewer_note if row else None


async def notify_recorded(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID,
    extra_time_percent: int | None,
    deadline_extension_days: int | None,
    relax_auto_submit: bool,
    other_adjustment: str | None,
) -> None:
    """Tell the candidate an adjustment was recorded for them — EN / HI / TE,
    the parameters only, never ``other_adjustment``'s text or either note.
    Best-effort: no recipient, no email, and never raises. Staged on the
    caller's transaction (the router commits), so the email exists iff the
    accommodation does."""
    applicant = await db.get(Applicant, applicant_id)
    if applicant is None or not applicant.email:
        return
    await enqueue_email(
        db,
        to=applicant.email,
        template="accommodation_recorded",
        lang=await candidate_language(db, applicant_id),
        ctx={
            "name": applicant.full_name,
            "extra_time_percent": extra_time_percent,
            "deadline_extension_days": deadline_extension_days,
            "relax_auto_submit": relax_auto_submit,
            "has_other_adjustment": bool(other_adjustment),
        },
        company_id=company_id,
        related_kind="candidate_accommodation",
        related_id=applicant_id,
    )


async def record_applied(
    db: AsyncSession, *, company_id: uuid.UUID, accommodation_id: uuid.UUID | None,
    target_kind: str, target_id: uuid.UUID,
) -> None:
    """An effective accommodation was actually used against a target (an exam
    attempt, an exam assignment, or an interview invite). A no-op when there
    was no accommodation to apply."""
    if accommodation_id is None:
        return
    _event(
        db, company_id=company_id, accommodation_id=accommodation_id, action="applied",
        actor=None, details={"target_kind": target_kind, "target_id": str(target_id)},
    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def _validate_scope_and_params(
    *, enrolment_id: uuid.UUID | None, round_id: uuid.UUID | None, exam_round_id: uuid.UUID | None,
    basis: str, extra_time_percent: int | None, deadline_extension_days: int | None,
    relax_auto_submit: bool, other_adjustment: str | None,
) -> None:
    if basis not in BASIS:
        raise AccommodationError(422, "basis must be candidate_request or hr_initiated.")
    if round_id is not None and enrolment_id is None:
        raise AccommodationError(422, "A round-scoped adjustment needs an application.")
    if exam_round_id is not None and enrolment_id is None:
        raise AccommodationError(422, "An exam-round-scoped adjustment needs an application.")
    if round_id is not None and exam_round_id is not None:
        raise AccommodationError(422, "Choose a workflow round or an exam round, not both.")
    if not (extra_time_percent or deadline_extension_days or relax_auto_submit or other_adjustment):
        raise AccommodationError(422, "Record at least one adjustment.")


async def _insert_row(
    db: AsyncSession, *, company_id: uuid.UUID, applicant_id: uuid.UUID, actor: uuid.UUID,
    enrolment_id: uuid.UUID | None, round_id: uuid.UUID | None, exam_round_id: uuid.UUID | None,
    extra_time_percent: int | None, deadline_extension_days: int | None, relax_auto_submit: bool,
    other_adjustment: str | None, interviewer_note: str | None, internal_note: str | None,
    basis: str, requested_on: date | None, effective_from: datetime | None,
    effective_until: datetime | None, supersedes_id: uuid.UUID | None,
) -> uuid.UUID:
    _validate_scope_and_params(
        enrolment_id=enrolment_id, round_id=round_id, exam_round_id=exam_round_id, basis=basis,
        extra_time_percent=extra_time_percent, deadline_extension_days=deadline_extension_days,
        relax_auto_submit=relax_auto_submit, other_adjustment=other_adjustment,
    )
    now = datetime.now(tz=UTC)
    aid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO candidate_accommodations"
            " (id, company_id, applicant_id, enrolment_id, round_id, exam_round_id,"
            "  extra_time_percent, deadline_extension_days, relax_auto_submit, other_adjustment,"
            "  interviewer_note, internal_note, basis, requested_on, effective_from,"
            "  effective_until, status, recorded_by_user_id, supersedes_id, created_at, updated_at)"
            " VALUES (:id,:c,:a,:e,:r,:er,:pct,:days,:rel,:other,:ivnote,:inote,:basis,:reqon,"
            "         :efrom,:euntil,'active',:actor,:sup,:n,:n)"
        ),
        {
            "id": aid, "c": company_id, "a": applicant_id, "e": enrolment_id, "r": round_id,
            "er": exam_round_id, "pct": extra_time_percent, "days": deadline_extension_days,
            "rel": relax_auto_submit, "other": other_adjustment, "ivnote": interviewer_note,
            "inote": internal_note, "basis": basis, "reqon": requested_on,
            "efrom": effective_from or now, "euntil": effective_until, "actor": actor,
            "sup": supersedes_id, "n": now,
        },
    )
    return aid


async def record(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    applicant_id: uuid.UUID,
    actor: uuid.UUID,
    enrolment_id: uuid.UUID | None,
    round_id: uuid.UUID | None,
    exam_round_id: uuid.UUID | None,
    extra_time_percent: int | None,
    deadline_extension_days: int | None,
    relax_auto_submit: bool,
    other_adjustment: str | None,
    interviewer_note: str | None,
    internal_note: str | None,
    basis: str,
    requested_on: date | None,
    effective_from: datetime | None,
    effective_until: datetime | None,
    meta: RequestMeta,
) -> uuid.UUID:
    """Record a new adjustment. Writes the row, a ``recorded`` event, an audit
    row (facts only) and a DPDP consent-ledger basis row. Caller commits."""
    aid = await _insert_row(
        db, company_id=company_id, applicant_id=applicant_id, actor=actor,
        enrolment_id=enrolment_id, round_id=round_id, exam_round_id=exam_round_id,
        extra_time_percent=extra_time_percent, deadline_extension_days=deadline_extension_days,
        relax_auto_submit=relax_auto_submit, other_adjustment=other_adjustment,
        interviewer_note=interviewer_note, internal_note=internal_note, basis=basis,
        requested_on=requested_on, effective_from=effective_from, effective_until=effective_until,
        supersedes_id=None,
    )
    now = datetime.now(tz=UTC)
    _event(
        db, company_id=company_id, accommodation_id=aid, action="recorded", actor=actor,
        details=_scope_facts(enrolment_id, round_id, exam_round_id),
    )
    _audit(
        db, actor=actor, action="accommodation.recorded", resource_id=aid,
        details={
            "company_id": str(company_id), "applicant_id": str(applicant_id),
            **_field_facts(
                extra_time_percent=extra_time_percent, deadline_extension_days=deadline_extension_days,
                relax_auto_submit=relax_auto_submit, other_adjustment=other_adjustment,
                interviewer_note=interviewer_note, internal_note=internal_note,
            ),
        },
        meta=meta,
    )
    await _record_basis(
        db, accommodation_id=aid, applicant_id=applicant_id, company_id=company_id, actor=actor,
        now=now, action="recorded", basis=basis, granted=True,
    )
    log.info("accommodation.recorded", accommodation_id=str(aid), company_id=str(company_id))
    return aid


async def revise(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    actor: uuid.UUID,
    accommodation_id: uuid.UUID,
    extra_time_percent: int | None,
    deadline_extension_days: int | None,
    relax_auto_submit: bool,
    other_adjustment: str | None,
    interviewer_note: str | None,
    internal_note: str | None,
    effective_from: datetime | None,
    effective_until: datetime | None,
    meta: RequestMeta,
) -> uuid.UUID:
    """Replace an active accommodation with a new row carrying new parameters,
    in the SAME scope (applicant, application and round never move on a
    revision). The old row is superseded, never edited."""
    old = await _get_active(db, company_id, accommodation_id)
    if old is None:
        raise AccommodationError(404, "Accommodation not found.")
    new_id = await _insert_row(
        db, company_id=company_id, applicant_id=old.applicant_id, actor=actor,
        enrolment_id=old.enrolment_id, round_id=old.round_id, exam_round_id=old.exam_round_id,
        extra_time_percent=extra_time_percent, deadline_extension_days=deadline_extension_days,
        relax_auto_submit=relax_auto_submit, other_adjustment=other_adjustment,
        interviewer_note=interviewer_note, internal_note=internal_note, basis=old.basis,
        requested_on=old.requested_on, effective_from=effective_from, effective_until=effective_until,
        supersedes_id=accommodation_id,
    )
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE candidate_accommodations SET superseded_at = :n, superseded_by_id = :new,"
            " updated_at = :n WHERE id = :old"
        ),
        {"n": now, "new": new_id, "old": accommodation_id},
    )
    _event(
        db, company_id=company_id, accommodation_id=accommodation_id, action="revised",
        actor=actor, details={"superseded_by": str(new_id)},
    )
    _audit(
        db, actor=actor, action="accommodation.revised", resource_id=accommodation_id,
        details={
            "company_id": str(company_id), "applicant_id": str(old.applicant_id),
            "revised_into": str(new_id),
            **_field_facts(
                extra_time_percent=extra_time_percent, deadline_extension_days=deadline_extension_days,
                relax_auto_submit=relax_auto_submit, other_adjustment=other_adjustment,
                interviewer_note=interviewer_note, internal_note=internal_note,
            ),
        },
        meta=meta,
    )
    await _record_basis(
        db, accommodation_id=new_id, applicant_id=old.applicant_id, company_id=company_id,
        actor=actor, now=now, action="revised", basis=old.basis, granted=True,
    )
    log.info("accommodation.revised", accommodation_id=str(accommodation_id), new_id=str(new_id))
    return new_id


async def revoke(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, accommodation_id: uuid.UUID,
    reason: str | None, meta: RequestMeta,
) -> None:
    """Revoke an active accommodation. Future attempts, assignments and
    invites see no adjustment from this row; anything already started or
    minted under it keeps what it already has (frozen on the attempt)."""
    old = await _get_active(db, company_id, accommodation_id)
    if old is None:
        raise AccommodationError(404, "Accommodation not found.")
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE candidate_accommodations SET status = 'revoked', revoked_by_user_id = :actor,"
            " revoked_at = :n, revoke_reason = :reason, updated_at = :n WHERE id = :id"
        ),
        {"actor": actor, "n": now, "reason": reason, "id": accommodation_id},
    )
    _event(
        db, company_id=company_id, accommodation_id=accommodation_id, action="revoked",
        actor=actor, details=_reason_facts(reason),
    )
    _audit(
        db, actor=actor, action="accommodation.revoked", resource_id=accommodation_id,
        details={"company_id": str(company_id), "applicant_id": str(old.applicant_id),
                 **_reason_facts(reason)},
        meta=meta,
    )
    await _record_basis(
        db, accommodation_id=accommodation_id, applicant_id=old.applicant_id, company_id=company_id,
        actor=actor, now=now, action="revoked", basis=old.basis, granted=False,
    )
    log.info("accommodation.revoked", accommodation_id=str(accommodation_id))


# ---------------------------------------------------------------------------
# Reads (HR only — enforced by the router, not here)
# ---------------------------------------------------------------------------
def _row_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "applicant_id": str(row["applicant_id"]),
        "enrolment_id": str(row["enrolment_id"]) if row["enrolment_id"] else None,
        "round_id": str(row["round_id"]) if row["round_id"] else None,
        "exam_round_id": str(row["exam_round_id"]) if row["exam_round_id"] else None,
        "extra_time_percent": row["extra_time_percent"],
        "deadline_extension_days": row["deadline_extension_days"],
        "relax_auto_submit": row["relax_auto_submit"],
        "other_adjustment": row["other_adjustment"],
        "interviewer_note": row["interviewer_note"],
        "internal_note": row["internal_note"],
        "basis": row["basis"],
        "requested_on": row["requested_on"].isoformat() if row["requested_on"] else None,
        "effective_from": row["effective_from"].isoformat(),
        "effective_until": row["effective_until"].isoformat() if row["effective_until"] else None,
        "status": row["status"],
        "recorded_by_user_id": (
            str(row["recorded_by_user_id"]) if row["recorded_by_user_id"] else None
        ),
        "revoked_by_user_id": str(row["revoked_by_user_id"]) if row["revoked_by_user_id"] else None,
        "recorded_by_name": row.get("recorded_by_name"),
        "revoked_by_name": row.get("revoked_by_name"),
        "revoked_at": row["revoked_at"].isoformat() if row["revoked_at"] else None,
        "revoke_reason": row["revoke_reason"],
        "supersedes_id": str(row["supersedes_id"]) if row["supersedes_id"] else None,
        "superseded_at": row["superseded_at"].isoformat() if row["superseded_at"] else None,
        "superseded_by_id": str(row["superseded_by_id"]) if row["superseded_by_id"] else None,
        "redacted_at": row["redacted_at"].isoformat() if row["redacted_at"] else None,
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
    }


async def list_for_applicant(
    db: AsyncSession, *, company_id: uuid.UUID, applicant_id: uuid.UUID,
) -> list[dict[str, Any]]:
    """Every accommodation ever recorded for this applicant, newest first —
    HR's full history, including revoked and superseded rows."""
    exists = await db.scalar(
        text("SELECT 1 FROM applicants WHERE id = :a AND company_id = :c AND deleted_at IS NULL"),
        {"a": applicant_id, "c": company_id},
    )
    if not exists:
        raise AccommodationError(404, "Applicant not found.")
    rows = (
        await db.execute(
            text(
                # Names are resolved here, as the offer history resolves its
                # actors, so the console never has to look people up itself.
                # revoked_by_user_id is NULL when the platform ended the row
                # (retention or erasure) rather than a person.
                "SELECT ca.*, COALESCE(ru.full_name, ru.email) AS recorded_by_name,"
                "       COALESCE(vu.full_name, vu.email) AS revoked_by_name"
                "  FROM candidate_accommodations ca"
                "  LEFT JOIN users ru ON ru.id = ca.recorded_by_user_id"
                "  LEFT JOIN users vu ON vu.id = ca.revoked_by_user_id"
                " WHERE ca.company_id = :c AND ca.applicant_id = :a"
                " ORDER BY ca.created_at DESC, ca.id"
            ),
            {"c": company_id, "a": applicant_id},
        )
    ).mappings().all()
    return [_row_out(dict(r)) for r in rows]


async def effective_preview(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID,
    round_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """What would apply right now for this application (and, optionally, one
    workflow round) — the HR-facing preview."""
    applicant_id = await db.scalar(
        text("SELECT applicant_id FROM enrolments WHERE id = :e AND company_id = :c"
             "   AND deleted_at IS NULL"),
        {"e": enrolment_id, "c": company_id},
    )
    if applicant_id is None:
        raise AccommodationError(404, "Application not found.")
    row = await effective_for(
        db, company_id=company_id, applicant_id=applicant_id, enrolment_id=enrolment_id,
        workflow_round_id=round_id,
    )
    if row is None:
        return {"effective": False}
    return {
        "effective": True,
        "accommodation_id": str(row.id),
        "extra_time_percent": row.extra_time_percent,
        "deadline_extension_days": row.deadline_extension_days,
        "relax_auto_submit": row.relax_auto_submit,
    }


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
_PURGEABLE_SQL = """
SELECT ca.id, ca.company_id, ca.status FROM candidate_accommodations ca
 WHERE ca.redacted_at IS NULL
   AND (ca.other_adjustment IS NOT NULL OR ca.interviewer_note IS NOT NULL
        OR ca.internal_note IS NOT NULL OR ca.revoke_reason IS NOT NULL)
   AND (
     (ca.effective_until IS NOT NULL AND ca.effective_until < :cutoff)
     OR (
       EXISTS (
         SELECT 1 FROM enrolments e
          WHERE e.applicant_id = ca.applicant_id AND e.company_id = ca.company_id
            AND e.deleted_at IS NULL
       )
       AND NOT EXISTS (
         SELECT 1 FROM enrolments e
          WHERE e.applicant_id = ca.applicant_id AND e.company_id = ca.company_id
            AND e.deleted_at IS NULL AND e.status NOT IN ('hired', 'rejected')
       )
       AND COALESCE(
             (SELECT max(t.occurred_at) FROM stage_transitions t
                JOIN enrolments e2 ON e2.id = t.enrolment_id
               WHERE e2.applicant_id = ca.applicant_id AND e2.company_id = ca.company_id
                 AND t.to_status IN ('hired', 'rejected')),
             'epoch'::timestamptz
           ) < :cutoff
     )
   )
 LIMIT :lim
"""


async def purge(db: AsyncSession, *, retention_days: int, dry_run: bool) -> int:
    """Redact notes 180 days (``retention_days``) after every one of the
    applicant's applications at the company is decided, or 180 days after
    ``effective_until`` — whichever a row qualifies under. The parameters
    (numbers) are kept, the scorecard precedent. Honours ``RETENTION_DRY_RUN``.

    F8: a row still ``active`` at redaction time is ended first -- ``pick``
    and ``_active_rows`` already stop seeing it the moment ``redacted_at`` is
    set, so leaving ``status`` at ``active`` would have HR's list keep
    reporting an adjustment that can no longer take effect. The guard trigger
    forbids changing ``status`` in the same statement as ``redacted_at``, so
    this is two statements, status first: that order leaves the row
    ``status='revoked'`` before it is also frozen by ``redacted_at``, which is
    exactly the shape a later erasure (BLOCKING 1's ``redacted_at IS NULL``
    guard) is written to skip outright. A row HR already revoked keeps
    whatever status it has -- there is nothing to end.

    Caller commits.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
    rows = (
        await db.execute(text(_PURGEABLE_SQL), {"cutoff": cutoff, "lim": PURGE_BATCH})
    ).all()
    if dry_run or not rows:
        log.info("accommodation.retention", candidates=len(rows), dry_run=dry_run)
        return len(rows)
    now = datetime.now(tz=UTC)
    for aid, cid, status in rows:
        if status == "active":
            # revoked_by_user_id stays NULL: the platform ended this, not a
            # person, and that column is a real FK to users. Naming a sentinel
            # id needs a fabricated account and fails without one -- the smoke
            # caught exactly that.
            await db.execute(
                text(
                    "UPDATE candidate_accommodations SET status = 'revoked',"
                    " revoked_at = :n, updated_at = :n"
                    " WHERE id = :id AND status = 'active'"
                ),
                {"n": now, "id": aid},
            )
            _event(
                db, company_id=cid, accommodation_id=aid, action="revoked", actor=None,
                details={"reason": "retention"},
            )
        await db.execute(
            text(
                "UPDATE candidate_accommodations SET"
                " other_adjustment = CASE WHEN other_adjustment IS NULL THEN NULL"
                "                         ELSE '[redacted]' END,"
                " revoke_reason = CASE WHEN revoke_reason IS NULL THEN NULL"
                "                      ELSE '[redacted]' END,"
                " interviewer_note = NULL, internal_note = NULL, redacted_at = :n,"
                " updated_at = :n WHERE id = :id"
            ),
            {"n": now, "id": aid},
        )
        _event(
            db, company_id=cid, accommodation_id=aid, action="redacted", actor=None,
            details={"reason": "retention"},
        )
    log.info("accommodation.retention", purged=len(rows))
    return len(rows)
