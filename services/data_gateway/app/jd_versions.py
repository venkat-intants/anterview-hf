"""Minting, publishing and reading JD versions — PH3-B3.

The one module that writes ``jd_versions``. Everything else calls in here, so
the invariants — monotonic numbering, exactly one published row, the requisition
and its published version never disagreeing — hold by construction rather than
by each caller remembering them.

THE TWO WAYS A VERSION IS MADE

``record_edit`` is the existing behaviour, preserved. ``PATCH /requisitions/{id}``
has always meant "change the advert, now", and a dozen screens use it. It keeps
meaning that; it additionally leaves a version behind. Nothing that worked
before this story stops working.

``save_draft`` / ``publish_version`` are the deliberate path (PH3-B6): edit
privately, review, then go live. A draft is invisible to the public surfaces
because they read the requisition's own columns, which a draft does not touch.

WHAT IS NEVER TOUCHED HERE
``round_criteria``. Group C froze a published workflow's competencies at
authoring time precisely so that editing the advert could not retrospectively
re-grade a candidate who had already sat the round. This module must never
acquire a write to that table; ``tests/unit/test_ph3_jd_versions.py`` asserts it.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:
    from sqlalchemy.engine import CursorResult

log = structlog.get_logger(__name__)

#: The columns that together ARE the job description. ``jd_text`` is the prose;
#: the three lists are equally part of the advert, and a version that captured
#: one without the others would preserve the wrong half.
JD_FIELDS: tuple[str, ...] = (
    "jd_text",
    "responsibilities",
    "required_skills",
    "nice_to_have_skills",
)

#: Of those, the ones stored as jsonb. Bound as a plain Python list, asyncpg
#: sends a Postgres ARRAY and the write fails on a type it cannot cast — so they
#: go over as JSON text with an explicit cast, like every other jsonb write in
#: this service.
_JSON_FIELDS: frozenset[str] = frozenset(JD_FIELDS[1:])

_MAX_CHANGE_NOTE = 500


def touches_jd(fields: dict[str, Any]) -> bool:
    """Whether a requisition patch changes the advert at all.

    A patch that only moves the closing date must not mint a version — a
    history of no-op entries is a history nobody reads.
    """
    return any(f in fields for f in JD_FIELDS)


def _params(content: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (json.dumps(v if v is not None else []) if k in _JSON_FIELDS else v)
        for k, v in content.items()
    }


def _content_sql(prefix: str = "") -> str:
    """``jd_text = :jd_text, responsibilities = CAST(:responsibilities AS jsonb), …``"""
    return ", ".join(
        f"{f} = CAST(:{prefix}{f} AS jsonb)" if f in _JSON_FIELDS else f"{f} = :{prefix}{f}"
        for f in JD_FIELDS
    )


async def _current_content(
    db: AsyncSession, *, requisition_id: uuid.UUID, company_id: uuid.UUID
) -> dict[str, Any]:
    """The live advert, as it stands on the requisition right now."""
    row = (
        await db.execute(
            text(
                # SAFE: JD_FIELDS is a module-level tuple of literal
                # column names (see the top of this file). Nothing a caller
                # supplies reaches this string; the two bound values are
                # parameters.
                f"SELECT {', '.join(JD_FIELDS)} FROM job_requisitions"  # nosec B608
                " WHERE id = :i AND company_id = :c"
            ),
            {"i": requisition_id, "c": company_id},
        )
    ).mappings().first()
    return dict(row) if row else dict.fromkeys(JD_FIELDS)


async def _next_version(db: AsyncSession, *, requisition_id: uuid.UUID) -> int:
    """One past the highest number ever used for this requisition.

    From MAX rather than from a count, so a discarded draft leaves a gap instead
    of letting the next version reuse a number a reader has already seen.
    """
    highest = await db.scalar(
        text("SELECT max(version) FROM jd_versions WHERE requisition_id = :r"),
        {"r": requisition_id},
    )
    return int(highest or 0) + 1


def _clean_note(note: str | None) -> str | None:
    if note is None:
        return None
    cleaned = " ".join(note.split())[:_MAX_CHANGE_NOTE]
    return cleaned or None


async def current_published(
    db: AsyncSession, *, requisition_id: uuid.UUID, company_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                "SELECT * FROM jd_versions"
                " WHERE requisition_id = :r AND company_id = :c AND status = 'published'"
            ),
            {"r": requisition_id, "c": company_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def current_draft(
    db: AsyncSession, *, requisition_id: uuid.UUID, company_id: uuid.UUID
) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                "SELECT * FROM jd_versions"
                " WHERE requisition_id = :r AND company_id = :c AND status = 'draft'"
            ),
            {"r": requisition_id, "c": company_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def list_versions(
    db: AsyncSession, *, requisition_id: uuid.UUID, company_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Every version, newest first, with the author's name resolved.

    Includes the full content: a history view that could not show what a
    version actually said would only be a list of dates.
    """
    rows = (
        await db.execute(
            text(
                "SELECT v.*, u.full_name AS created_by_name"
                "  FROM jd_versions v"
                "  LEFT JOIN users u ON u.id = v.created_by_user_id"
                " WHERE v.requisition_id = :r AND v.company_id = :c"
                " ORDER BY v.version DESC"
            ),
            {"r": requisition_id, "c": company_id},
        )
    ).mappings().all()
    return [dict(r) for r in rows]


