"""Offers — PH4-A3.

WHO DOES WHAT
- HR managers write an offer for an application a person has decided to hire
  (status ``hired`` — the final decision is D-05's, never this module's), from a
  template or from scratch, and submit it.
- The company super admin approves it or sends it back (decision D4-2). Nobody
  approves an offer they wrote or submitted — here and at the database.
- HR sends the approved offer. The candidate reads it through a link and
  answers it with a one-time code sent to their email (app.offer_security).
- HR can withdraw an offer until it is answered. An unanswered offer expires.

WHAT IT NEVER DOES
It never changes an application's status or its decision record. The outcome
of an offer is kept on ``enrolments.offer_outcome``, beside the decision, not
in place of it. No agent can call any of this: every route needs a person's
session, and agents hold no write tool.

Compensation is read only by HR managers and super admins of the company, and
by the candidate it is addressed to. Every change and every candidate action
lands in ``offer_events`` (facts only) and the audit log. Callers commit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.interviewer_scorecards import RequestMeta
from app.mailer import candidate_language, enqueue_email
from app.models import AuditLog
from app.notifications_util import create_notification
from app.offer_security import (
    MAX_CODE_ATTEMPTS,
    codes_match,
    hash_code,
    hash_offer_token,
    hash_session_token,
    mint_code,
    mint_offer_token,
)

log = structlog.get_logger()

EMPLOYMENT = ("full_time", "part_time", "contract", "internship")
PERIODS = ("annual", "monthly", "hourly")
EDITABLE = ("draft", "rejected")
BEFORE_ANSWER = ("draft", "pending_approval", "approved", "rejected", "sent")
FINAL = ("accepted", "declined", "expired", "withdrawn")
CODES_PER_WINDOW = 3
CODE_WINDOW = timedelta(minutes=15)
# Wrong codes over an offer's whole life (security review M1). Per-window limits
# alone still allow ~1,400 guesses a day; at this cap answering and document
# access lock until HR re-sends the offer, which rotates the link.
MAX_CODE_FAILURES = 20
SESSION_MINUTES = 60
LOCKED = ("Too many wrong codes. For your security this offer is locked — please ask "
          "the hiring team to send it to you again.")
TEXT_LIMITS = {"job_title": 200, "location": 200, "bonus": 1000, "equity": 1000,
               "benefits": 4000, "terms": 20000}
CONTENT_FIELDS = ("job_title", "employment_type", "start_date", "location", "base_salary",
                  "currency", "pay_period", "bonus", "equity", "benefits", "terms",
                  "probation_months", "notice_period_days", "valid_days")


class OfferError(Exception):
    """Refused. Carries the HTTP status and a sentence for a person.

    ``keep`` marks the few refusals whose own writes must survive the refusal:
    a wrong code still counts as an attempt (or guessing is unbounded), and an
    offer found expired stays expired. The router commits before answering.
    """

    def __init__(self, status_code: int, detail: str, *, keep: bool = False) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.keep = keep


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
def _audit(db: AsyncSession, *, actor: uuid.UUID | None, action: str, offer_id: uuid.UUID,
           details: dict[str, Any], meta: RequestMeta, actor_type: str = "user") -> None:
    db.add(AuditLog(
        actor_id=actor, actor_type=actor_type, action=action, resource_type="offer",
        resource_id=offer_id, details=details, ip_address=meta.ip_address,
        user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC),
    ))


async def _event(db: AsyncSession, *, offer: dict[str, Any], action: str, actor_type: str,
                 actor: uuid.UUID | None, details: dict[str, Any] | None = None) -> None:
    import json  # noqa: PLC0415 — only for the JSONB literal

    await db.execute(
        text(
            "INSERT INTO offer_events (id, company_id, offer_id, action, actor_type,"
            " actor_user_id, details) VALUES (gen_random_uuid(), :c, :o, :a, :t, :u,"
            " CAST(:d AS jsonb))"
        ),
        {"c": offer["company_id"], "o": offer["id"], "a": action, "t": actor_type, "u": actor,
         "d": json.dumps(details or {}, default=str)},
    )


async def _record(db: AsyncSession, *, offer: dict[str, Any], action: str, actor: uuid.UUID | None,
                  meta: RequestMeta, actor_type: str = "user",
                  details: dict[str, Any] | None = None) -> None:
    facts = {"company_id": str(offer["company_id"]), "enrolment_id": str(offer["enrolment_id"]),
             **(details or {})}
    await _event(db, offer=offer, action=action, actor_type=actor_type, actor=actor, details=details)
    _audit(db, actor=actor, action=f"offer.{action}", offer_id=offer["id"], details=facts,
           meta=meta, actor_type="user" if actor_type == "user" else "system")


def _money(value: Any) -> str | None:
    return None if value is None else f"{Decimal(value):.2f}"


def offer_out(o: dict[str, Any]) -> dict[str, Any]:
    """What HR managers and the approving super admin read — compensation included."""
    return {
        "id": str(o["id"]), "enrolment_id": str(o["enrolment_id"]),
        "requisition_id": str(o["requisition_id"]) if o.get("requisition_id") else None,
        "template_id": str(o["template_id"]) if o.get("template_id") else None,
        "status": o["status"], "job_title": o["job_title"],
        "employment_type": o["employment_type"],
        "start_date": o["start_date"].isoformat() if o.get("start_date") else None,
        "location": o["location"], "base_salary": _money(o["base_salary"]),
        "currency": o["currency"], "pay_period": o["pay_period"], "bonus": o["bonus"],
        "equity": o["equity"], "benefits": o["benefits"], "terms": o["terms"],
        "probation_months": o["probation_months"], "notice_period_days": o["notice_period_days"],
        "valid_days": o["valid_days"],
        "created_by_user_id": str(o["created_by_user_id"]) if o.get("created_by_user_id") else None,
        "submitted_at": _iso(o.get("submitted_at")), "decided_at": _iso(o.get("decided_at")),
        "approval_note": o.get("approval_note"),
        "sent_at": _iso(o.get("sent_at")), "expires_at": _iso(o.get("expires_at")),
        "first_viewed_at": _iso(o.get("first_viewed_at")),
        "responded_at": _iso(o.get("responded_at")), "decline_reason": o.get("decline_reason"),
        "withdrawn_at": _iso(o.get("withdrawn_at")), "withdraw_reason": o.get("withdraw_reason"),
        "preboarding_completed_at": _iso(o.get("preboarding_completed_at")),
        "candidate_name": o.get("candidate_name"),
        "created_at": _iso(o.get("created_at")), "updated_at": _iso(o.get("updated_at")),
    }


def candidate_out(o: dict[str, Any], company: str | None) -> dict[str, Any]:
    """What the candidate reads: their offer, nothing about how it was approved."""
    return {
        "status": o["status"], "company": company, "job_title": o["job_title"],
        "employment_type": o["employment_type"],
        "start_date": o["start_date"].isoformat() if o.get("start_date") else None,
        "location": o["location"], "base_salary": _money(o["base_salary"]),
        "currency": o["currency"], "pay_period": o["pay_period"], "bonus": o["bonus"],
        "equity": o["equity"], "benefits": o["benefits"], "terms": o["terms"],
        "probation_months": o["probation_months"], "notice_period_days": o["notice_period_days"],
        "expires_at": _iso(o.get("expires_at")), "responded_at": _iso(o.get("responded_at")),
        "preboarding_completed_at": _iso(o.get("preboarding_completed_at")),
    }


def _iso(v: datetime | date | None) -> str | None:
    return v.isoformat() if v else None


# ---------------------------------------------------------------------------
# Field validation
# ---------------------------------------------------------------------------
def clean_fields(fields: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    """Validate what HR may set on an offer or template. Words, not constraint names."""
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key in TEXT_LIMITS:
            v = (value or "").strip() or None
            if key == "job_title" and not v:
                raise OfferError(422, "Give the offer a job title.")
            if v and len(v) > TEXT_LIMITS[key]:
                raise OfferError(422, f"The {key.replace('_', ' ')} is at most "
                                      f"{TEXT_LIMITS[key]} characters.")
            out[key] = v
        elif key == "employment_type":
            if value not in EMPLOYMENT:
                raise OfferError(422, "Choose full time, part time, contract or internship.")
            out[key] = value
        elif key == "pay_period":
            if value not in PERIODS:
                raise OfferError(422, "Choose annual, monthly or hourly pay.")
            out[key] = value
        elif key == "currency":
            v = str(value or "").strip().upper()
            if len(v) != 3 or not v.isalpha():
                raise OfferError(422, "Currency is a three-letter code, such as INR.")
            out[key] = v
        elif key == "base_salary":
            try:
                amount = Decimal(str(value))
            except (InvalidOperation, ValueError):
                raise OfferError(422, "The base salary must be a number.") from None
            if amount <= 0 or amount >= Decimal("1e12"):
                raise OfferError(422, "The base salary must be more than zero.")
            out[key] = amount.quantize(Decimal("0.01"))
        elif key == "start_date":
            if value is None:
                out[key] = None
            else:
                d = value if isinstance(value, date) else date.fromisoformat(str(value))
                out[key] = d
        elif key == "probation_months":
            if value is not None and not 0 <= int(value) <= 24:
                raise OfferError(422, "Probation is 0 to 24 months.")
            out[key] = None if value is None else int(value)
        elif key == "notice_period_days":
            if value is not None and not 0 <= int(value) <= 365:
                raise OfferError(422, "The notice period is 0 to 365 days.")
            out[key] = None if value is None else int(value)
        elif key == "valid_days":
            if not 1 <= int(value) <= 60:
                raise OfferError(422, "An offer stays open for 1 to 60 days.")
            out[key] = int(value)
    if not partial and "base_salary" not in out:
        raise OfferError(422, "Set the base salary.")
    return out


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
TEMPLATE_FIELDS = ("name", "employment_type", "currency", "pay_period", "probation_months",
                   "notice_period_days", "benefits", "terms", "valid_days")


def _template_out(r: Any) -> dict[str, Any]:
    return {k: (str(r[k]) if k == "id" else r[k]) for k in ("id", *TEMPLATE_FIELDS)}


async def list_templates(db: AsyncSession, *, company_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text("SELECT * FROM offer_templates WHERE company_id = :c AND deleted_at IS NULL"
                 " ORDER BY lower(name) LIMIT 200"),
            {"c": company_id},
        )
    ).mappings().all()
    return [_template_out(r) for r in rows]


async def save_template(
    db: AsyncSession, *, company_id: uuid.UUID, template_id: uuid.UUID | None,
    fields: dict[str, Any], actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    name = (fields.pop("name", None) or "").strip()
    clean = clean_fields({k: v for k, v in fields.items() if k in TEMPLATE_FIELDS}, partial=True)
    if template_id is None:
        if not 1 <= len(name) <= 120:
            raise OfferError(422, "Give the template a name of up to 120 characters.")
        template_id = uuid.uuid4()
        cols = ["id", "company_id", "name", "created_by_user_id", "updated_by_user_id", *clean]
        params = {"id": template_id, "company_id": company_id, "name": name,
                  "created_by_user_id": actor, "updated_by_user_id": actor, **clean}
        sql = (f"INSERT INTO offer_templates ({', '.join(cols)})"  # nosec B608
               f" VALUES ({', '.join(':' + c for c in cols)})")
        action = "created"
    else:
        exists = await db.scalar(
            text("SELECT 1 FROM offer_templates WHERE id = :i AND company_id = :c"
                 " AND deleted_at IS NULL"),
            {"i": template_id, "c": company_id},
        )
        if not exists:
            raise OfferError(404, "Template not found.")
        if name:
            if len(name) > 120:
                raise OfferError(422, "A template name is at most 120 characters.")
            clean["name"] = name
        sets = ", ".join(f"{c} = :{c}" for c in clean)
        sql = (f"UPDATE offer_templates SET {sets}{', ' if sets else ''}"  # nosec B608
               "updated_by_user_id = :actor, updated_at = now() WHERE id = :tid")
        params = {**clean, "actor": actor, "tid": template_id}
        action = "updated"
    # The column names come from TEMPLATE_FIELDS, never from the request.
    try:
        sp = await db.begin_nested()
        await db.execute(text(sql), params)
        await sp.commit()
    except IntegrityError as exc:
        await sp.rollback()
        if "uq_offer_templates_name" in str(exc.orig):
            raise OfferError(409, "A template with that name already exists.") from exc
        raise
    db.add(AuditLog(actor_id=actor, actor_type="user", action=f"offer_template.{action}",
                    resource_type="offer_template", resource_id=template_id,
                    details={"company_id": str(company_id), "fields": sorted(clean)},
                    ip_address=meta.ip_address, user_agent=meta.user_agent,
                    event_ts=datetime.now(tz=UTC)))
    row = (await db.execute(text("SELECT * FROM offer_templates WHERE id = :i"),
                            {"i": template_id})).mappings().one()
    return _template_out(row)


async def delete_template(db: AsyncSession, *, company_id: uuid.UUID, template_id: uuid.UUID,
                          actor: uuid.UUID, meta: RequestMeta) -> None:
    gone = await db.scalar(
        text("UPDATE offer_templates SET deleted_at = now(), updated_by_user_id = :a"
             " WHERE id = :i AND company_id = :c AND deleted_at IS NULL RETURNING id"),
        {"i": template_id, "c": company_id, "a": actor},
    )
    if gone is None:
        raise OfferError(404, "Template not found.")
    db.add(AuditLog(actor_id=actor, actor_type="user", action="offer_template.deleted",
                    resource_type="offer_template", resource_id=template_id,
                    details={"company_id": str(company_id)}, ip_address=meta.ip_address,
                    user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC)))


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
_OFFER_SQL = """
SELECT o.*, a.full_name AS candidate_name, a.email AS candidate_email,
       a.user_id AS candidate_user_id, co.name AS company_name
  FROM offers o
  JOIN applicants a ON a.id = o.applicant_id
  JOIN companies co ON co.id = o.company_id
 WHERE o.id = :o AND o.company_id = :c
