"""Requisition, enrolment and stage-transition helpers — Group B.

Three things live here because all three are used by both the HTTP routers and
the background workers, and each has exactly one correct implementation that
must not be duplicated:

* :func:`normalise_title` — the grouping rule. It has to agree, character for
  character, with the partial unique index created in migration
  ``d6f8a0c2e4b7``. If the two ever disagree, the API will happily accept a
  title the database then rejects, or worse, create a second requisition the
  index considers identical.

* :func:`record_transition` — the only way a status changes, and
  :func:`record_round_move` — the only way the current round changes. Writing
  either without its ledger entry is how time-in-stage silently becomes wrong,
  so each is done together with its entry and never separately. The ledger
  itself is append-only at the database (migration ``c9e1a3b5d7f0``).

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
from typing import Any, cast

import structlog
from sqlalchemy import CursorResult, text
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


async def live_enrolments(
    db: AsyncSession, *, applicant_id: uuid.UUID, company_id: uuid.UUID
) -> list[uuid.UUID]:
    """Every live application this person holds at this company, any status.

    What the applicant-shaped screens (the pipeline board, the applicant board)
    use to route a status change to the ledger. One means the change is
    unambiguous; several means the screen cannot know which opening it is about.
    """
    rows = (
        await db.execute(
            text(
                "SELECT id FROM enrolments"
                " WHERE applicant_id = :a AND company_id = :c AND deleted_at IS NULL"
                " ORDER BY created_at"
            ),
            {"a": applicant_id, "c": company_id},
        )
    ).all()
    return [uuid.UUID(str(r[0])) for r in rows]


def ambiguous_decision_detail(full_name: str, count: int) -> str:
    return (
        f"{full_name} has applied to {count} openings, so this decision needs to name one. "
        "Record it from that opening's decision queue."
    )


async def record_ledger_entry(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    company_id: uuid.UUID,
    status: str,
    actor_user_id: uuid.UUID | None,
    automated: bool,
    reason: str | None,
    from_round_id: uuid.UUID | None = None,
    to_round_id: uuid.UUID | None = None,
) -> None:
    """A ledger entry for a move that did not change the status. Caller commits.

    Round-to-round advances and moves between openings change where a candidate
    is without changing what their status says, and the ledger used to have no
    way to record either — ``record_transition`` writes nothing when the status
    is unchanged. This is the low-level insert for those; ``from_status`` and
    ``to_status`` are both the current status.
    """
    await db.execute(
        text(
            "INSERT INTO stage_transitions (company_id, enrolment_id, from_status, to_status,"
            " actor_user_id, automated, reason, occurred_at, from_round_id, to_round_id)"
            " VALUES (:c, :e, :s, :s, :a, :auto, :r, :n, :fr, :tr)"
        ),
        {"c": company_id, "e": enrolment_id, "s": status, "a": actor_user_id,
         "auto": automated, "r": reason, "n": datetime.now(tz=UTC),
         "fr": from_round_id, "tr": to_round_id},
    )


async def record_round_move(
    db: AsyncSession,
    *,
    enrolment_id: uuid.UUID,
    company_id: uuid.UUID,
    to_round_id: uuid.UUID | None,
    actor_user_id: uuid.UUID | None,
    automated: bool,
    reason: str | None = None,
) -> bool:
    """Move an enrolment to ``to_round_id`` (None = out of the workflow) and
    record the move. Caller commits.

    The single writer of ``current_round_id``, the way :func:`record_transition`
    is the single writer of status: setting the round without the ledger entry
    is how round-level time-in-stage and funnel history silently go missing.
    Returns False — and writes nothing — when the enrolment is not found or is
    already on that round.
    """
    row = (
        await db.execute(
            text(
                "SELECT status, current_round_id FROM enrolments"
                " WHERE id = :e AND company_id = :c AND deleted_at IS NULL FOR UPDATE"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).first()
    if row is None or row[1] == to_round_id:
        return False
    await db.execute(
        text("UPDATE enrolments SET current_round_id = :r, updated_at = :n WHERE id = :e"),
        {"r": to_round_id, "n": datetime.now(tz=UTC), "e": enrolment_id},
    )
    await record_ledger_entry(
        db, enrolment_id=enrolment_id, company_id=company_id, status=str(row[0]),
        actor_user_id=actor_user_id, automated=automated, reason=reason,
        from_round_id=row[1], to_round_id=to_round_id,
    )
    return True


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
# Finding the opening for a free-text title
# ---------------------------------------------------------------------------
# The same expression as the partial unique index — see normalise_title.
_FIND_BY_TITLE_SQL = """
SELECT id, title, level, jd_text FROM job_requisitions
 WHERE company_id = :c AND deleted_at IS NULL
   AND lower(btrim(regexp_replace(title, '\\s+', ' ', 'g'))) = :k
 LIMIT 1