async def record_edit(
    db: AsyncSession,
    *,
    requisition_id: uuid.UUID,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    change_note: str | None = None,
) -> uuid.UUID | None:
    """Capture the requisition's CURRENT advert as a new published version.

    Called after ``PATCH /requisitions/{id}`` has written the new content, which
    is why it reads the row rather than taking the content as an argument: the
    patch is partial, and reconstructing "what the advert now says" from the
    changed fields alone would produce a version that never existed.

    Returns the new version's id, or None when the advert is empty — an opening
    with no JD at all has no history worth starting.

    The previous published version becomes ``archived`` in the same statement
    order the unique index requires: demote first, then insert. The other way
    round violates ``uq_jd_versions_one_published`` mid-transaction.
    """
    content = await _current_content(
        db, requisition_id=requisition_id, company_id=company_id
    )
    if not _has_content(content):
        return None

    now = datetime.now(tz=UTC)
    await _demote_published(db, requisition_id=requisition_id, now=now)

    version_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO jd_versions (id, company_id, requisition_id, version, status,"
            " jd_text, responsibilities, required_skills, nice_to_have_skills,"
            " change_note, created_by_user_id, created_at, updated_at, published_at)"
            " VALUES (:i,:c,:r,:v,'published',:jd_text,"
            " CAST(:responsibilities AS jsonb), CAST(:required_skills AS jsonb),"
            " CAST(:nice_to_have_skills AS jsonb), :note,:by,:n,:n,:n)"
        ),
        {
            "i": version_id, "c": company_id, "r": requisition_id,
            "v": await _next_version(db, requisition_id=requisition_id),
            "note": _clean_note(change_note), "by": actor_user_id, "n": now,
            **_params(content),
        },
    )
    await db.execute(
        text("UPDATE job_requisitions SET published_jd_version_id = :v WHERE id = :i"),
        {"v": version_id, "i": requisition_id},
    )
    log.info(
        "jd.version_recorded",
        requisition_id=str(requisition_id), version_id=str(version_id),
    )
    return version_id


async def save_draft(
    db: AsyncSession,
    *,
    requisition_id: uuid.UUID,
    company_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
    content: dict[str, Any],
    change_note: str | None = None,
) -> dict[str, Any]:
    """Create or update the requisition's single draft version. Caller commits.

    The draft does NOT touch the requisition's own columns, which is exactly why
    the public surfaces cannot see it: they read the requisition.

    Updating rather than creating a second row is what keeps "the draft"
    singular. A version number is allocated once, when the draft is first
    created, so a recruiter who saves eleven times does not burn eleven numbers
    and make the history look like eleven revisions.
    """
    now = datetime.now(tz=UTC)
    supplied = {f: content.get(f) for f in JD_FIELDS if f in content}
    existing = await current_draft(
        db, requisition_id=requisition_id, company_id=company_id
    )

    if existing is not None:
        merged = {f: supplied.get(f, existing[f]) for f in JD_FIELDS}
        if supplied:
            await db.execute(
                text(
                    # SAFE: _content_sql() is built from JD_FIELDS,
                    # a module-level tuple of literal column names. Every
                    # value is bound as a parameter.
                    f"UPDATE jd_versions SET {_content_sql()},"  # nosec B608
                    " change_note = COALESCE(:note, change_note), updated_at = :n"
                    " WHERE id = :i"
                ),
                {
                    "i": existing["id"], "note": _clean_note(change_note), "n": now,
                    **_params(merged),
                },
            )
        # Returned from what was just written rather than re-read. The row is
        # fully determined here — every value either came from the caller or
        # was already on `existing` — so a second round trip would only be a
        # chance for the two to disagree.
        return {
            **existing, **merged,
            "change_note": _clean_note(change_note) or existing["change_note"],
            "updated_at": now if supplied else existing["updated_at"],
        }

    # A new draft starts from the live advert, so editing one field does not
    # silently blank the rest.
    base = await _current_content(db, requisition_id=requisition_id, company_id=company_id)
    merged = {**base, **supplied}
    draft_id = uuid.uuid4()
    version = await _next_version(db, requisition_id=requisition_id)
    await db.execute(
        text(
            "INSERT INTO jd_versions (id, company_id, requisition_id, version, status,"
            " jd_text, responsibilities, required_skills, nice_to_have_skills,"
            " change_note, created_by_user_id, created_at, updated_at)"
            " VALUES (:i,:c,:r,:v,'draft',:jd_text,"
            " CAST(:responsibilities AS jsonb), CAST(:required_skills AS jsonb),"
            " CAST(:nice_to_have_skills AS jsonb), :note,:by,:n,:n)"
        ),
        {
            "i": draft_id, "c": company_id, "r": requisition_id,
            "v": version,
            "note": _clean_note(change_note), "by": actor_user_id, "n": now,
            **_params(merged),
        },
    )
    return {
        "id": draft_id, "company_id": company_id, "requisition_id": requisition_id,
        "version": version, "status": "draft", **merged,
        "change_note": _clean_note(change_note), "created_by_user_id": actor_user_id,
        "created_at": now, "updated_at": now,
        "published_at": None, "superseded_at": None,
    }