"""


async def _load(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID,
                lock: bool = True) -> dict[str, Any]:
    sql = _OFFER_SQL + (" FOR UPDATE OF o" if lock else "")
    row = (await db.execute(text(sql), {"o": offer_id, "c": company_id})).mappings().first()
    if row is None:
        raise OfferError(404, "Offer not found.")
    return dict(row)


async def get_offer(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID) -> dict[str, Any]:
    return offer_out(await _load(db, company_id=company_id, offer_id=offer_id, lock=False))


async def offers_for_enrolment(db: AsyncSession, *, company_id: uuid.UUID,
                               enrolment_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text("SELECT o.*, a.full_name AS candidate_name FROM offers o"
                 " JOIN applicants a ON a.id = o.applicant_id"
                 " WHERE o.enrolment_id = :e AND o.company_id = :c ORDER BY o.created_at DESC"
                 " LIMIT 20"),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().all()
    return [offer_out(dict(r)) for r in rows]


async def history(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text("SELECT e.action, e.actor_type, e.created_at, e.details,"
                 "       COALESCE(u.full_name, u.email) AS actor_name"
                 "  FROM offer_events e LEFT JOIN users u ON u.id = e.actor_user_id"
                 " WHERE e.offer_id = :o AND e.company_id = :c ORDER BY e.created_at"),
            {"o": offer_id, "c": company_id},
        )
    ).mappings().all()
    return [{"action": r["action"], "actor_type": r["actor_type"], "actor_name": r["actor_name"],
             "at": r["created_at"].isoformat(), "details": r["details"]} for r in rows]


# ---------------------------------------------------------------------------
# HR: write, submit, send, withdraw
# ---------------------------------------------------------------------------
async def create_offer(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID,
    template_id: uuid.UUID | None, fields: dict[str, Any], actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    e = (
        await db.execute(
            text(
                "SELECT e.id, e.status, e.applicant_id, e.requisition_id,"
                "       COALESCE(jr.title, e.target_job_title) AS job_title,"
                "       ((a.full_name = '[redacted]' AND a.email IS NULL)"
                "        OR EXISTS (SELECT 1 FROM erasure_requests er"
                "                    WHERE er.user_id = a.user_id)) AS candidate_erased"
                "  FROM enrolments e JOIN applicants a ON a.id = e.applicant_id"
                "  LEFT JOIN job_requisitions jr ON jr.id = e.requisition_id"
                " WHERE e.id = :e AND e.company_id = :c AND e.deleted_at IS NULL FOR SHARE OF e"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().first()
    if e is None:
        raise OfferError(404, "Application not found.")
    if e["candidate_erased"]:
        raise OfferError(409, "This candidate's personal data has been erased or is being "
                              "erased, so no offer can be made.")
    if e["status"] != "hired":
        raise OfferError(409, "An offer follows a hiring decision. Record the decision to hire "
                              "first, from the decision queue.")
    base: dict[str, Any] = {"job_title": e["job_title"]}
    if template_id is not None:
        tpl = (
            await db.execute(
                text("SELECT * FROM offer_templates WHERE id = :t AND company_id = :c"
                     " AND deleted_at IS NULL"),
                {"t": template_id, "c": company_id},
            )
        ).mappings().first()
        if tpl is None:
            raise OfferError(404, "Template not found.")
        base.update({k: tpl[k] for k in TEMPLATE_FIELDS if k != "name" and tpl[k] is not None})
    clean = clean_fields({**base, **fields}, partial=False)
    offer_id = uuid.uuid4()
    cols = ["id", "company_id", "enrolment_id", "applicant_id", "requisition_id", "template_id",
            "created_by_user_id", *clean]
    params = {"id": offer_id, "company_id": company_id, "enrolment_id": enrolment_id,
              "applicant_id": e["applicant_id"], "requisition_id": e["requisition_id"],
              "template_id": template_id, "created_by_user_id": actor, **clean}
    sp = await db.begin_nested()
    try:
        await db.execute(
            text(f"INSERT INTO offers ({', '.join(cols)})"  # nosec B608
                 f" VALUES ({', '.join(':' + c for c in cols)})"),
            params,
        )
        await sp.commit()
    except IntegrityError as exc:
        await sp.rollback()
        if "uq_offers_live_per_enrolment" in str(exc.orig):
            raise OfferError(409, "This application already has an offer in progress.") from exc
        raise
    offer = await _load(db, company_id=company_id, offer_id=offer_id)
    await _record(db, offer=offer, action="created", actor=actor, meta=meta,
                  details={"from_template": template_id is not None})
    return offer_out(offer)


async def update_offer(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID,
                       fields: dict[str, Any], actor: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    offer = await _load(db, company_id=company_id, offer_id=offer_id)
    if offer["status"] not in EDITABLE:
        raise OfferError(409, f"This offer is {offer['status'].replace('_', ' ')}; it can be "
                              "changed only as a draft or after it was sent back.")
    clean = clean_fields({k: v for k, v in fields.items() if k in CONTENT_FIELDS}, partial=True)
    if not clean:
        return offer_out(offer)
    sets = ", ".join(f"{c} = :{c}" for c in clean)
    await db.execute(
        text(f"UPDATE offers SET {sets}, updated_at = now() WHERE id = :oid"),  # nosec B608
        {**clean, "oid": offer_id},
    )
    await _record(db, offer=offer, action="updated", actor=actor, meta=meta,
                  details={"fields": sorted(clean)})
    return offer_out(await _load(db, company_id=company_id, offer_id=offer_id))


async def _super_admins(db: AsyncSession, company_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        await db.execute(
            text("SELECT u.id FROM users u JOIN user_roles ur ON ur.user_id = u.id"
                 " JOIN roles r ON r.id = ur.role_id AND r.name = 'super_admin'"
                 " WHERE u.company_id = :c AND u.is_active AND u.deleted_at IS NULL"),
            {"c": company_id},
        )
    ).all()
    return [r[0] for r in rows]


async def submit(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID,
                 actor: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    offer = await _load(db, company_id=company_id, offer_id=offer_id)
    if offer["status"] not in EDITABLE:
        raise OfferError(409, "Only a draft, or an offer that was sent back, can be submitted.")
    await db.execute(
        text("UPDATE offers SET status = 'pending_approval', submitted_by_user_id = :u,"
             " submitted_at = now(), decided_by_user_id = NULL, decided_at = NULL,"
             " updated_at = now() WHERE id = :o"),
        {"u": actor, "o": offer_id},
    )
    await _record(db, offer=offer, action="submitted", actor=actor, meta=meta)
    for admin in await _super_admins(db, company_id):
        await create_notification(
            db, user_id=admin, kind="offer_review",
            title=f"Offer to approve: {offer['job_title']}",
            body=f"{offer['candidate_name']} · {offer['currency']} {_money(offer['base_salary'])}",
            link=f"/superadmin/offers/{offer_id}",
        )
    return offer_out(await _load(db, company_id=company_id, offer_id=offer_id))


async def recall(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID,
                 actor: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    offer = await _load(db, company_id=company_id, offer_id=offer_id)
    if offer["status"] != "pending_approval":
        raise OfferError(409, "Only an offer awaiting approval can be recalled.")
    await db.execute(text("UPDATE offers SET status = 'draft', updated_at = now() WHERE id = :o"),
                     {"o": offer_id})
    await _record(db, offer=offer, action="recalled", actor=actor, meta=meta)
    return offer_out(await _load(db, company_id=company_id, offer_id=offer_id))


async def reopen(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID,
                 actor: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    """An approved offer back to draft: changing it needs approving again."""
    offer = await _load(db, company_id=company_id, offer_id=offer_id)
    if offer["status"] != "approved":
        raise OfferError(409, "Only an approved offer that has not been sent can be reopened.")
    await db.execute(
        text("UPDATE offers SET status = 'draft', decided_by_user_id = NULL, decided_at = NULL,"
             " updated_at = now() WHERE id = :o"),
        {"o": offer_id},
    )
    await _record(db, offer=offer, action="reopened", actor=actor, meta=meta)
    return offer_out(await _load(db, company_id=company_id, offer_id=offer_id))


async def decide(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID, approve: bool,
                 note: str | None, reviewer: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    """The super admin approves or sends back. Never their own (D4-2)."""
    offer = await _load(db, company_id=company_id, offer_id=offer_id)
    if offer["status"] != "pending_approval":
        raise OfferError(409, "This offer is not awaiting approval.")
    if reviewer in (offer["submitted_by_user_id"], offer["created_by_user_id"]):
        raise OfferError(403, "An offer is approved by someone other than the person who wrote "
                              "or submitted it.")
    clean = (note or "").strip() or None
    if not approve and (clean is None or len(clean) < 10):
        raise OfferError(422, "Say what needs to change — at least 10 characters.")
    if clean and len(clean) > 1000:
        raise OfferError(422, "A note is at most 1000 characters.")
    await db.execute(
        text("UPDATE offers SET status = :s, decided_by_user_id = :u, decided_at = now(),"
             " approval_note = :n, updated_at = now() WHERE id = :o"),
        {"s": "approved" if approve else "rejected", "u": reviewer, "n": clean, "o": offer_id},
    )
    action = "approved" if approve else "rejected"
    await _record(db, offer=offer, action=action, actor=reviewer, meta=meta,
                  details={"has_note": bool(clean), "note_chars": len(clean or "")})
    for who in {offer["submitted_by_user_id"], offer["created_by_user_id"]} - {None}:
        await create_notification(
            db, user_id=who, kind="offer_review",
            title=f"Offer {'approved' if approve else 'sent back'}: {offer['job_title']}",
            body=offer["candidate_name"] or "", link="/hr/offers",
        )
    return offer_out(await _load(db, company_id=company_id, offer_id=offer_id))


async def pending_for_company(db: AsyncSession, *, company_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text("SELECT o.*, a.full_name AS candidate_name FROM offers o"
                 " JOIN applicants a ON a.id = o.applicant_id"
                 " WHERE o.company_id = :c AND o.status = 'pending_approval'"
                 " ORDER BY o.submitted_at LIMIT 200"),
            {"c": company_id},
        )
    ).mappings().all()
    return [offer_out(dict(r)) for r in rows]


async def _email_candidate(db: AsyncSession, offer: dict[str, Any], template: str,
                           ctx: dict[str, Any], key: str) -> None:
    lang = await candidate_language(db, offer["applicant_id"])
    await enqueue_email(
        db, to=offer["candidate_email"], template=template, lang=lang,
        ctx={"name": offer["candidate_name"], "job_title": offer["job_title"], **ctx},
        to_user_id=offer["candidate_user_id"], company_id=offer["company_id"],
        related_kind="offer", related_id=offer["id"], dedupe_key=key,
    )


def offer_link(raw_token: str) -> str:
    return f"{settings.app_base_url.rstrip('/')}/offer#{raw_token}"


async def send(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID,
               actor: uuid.UUID, meta: RequestMeta, again: bool = False) -> dict[str, Any]:
    """Send an approved offer — or, with ``again``, send a fresh link for a sent
    one (the old link stops working)."""
    offer = await _load(db, company_id=company_id, offer_id=offer_id)
    if not offer["candidate_email"]:
        raise OfferError(409, "This candidate has no email address to send the offer to.")
    raw = mint_offer_token()
    now = datetime.now(tz=UTC)
    if again:
        if offer["status"] not in ("sent", "accepted") or offer["preboarding_completed_at"]:
            raise OfferError(409, "Only an offer out with the candidate, or accepted and still "
                                  "in preboarding, can be re-sent.")
        # A new link, the old one retired, and the wrong-code count cleared — the
        # way back from a lock (security review M1).
        await db.execute(
            text("UPDATE offers SET token_hash = :h, code_failures = 0, updated_at = now()"
                 " WHERE id = :o"),
            {"h": hash_offer_token(raw), "o": offer_id},
        )
        await db.execute(text("DELETE FROM offer_sessions WHERE offer_id = :o"), {"o": offer_id})
        expires = offer["expires_at"]
    else:
        if offer["status"] != "approved":
            raise OfferError(409, "Only an approved offer can be sent. Submit it for approval "
                                  "first.")
        expires = now + timedelta(days=int(offer["valid_days"]))
        await db.execute(
            text("UPDATE offers SET status = 'sent', token_hash = :h, sent_at = :n,"
                 " sent_by_user_id = :u, expires_at = :x, updated_at = :n WHERE id = :o"),
            {"h": hash_offer_token(raw), "n": now, "u": actor, "x": expires, "o": offer_id},
        )
    await _record(db, offer=offer, action="resent" if again else "sent", actor=actor, meta=meta,
                  details={"expires_at": expires.isoformat()})
    await _email_candidate(
        db, offer, "offer_ready",
        {"company": offer["company_name"], "offer_url": offer_link(raw),
         "expires": expires.strftime("%d %b %Y"), "resent": again},
        key=f"offer:{offer_id}:{'resent:' + now.strftime('%Y%m%d%H%M%S') if again else 'sent'}",
    )
    return offer_out(await _load(db, company_id=company_id, offer_id=offer_id))


async def _set_outcome(db: AsyncSession, offer: dict[str, Any], outcome: str) -> None:
    """The offer's outcome, beside the decision — never the status or the ledger."""
    await db.execute(
        text("UPDATE enrolments SET offer_outcome = :o, updated_at = now() WHERE id = :e"),
        {"o": outcome, "e": offer["enrolment_id"]},
    )


