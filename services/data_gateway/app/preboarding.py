"""Documents and preboarding — PH4-A4.

WHO DOES WHAT
- HR managers say which documents an opening needs (mandatory or optional,
  with or without an expiry date).
- After accepting an offer, the candidate uploads them through their offer link
  (never anyone else's: the link names one offer), sees each one's status, and
  replaces any HR rejects or asks to have replaced.
- HR managers open a document only through a five-minute signed link — every
  open recorded — and verify it, reject it with a reason, or ask for a
  replacement.
- When every mandatory document is verified and in date, HR marks preboarding
  complete (the database refuses it otherwise), and can then prepare a signed
  payload for their HRMS.

WHAT IT NEVER DOES
Change an application's status, or read a document into the API: bytes go
into object storage on the way in, and come out only by signed link.
Nothing here is reachable by an agent. Callers commit.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import document_storage as store
from app.config import settings
from app.interviewer_scorecards import RequestMeta
from app.mailer import candidate_language, enqueue_email
from app.models import AuditLog
from app.notifications_util import create_notification
from app.offer_security import export_key_id, sign_export
from app.offers import OfferError, with_documents_session

log = structlog.get_logger()

DOC_TYPES = ("identity", "address", "education", "employment", "tax", "bank", "photo",
             "medical", "other")
EXPORT_SCHEMA = "anthire.preboarding.v1"


def _iso(v: datetime | date | None) -> str | None:
    return v.isoformat() if v else None


async def _event(db: AsyncSession, *, company_id: Any, offer_id: Any, document_id: Any,
                 action: str, actor_type: str, actor: uuid.UUID | None,
                 details: dict[str, Any] | None = None) -> None:
    await db.execute(
        text("INSERT INTO document_events (id, company_id, offer_id, document_id, action,"
             " actor_type, actor_user_id, details) VALUES (gen_random_uuid(), :c, :o, :d, :a,"
             " :t, :u, CAST(:x AS jsonb))"),
        {"c": company_id, "o": offer_id, "d": document_id, "a": action, "t": actor_type,
         "u": actor, "x": json.dumps(details or {}, default=str)},
    )


def _audit(db: AsyncSession, *, actor: uuid.UUID | None, action: str, resource_id: uuid.UUID,
           details: dict[str, Any], meta: RequestMeta, actor_type: str = "user",
           resource_type: str = "candidate_document") -> None:
    db.add(AuditLog(actor_id=actor, actor_type=actor_type, action=action,
                    resource_type=resource_type, resource_id=resource_id,
                    details=details, ip_address=meta.ip_address, user_agent=meta.user_agent,
                    event_ts=datetime.now(tz=UTC)))


# ---------------------------------------------------------------------------
# Requirements (HR)
# ---------------------------------------------------------------------------
def _req_out(r: Any) -> dict[str, Any]:
    return {"id": str(r["id"]), "name": r["name"], "doc_type": r["doc_type"],
            "description": r["description"], "mandatory": r["mandatory"],
            "requires_expiry": r["requires_expiry"], "position": r["position"]}


async def list_requirements(db: AsyncSession, *, company_id: uuid.UUID,
                            requisition_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text("SELECT * FROM document_requirements WHERE requisition_id = :r"
                 " AND company_id = :c AND deleted_at IS NULL ORDER BY position, created_at"),
            {"r": requisition_id, "c": company_id},
        )
    ).mappings().all()
    return [_req_out(r) for r in rows]


def _clean_requirement(fields: dict[str, Any], *, partial: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "name" in fields or not partial:
        name = (fields.get("name") or "").strip()
        if not 1 <= len(name) <= 120:
            raise OfferError(422, "Name the document, in up to 120 characters.")
        out["name"] = name
    if "doc_type" in fields or not partial:
        if fields.get("doc_type") not in DOC_TYPES:
            raise OfferError(422, "Choose what kind of document this is.")
        out["doc_type"] = fields["doc_type"]
    if "description" in fields:
        d = (fields.get("description") or "").strip() or None
        if d and len(d) > 1000:
            raise OfferError(422, "A description is at most 1000 characters.")
        out["description"] = d
    for flag in ("mandatory", "requires_expiry"):
        if flag in fields:
            out[flag] = bool(fields[flag])
    if "position" in fields:
        out["position"] = int(fields["position"])
    return out


async def add_requirement(db: AsyncSession, *, company_id: uuid.UUID, requisition_id: uuid.UUID,
                          fields: dict[str, Any], actor: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    exists = await db.scalar(
        text("SELECT 1 FROM job_requisitions WHERE id = :r AND company_id = :c"),
        {"r": requisition_id, "c": company_id},
    )
    if not exists:
        raise OfferError(404, "Opening not found.")
    clean = _clean_requirement(fields, partial=False)
    rid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO document_requirements (id, company_id, requisition_id, name, doc_type,"
             " description, mandatory, requires_expiry, position, created_by_user_id,"
             " created_at, updated_at) VALUES (:i, :c, :r, :n, :t, :d, :m, :x, :p, :u, now(), now())"),
        {"i": rid, "c": company_id, "r": requisition_id, "n": clean["name"], "t": clean["doc_type"],
         "d": clean.get("description"), "m": clean.get("mandatory", True),
         "x": clean.get("requires_expiry", False), "p": clean.get("position", 0), "u": actor},
    )
    db.add(AuditLog(actor_id=actor, actor_type="user", action="document_requirement.added",
                    resource_type="document_requirement", resource_id=rid,
                    details={"company_id": str(company_id), "requisition_id": str(requisition_id),
                             "doc_type": clean["doc_type"],
                             "mandatory": clean.get("mandatory", True)},
                    ip_address=meta.ip_address, user_agent=meta.user_agent,
                    event_ts=datetime.now(tz=UTC)))
    row = (await db.execute(text("SELECT * FROM document_requirements WHERE id = :i"),
                            {"i": rid})).mappings().one()
    return _req_out(row)


async def update_requirement(db: AsyncSession, *, company_id: uuid.UUID, requirement_id: uuid.UUID,
                             fields: dict[str, Any], actor: uuid.UUID, meta: RequestMeta,
                             remove: bool = False) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text("SELECT * FROM document_requirements WHERE id = :i AND company_id = :c"
                 " AND deleted_at IS NULL FOR UPDATE"),
            {"i": requirement_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise OfferError(404, "Requirement not found.")
    if remove:
        await db.execute(text("UPDATE document_requirements SET deleted_at = now(),"
                              " updated_at = now() WHERE id = :i"), {"i": requirement_id})
        action, out = "document_requirement.removed", None
    else:
        clean = _clean_requirement(fields, partial=True)
        merged = {**dict(row), **clean}
        await db.execute(
            text("UPDATE document_requirements SET name = :n, doc_type = :t, description = :d,"
                 " mandatory = :m, requires_expiry = :x, position = :p, updated_at = now()"
                 " WHERE id = :i"),
            {"n": merged["name"], "t": merged["doc_type"], "d": merged["description"],
             "m": merged["mandatory"], "x": merged["requires_expiry"], "p": merged["position"],
             "i": requirement_id},
        )
        action, out = "document_requirement.updated", _req_out(merged)
    db.add(AuditLog(actor_id=actor, actor_type="user", action=action,
                    resource_type="document_requirement", resource_id=requirement_id,
                    details={"company_id": str(company_id)}, ip_address=meta.ip_address,
                    user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC)))
    return out


# ---------------------------------------------------------------------------
# The state of an offer's documents
# ---------------------------------------------------------------------------
_CHECKLIST_SQL = """
SELECT r.id AS requirement_id, r.name, r.doc_type, r.description, r.mandatory,
       r.requires_expiry, r.position,
       d.id AS document_id, d.status, d.version, d.original_name, d.content_type,
       d.size_bytes, d.expires_on, d.uploaded_at, d.reviewed_at, d.review_note,
       COALESCE(u.full_name, u.email) AS reviewed_by
  FROM document_requirements r
  LEFT JOIN candidate_documents d
         ON d.requirement_id = r.id AND d.offer_id = :o AND d.superseded_at IS NULL
        AND d.redacted_at IS NULL
  LEFT JOIN users u ON u.id = d.reviewed_by_user_id
 WHERE r.requisition_id = :r AND r.company_id = :c AND r.deleted_at IS NULL
 ORDER BY r.position, r.created_at
