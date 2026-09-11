"""Canonical applicant enrichment writes — ATS score and resume embedding.

Both of these used to live as private helpers inside ``routers/hr_applicants``,
which was correct while the upload path was the only caller. The reconciliation
loop (A1) is now a second caller, and the ATS field mapping in particular is the
kind of thing that acquires a new column later: duplicating it would guarantee
that one of the two copies is eventually missing a field, with no test to notice.

So the mapping lives here once. ``hr_applicants`` imports these under their old
private names, which keeps its seven call sites and the existing unit test
working untouched.

Nothing in this module decides *whether* to enrich — it only performs the write.
Callers own the transaction boundary and the retry policy.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Optional

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.embedding_client import to_pgvector_literal
from app.models import Applicant


def apply_ats_score(a: Applicant, score: dict[str, Any]) -> None:
    """Map a scorer response onto the applicant row. Does not commit.

    Single source of truth for the ATS column set: add a field to the scorer and
    it is added here once, for both the upload path and reconciliation.
    """
    a.ats_overall = int(score.get("overall", 0))
    a.ats_breakdown = score.get("breakdown")
    a.ats_strengths = score.get("strengths")
    a.ats_concerns = score.get("concerns")
    a.ats_recommendation = score.get("recommendation")
    a.ats_summary = score.get("summary")
    a.updated_at = datetime.now(tz=UTC)


async def apply_ats_to_enrolment(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    score: dict[str, Any],
    resume_key: str | None,
) -> bool:
    """Write a scorer response onto the ENROLMENT it was produced for. No commit.

    D-06a: an ATS score belongs to one application — the same CV scores
    differently against a Python role and a nursing one — so the enrolment is
    where it lives. ``scored_resume_s3_key`` and ``scored_at`` record exactly
    which CV produced it, so the score stays reproducible after the person
    uploads a newer one.

    Returns True when this is the applicant's most recent live enrolment. The
    caller then mirrors the score onto the applicant row with
    :func:`apply_ats_score`, because the legacy ``applicants.ats_*`` columns
    still feed the pipeline, the applicant list and interview eligibility, and
    they describe the latest application (which is what a returning candidate's
    ``applicants.target_*`` is overwritten to). An older enrolment's re-score
    leaves them alone.
    """
    now = datetime.now(tz=UTC)
    row = (
        await db.execute(
            text(
                "UPDATE enrolments SET ats_overall = :o, ats_breakdown = CAST(:b AS jsonb),"
                " ats_strengths = CAST(:s AS jsonb), ats_concerns = CAST(:c AS jsonb),"
                " ats_recommendation = :r, ats_summary = :su,"
                " scored_resume_s3_key = :k, scored_at = :n, updated_at = :n"
                " WHERE id = :e AND deleted_at IS NULL"
                " RETURNING applicant_id, created_at"
            ),
            {"o": int(score.get("overall", 0)), "b": _json(score.get("breakdown")),
             "s": _json(score.get("strengths")), "c": _json(score.get("concerns")),
             "r": score.get("recommendation"), "su": score.get("summary"),
             "k": resume_key, "n": now, "e": enrolment_id},
        )
    ).first()
    if row is None:
        return False
    newer = await db.scalar(
        text(
            "SELECT 1 FROM enrolments WHERE applicant_id = :a AND deleted_at IS NULL"
            " AND id <> :e AND created_at > :t LIMIT 1"
        ),
        {"a": row[0], "e": enrolment_id, "t": row[1]},
    )
    return newer is None


def _json(value: Any) -> str | None:
    import json  # noqa: PLC0415 — only needed on this write path

    return json.dumps(value) if value is not None else None


# Name sources a PERSON is responsible for. The scorer may not overwrite these.
# NULL is absent on purpose: rows predating ``full_name_source`` all arrived
# through bulk upload, so treating them as filename placeholders preserves the
# behaviour they have today rather than freezing a name nobody typed.
AUTHORED_NAME_SOURCES: frozenset[str] = frozenset({"candidate", "hr"})


def apply_extracted_identity(
    a: Applicant, score: dict[str, Any], *, email_taken: bool = False
) -> bool:
    """Fill in the name and email the scorer read out of the PDF. No commit.

    ``email_taken`` — the caller found the extracted address already belongs to
    another live applicant in this company (B4). It then goes to
    ``parsed_email`` instead: as ``email`` it would make a second applicant for
    one person (and break the unique index), and dropping it would hide the
    match the review screen needs to propose a merge.

    Only for a row still flagged ``pending_enrichment`` — i.e. one stored
    without being read.

    FILLS, NEVER REPLACES — for the name as well as the email now.

    The email branch was always careful: it writes only when ``a.email is
    None``, because an address read off a PDF is a guess and a person's own
    answer is not. The name branch was not, and overwrote ``full_name``
    outright. That was correct while the only way in was HR's bulk upload,
    where the name is derived from a filename and the parsed name is strictly
    better — and it quietly became wrong when candidates started applying for
    themselves and typing their own names. An application submitted as "Nadia
    Newbie" became "Priya Sharma", the name inside the PDF, with nobody told.

    So the two branches now behave the same way, and ``full_name_source`` is
    what says whether there is anything to protect.

    ``parsed_full_name`` is recorded either way. It is what lets the candidate
    be asked "we read this from your CV — is it right?", and what lets HR see
    that the CV and the form disagree rather than only ever seeing one of them.

    Returns True when anything changed, so the caller can log it.

    A malformed extracted email is DROPPED rather than stored. The address came
    out of a PDF, so "Jane Doe | jane@" is a plausible read and must not become
    a permanently un-emailable row.
    """
    if not a.pending_enrichment:
        return False

    changed = False
    name = str(score.get("candidate_name") or "").strip()[:200]
    if name:
        # Kept whether or not it is used — this is the confirmation material.
        if a.parsed_full_name != name:
            a.parsed_full_name = name
            changed = True
        if (a.full_name_source or "filename") not in AUTHORED_NAME_SOURCES:
            a.full_name = name
            a.full_name_source = "resume"
            changed = True

    raw_email = str(score.get("candidate_email") or "").strip()[:320]
    if raw_email and a.email is None:
        email = valid_email_or_none(raw_email)
        if email is not None and email_taken:
            a.parsed_email = email
            changed = True
        elif email is not None:
            a.email = email
            changed = True

    a.pending_enrichment = False
    a.updated_at = datetime.now(tz=UTC)
    return changed


# Pydantic rather than a regex, and it lives here rather than in the router
# because BOTH ingest paths need it now: the synchronous upload validates the
# address the scorer read, and reconciliation validates the same thing hours
# later. hr_applicants aliases this one — a second implementation is how the
# two would drift, and the drift would show up as a mailer failure rather than
# as anything obviously wrong here.
#
# Normalisation is the reason this is not a regex: a resume header usually
# carries ``Jane Doe <jane@example.com>``, and pydantic unwraps that to a bare
# address the mailer can use without re-parsing.
_OPTIONAL_EMAIL: TypeAdapter[str | None] = TypeAdapter(Optional[EmailStr])  # noqa: UP007


def valid_email_or_none(value: str | None) -> str | None:
    """Return *value* as a normalised address, or None. Never raises.

    For MACHINE-EXTRACTED addresses only. A human typing their email into a
    form should be validated at the schema so they see the error and can fix
    it; silently dropping what they typed would be worse than refusing it.
    """
    try:
        return _OPTIONAL_EMAIL.validate_python(value)
    except ValidationError:
        return None


async def store_embedding(
    db: AsyncSession, company_id: uuid.UUID, applicant_id: uuid.UUID, vec: list[float]
) -> None:
    """Persist a resume embedding on the applicant row (the ORM does not map it).

    company_id is in the predicate as defence-in-depth: every applicant write
    stays tenant-scoped, even where applicant_id is already known to be owned.
    """
    if not vec:
        return
    await db.execute(
        text(
            "UPDATE applicants SET embedding = CAST(:emb AS halfvec) "
            "WHERE id = :id AND company_id = :cid"
        ),
        {"emb": to_pgvector_literal(vec), "id": applicant_id, "cid": company_id},
    )
    await db.commit()