"""


async def requisition_for_title(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    title: str,
    level: str,
    jd_text: str | None,
    actor_user_id: uuid.UUID | None,
) -> dict[str, Any]:
    """The opening a free-text job title belongs to, creating it if none exists.

    HR's upload forms still take a typed title. Every applicant has to land on
    an opening (B1/B4), so the title is resolved the way the Group B backfill
    grouped them — :func:`normalise_title`, never a fuzzy match — to the one
    live requisition the partial unique index allows per title.

    A requisition minted here is flagged ``from_backfill``: nobody chose to
    open it, a typed title implied it, and the review screen is where HR
    confirms that, renames it, or splits it — exactly as for the backfill's own
    guesses. Returns ``{"id", "title", "level", "jd_text", "created"}``. Caller
    commits.
    """
    key = normalise_title(title)
    if not key:
        raise ValueError("a job title is required to file an applicant under an opening")
    params = {"c": company_id, "k": key}
    row = (await db.execute(text(_FIND_BY_TITLE_SQL), params)).mappings().first()
    if row is not None:
        return {**dict(row), "created": False}

    now = datetime.now(tz=UTC)
    new_id = uuid.uuid4()
    try:
        # A savepoint, so losing a race to a concurrent upload of the same new
        # title rolls back only this insert, not the caller's transaction.
        async with db.begin_nested():
            await db.execute(
                text(
                    "INSERT INTO job_requisitions (id, company_id, title, level, jd_text,"
                    " owner_user_id, created_by_user_id, status, from_backfill,"
                    " created_at, updated_at)"
                    " VALUES (:i,:c,:t,:l,:jd,:o,:o,'open',true,:n,:n)"
                ),
                {"i": new_id, "c": company_id, "t": title.strip()[:300], "l": level or "mid",
                 "jd": jd_text, "o": actor_user_id, "n": now},
            )
    except IntegrityError:
        row = (await db.execute(text(_FIND_BY_TITLE_SQL), params)).mappings().first()
        if row is None:
            raise
        return {**dict(row), "created": False}
    log.info("requisition.created_from_title", company_id=str(company_id),
             requisition_id=str(new_id))
    return {"id": new_id, "title": title.strip()[:300], "level": level or "mid",
            "jd_text": jd_text, "created": True}


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
        # AsyncSession.execute is typed Result[Any]; rowcount is a DBAPI
        # cursor attribute and lives on CursorResult. Every branch here is a
        # plain UPDATE, which always returns one.
        moved[key] = int(cast("CursorResult[Any]", res).rowcount or 0)

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
    source_titles: list[str] | None = None,
    new_title: str,
    level: str | None,
    actor_user_id: uuid.UUID,
    enrolment_ids: list[uuid.UUID] | None = None,
) -> dict[str, Any]:
    """Undo one of the backfill's guesses. Caller commits.

    Two ways to say who moves. ``enrolment_ids`` names the candidates — the one
    that always works. ``source_titles`` picks them by the spelling they applied
    under, which only helps when an opening folded several spellings together:
    the backfill groups by normalised title, so in an opening it created every
    candidate normalises to the same key, and picking "by title" there selects
    all of them and is refused. That made split useless on exactly the openings
    the review screen exists for — hence explicit candidates.

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
    if not source_titles and not enrolment_ids:
        raise ValueError("choose the candidates to move")
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

    # Titles match on the normalised form, because that is what the backfill
    # grouped on — the raw string would miss the very spellings that caused
    # the fold in the first place.
    wanted = {normalise_title(t) for t in (source_titles or [])}
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

    if enrolment_ids:
        chosen = set(enrolment_ids)
        moving = [r for r in rows if r["id"] in chosen]
        if len(moving) != len(chosen):
            # Usually a stale page: someone was moved or merged meanwhile.
            raise ValueError(
                f"{len(chosen) - len(moving)} of those candidates are not in this opening"
                " — reload the page and choose again"
            )
    else:
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
    # B2: a candidate filed under a different opening is a move worth keeping.
    # Their status does not change, so it is a ledger entry rather than a
    # transition — and it is a person's doing.
    statuses: dict[Any, str] = {
        r[0]: str(r[1])
        for r in (await db.execute(
            text("SELECT id, status FROM enrolments WHERE id = ANY(:ids)"), {"ids": moved_ids}
        )).all()
    }
    for eid in moved_ids:
        await record_ledger_entry(
            db, enrolment_id=eid, company_id=company_id, status=str(statuses.get(eid, "new")),
            actor_user_id=actor_user_id, automated=False,
            reason=f"moved to the opening '{new_title.strip()}' (split)",
        )

    log.info(
        "requisition.split",
        company_id=str(company_id), source=str(requisition_id), created=str(new_id),
        moved=len(moved_ids), actor=str(actor_user_id),
        source_titles=sorted(wanted), by_candidate=bool(enrolment_ids),
    )
    return {
        "requisition_id": str(new_id),
        "title": new_title.strip(),
        "moved": len(moved_ids),
        "left_behind": len(rows) - len(moved_ids),
    }