"""


def _state(row: Any, today: date) -> str:
    """outstanding | submitted | verified | rejected | replacement_requested | expired."""
    if row["document_id"] is None:
        return "outstanding"
    if row["status"] == "verified" and row["expires_on"] is not None and row["expires_on"] <= today:
        return "expired"
    return str(row["status"])


async def checklist(db: AsyncSession, offer: dict[str, Any], *, for_candidate: bool) -> dict[str, Any]:
    today = datetime.now(tz=UTC).date()
    rows = (
        await db.execute(text(_CHECKLIST_SQL), {"o": offer["id"], "r": offer["requisition_id"],
                                                "c": offer["company_id"]})
    ).mappings().all()
    items = []
    for r in rows:
        state = _state(r, today)
        item = {
            "requirement_id": str(r["requirement_id"]), "name": r["name"],
            "doc_type": r["doc_type"], "description": r["description"],
            "mandatory": r["mandatory"], "requires_expiry": r["requires_expiry"],
            "state": state,
            "document": None if r["document_id"] is None else {
                "id": str(r["document_id"]), "version": r["version"],
                "file_name": r["original_name"], "content_type": r["content_type"],
                "size_bytes": r["size_bytes"], "expires_on": _iso(r["expires_on"]),
                "uploaded_at": _iso(r["uploaded_at"]), "reviewed_at": _iso(r["reviewed_at"]),
                # The reason is for the candidate to act on; who reviewed is HR's.
                "review_note": r["review_note"] if state in (
                    "rejected", "replacement_requested") else None,
                **({} if for_candidate else {"reviewed_by": r["reviewed_by"]}),
            },
        }
        items.append(item)
    unresolved = [i for i in items if i["mandatory"] and i["state"] != "verified"]
    return {
        "offer_status": offer["status"],
        "preboarding_completed_at": _iso(offer.get("preboarding_completed_at")),
        "items": items,
        "outstanding": [i["name"] for i in unresolved],
        "complete_ready": offer["status"] == "accepted" and not unresolved,
    }


# ---------------------------------------------------------------------------
# The candidate's side (by offer link)
# ---------------------------------------------------------------------------
async def candidate_checklist(db: AsyncSession, *, raw: str | None,
                              session: str | None) -> dict[str, Any]:
    offer = await with_documents_session(db, raw=raw, session=session)
    return await checklist(db, offer, for_candidate=True)


async def upload(db: AsyncSession, *, raw: str | None, session: str | None,
                 requirement_id: uuid.UUID, data: bytes, filename: str | None,
                 expires_on: date | None, meta: RequestMeta) -> dict[str, Any]:
    """Store one document. Returns ``_storage_key`` for the router, which deletes
    the object again if its commit fails (the rows then do not exist)."""
    offer = await with_documents_session(db, raw=raw, session=session)
    if offer["preboarding_completed_at"] is not None:
        raise OfferError(409, "Your documents are complete; nothing more is needed.")
    # Consent to share documents is recorded at acceptance; once withdrawn
    # (the documents step's own POST /offer/documents/consent/withdraw, or a
    # signed-in DELETE /consent), nothing more is taken.
    consented = await db.scalar(
        text("SELECT bool_or(revoked_at IS NULL) FROM dpdp_consent_ledger"
             " WHERE user_id = :u AND consent_type = 'preboarding_documents' AND granted"),
        {"u": offer["candidate_user_id"]},
    )
    if not consented:
        raise OfferError(409, "You have withdrawn your consent to share documents, so none can "
                              "be uploaded. Contact the hiring team if you want to continue.")
    req = (
        await db.execute(
            text("SELECT * FROM document_requirements WHERE id = :i AND requisition_id = :r"
                 " AND company_id = :c AND deleted_at IS NULL"),
            {"i": requirement_id, "r": offer["requisition_id"], "c": offer["company_id"]},
        )
    ).mappings().first()
    if req is None:
        raise OfferError(404, "That document is not one requested for this offer.")
    if req["requires_expiry"]:
        if expires_on is None:
            raise OfferError(422, "Give the date this document expires.")
        if expires_on <= datetime.now(tz=UTC).date():
            raise OfferError(422, "This document has already expired. Upload a current one.")
    current = (
        await db.execute(
            text("SELECT id, status, version, expires_on FROM candidate_documents"
                 " WHERE offer_id = :o AND requirement_id = :r AND superseded_at IS NULL"
                 " FOR UPDATE"),
            {"o": offer["id"], "r": requirement_id},
        )
    ).mappings().first()
    # A verified document past its expiry date is shown to the candidate as
    # expired, and they may send a current one without waiting to be asked.
    lapsed = (current is not None and current["status"] == "verified"
              and current["expires_on"] is not None
              and current["expires_on"] <= datetime.now(tz=UTC).date())
    if current is not None and current["status"] in ("submitted", "verified") and not lapsed:
        raise OfferError(409, "This document is already with the hiring team. You can replace it "
                              "if they ask you to.")
    try:
        checked = store.check(data, filename, max_bytes=settings.preboarding_document_max_bytes)
    except store.DocumentRejectedError as exc:
        raise OfferError(422, str(exc)) from exc
    doc_id = uuid.uuid4()
    key = store.storage_key(offer["company_id"], offer["id"], doc_id)
    version = (current["version"] + 1) if current else 1
    sp = await db.begin_nested()
    try:
        if current is not None:
            await db.execute(
                text("UPDATE candidate_documents SET superseded_at = now(), superseded_by_id = :n"
                     " WHERE id = :i"),
                {"n": doc_id, "i": current["id"]},
            )
        await db.execute(
            text("INSERT INTO candidate_documents (id, company_id, offer_id, requirement_id,"
                 " enrolment_id, version, storage_key, original_name, content_type, size_bytes,"
                 " sha256, expires_on, uploaded_at) VALUES (:i, :c, :o, :r, :e, :v, :k, :n, :t,"
                 " :s, :h, :x, now())"),
            {"i": doc_id, "c": offer["company_id"], "o": offer["id"], "r": requirement_id,
             "e": offer["enrolment_id"], "v": version, "k": key, "n": checked.safe_name,
             "t": checked.content_type, "s": checked.size_bytes, "h": checked.sha256,
             "x": expires_on if req["requires_expiry"] else None},
        )
        await sp.commit()
    except IntegrityError as exc:
        await sp.rollback()
        # Two first uploads for one document at once: the unique index lets one
        # through; the other is told so, not given a 500 (code review).
        if "uq_candidate_documents_current" in str(exc.orig):
            raise OfferError(409, "This document was just uploaded. Refresh to see it.") from exc
        raise
    # Rows first, bytes second: a failed insert leaves no orphan object. After
    # the bytes are in, anything that fails removes them again.
    try:
        await store.store(settings, key, data, checked.content_type)
    except store.StorageUnavailableError as exc:
        # Raising rolls the request's transaction back, so the row inserted
        # above — and the "superseded" mark on the one it replaced — go with it.
        raise OfferError(
            503, "We could not store your document just now. Please try again in a few minutes."
        ) from exc
    try:
        return await _after_upload(db, offer=offer, req=req, doc_id=doc_id, version=version,
                                   checked=checked, key=key, meta=meta)
    except Exception:
        await store.remove(settings, [key])
        raise


async def _after_upload(db: AsyncSession, *, offer: dict[str, Any], req: Any, doc_id: uuid.UUID,
                        version: int, checked: Any, key: str, meta: RequestMeta) -> dict[str, Any]:
    requirement_id = req["id"]
    facts = {"company_id": str(offer["company_id"]), "offer_id": str(offer["id"]),
             "requirement_id": str(requirement_id), "version": version,
             "content_type": checked.content_type, "size_bytes": checked.size_bytes}
    await _event(db, company_id=offer["company_id"], offer_id=offer["id"], document_id=doc_id,
                 action="uploaded", actor_type="candidate", actor=offer["candidate_user_id"],
                 details={"version": version})
    _audit(db, actor=offer["candidate_user_id"], action="document.uploaded", resource_id=doc_id,
           details=facts, meta=meta, actor_type="system")
    for who in {offer["created_by_user_id"], offer["sent_by_user_id"]} - {None}:
        await create_notification(
            db, user_id=who, kind="document_uploaded",
            title=f"Document to review: {offer['candidate_name']}", body=req["name"],
            link="/hr/offers", dedupe_key=f"doc-uploaded:{doc_id}:{who}",
        )
    # Every upload is told to the candidate's own inbox, so a document someone
    # else supplied through their link is noticed (security review H1).
    lang = await candidate_language(db, offer["applicant_id"])
    await enqueue_email(
        db, to=offer["candidate_email"], template="document_received", lang=lang,
        ctx={"name": offer["candidate_name"], "job_title": offer["job_title"],
             "company": offer["company_name"], "document": req["name"]},
        to_user_id=offer["candidate_user_id"], company_id=offer["company_id"],
        related_kind="candidate_document", related_id=doc_id,
        dedupe_key=f"doc-received:{doc_id}",
    )
    return {"document_id": str(doc_id), "version": version, "state": "submitted",
            "_storage_key": key}


# ---------------------------------------------------------------------------
# HR review
# ---------------------------------------------------------------------------
async def _hr_offer(db: AsyncSession, company_id: uuid.UUID, offer_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await db.execute(
            text("SELECT o.*, a.full_name AS candidate_name, a.email AS candidate_email,"
                 "       a.user_id AS candidate_user_id, co.name AS company_name"
                 "  FROM offers o JOIN applicants a ON a.id = o.applicant_id"
                 "  JOIN companies co ON co.id = o.company_id"
                 " WHERE o.id = :o AND o.company_id = :c FOR UPDATE OF o"),
            {"o": offer_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise OfferError(404, "Offer not found.")
    return dict(row)


async def hr_checklist(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID) -> dict[str, Any]:
    offer = await _hr_offer(db, company_id, offer_id)
    out = await checklist(db, offer, for_candidate=False)
    history = (
        await db.execute(
            text("SELECT e.action, e.actor_type, e.created_at, e.document_id, e.details,"
                 "       COALESCE(u.full_name, u.email) AS actor_name"
                 "  FROM document_events e LEFT JOIN users u ON u.id = e.actor_user_id"
                 " WHERE e.offer_id = :o ORDER BY e.created_at DESC LIMIT 200"),
            {"o": offer_id},
        )
    ).mappings().all()
    out["history"] = [{"action": h["action"], "actor_type": h["actor_type"],
                       "actor_name": h["actor_name"], "at": h["created_at"].isoformat(),
                       "document_id": str(h["document_id"]) if h["document_id"] else None,
                       "details": h["details"]} for h in history]
    out["candidate_name"] = offer["candidate_name"]
    out["job_title"] = offer["job_title"]
    return out


async def review(db: AsyncSession, *, company_id: uuid.UUID, document_id: uuid.UUID, action: str,
                 note: str | None, actor: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    """verify | reject | request_replacement."""
    target = {"verify": "verified", "reject": "rejected",
              "request_replacement": "replacement_requested"}.get(action)
    if target is None:
        raise OfferError(422, "Choose verify, reject or request a replacement.")
    doc = (
        await db.execute(
            text("SELECT d.*, r.name AS requirement_name FROM candidate_documents d"
                 " JOIN document_requirements r ON r.id = d.requirement_id"
                 " WHERE d.id = :d AND d.company_id = :c AND d.redacted_at IS NULL"
                 " FOR UPDATE OF d"),
            {"d": document_id, "c": company_id},
        )
    ).mappings().first()
    if doc is None:
        raise OfferError(404, "Document not found.")
    if doc["superseded_at"] is not None:
        raise OfferError(409, "A newer version of this document has been uploaded.")
    allowed = {"submitted": {"verified", "rejected", "replacement_requested"},
               "verified": {"replacement_requested"}}
    if target not in allowed.get(doc["status"], set()):
        raise OfferError(409, f"This document is {doc['status'].replace('_', ' ')}; it cannot be "
                              f"{target.replace('_', ' ')}.")
    why = (note or "").strip() or None
    if target != "verified" and (why is None or len(why) < 5):
        raise OfferError(422, "Tell the candidate what is wrong, so they can fix it.")
    if why and len(why) > 1000:
        raise OfferError(422, "A reason is at most 1000 characters.")
    if target == "verified" and doc["expires_on"] is not None \
            and doc["expires_on"] <= datetime.now(tz=UTC).date():
        raise OfferError(409, "This document has expired; ask for a current one instead.")
    await db.execute(
        text("UPDATE candidate_documents SET status = :s, reviewed_by_user_id = :u,"
             " reviewed_at = now(), review_note = :n WHERE id = :d"),
        {"s": target, "u": actor, "n": why if target != "verified" else None, "d": document_id},
    )
    await _event(db, company_id=company_id, offer_id=doc["offer_id"], document_id=document_id,
                 action=target, actor_type="user", actor=actor,
                 details={"has_reason": bool(why), "reason_chars": len(why or "")})
    _audit(db, actor=actor, action=f"document.{target}", resource_id=document_id, meta=meta,
           details={"company_id": str(company_id), "offer_id": str(doc["offer_id"]),
                    "has_reason": bool(why), "reason_chars": len(why or "")})
    if target != "verified":
        offer = await _hr_offer(db, company_id, doc["offer_id"])
        lang = await candidate_language(db, offer["applicant_id"])
        await enqueue_email(
            db, to=offer["candidate_email"], template="document_update", lang=lang,
            ctx={"name": offer["candidate_name"], "job_title": offer["job_title"],
                 "company": offer["company_name"], "document": doc["requirement_name"],
                 "reason": why, "kind": target},
            to_user_id=offer["candidate_user_id"], company_id=company_id,
            related_kind="candidate_document", related_id=document_id,
            dedupe_key=f"doc-review:{document_id}:{target}",
        )
    return {"document_id": str(document_id), "state": target}


async def hr_download(db: AsyncSession, *, company_id: uuid.UUID, document_id: uuid.UUID,
                      actor: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    offer_id = await db.scalar(
        text("SELECT offer_id FROM candidate_documents WHERE id = :d AND company_id = :c"),
        {"d": document_id, "c": company_id},
    )
    if offer_id is None:
        raise OfferError(404, "Document not found.")
    offer = await _hr_offer(db, company_id, offer_id)
    return await _signed(db, offer, document_id, actor=actor, actor_type="user", meta=meta)


async def _signed(db: AsyncSession, offer: dict[str, Any], document_id: uuid.UUID, *,
                  actor: uuid.UUID | None, actor_type: str, meta: RequestMeta) -> dict[str, Any]:
    doc = (
        await db.execute(
            text("SELECT storage_key, original_name FROM candidate_documents"
                 " WHERE id = :d AND offer_id = :o AND redacted_at IS NULL"),
            {"d": document_id, "o": offer["id"]},
        )
    ).mappings().first()
    if doc is None or doc["storage_key"] is None:
        raise OfferError(404, "Document not found.")
    url = await store.signed_download(settings, doc["storage_key"], doc["original_name"] or "document")
    await _event(db, company_id=offer["company_id"], offer_id=offer["id"], document_id=document_id,
                 action="downloaded", actor_type=actor_type, actor=actor)
    _audit(db, actor=actor, action="document.downloaded", resource_id=document_id, meta=meta,
           details={"company_id": str(offer["company_id"]), "offer_id": str(offer["id"]),
                    "by": actor_type},
           actor_type="user" if actor_type == "user" else "system")
    return {"url": url, "expires_in": store.PRESIGN_SECONDS}


# ---------------------------------------------------------------------------
# Completion and the HRMS handoff
# ---------------------------------------------------------------------------
async def withdraw_consent(db: AsyncSession, *, raw: str | None, session: str | None,
                           meta: RequestMeta) -> dict[str, Any]:
    """The candidate withdraws their consent to share documents (DPDP §11).

    Reachable with the same credential that authorises an upload — the offer
    link and a live documents session — because most candidates here have no
    account to sign in with: the identity made at acceptance is claimed by
    email, and may never be. Nothing already sent is deleted by this; it stops
    anything further being taken (`upload` refuses next), and the hiring team
    is told so they can talk to the candidate. Caller commits.
    """
    offer = await with_documents_session(db, raw=raw, session=session)
    revoked = (
        await db.execute(
            text("UPDATE dpdp_consent_ledger SET revoked_at = now()"
                 " WHERE user_id = :u AND consent_type = 'preboarding_documents'"
                 "   AND granted AND revoked_at IS NULL RETURNING id"),
            {"u": offer["candidate_user_id"]},
        )
    ).all()
    if not revoked:
        raise OfferError(409, "Your consent to share documents is already withdrawn.")
    _audit(db, actor=offer["candidate_user_id"], action="document.consent_withdrawn",
           resource_id=offer["id"],
           details={"company_id": str(offer["company_id"]), "rows": len(revoked)},
           meta=meta, actor_type="candidate", resource_type="offer")
    for who in {offer["created_by_user_id"], offer["sent_by_user_id"]} - {None}:
        await create_notification(
            db, user_id=who, kind="offer_update",
            title=f"Documents consent withdrawn: {offer['candidate_name']}",
            body="They can send no more documents until they agree again.",
            link=f"/hr/offers/{offer['id']}",
        )
    return {"withdrawn": True}


async def documents_consent_withdrawn_elsewhere(
    db: AsyncSession, *, user_id: uuid.UUID, rows: int, meta: RequestMeta,
) -> None:
    """What a documents-consent withdrawal owes when it came through the
    signed-in ``DELETE /consent`` rather than the documents step
    (``withdraw_consent``): an audit row and a notice to the hiring team, for
    every offer it stops. The consent is held per candidate, so that is every
    accepted offer of theirs still in preboarding. Caller commits."""
    offers = (
        await db.execute(
            text("SELECT o.id, o.company_id, o.created_by_user_id, o.sent_by_user_id,"
                 "       a.full_name AS candidate_name"
                 "  FROM offers o JOIN applicants a ON a.id = o.applicant_id"
                 " WHERE a.user_id = :u AND o.status = 'accepted'"
                 "   AND o.preboarding_completed_at IS NULL"),
            {"u": user_id},
        )
    ).mappings().all()
    for offer in offers:
        _audit(db, actor=user_id, action="document.consent_withdrawn", resource_id=offer["id"],
               details={"company_id": str(offer["company_id"]), "rows": rows,
                        "via": "DELETE /consent"},
               meta=meta, actor_type="candidate", resource_type="offer")
        for who in {offer["created_by_user_id"], offer["sent_by_user_id"]} - {None}:
            await create_notification(
                db, user_id=who, kind="offer_update",
                title=f"Documents consent withdrawn: {offer['candidate_name']}",
                body="They can send no more documents until they agree again.",
                link=f"/hr/offers/{offer['id']}",
            )


async def complete(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID,
                   actor: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    offer = await _hr_offer(db, company_id, offer_id)
    if offer["status"] != "accepted":
        raise OfferError(409, "Preboarding follows an accepted offer.")
    if offer["preboarding_completed_at"] is not None:
        raise OfferError(409, "Preboarding is already complete.")
    state = await checklist(db, offer, for_candidate=False)
    if state["outstanding"]:
        raise OfferError(409, "These mandatory documents are not verified yet: "
                              + ", ".join(state["outstanding"]) + ".")
    sp = await db.begin_nested()
    try:
        await db.execute(
            text("UPDATE offers SET preboarding_completed_at = now(),"
                 " preboarding_completed_by = :u, updated_at = now() WHERE id = :o"),
            {"u": actor, "o": offer_id},
        )
        await sp.commit()
    except DBAPIError as exc:
        await sp.rollback()
        # The database's own check (a document changed under us): say so.
        raise OfferError(409, "A mandatory document is no longer verified. Refresh and check "
                              "again.") from exc
    await db.execute(
        text("INSERT INTO offer_events (id, company_id, offer_id, action, actor_type,"
             " actor_user_id) VALUES (gen_random_uuid(), :c, :o, 'preboarding_completed',"
             " 'user', :u)"),
        {"c": company_id, "o": offer_id, "u": actor},
    )
    db.add(AuditLog(actor_id=actor, actor_type="user", action="offer.preboarding_completed",
                    resource_type="offer", resource_id=offer_id,
                    details={"company_id": str(company_id)}, ip_address=meta.ip_address,
                    user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC)))
    return await hr_checklist(db, company_id=company_id, offer_id=offer_id)


def export_payload(offer: dict[str, Any], documents: list[dict[str, Any]],
                   *, prepared_at: datetime, export_id: uuid.UUID) -> dict[str, Any]:
    """Exactly what an HRMS needs to create the employee — and nothing else: no
    scores, no interview evidence, no document contents or storage keys, no
    reasons anyone wrote. Documents are listed as verified facts."""
    return {
        "schema": EXPORT_SCHEMA,
        "export_id": str(export_id),
        "prepared_at": prepared_at.isoformat(),
        "company": {"id": str(offer["company_id"]), "name": offer["company_name"]},
        "candidate": {"full_name": offer["candidate_name"], "email": offer["candidate_email"]},
        "offer": {
            "id": str(offer["id"]), "job_title": offer["job_title"],
            "employment_type": offer["employment_type"],
            "start_date": _iso(offer["start_date"]), "location": offer["location"],
            "base_salary": f"{offer['base_salary']:.2f}", "currency": offer["currency"],
            "pay_period": offer["pay_period"], "probation_months": offer["probation_months"],
            "notice_period_days": offer["notice_period_days"],
            "accepted_at": _iso(offer["responded_at"]),
        },
        "documents": documents,
        "preboarding_completed_at": _iso(offer["preboarding_completed_at"]),
    }


async def export_to_hrms(db: AsyncSession, *, company_id: uuid.UUID, offer_id: uuid.UUID,
                         actor: uuid.UUID, meta: RequestMeta) -> dict[str, Any]:
    offer = await _hr_offer(db, company_id, offer_id)
    if offer["preboarding_completed_at"] is None:
        raise OfferError(409, "Complete preboarding before preparing the HRMS handoff.")
    docs = (
        await db.execute(
            text("SELECT r.doc_type, r.name, d.version, d.content_type, d.sha256, d.expires_on,"
                 "       d.reviewed_at"
                 "  FROM candidate_documents d JOIN document_requirements r ON r.id = d.requirement_id"
                 " WHERE d.offer_id = :o AND d.superseded_at IS NULL AND d.status = 'verified'"
                 "   AND d.redacted_at IS NULL ORDER BY r.position, r.name"),
            {"o": offer_id},
        )
    ).mappings().all()
    documents = [{"type": d["doc_type"], "name": d["name"], "version": d["version"],
                  "content_type": d["content_type"], "sha256": d["sha256"],
                  "expires_on": _iso(d["expires_on"]), "verified_at": _iso(d["reviewed_at"])}
                 for d in docs]
    export_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    payload = export_payload(offer, documents, prepared_at=now, export_id=export_id)
    signature = sign_export(payload)
    key_id = export_key_id()
    await db.execute(
        text("INSERT INTO hrms_exports (id, company_id, offer_id, payload, signature, key_id,"
             " actor_user_id, created_at) VALUES (:i, :c, :o, CAST(:p AS jsonb), :s, :k, :u, :n)"),
        {"i": export_id, "c": company_id, "o": offer_id, "p": json.dumps(payload), "s": signature,
         "k": key_id, "u": actor, "n": now},
    )
    await db.execute(
        text("INSERT INTO offer_events (id, company_id, offer_id, action, actor_type,"
             " actor_user_id, details) VALUES (gen_random_uuid(), :c, :o, 'exported', 'user',"
             " :u, CAST(:d AS jsonb))"),
        {"c": company_id, "o": offer_id, "u": actor,
         "d": json.dumps({"export_id": str(export_id), "key_id": key_id})},
    )
    db.add(AuditLog(actor_id=actor, actor_type="user", action="offer.hrms_exported",
                    resource_type="offer", resource_id=offer_id,
                    details={"company_id": str(company_id), "export_id": str(export_id),
                             "key_id": key_id, "documents": len(documents)},
                    ip_address=meta.ip_address, user_agent=meta.user_agent, event_ts=now))
    return {"export_id": str(export_id), "algorithm": "HMAC-SHA256", "key_id": key_id,
            "signature": signature, "payload": payload}


# ---------------------------------------------------------------------------
# Retention (DPDP): documents are not kept once they have served
# ---------------------------------------------------------------------------
# Whose documents have served their purpose (security review M2 added the last
# two): the offer ended without an acceptance; preboarding completed; the hire
# was reversed by a rejection; or preboarding never completed at all.
_PURGEABLE_DOCS_SQL = """
SELECT d.id, d.storage_key, d.offer_id, d.company_id
  FROM candidate_documents d
  JOIN offers o ON o.id = d.offer_id
  JOIN enrolments e ON e.id = o.enrolment_id
 WHERE d.redacted_at IS NULL AND d.storage_key IS NOT NULL
   AND (   (o.status IN ('declined', 'withdrawn', 'expired') AND o.updated_at < :cutoff)
        OR (o.preboarding_completed_at IS NOT NULL AND o.preboarding_completed_at < :cutoff)
        OR (o.status = 'accepted' AND o.preboarding_completed_at IS NULL
            AND (e.status = 'rejected' OR o.responded_at < :cutoff)))
 ORDER BY d.uploaded_at
 LIMIT 2000