async def withdraw(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID,
                   reason: str | None, actor: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    offer = await _load(db, company_id=company_id, offer_id=offer_id)
    if offer["status"] not in BEFORE_ANSWER:
        raise OfferError(409, f"This offer is {offer['status']}; it can no longer be withdrawn.")
    why = (reason or "").strip() or None
    if why and len(why) > 1000:
        raise OfferError(422, "A reason is at most 1000 characters.")
    was_sent = offer["status"] == "sent"
    await db.execute(
        text("UPDATE offers SET status = 'withdrawn', withdrawn_by_user_id = :u,"
             " withdrawn_at = now(), withdraw_reason = :r, updated_at = now() WHERE id = :o"),
        {"u": actor, "r": why, "o": offer_id},
    )
    await _record(db, offer=offer, action="withdrawn", actor=actor, meta=meta,
                  details={"was_sent": was_sent, "has_reason": bool(why),
                           "reason_chars": len(why or "")})
    if was_sent:
        await _set_outcome(db, offer, "offer_withdrawn")
        await _email_candidate(db, offer, "offer_update",
                               {"company": offer["company_name"], "kind": "withdrawn"},
                               key=f"offer:{offer_id}:withdrawn")
    return offer_out(await _load(db, company_id=company_id, offer_id=offer_id))


# ---------------------------------------------------------------------------
# The candidate's side — reached only by the link
# ---------------------------------------------------------------------------
_BY_TOKEN_SQL = """
SELECT o.*, a.full_name AS candidate_name, a.email AS candidate_email,
       a.user_id AS candidate_user_id, co.name AS company_name,
       e.status AS enrolment_status
  FROM offers o
  JOIN applicants a ON a.id = o.applicant_id
  JOIN companies co ON co.id = o.company_id
  JOIN enrolments e ON e.id = o.enrolment_id
 WHERE o.token_hash = :h AND o.redacted_at IS NULL
 FOR UPDATE OF o
"""
NOT_AVAILABLE = "This offer link isn't available."


async def by_token(db: AsyncSession, raw: str | None) -> dict[str, Any]:
    """The offer a link opens, or one 404 for every failure — no oracle."""
    if not raw or len(raw) > 200:
        raise OfferError(404, NOT_AVAILABLE)
    row = (await db.execute(text(_BY_TOKEN_SQL), {"h": hash_offer_token(raw)})).mappings().first()
    if row is None:
        raise OfferError(404, NOT_AVAILABLE)
    o = dict(row)
    now = datetime.now(tz=UTC)
    grace = timedelta(days=settings.offer_link_grace_days)
    if o["status"] not in ("sent", "accepted", "declined", "expired", "withdrawn"):
        raise OfferError(404, NOT_AVAILABLE)
    # A hire reversed by a rejection ends everything the offer opened.
    if o["status"] == "accepted" and o["enrolment_status"] == "rejected":
        raise OfferError(404, NOT_AVAILABLE)
    ended = o["preboarding_completed_at"] or (
        o["responded_at"] if o["status"] == "declined" else None
    ) or o["withdrawn_at"] or (o["expires_at"] if o["status"] == "expired" else None)
    if ended is not None and now > ended + grace:
        raise OfferError(404, NOT_AVAILABLE)
    # An acceptance whose preboarding never completes does not keep the link
    # alive for ever (security review H1/M2): it lasts the document-retention
    # period from the acceptance, the period after which its files are purged.
    if (o["status"] == "accepted" and o["preboarding_completed_at"] is None
            and o["responded_at"] is not None
            and now > o["responded_at"] + timedelta(days=settings.preboarding_document_retention_days)):
        raise OfferError(404, NOT_AVAILABLE)
    return o


async def candidate_view(db: AsyncSession, *, raw: str | None, meta: RequestMeta) -> dict[str, Any]:
    offer = await by_token(db, raw)
    if offer["status"] == "sent" and offer["expires_at"] <= datetime.now(tz=UTC):
        await _expire(db, offer)
        offer = await by_token(db, raw)
    recent = await db.scalar(
        text("SELECT 1 FROM offer_events WHERE offer_id = :o AND action = 'viewed'"
             " AND created_at > now() - interval '1 hour' LIMIT 1"),
        {"o": offer["id"]},
    )
    if not recent:
        await db.execute(
            text("UPDATE offers SET first_viewed_at = COALESCE(first_viewed_at, now())"
                 " WHERE id = :o"),
            {"o": offer["id"]},
        )
        await _record(db, offer=offer, action="viewed", actor=offer["candidate_user_id"],
                      meta=meta, actor_type="candidate")
    return candidate_out(offer, offer["company_name"])


async def request_code(db: AsyncSession, *, raw: str | None, purpose: str,
                       meta: RequestMeta) -> dict[str, Any]:
    """A one-time code: to accept, to decline, or to open the documents."""
    if purpose not in ("accept", "decline", "documents"):
        raise OfferError(422, "Choose to accept or decline.")
    offer = await by_token(db, raw)
    if offer["code_failures"] >= MAX_CODE_FAILURES:
        raise OfferError(423, LOCKED)
    if purpose == "documents":
        if offer["status"] != "accepted" or offer["preboarding_completed_at"] is not None:
            raise OfferError(409, "Documents are open only after you accept, until preboarding "
                                  "is complete.")
    elif offer["status"] != "sent":
        raise OfferError(409, "This offer can no longer be answered.")
    elif offer["expires_at"] <= datetime.now(tz=UTC):
        await _expire(db, offer)
        raise OfferError(409, "This offer has expired and can no longer be answered.", keep=True)
    if not offer["candidate_email"]:
        raise OfferError(409, "There is no email address to send a code to.")
    recent = await db.scalar(
        text("SELECT count(*) FROM offer_codes WHERE offer_id = :o AND created_at > :t"),
        {"o": offer["id"], "t": datetime.now(tz=UTC) - CODE_WINDOW},
    )
    if int(recent or 0) >= CODES_PER_WINDOW:
        raise OfferError(429, "Too many codes requested. Please wait a few minutes and try again.")
    code = mint_code()
    await db.execute(
        text("INSERT INTO offer_codes (id, offer_id, purpose, code_hash, expires_at)"
             " VALUES (gen_random_uuid(), :o, :p, :h, now() + make_interval(mins => :m))"),
        {"o": offer["id"], "p": purpose, "h": hash_code(str(offer["id"]), purpose, code),
         "m": settings.offer_code_ttl_minutes},
    )
    await _record(db, offer=offer, action="code_requested", actor=offer["candidate_user_id"],
                  meta=meta, actor_type="candidate", details={"purpose": purpose})
    await _email_candidate(
        db, offer, "offer_code",
        {"company": offer["company_name"], "code": code, "purpose": purpose,
         "minutes": settings.offer_code_ttl_minutes},
        key=f"offer_code:{offer['id']}:{uuid.uuid4().hex}",
    )
    return {"sent": True, "minutes": settings.offer_code_ttl_minutes}


async def _check_code(db: AsyncSession, offer: dict[str, Any], purpose: str, code: str) -> None:
    row = (
        await db.execute(
            text("SELECT id, code_hash, attempts, expires_at FROM offer_codes"
                 " WHERE offer_id = :o AND purpose = :p AND consumed_at IS NULL"
                 " ORDER BY created_at DESC LIMIT 1 FOR UPDATE"),
            {"o": offer["id"], "p": purpose},
        )
    ).mappings().first()
    wrong = "That code is not right, or it has expired. Request a new one."
    if offer["code_failures"] >= MAX_CODE_FAILURES:
        raise OfferError(423, LOCKED)
    if row is None or row["expires_at"] <= datetime.now(tz=UTC):
        raise OfferError(422, wrong)
    if row["attempts"] >= MAX_CODE_ATTEMPTS:
        raise OfferError(429, "Too many attempts with this code. Request a new one.")
    if not codes_match(row["code_hash"], str(offer["id"]), purpose, code or ""):
        # The attempt counts even though the request fails (keep=True: the
        # router commits it) — per code AND over the offer's life (M1).
        await db.execute(text("UPDATE offer_codes SET attempts = attempts + 1 WHERE id = :i"),
                         {"i": row["id"]})
        failures = await db.scalar(
            text("UPDATE offers SET code_failures = code_failures + 1 WHERE id = :o"
                 " RETURNING code_failures"),
            {"o": offer["id"]},
        )
        if failures == MAX_CODE_FAILURES:
            await _tell_hr(db, offer, "Offer locked after repeated wrong codes")
        raise OfferError(422, wrong, keep=True)
    await db.execute(text("UPDATE offer_codes SET consumed_at = now() WHERE id = :i"),
                     {"i": row["id"]})


async def _ensure_candidate_identity(db: AsyncSession, offer: dict[str, Any]) -> uuid.UUID:
    """The person accepting needs an identity the consent ledger and erasure can
    key on. An applicant HR added has none: a guest one is made, as interview
    redemption does."""
    if offer["candidate_user_id"] is not None:
        return uuid.UUID(str(offer["candidate_user_id"]))
    # The same shape public apply gives a guest (security review L3): no
    # company — a candidate is never tenant staff — and the address pattern that
    # activation and account creation recognise and relink to a real account.
    uid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO users (id, email, password_hash, full_name, company_id,"
             " preferred_language, is_active, notify_login_email, must_change_password,"
             " created_at, updated_at)"
             " VALUES (:i, :e, NULL, :n, NULL, 'en', true, false, false, now(), now())"),
        {"i": uid, "e": f"guest+{uid}@applicants.invalid", "n": offer["candidate_name"]},
    )
    await db.execute(
        text("INSERT INTO user_roles (user_id, role_id, assigned_at) VALUES"
             " (:u, (SELECT id FROM roles WHERE name = 'guest_candidate'), now())"),
        {"u": uid},
    )
    await db.execute(
        text("UPDATE applicants SET user_id = :u, updated_at = now() WHERE id = :a"
             " AND user_id IS NULL"),
        {"u": uid, "a": offer["applicant_id"]},
    )
    return uid