async def discard_draft(
    db: AsyncSession, *, requisition_id: uuid.UUID, company_id: uuid.UUID
) -> bool:
    """Throw away the draft. Its version number is not reused — the gap is the
    record that somebody started a revision and abandoned it."""
    result = await db.execute(
        text(
            "DELETE FROM jd_versions"
            " WHERE requisition_id = :r AND company_id = :c AND status = 'draft'"
        ),
        {"r": requisition_id, "c": company_id},
    )
    # AsyncSession.execute is typed Result[Any]; rowcount is a DBAPI cursor
    # attribute. Same cast the mailer and requisitions modules use.
    return bool(cast("CursorResult[Any]", result).rowcount)


async def publish_version(
    db: AsyncSession,
    *,
    requisition_id: uuid.UUID,
    company_id: uuid.UUID,
    version_id: uuid.UUID,
    actor_user_id: uuid.UUID | None,
) -> dict[str, Any]:
    """Make one version the live advert. Caller commits.

    Works for a draft (the normal case) and for an archived version (a revert,
    which the doc's "historical versions cannot be accidentally overwritten"
    requires to be a forward action rather than an edit of the past).

    Three writes, one transaction: demote whatever is published, promote this
    one, copy its content onto the requisition. The requisition and its
    published version can never be observed disagreeing.
    """
    row = (
        await db.execute(
            text(
                "SELECT * FROM jd_versions"
                " WHERE id = :i AND requisition_id = :r AND company_id = :c"
            ),
            {"i": version_id, "r": requisition_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise LookupError("no such JD version for this requisition")
    if row["status"] == "published":
        return dict(row)

    now = datetime.now(tz=UTC)
    await _demote_published(db, requisition_id=requisition_id, now=now)
    await db.execute(
        text(
            "UPDATE jd_versions"
            "   SET status = 'published', published_at = :n, superseded_at = NULL,"
            "       updated_at = :n"
            " WHERE id = :i"
        ),
        {"i": version_id, "n": now},
    )
    # The live document follows the published version, always in the same
    # transaction. This is the copy that keeps every existing reader working.
    await db.execute(
        text(
            # SAFE: _content_sql() is built from JD_FIELDS, a
            # module-level tuple of literal column names. Every value is
            # bound as a parameter.
            f"UPDATE job_requisitions SET {_content_sql()},"  # nosec B608
            " published_jd_version_id = :v, updated_at = :n"
            " WHERE id = :i AND company_id = :c"
        ),
        {
            "i": requisition_id, "c": company_id, "v": version_id, "n": now,
            **_params({f: row[f] for f in JD_FIELDS}),
        },
    )
    log.info(
        "jd.version_published",
        requisition_id=str(requisition_id), version_id=str(version_id),
        version=int(row["version"]), actor=str(actor_user_id) if actor_user_id else None,
    )
    published = (
        await db.execute(text("SELECT * FROM jd_versions WHERE id = :i"), {"i": version_id})
    ).mappings().first()
    return dict(published) if published else dict(row)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
async def _demote_published(
    db: AsyncSession, *, requisition_id: uuid.UUID, now: datetime
) -> None:
    """Archive whatever is currently published, stamping when it stopped being.

    Must run BEFORE the new row is published: ``uq_jd_versions_one_published``
    is checked per statement, so promoting first would collide.
    """
    await db.execute(
        text(
            "UPDATE jd_versions"
            "   SET status = 'archived', superseded_at = :n, updated_at = :n"
            " WHERE requisition_id = :r AND status = 'published'"
        ),
        {"r": requisition_id, "n": now},
    )


def _has_content(content: dict[str, Any]) -> bool:
    if (content.get("jd_text") or "").strip():
        return True
    return any(content.get(f) for f in _JSON_FIELDS)