# ---------------------------------------------------------------------------
# Delivery risk (E3)
# ---------------------------------------------------------------------------
# The roll-up board's job is "which of these needs me?", and a list of openings
# sorted by nothing answers it no better than the openings themselves. E3 asks
# for a computed signal "derived from the observed transition rate against the
# closing date" — i.e. project the rate a role has actually hired at forward to
# its close, and say whether that lands on target.
#
# Deliberately arithmetic, not a model. This drives an HR manager's attention,
# and a number they cannot reproduce on paper is one they will not trust or act
# on. Every input is visible on the same card: hires so far, target, and the
# dates.
#
# It is also deliberately NOT a decision about any person — it describes an
# opening's throughput, never a candidate — so nothing here touches D-05.

# Below this fraction of the target at the projected rate, the opening is not
# going to make it without intervention. 0.6 rather than a tighter number
# because early noise is large: two weeks into a ten-week opening a single hire
# swings the projection wildly, and a board that cries "off track" at every new
# role stops being read.
_OFF_TRACK_RATIO = 0.6
# An opening younger than this has no meaningful rate yet. Projecting from three
# days of data produces confident nonsense in both directions.
_MIN_DAYS_FOR_A_RATE = 7

DeliveryRisk = str  # 'on_track' | 'at_risk' | 'off_track'