"""


async def purge_documents(db: AsyncSession, *, retention_days: int, dry_run: bool) -> int:
    """Delete the files, and blank the rows, of documents whose purpose is over
    (_PURGEABLE_DOCS_SQL): ``retention_days`` after the offer ended without an
    acceptance, after preboarding completed (the HRMS holds them from then), or
    after an acceptance whose preboarding never completed; and at the next run
    once a hire is reversed by a rejection before preboarding completed.
    Honours RETENTION_DRY_RUN.

    Files first, rows second, and only on a full count: a storage shortfall
    raises, nothing is committed, and the next nightly run retries the same set
    (the session purge's rule). Caller commits.
    """
    from datetime import timedelta  # noqa: PLC0415

    cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
    rows = (await db.execute(text(_PURGEABLE_DOCS_SQL), {"cutoff": cutoff})).mappings().all()
    if dry_run or not rows:
        log.info("preboarding.retention.candidates", documents=len(rows), dry_run=dry_run)
        return len(rows)
    keys = [r["storage_key"] for r in rows]
    # Plus anything under those offers' prefixes that no row names: an object a
    # failed commit left behind (security review L4).
    for company_id, offer_id in {(r["company_id"], r["offer_id"]) for r in rows}:
        keys += await store.keys_under(settings, store.offer_prefix(company_id, offer_id))
    keys = list(dict.fromkeys(keys))
    removed = await store.remove(settings, keys)
    if removed != len(keys):
        raise RuntimeError(f"preboarding purge removed {removed} of {len(keys)} files; "
                           "nothing redacted, retry next run")
    for r in rows:
        await db.execute(
            text("UPDATE candidate_documents SET storage_key = NULL, original_name = NULL,"
                 " review_note = NULL, redacted_at = now() WHERE id = :i"),
            {"i": r["id"]},
        )
        await _event(db, company_id=r["company_id"], offer_id=r["offer_id"], document_id=r["id"],
                     action="deleted", actor_type="system", actor=None,
                     details={"reason": "retention"})
    log.info("preboarding.retention.purged", documents=len(rows))
    return len(rows)
