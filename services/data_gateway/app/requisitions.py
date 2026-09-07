"""Requisition, enrolment and stage-transition helpers — Group B.

Three things live here because all three are used by both the HTTP routers and
the background workers, and each has exactly one correct implementation that
must not be duplicated:

* :func:`normalise_title` — the grouping rule. It has to agree, character for
  character, with the partial unique index created in migration
  ``d6f8a0c2e4b7``. If the two ever disagree, the API will happily accept a
  title the database then rejects, or worse, create a second requisition the
  index considers identical.

* :func:`record_transition` — the only way a status changes. Writing the status
  without writing the ledger entry is how time-in-stage silently becomes wrong,
  so the two are done together in one function and never separately.

* :func:`merge_candidates` — detection only. Under D-06 one person is one
  applicant per company, but merging two existing rows repoints their exam and
  interview history onto a survivor and cannot be undone. So this reports; a
  human decides.

:func:`split_requisition` is the counterpart to that last one. The backfill
groups on :func:`normalise_title`, so it can fold two genuinely different jobs
into one opening; this is how a human takes them back apart. It lives here
rather than in the router because it has to agree with the same normalisation
rule the grouping used.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

# Every status an enrolment may hold. 'held' is reserved for the Phase 2
# workflow runner and is not reachable from any Group B path.
VALID_STATUSES: frozenset[str] = frozenset(
    {"new", "shortlisted", "interviewed", "held", "hired", "rejected"}
)
# Statuses that end a candidacy. Only a person may write these (D-05).
TERMINAL_STATUSES: frozenset[str] = frozenset({"hired", "rejected"})

_WS = re.compile(r"\s+")


def normalise_title(title: str) -> str:
    """The grouping key for a requisition title.

    Case-folds, collapses internal whitespace, trims. Nothing else — deliberately
    no stemming, no synonym table, no edit-distance matching. A fuzzy match that
    is wrong silently merges two genuinely different openings, and the cost of
    that is a candidate assessed against a role they did not apply for.

    MUST stay identical to the index expression in migration ``d6f8a0c2e4b7``::

        lower(btrim(regexp_replace(title, '\\s+', ' ', 'g')))
    """
    return _WS.sub(" ", (title or "").strip()).lower()


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------
async def record_transition(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    company_id: uuid.UUID,
    to_status: str,
    actor_user_id: uuid.UUID | None,
    automated: bool,
    reason: str | None = None,
    sync_applicant: bool = True,
) -> str | None:
    """Move an enrolment to ``to_status`` and record the move. Caller commits.

    Returns the previous status, or None when the enrolment was not found or
    was already in ``to_status`` (in which case nothing is written — a no-op
    move must not manufacture a ledger entry, or time-in-stage resets every
    time someone re-saves a form).

    ``sync_applicant`` keeps the legacy ``applicants.status`` column in step
    while it remains the source of truth for the pipeline SQL, the applicant
    list, the interview-eligibility gate and the frontend. It exists so this
    function can be the single writer during the transition; it goes away with
    those readers.
    """
    if to_status not in VALID_STATUSES:
        raise ValueError(f"unknown status {to_status!r}")

    row = (
        await db.execute(
            text(
                "SELECT status, applicant_id FROM enrolments "
                "WHERE id = :e AND company_id = :c AND deleted_at IS NULL FOR UPDATE"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).first()
    if row is None:
        return None
    previous, applicant_id = row[0], row[1]
    if previous == to_status:
        return None

    now = datetime.now(tz=UTC)
    await db.execute(
        text("UPDATE enrolments SET status = :s, updated_at = :n WHERE id = :e"),
        {"s": to_status, "n": now, "e": enrolment_id},
    )
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at)"
            " VALUES (:c, :e, :f, :t, :a, :auto, :r, :n)"
        ),
        {"c": company_id, "e": enrolment_id, "f": previous, "t": to_status,
         "a": actor_user_id, "auto": automated, "r": reason, "n": now},
    )

    if sync_applicant:
        # Only when this is the applicant's sole live enrolment. With several,
        # there is no single status the legacy column could honestly hold, so
        # leaving it is better than picking one arbitrarily and having the
        # pipeline show a candidate as 'hired' for a role they never applied to.
        live = await db.scalar(
            text(
                "SELECT count(*) FROM enrolments "
                "WHERE applicant_id = :a AND deleted_at IS NULL"
            ),
            {"a": applicant_id},
        )
        if int(live or 0) <= 1:
            await db.execute(
                text("UPDATE applicants SET status = :s, updated_at = :n WHERE id = :a"),
                {"s": to_status, "n": now, "a": applicant_id},
            )

    log.info(
        "enrolment.transition",
        enrolment_id=str(enrolment_id), **{"from": previous}, to=to_status,
        automated=automated, actor=str(actor_user_id) if actor_user_id else None,
    )
    return str(previous)


async def time_in_stage_days(db: AsyncSession, enrolment_id: uuid.UUID) -> float | None:
    """Days since the enrolment last changed status, from the ledger."""
    last = await db.scalar(
        text(
            "SELECT max(occurred_at) FROM stage_transitions WHERE enrolment_id = :e"
        ),
        {"e": enrolment_id},
    )
    if last is None:
        return None
    return round((datetime.now(tz=UTC) - last).total_seconds() / 86400, 2)


# ---------------------------------------------------------------------------
# Merge candidates (detection only — see the module docstring)
# ---------------------------------------------------------------------------
@dataclass
class MergeCandidate:
    """Several applicant rows at one company that appear to be one person."""

    email: str
    applicant_ids: list[str]
    names: list[str]
    enrolment_count: int
    has_history: bool  # any exam attempt, assignment or interview invite

    def as_dict(self) -> dict[str, Any]:
        return {
            "email": self.email,
            "applicant_ids": self.applicant_ids,
            "names": self.names,
            "enrolment_count": self.enrolment_count,
            "has_history": self.has_history,
        }


_MERGE_SQL = """
WITH dupes AS (
    SELECT lower(btrim(email)) AS key,
           array_agg(id ORDER BY created_at)         AS ids,
           array_agg(full_name ORDER BY created_at)  AS names
      FROM applicants
     WHERE company_id = :c AND deleted_at IS NULL
       AND email IS NOT NULL AND btrim(email) <> ''
     GROUP BY lower(btrim(email))
    HAVING count(*) > 1
)
SELECT d.key, d.ids, d.names,
       (SELECT count(*) FROM enrolments e
         WHERE e.applicant_id = ANY(d.ids) AND e.deleted_at IS NULL) AS enrolments,
       EXISTS (SELECT 1 FROM exam_assignments ea WHERE ea.applicant_id = ANY(d.ids))
       OR EXISTS (SELECT 1 FROM interview_invites ii WHERE ii.applicant_id = ANY(d.ids))
           AS has_history
  FROM dupes d
 ORDER BY d.key
 LIMIT :lim
