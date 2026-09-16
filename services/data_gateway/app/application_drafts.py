"""Save and resume an application — PH3-B4c, with PH3-B5's confirmation step.

THE ONE RULE THIS MODULE EXISTS TO KEEP
A draft holds a name, an email, a phone number and a CV. That is personal data,
and CLAUDE.md's third hard constraint forbids storing it without a
``dpdp_consent_ledger`` entry. ``public_apply`` has always honoured that by
writing the CV and the ledger row in one transaction; a draft saved before the
consent checkbox would have broken it outright.

So consent is taken at the FIRST SAVE. :func:`start` refuses without it, mints
the ``guest_candidate`` user the ledger's NOT NULL ``user_id`` requires, and
writes the ledger entry in the same transaction as the draft row. The invariant
reads exactly as it did — no personal data without recorded permission — one
step earlier in the flow.

The ledger entry is the same one a submitted application uses
(``application_data`` / ``recruitment``) and is idempotent per user, so
submitting after drafting neither asks twice nor records twice.

THE TOKEN IS THE ONLY CREDENTIAL
There is no login here. The candidate keeps a resume link containing a 256-bit
opaque token; the database stores only its hash, so a leaked dump does not hand
somebody every half-finished application in the system. The same pattern
``interview_link`` and ``exam_link`` already use.

EXPIRY IS RETENTION
An expired draft holds personal data, so it is purged by the DPDP retention cron
rather than by a bespoke sweep, and ``expires_at`` is written at creation rather
than computed at read time — a later policy change must not retroactively delete
a draft somebody is still working on.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

#: How long a draft survives without being touched. Long enough that a
#: candidate can finish over a weekend, short enough that abandoned personal
#: data does not sit indefinitely. Saving again extends it.
DRAFT_TTL_DAYS = 30

#: Bytes of randomness in the resume token. Same as the interview link's.
_TOKEN_BYTES = 32

#: The fields a candidate may put in a draft. Anything else in the payload is
#: ignored rather than rejected — a client that learns a new field before the
#: server does should not make the whole save fail.
DRAFT_FIELDS: tuple[str, ...] = (
    "full_name",
    "phone",
    "years_experience",
    "current_company",
    "current_title",
    "linkedin_url",
    "github_url",
    "language",
)

_TEXT_LIMITS: dict[str, int] = {
    "full_name": 200,
    "phone": 40,
    "current_company": 200,
    "current_title": 200,
    "linkedin_url": 500,
    "github_url": 500,
}


class DraftError(RuntimeError):
    """A draft could not be created or resumed, with a candidate-safe reason."""


class ConsentRequiredError(DraftError):
    """No consent, no stored personal data. The invariant, as an exception."""


def mint_token() -> tuple[str, str]:
    """Return ``(raw, hash)``. Only the hash is ever stored."""
    raw = secrets.token_urlsafe(_TOKEN_BYTES)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _clean(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(str(value).split())[:limit]
    return cleaned or None


def sanitise(fields: dict[str, Any]) -> dict[str, Any]:
    """Trim and bound whatever the client sent. Never raises.

    Unknown keys are dropped. A draft is allowed to be incomplete — that is
    what a draft is — so nothing here is required and nothing is validated
    against the opening's rules; those apply at submission.
    """
    out: dict[str, Any] = {}
    for field in DRAFT_FIELDS:
        if field not in fields:
            continue
        value = fields[field]
        if field == "years_experience":
            try:
                years = int(value) if value not in (None, "") else None
            except (TypeError, ValueError):
                years = None
            out[field] = None if years is None else max(0, min(60, years))
        elif field == "language":
            out[field] = value if value in ("en", "hi", "te") else "en"
        else:
            out[field] = _clean(value, _TEXT_LIMITS.get(field, 200))
    return out


async def start(
    db: AsyncSession,
    *,
    requisition_id: uuid.UUID,
    company_id: uuid.UUID,
    email: str,
    consent_granted: bool,
    user_id: uuid.UUID,
    source: tuple[str, str | None] = ("direct", None),
    now: datetime | None = None,
) -> tuple[dict[str, Any], str]:
    """Create a draft for this opening. Caller commits.

    ``source`` is the acquisition channel the tracked link carried (PH3-B1),
    already normalised by the caller. Stored on the draft so a link opened three
    weeks ago still attributes the application it eventually becomes.

    ``consent_granted`` is checked HERE and not merely trusted from the caller,
    because this function is the last place before a row carrying a person's
    email is written. Returns ``(draft, raw_token)``; the raw token is returned
    once and never stored.

    ALWAYS CREATES. ``user_id`` must be an identity the caller has just minted,
    never one resolved from user input — see the comment in the body.

    The caller is responsible for having minted ``user_id`` and written the
    consent ledger entry in this same transaction — see the module docstring.
    """
    if not consent_granted:
        raise ConsentRequiredError(
            "We need your permission to store your details before we can save a draft."
        )

    now = now or datetime.now(tz=UTC)
    address = email.strip().lower()[:320]

    # NO LOOKUP, NO REUSE. This function used to find a live draft for
    # (requisition, user) and hand back its contents with a freshly rotated
    # token. Its caller resolved `user_id` from an email in an unauthenticated
    # request body, so that branch let anyone holding the apply link and a
    # candidate's address read and take over that candidate's application.
    #
    # The caller now mints a fresh identity per call, so there is never an
    # existing draft to find — and this function no longer offers a way to
    # reach one even if there were.
    raw, token_hash = mint_token()
    draft_id = uuid.uuid4()
    channel, channel_detail = source
    await db.execute(
        text(
            "INSERT INTO application_drafts"
            " (id, company_id, requisition_id, user_id, token_hash, email,"
            "  source, source_detail, status, expires_at, created_at, updated_at)"
            " VALUES (:i,:c,:r,:u,:h,:e,:src,:srcd,'draft',:exp,:n,:n)"
        ),
        {"i": draft_id, "c": company_id, "r": requisition_id, "u": user_id,
         "h": token_hash, "e": address, "src": channel, "srcd": channel_detail,
         "exp": now + timedelta(days=DRAFT_TTL_DAYS), "n": now},
    )
    log.info(
        "application_draft.started",
        requisition_id=str(requisition_id), company_id=str(company_id),
    )
    return (
        {
            "id": draft_id, "company_id": company_id, "requisition_id": requisition_id,
            "user_id": user_id, "email": address, "status": "draft",
            "expires_at": now + timedelta(days=DRAFT_TTL_DAYS),
            "source": channel, "source_detail": channel_detail,
            "answers": {}, "parsed": {}, "confirmed_at": None,
            "resume_s3_key": None, "resume_filename": None,
            "created_at": now, "updated_at": now,
            **dict.fromkeys(DRAFT_FIELDS),
            "language": "en",
        },
        raw,
    )


async def load(
    db: AsyncSession, *, raw_token: str, now: datetime | None = None
) -> dict[str, Any] | None:
    """The draft this token opens, or None.

    One lookup by hash. Expiry is enforced here as well as by the retention
    cron: the cron runs daily and a link must stop working at its stated time,
    not within a day of it.
    """
    now = now or datetime.now(tz=UTC)
    row = (
        await db.execute(
            text(
                "SELECT d.*, r.title, r.level, c.name AS company_name"
                "  FROM application_drafts d"
                "  JOIN job_requisitions r ON r.id = d.requisition_id"
                "  JOIN companies c ON c.id = d.company_id"
                " WHERE d.token_hash = :h AND d.status = 'draft'"
            ),
            {"h": hash_token(raw_token)},
        )
    ).mappings().first()
    if row is None:
        return None
    if row["expires_at"] <= now:
        return None
    return dict(row)


async def save(
    db: AsyncSession,
    *,
    draft_id: uuid.UUID,
    fields: dict[str, Any],
    answers: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """Write progress. Caller commits.

    Every field is optional and nothing is validated against the opening's
    rules — a draft is allowed to be incomplete. Saving extends the expiry, so
    somebody who comes back weekly does not lose their work to a deadline they
    were never shown.
    """
    now = now or datetime.now(tz=UTC)
    clean = sanitise(fields)
    assignments = [f"{k} = :{k}" for k in clean]
    params: dict[str, Any] = {**clean, "i": draft_id, "n": now,
                              "exp": now + timedelta(days=DRAFT_TTL_DAYS)}
    if answers is not None:
        assignments.append("answers = CAST(:answers AS jsonb)")
        params["answers"] = json.dumps(answers)
    assignments.extend(["updated_at = :n", "expires_at = :exp"])
    await db.execute(
        text(
            # SAFE: `assignments` is built from sanitise()'s output,
            # whose keys are filtered to DRAFT_FIELDS (a module-level tuple of
            # literal column names) before they get here. The candidate's
            # values are bound as parameters, never interpolated.
            f"UPDATE application_drafts SET {', '.join(assignments)}"  # nosec B608
            " WHERE id = :i AND status = 'draft'"
        ),
        params,
    )


async def attach_resume(
    db: AsyncSession,
    *,
    draft_id: uuid.UUID,
    s3_key: str,
    filename: str | None,
    parsed: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Record the uploaded CV and what the parser read out of it. Caller commits.

    ``parsed`` is what PH3-B5 shows the candidate for confirmation. Re-uploading
    clears ``confirmed_at``: a confirmation is about one particular CV, and
    carrying it across a replacement would mean the candidate had confirmed
    something they never saw.
    """
    now = now or datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE application_drafts"
            "   SET resume_s3_key = :k, resume_filename = :f,"
            "       parsed = CAST(:p AS jsonb), confirmed_at = NULL, updated_at = :n"
            " WHERE id = :i AND status = 'draft'"
        ),
        {"k": s3_key, "f": _clean(filename, 255), "p": json.dumps(parsed),
         "n": now, "i": draft_id},
    )