async def merge_requisitions(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    source_id: uuid.UUID,
    into_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> dict[str, Any]:
    """Fold one opening into another — split's inverse. Caller commits.

    For two openings that are really one job: "Data Analyst" typed one way by
    one recruiter and another way by another, too different for the
    case-and-spacing rule to join. Every live candidate in ``source_id`` moves
    to ``into_id``; the emptied source is closed and retired, which frees its
    title.

    Refuses rather than guesses:

    * the source has a workflow. Its candidates were — or will be — assessed
      against that workflow's published rubric; moving them under another
      opening would silently re-scope what they are being assessed against.
      That is a decision about their assessment, not about data cleaning.
    * someone has applied to both. They would hold two applications to one
      opening, and which one survives is a decision about their candidacy
      (merge the people first, or decide one of the applications).
    * the two are the same opening, or either is gone.

    Each moved candidate gets a ledger entry, so "which opening did I apply
    to?" still has an answer after the fact.
    """
    if source_id == into_id:
        raise ValueError("an opening cannot be merged into itself")
    rows = (
        await db.execute(
            text(
                "SELECT id, title FROM job_requisitions"
                " WHERE id IN (:s, :t) AND company_id = :c AND deleted_at IS NULL"
            ),
            {"s": source_id, "t": into_id, "c": company_id},
        )
    ).all()
    titles = {r[0]: r[1] for r in rows}
    if source_id not in titles or into_id not in titles:
        raise ValueError("opening not found")

    has_workflow = await db.scalar(
        text("SELECT 1 FROM workflows WHERE requisition_id = :s AND deleted_at IS NULL LIMIT 1"),
        {"s": source_id},
    )
    if has_workflow:
        raise ValueError(
            f"'{titles[source_id]}' has its own hiring workflow. Its candidates are assessed "
            "against it, so merging would change what they are assessed on — merge the other "
            "way round, or decide on them first"
        )
    both = (
        await db.execute(
            text(
                "SELECT a.full_name FROM enrolments s"
                "  JOIN enrolments t ON t.applicant_id = s.applicant_id"
                "   AND t.requisition_id = :t AND t.deleted_at IS NULL"
                "  JOIN applicants a ON a.id = s.applicant_id"
                " WHERE s.requisition_id = :s AND s.deleted_at IS NULL"
                " ORDER BY a.full_name"
            ),
            {"s": source_id, "t": into_id},
        )
    ).all()
    if both:
        names = [r[0] for r in both]
        shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
        raise ValueError(
            f"{len(names)} candidate(s) applied to both openings ({shown}); decide which "
            "application stands before merging"
        )

    moving = (
        await db.execute(
            text(
                "SELECT id, status FROM enrolments"
                " WHERE requisition_id = :s AND company_id = :c AND deleted_at IS NULL"
            ),
            {"s": source_id, "c": company_id},
        )
    ).all()
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "UPDATE enrolments SET requisition_id = :t, updated_at = :n"
            " WHERE requisition_id = :s AND company_id = :c AND deleted_at IS NULL"
        ),
        {"t": into_id, "s": source_id, "c": company_id, "n": now},
    )
    for eid, status_ in moving:
        await record_ledger_entry(
            db, enrolment_id=eid, company_id=company_id, status=str(status_),
            actor_user_id=actor_user_id, automated=False,
            reason=f"moved to the opening '{titles[into_id]}' (merged from '{titles[source_id]}')",
        )
    # Retired, not deleted: its row still anchors anything that names it
    # (the audit log, a stale public link), and deleted_at frees its title.
    await db.execute(
        text(
            "UPDATE job_requisitions SET status = 'closed', deleted_at = :n, updated_at = :n"
            " WHERE id = :s AND company_id = :c"
        ),
        {"s": source_id, "c": company_id, "n": now},
    )
    log.info("requisition.merged", company_id=str(company_id), source=str(source_id),
             into=str(into_id), moved=len(moving), actor=str(actor_user_id))
    return {"into_requisition_id": str(into_id), "title": titles[into_id],
            "moved": len(moving)}


def delivery_risk(
    *,
    target_hires: int | None,
    hired: int,
    created_at: datetime | None,
    closes_at: datetime | None,
    now: datetime | None = None,
) -> DeliveryRisk | None:
    """Project hiring throughput forward to the closing date.

    Returns None — meaning "no signal", which the board renders as nothing at
    all — when the question cannot honestly be asked:

      * no closing date, so there is nothing to be late for;
      * no target, so there is no definition of enough;
      * the opening is younger than a week, so there is no rate to project.

    A returned band is one of on_track / at_risk / off_track. Meeting the target
    is on_track whatever the dates say, and a closing date already past with the
    target unmet is off_track without needing a projection.
    """
    if not target_hires or target_hires <= 0 or closes_at is None or created_at is None:
        return None

    now = now or datetime.now(tz=UTC)
    if hired >= target_hires:
        return "on_track"

    days_open = (now - created_at).total_seconds() / 86_400
    days_left = (closes_at - now).total_seconds() / 86_400
    if days_left <= 0:
        # The window has closed and the target was not met. No projection needed
        # — this is an observation, not a forecast.
        return "off_track"
    if days_open < _MIN_DAYS_FOR_A_RATE:
        return None

    # Hires per day so far, carried forward over the days that remain.
    rate = hired / days_open
    projected = hired + rate * days_left
    if projected >= target_hires:
        return "on_track"
    if projected >= target_hires * _OFF_TRACK_RATIO:
        return "at_risk"
    return "off_track"