"""


async def merge_candidates(
    db: AsyncSession, company_id: uuid.UUID, *, limit: int = 200
) -> list[MergeCandidate]:
    """Applicant rows at one company sharing an email — probably one person.

    Detection only. ``has_history`` is the flag that matters to a reviewer: a
    duplicate with no exam or interview attached is trivial to merge, whereas
    one with history means someone's assessment record is about to move, and
    that deserves a deliberate look.
    """
    rows = (
        await db.execute(text(_MERGE_SQL), {"c": company_id, "lim": limit})
    ).mappings().all()
    return [
        MergeCandidate(
            email=r["key"],
            applicant_ids=[str(i) for i in r["ids"]],
            names=list(r["names"]),
            enrolment_count=int(r["enrolments"] or 0),
            has_history=bool(r["has_history"]),
        )
        for r in rows
    ]


async def merge_applicants(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    survivor_id: uuid.UUID,
    absorbed_ids: list[uuid.UUID],
    actor_user_id: uuid.UUID,
) -> dict[str, int]:
    """Fold ``absorbed_ids`` into ``survivor_id``. Caller commits.

    Repoints every enrolment, exam assignment, exam attempt and interview invite
    onto the survivor, then soft-deletes the absorbed rows. Their PII is left
    intact rather than blanked, because a merge is not an erasure and the DPDP
    executor is the only thing that should ever redact a person.

    Refuses rather than guesses in two cases: anything outside ``company_id``,
    and an absorbed applicant holding an enrolment in a requisition the survivor
    is already enrolled in — that would violate one-enrolment-per-opening, and
    which of the two applications to keep is a human judgement about someone's
    candidacy, not a tie-break.
    """
    if survivor_id in absorbed_ids:
        raise ValueError("survivor cannot also be absorbed")
    if not absorbed_ids:
        return {"enrolments": 0, "assignments": 0, "attempts": 0, "invites": 0}

    owned = await db.scalar(
        text(
            "SELECT count(*) FROM applicants "
            "WHERE id = ANY(:ids) AND company_id = :c AND deleted_at IS NULL"
        ),
        {"ids": [survivor_id, *absorbed_ids], "c": company_id},
    )
    if int(owned or 0) != len(absorbed_ids) + 1:
        raise ValueError("all applicants must exist and belong to this company")

    clash = (
        await db.execute(
            text(
                "SELECT r.title FROM enrolments a"
                " JOIN enrolments b ON b.requisition_id = a.requisition_id"
                "  AND b.applicant_id = :s AND b.deleted_at IS NULL"
                " JOIN job_requisitions r ON r.id = a.requisition_id"
                " WHERE a.applicant_id = ANY(:ids) AND a.deleted_at IS NULL"
            ),
            {"s": survivor_id, "ids": absorbed_ids},
        )
    ).scalars().all()
    if clash:
        raise ValueError(
            "both applicants are enrolled in the same opening ("
            + ", ".join(sorted(set(clash)))
            + "); resolve that enrolment before merging"
        )

    now = datetime.now(tz=UTC)
    moved: dict[str, int] = {}
    for key, table, col in (
        ("enrolments", "enrolments", "applicant_id"),
        ("assignments", "exam_assignments", "applicant_id"),
        ("attempts", "exam_attempts", "applicant_id"),
        ("invites", "interview_invites", "applicant_id"),
    ):
        res = await db.execute(
            text(f"UPDATE {table} SET {col} = :s WHERE {col} = ANY(:ids)"),
            {"s": survivor_id, "ids": absorbed_ids},
        )
        moved[key] = int(res.rowcount or 0)

    await db.execute(
        text("UPDATE applicants SET deleted_at = :n, updated_at = :n WHERE id = ANY(:ids)"),
        {"n": now, "ids": absorbed_ids},
    )
    log.info(
        "applicant.merged",
        company_id=str(company_id), survivor=str(survivor_id),
        absorbed=[str(i) for i in absorbed_ids], actor=str(actor_user_id), moved=moved,
    )
    return moved


async def split_requisition(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    requisition_id: uuid.UUID,
    source_titles: list[str],
    new_title: str,
    level: str | None,
    actor_user_id: uuid.UUID,
) -> dict[str, Any]:
    """Undo one of the backfill's guesses. Caller commits.

    The Group B backfill grouped applicants by normalised title, so
    "Python Developer", "python developer" and "Python  Developer" became one
    opening. That is right almost always. When it is wrong — two genuinely
    different jobs that happened to be spelled alike — this moves the
    misfiled candidates into an opening of their own.

    Refuses rather than guesses in four cases:

    * a candidate already running a workflow. They are mid-process against a
      published rubric that belongs to the OLD opening; re-filing them would
      either strand them at a round that is no longer theirs or silently
      re-scope what they are being assessed against. Neither is a data-cleaning
      decision, so the split stops and names them.
    * moving every candidate out. That is a rename, and rename already exists
      (and clears ``from_backfill`` by itself), so doing it here would just be
      a second way to spell the same operation.
    * a title that collides with another open requisition — the partial unique
      index would reject it anyway; this reports it as a conflict rather than a
      failed transaction.
    * source titles that match nothing, which almost always means the caller is
      working from a stale review page.

    The new requisition is NOT marked ``from_backfill``: a human chose it, so it
    does not belong back in the review queue. The source keeps its flag until
    someone confirms it separately.
    """
    if not source_titles:
        raise ValueError("no source titles given")
    if not normalise_title(new_title):
        raise ValueError("the new opening needs a title")

    src = (
        await db.execute(
            text(
                "SELECT title, level FROM job_requisitions"
                " WHERE id = :i AND company_id = :c AND deleted_at IS NULL"
            ),
            {"i": requisition_id, "c": company_id},
        )
    ).mappings().first()
    if src is None:
        raise ValueError("requisition not found")

    # Match on the normalised form, because that is what the backfill grouped
    # on — matching the raw string would miss the very spellings that caused
    # the fold in the first place.
    wanted = {normalise_title(t) for t in source_titles}
    rows = (
        await db.execute(
            text(
                "SELECT e.id, e.target_job_title, e.workflow_id, a.full_name"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id"
                " WHERE e.requisition_id = :r AND e.company_id = :c AND e.deleted_at IS NULL"
            ),
            {"r": requisition_id, "c": company_id},
        )
    ).mappings().all()

    moving = [r for r in rows if normalise_title(r["target_job_title"] or "") in wanted]
    if not moving:
        raise ValueError("none of those titles have candidates in this opening")
    if len(moving) == len(rows):
        raise ValueError(
            "that would move every candidate out — rename the opening instead of splitting it"
        )

    running = sorted({r["full_name"] for r in moving if r["workflow_id"] is not None})
    if running:
        shown = ", ".join(running[:5]) + (f" and {len(running) - 5} more" if len(running) > 5 else "")
        raise ValueError(
            f"{len(running)} candidate(s) are part-way through this opening's workflow "
            f"({shown}); decide on them before splitting"
        )

    now = datetime.now(tz=UTC)
    new_id = uuid.uuid4()
    try:
        await db.execute(
            text(
                "INSERT INTO job_requisitions (id, company_id, title, level,"
                " owner_user_id, created_by_user_id, status, from_backfill,"
                " created_at, updated_at)"
                " VALUES (:i,:c,:t,:l,:o,:o,'open',false,:n,:n)"
            ),
            {"i": new_id, "c": company_id, "t": new_title.strip(),
             "l": level or src["level"] or "mid", "o": actor_user_id, "n": now},
        )
    except IntegrityError as exc:
        raise ValueError(f"An open requisition for '{new_title.strip()}' already exists.") from exc

    moved_ids = [r["id"] for r in moving]
    await db.execute(
        text(
            "UPDATE enrolments SET requisition_id = :new, updated_at = :n"
            " WHERE id = ANY(:ids) AND company_id = :c"
        ),
        {"new": new_id, "ids": moved_ids, "c": company_id, "n": now},
    )

    log.info(
        "requisition.split",
        company_id=str(company_id), source=str(requisition_id), created=str(new_id),
        moved=len(moved_ids), actor=str(actor_user_id),
        source_titles=sorted(wanted),
    )
    return {
        "requisition_id": str(new_id),
        "title": new_title.strip(),
        "moved": len(moved_ids),
        "left_behind": len(rows) - len(moved_ids),
    }