async def _record_document_consent(db: AsyncSession, *, user_id: uuid.UUID,
                                   offer: dict[str, Any]) -> None:
    """DPDP: accepting is when the candidate agrees to send identity and other
    documents for preboarding — recorded, with facts only, never re-granted over
    a withdrawal (the same rule public apply follows)."""
    import json  # noqa: PLC0415

    existing = await db.scalar(
        text("SELECT 1 FROM dpdp_consent_ledger WHERE user_id = :u"
             " AND consent_type = 'preboarding_documents' AND purpose = 'onboarding' LIMIT 1"),
        {"u": user_id},
    )
    if existing:
        return
    await db.execute(
        text("INSERT INTO dpdp_consent_ledger (id, user_id, consent_type, granted, granted_at,"
             " purpose, evidence) VALUES (gen_random_uuid(), :u, 'preboarding_documents', true,"
             " now(), 'onboarding', CAST(:ev AS jsonb))"),
        {"u": user_id, "ev": json.dumps({"source": "offer_acceptance",
                                         "offer_id": str(offer["id"]),
                                         "company_id": str(offer["company_id"])})},
    )


async def _tell_hr(db: AsyncSession, offer: dict[str, Any], title: str) -> None:
    for who in {offer["created_by_user_id"], offer["sent_by_user_id"]} - {None}:
        await create_notification(db, user_id=who, kind="offer_update",
                                  title=f"{title}: {offer['candidate_name']}",
                                  body=offer["job_title"], link="/hr/offers")