async def confirm(
    db: AsyncSession,
    *,
    draft_id: uuid.UUID,
    corrections: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Record that the candidate reviewed the extracted details — PH3-B5.

    Corrections are written to the draft's own fields, which is what makes them
    the values the application is finally submitted with. The confirmation is a
    timestamp rather than a boolean so "when did they agree to this?" has an
    answer.
    """
    now = now or datetime.now(tz=UTC)
    await save(db, draft_id=draft_id, fields=corrections, now=now)
    await db.execute(
        text(
            "UPDATE application_drafts SET confirmed_at = :n, updated_at = :n"
            " WHERE id = :i AND status = 'draft'"
        ),
        {"n": now, "i": draft_id},
    )
    log.info("application_draft.confirmed", draft_id=str(draft_id))


async def mark_submitted(
    db: AsyncSession, *, draft_id: uuid.UUID, now: datetime | None = None
) -> None:
    """The draft became an application. Caller commits.

    Kept rather than deleted, for the length of the retention window: it is the
    record that the candidate confirmed their details, and deleting it at the
    moment of submission would destroy that at exactly the wrong time.
    """
    now = now or datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE application_drafts"
            "   SET status = 'submitted', submitted_at = :n, updated_at = :n,"
            # The token stops working the moment the draft becomes an
            # application. Nothing here is resumable any more.
            "       token_hash = :h"
            " WHERE id = :i"
        ),
        {"n": now, "i": draft_id, "h": hash_token(f"submitted:{draft_id}")},
    )


async def purge_expired(
    db: AsyncSession, *, now: datetime | None = None, limit: int = 500
) -> list[str]:
    """Delete drafts past their expiry. Returns the S3 keys to clean up.

    Called from the DPDP retention cron rather than a bespoke sweep, because an
    expired draft is personal data past its purpose and that is precisely what
    that cron is for.
    """
    now = now or datetime.now(tz=UTC)
    rows = (
        await db.execute(
            text(
                "DELETE FROM application_drafts"
                " WHERE id IN ("
                "   SELECT id FROM application_drafts"
                "    WHERE status = 'draft' AND expires_at <= :n"
                "    LIMIT :lim)"
                " RETURNING resume_s3_key"
            ),
            {"n": now, "lim": limit},
        )
    ).mappings().all()
    keys = [r["resume_s3_key"] for r in rows if r["resume_s3_key"]]
    if rows:
        log.info("application_draft.purged", drafts=len(rows), objects=len(keys))
    return keys