async def answer(db: AsyncSession, *, raw: str | None, accept: bool, code: str,
                 name: str | None, reason: str | None, meta: RequestMeta) -> dict[str, Any]:
    """Accept or decline, with the emailed code. Final either way."""
    offer = await by_token(db, raw)
    purpose = "accept" if accept else "decline"
    if offer["status"] != "sent":
        raise OfferError(409, f"This offer is already {offer['status']}.")
    if offer["expires_at"] <= datetime.now(tz=UTC):
        await _expire(db, offer)
        raise OfferError(409, "This offer has expired and can no longer be accepted.", keep=True)
    await _check_code(db, offer, purpose, code)
    if accept:
        signed = (name or "").strip()
        if not 2 <= len(signed) <= 200:
            raise OfferError(422, "Type your full name to accept.")
        user_id = await _ensure_candidate_identity(db, offer)
        await db.execute(
            text("UPDATE offers SET status = 'accepted', responded_at = now(),"
                 " accepted_name = :n, updated_at = now() WHERE id = :o"),
            {"n": signed, "o": offer["id"]},
        )
        await _record_document_consent(db, user_id=user_id, offer=offer)
        await _set_outcome(db, offer, "offer_accepted")
        await _record(db, offer=offer, action="accepted", actor=user_id, meta=meta,
                      actor_type="candidate")
        await _tell_hr(db, offer, "Offer accepted")
    else:
        why = (reason or "").strip() or None
        if why and len(why) > 1000:
            raise OfferError(422, "A reason is at most 1000 characters.")
        await db.execute(
            text("UPDATE offers SET status = 'declined', responded_at = now(),"
                 " decline_reason = :r, updated_at = now() WHERE id = :o"),
            {"r": why, "o": offer["id"]},
        )
        await _set_outcome(db, offer, "offer_declined")
        await _record(db, offer=offer, action="declined", actor=offer["candidate_user_id"],
                      meta=meta, actor_type="candidate",
                      details={"has_reason": bool(why), "reason_chars": len(why or "")})
        await _tell_hr(db, offer, "Offer declined")
    fresh = await by_token(db, raw)
    return candidate_out(fresh, fresh["company_name"])


# ---------------------------------------------------------------------------
# Expiry
# ---------------------------------------------------------------------------
async def _expire(db: AsyncSession, offer: dict[str, Any]) -> None:
    """Expire a sent offer past its deadline. Does nothing — no outcome, no
    notice — unless this call is what changed it (code review)."""
    changed = await db.scalar(
        text("UPDATE offers SET status = 'expired', updated_at = now()"
             " WHERE id = :o AND status = 'sent' AND expires_at <= now() RETURNING id"),
        {"o": offer["id"]},
    )
    if changed is None:
        return
    await _set_outcome(db, offer, "offer_expired")
    await _event(db, offer=offer, action="expired", actor_type="system", actor=None)
    await _tell_hr(db, offer, "Offer expired")


_DUE_SQL = """
SELECT o.*, a.full_name AS candidate_name, a.email AS candidate_email,
       a.user_id AS candidate_user_id, co.name AS company_name
  FROM offers o
  JOIN applicants a ON a.id = o.applicant_id
  JOIN companies co ON co.id = o.company_id
 WHERE o.status = 'sent' AND o.expires_at <= now()
 ORDER BY o.expires_at
 LIMIT 200
 FOR UPDATE OF o SKIP LOCKED
"""


async def expire_due(db: AsyncSession) -> int:
    """The sweep: every sent offer past its expiry becomes expired. Caller commits."""
    rows = (await db.execute(text(_DUE_SQL))).mappings().all()
    for r in rows:
        await _expire(db, dict(r))
    return len(rows)


# ---------------------------------------------------------------------------
# The candidate portal
# ---------------------------------------------------------------------------
async def candidate_offers(db: AsyncSession, *, user_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text("SELECT o.*, co.name AS company_name FROM offers o"
                 " JOIN applicants a ON a.id = o.applicant_id"
                 " JOIN companies co ON co.id = o.company_id"
                 " WHERE a.user_id = :u AND o.status IN"
                 " ('sent', 'accepted', 'declined', 'expired', 'withdrawn')"
                 " ORDER BY o.sent_at DESC LIMIT 20"),
            {"u": user_id},
        )
    ).mappings().all()
    return [{"id": str(r["id"]), **candidate_out(dict(r), r["company_name"])} for r in rows]


async def candidate_link(db: AsyncSession, *, user_id: uuid.UUID, offer_id: uuid.UUID,
                         meta: RequestMeta) -> dict[str, Any]:
    """A signed-in candidate opens their offer: a fresh link, the old one retired."""
    row = (
        await db.execute(
            text("SELECT o.*, a.user_id AS candidate_user_id FROM offers o"
                 " JOIN applicants a ON a.id = o.applicant_id"
                 " WHERE o.id = :o AND a.user_id = :u AND o.redacted_at IS NULL"
                 "   AND o.status IN ('sent', 'accepted') FOR UPDATE OF o"),
            {"o": offer_id, "u": user_id},
        )
    ).mappings().first()
    if row is None:
        raise OfferError(404, NOT_AVAILABLE)
    raw = mint_offer_token()
    await db.execute(text("UPDATE offers SET token_hash = :h, updated_at = now() WHERE id = :o"),
                     {"h": hash_offer_token(raw), "o": offer_id})
    await _record(db, offer=dict(row), action="resent", actor=user_id, meta=meta,
                  actor_type="candidate", details={"by": "candidate_portal"})
    return {"url": offer_link(raw)}


# ---------------------------------------------------------------------------
# The preboarding session (security review H1)
# ---------------------------------------------------------------------------
async def open_documents_session(db: AsyncSession, *, raw: str | None, code: str,
                                 meta: RequestMeta) -> dict[str, Any]:
    """Trade a ``documents`` code for an hour-long session token. The token is
    returned to the page, never emailed, and stored only as a hash."""
    offer = await by_token(db, raw)
    if offer["status"] != "accepted" or offer["preboarding_completed_at"] is not None:
        raise OfferError(409, "Documents are open only after you accept, until preboarding is "
                              "complete.")
    await _check_code(db, offer, "documents", code)
    token = mint_offer_token()
    expires = datetime.now(tz=UTC) + timedelta(minutes=SESSION_MINUTES)
    await db.execute(
        text("INSERT INTO offer_sessions (id, offer_id, token_hash, expires_at)"
             " VALUES (gen_random_uuid(), :o, :h, :x)"),
        {"o": offer["id"], "h": hash_session_token(token), "x": expires},
    )
    await _record(db, offer=offer, action="viewed", actor=offer["candidate_user_id"], meta=meta,
                  actor_type="candidate", details={"documents_session": True})
    return {"session_token": token, "expires_at": expires.isoformat()}


async def with_documents_session(db: AsyncSession, *, raw: str | None,
                                 session: str | None) -> dict[str, Any]:
    """The offer, if the link AND a live preboarding session both hold. One
    answer for every failure of either, as for the link alone."""
    offer = await by_token(db, raw)
    if not session or len(session) > 200:
        raise OfferError(401, "Enter the code we emailed you to open your documents.")
    ok = await db.scalar(
        text("SELECT 1 FROM offer_sessions WHERE offer_id = :o AND token_hash = :h"
             " AND expires_at > now()"),
        {"o": offer["id"], "h": hash_session_token(session)},
    )
    if not ok:
        raise OfferError(401, "Enter the code we emailed you to open your documents.")
    if offer["status"] != "accepted":
        raise OfferError(409, "Documents are requested once you accept the offer.")
    return offer
