"""The 90-day hire check-in — PH5-D5-2 (C1 quality-of-hire signal).

HR records, once per hire, whether the person is still employed at roughly 90
days after the job actually started and — while they are — a coarse
performance read. This is a SIGNAL for PH5-C1's quality-of-hire analytics
(retention, performance mix, by channel), never a decision: this module never
writes ``enrolments`` and never calls a lifecycle or decision writer
(``tests/unit/test_ph5_w1_checkins.py::test_hire_checkins_never_touches_the_pipeline``
fails if it ever does). The database enforces the same thing independently —
``hire_checkins_lifecycle`` (migration ``8f41cb18a299``) refuses an INSERT
unless the target enrolment's hire stands at that moment, and nothing in this
table can be joined back into a status write.

THE WINDOW (security review MEDIUM-1)
A check-in is only meaningful between the job's start date and
``CHECKIN_WINDOW_MAX_DAYS`` (180) after it: ``left`` is accepted from day 0,
``employed`` only from day ``DUE_AFTER_DAYS`` (80) — recording "still
employed" on day 10 would count as 90-day retention it has not earned yet —
and BOTH are refused past day 180. Before this window existed, a live purge
deleting an old check-in let the hire reappear on ``due()`` and let
``record()`` accept a brand-new "90-day" check-in arbitrarily late,
effectively restarting the retention clock forever. ``due()`` excludes a hire
past day 180 in SQL, not just in Python, so a large backlog cannot burn the
whole candidate cap on hires that could never be recorded anyway.

NO FREE TEXT
The row is exactly ``employment``, ``left_reason`` (iff ``left``) and
``performance`` (iff ``employed``) — no note, and (security review LOW-2) no
correction reason either: collecting one had no stated purpose and was one
refactor away from becoming free text in an audit log that is never
redacted. The audit trail follows the same rule: a create/correct audit row
carries only ``checkin_id``, ``enrolment_id``, ``kind`` and, for a
correction, which row it corrects — never the recorded values (AR-5: the
audit log outlives both erasure and this table's own 24-month retention, and
is never itself redacted, so it must never hold what those exist to let go
of). Note that the audit row's timestamp for "employed" is itself weakly
informative — a person cannot record ``employed`` before day 80, so its
audit date bounds their retention outcome even though the row holds no
value.

"THE HIRE STANDS"
``HIRE_STANDS_SQL`` below is byte-identical to the trigger's own copy
(migration ``8f41cb18a299``) and to PH5-C2's metric layer's own ``hired``
flag — a unit test asserts all copies match. A hire whose offer was later
declined, expired or withdrawn is not a hire to check in on, whatever
``enrolments.status`` currently says.

CORRECTIONS SUPERSEDE, THEY DO NOT OVERWRITE
``correct`` inserts a NEW row carrying ``supersedes_id`` and stamps the OLD
row's ``superseded_at`` — nothing is edited in place, on the
``interviewer_scorecards`` precedent. The composite foreign key
``(supersedes_id, enrolment_id, company_id)`` (security review MEDIUM-3)
means a correction can only ever point at an earlier row of the SAME hire —
not merely the same company — so a bug that mixed up two enrolments would be
refused by the database, not just by application code; the trigger also
checks the target is already superseded and of the same ``kind``, and
``uq_hire_checkins_supersedes`` gives each row at most one successor.

RETENTION AND ERASURE
Unlike ``interviewer_scorecards``, this table is not kept forever: 24-month
retention (``purge``, below) and the erasure executor's own step both delete
rows outright. There is nothing to redact and nothing to keep against an
anonymised applicant — no note, no ``applicant_id`` (the person is reached
through ``enrolment_id -> enrolments.applicant_id``). DELETE is otherwise
unrestricted at the database (see migration ``8f41cb18a299``): a caller could
in principle delete a live check-in and immediately record a fresh one,
which skips supersession and its audit trail entirely — an overwrite in
substance, and a real gap this module does not close (it is not in
``docs/ACCEPTED-RISKS.md``). It is mitigated by a TEST, not a database
control: only two callers ever issue a DELETE against this table at all —
``purge`` below and the erasure executor's step 5j, neither reachable from an
HR-facing route — and
``test_delete_from_hire_checkins_has_exactly_two_callers`` greps all of
``services/`` and fails the moment a third one appears. It is not enforced
structurally because the retention job and any other caller connect to
Postgres as the same database role; a session-level marker distinguishing
"this is the retention job" would be advisory only, since nothing stops a
different caller on the same role from setting it too.

TENANCY AND DATA CLASS
Every function here takes ``company_id`` from the caller's session
(``HrCtxDep``), never from the request body. For any future agent tool: this
table's data class is ``candidate_pii`` (an individual's employment outcome),
never ``platform_aggregate`` — it must not be read cross-tenant, and it must
never be read by the scorer, the assessment panel or the role engine, none of
which have any business with a post-hire HR record.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

log = structlog.get_logger(__name__)

KIND_90_DAY = "90_day"
EMPLOYMENT_VALUES: frozenset[str] = frozenset({"employed", "left"})
LEFT_REASONS: frozenset[str] = frozenset({"voluntary", "involuntary", "unknown"})
PERFORMANCE_VALUES: frozenset[str] = frozenset({"below", "meets", "exceeds"})

#: A check-in is "due" once employment_start is at least this many days ago —
#: also the earliest day 'employed' may be recorded (see
#: ``_assert_within_window``): a hire ten days in is not yet 90-day
#: retention, whatever HR types.
DUE_AFTER_DAYS = 80
#: The target day the check-in is FOR (shown to HR alongside employment_start
#: in ``due()`` and the window info below), distinct from ``DUE_AFTER_DAYS``,
#: which is when it starts appearing on the due list.
CHECKIN_TARGET_DAYS = 90
#: The window closes this many days after the start date — security review
#: MEDIUM-1. Both 'employed' and 'left' are refused past this; an unknown
#: start date counts as outside the window (there is no way to say it is
#: inside one).
CHECKIN_WINDOW_MAX_DAYS = 180
DUE_LIMIT = 200
#: Internal cap on how many "hired, stands, no check-in, in window" enrolments
#: are examined per ``due()`` call before the final ``DUE_LIMIT``. Bounds
#: worst-case work; the window itself is now filtered in SQL (below), so this
#: cap is no longer spent on hires that could never be recorded anyway.
_DUE_CANDIDATE_CAP = 500

#: Per-run cap: how many check-in CHAINS (not rows) one purge() call examines.
RETENTION_PURGE_BATCH = 500

_WINDOW_CLOSED_DETAIL = (
    "The 90-day check-in window for this hire has closed (it runs until 180 "
    "days after the start date)."
)

#: The three offer outcomes that mean a recorded hire did not stand. Mirrors
#: ``app.metrics.definitions.REVERSED_OFFER_OUTCOMES`` and the ``OUTCOMES``
#: vocabulary in migration ``b9d1f3a5c7e2`` (offers) — pinned by a unit test
#: so the three literals can never quietly drift apart.
REVERSED_OFFER_OUTCOMES: tuple[str, ...] = (
    "offer_declined", "offer_expired", "offer_withdrawn",
)

#: "The hire stands" — byte-identical to ``app.metrics.definitions.HIRE_STANDS_SQL``
#: (a unit test asserts it). Aliased to ``e`` on purpose: every query below
#: that uses it aliases ``enrolments`` as ``e``, which is what lets this
#: fragment drop straight into a query that also joins ``applicants`` (its
#: own ``status`` column) with no ambiguity. The trigger
#: (``hire_checkins_lifecycle``, migration ``8f41cb18a299``) enforces the same
#: RULE but is not this same STRING — PL/pgSQL spells it
#: ``<> ALL (ARRAY[...])`` over the same three literals rather than
#: ``NOT IN (...)``; a unit test checks the trigger's literals, not a string
#: match, since the two are different syntax for one rule.
HIRE_STANDS_SQL = (
    "e.status = 'hired' AND COALESCE(e.offer_outcome, '') NOT IN "
    "('offer_declined', 'offer_expired', 'offer_withdrawn')"
)


class CheckinError(Exception):
    """Refused. Carries the HTTP status and a sentence for a person."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class RequestMeta:
    """Who is asking, for the audit row. Both optional: a background caller has neither."""

    ip_address: str | None = None
    user_agent: str | None = None


_ERASED_DETAIL = (
    "This candidate's personal data has been erased or is being erased, so nothing "
    "more can be recorded about them."
)

_DOUBLE_SUBMIT_DETAIL = "A check-in has already been recorded for this hire."


def _audit(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    action: str,
    checkin_id: uuid.UUID,
    enrolment_id: uuid.UUID,
    extra: dict[str, Any] | None = None,
    meta: RequestMeta,
) -> None:
    """checkin_id, enrolment_id and kind only — never the recorded values."""
    db.add(
        AuditLog(
            actor_id=actor_id,
            actor_type="user",
            action=action,
            resource_type="hire_checkin",
            resource_id=checkin_id,
            details={
                "enrolment_id": str(enrolment_id), "kind": KIND_90_DAY, **(extra or {}),
            },
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            event_ts=datetime.now(tz=UTC),
        )
    )


def _validate_payload(
    employment: str, left_reason: str | None, performance: str | None,
) -> None:
    """The vocabulary and the iff rules, in words, before the database's CHECK
    constraints would refuse the same thing with a constraint name."""
    if employment not in EMPLOYMENT_VALUES:
        raise CheckinError(422, f"employment must be one of {sorted(EMPLOYMENT_VALUES)}.")
    if employment == "left":
        if left_reason not in LEFT_REASONS:
            raise CheckinError(
                422, f"left_reason is required and must be one of {sorted(LEFT_REASONS)} "
                     "when employment is 'left'."
            )
        if performance is not None:
            raise CheckinError(
                422, "performance must be left blank when employment is 'left'."
            )
    else:  # employed
        if left_reason is not None:
            raise CheckinError(
                422, "left_reason must be left blank when employment is 'employed'."
            )
        if performance not in PERFORMANCE_VALUES:
            raise CheckinError(
                422, f"performance is required and must be one of "
                     f"{sorted(PERFORMANCE_VALUES)} when employment is 'employed'."
            )


# ---------------------------------------------------------------------------
# The hire-stands + not-erased gate
# ---------------------------------------------------------------------------
_ENROLMENT_GATE_SQL = f"""
SELECT e.id, e.applicant_id, e.status, e.offer_outcome,
       ({HIRE_STANDS_SQL}) AS hire_stands
  FROM enrolments e
 WHERE e.id = :e AND e.company_id = :c AND e.deleted_at IS NULL
"""  # nosec B608 - interpolates the HIRE_STANDS_SQL module constant only


async def _load_enrolment_gate(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, for_share: bool = False
) -> dict[str, Any] | None:
    sql = _ENROLMENT_GATE_SQL + (" FOR SHARE OF e" if for_share else "")
    row = (await db.execute(text(sql), {"e": enrolment_id, "c": company_id})).mappings().first()
    return dict(row) if row is not None else None


def _not_hired_detail(row: dict[str, Any]) -> str:
    if row["status"] != "hired":
        return (
            f"This application is '{row['status']}', not hired. A 90-day check-in can only "
            "be recorded for a hire that stands."
        )
    return (
        f"This hire was reversed (offer {row['offer_outcome']}). A 90-day check-in can only "
        "be recorded for a hire that stands."
    )


async def _candidate_erased(db: AsyncSession, *, applicant_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            text(
                "SELECT (a.full_name = '[redacted]' AND a.email IS NULL)"
                "        OR EXISTS (SELECT 1 FROM erasure_requests er WHERE er.user_id = a.user_id)"
                "  FROM applicants a WHERE a.id = :a"
            ),
            {"a": applicant_id},
        )
    )


async def _assert_writable(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID, for_share: bool = False
) -> dict[str, Any]:
    """404 / 409 for "this application cannot take a check-in right now"."""
    row = await _load_enrolment_gate(
        db, company_id=company_id, enrolment_id=enrolment_id, for_share=for_share
    )
    if row is None:
        raise CheckinError(404, "Application not found.")
    if not row["hire_stands"]:
        raise CheckinError(409, _not_hired_detail(row))
    if await _candidate_erased(db, applicant_id=row["applicant_id"]):
        raise CheckinError(409, _ERASED_DETAIL)
    return row


# ---------------------------------------------------------------------------
# The employment-start clock
# ---------------------------------------------------------------------------
_EMPLOYMENT_START_SQL = """
SELECT
    (SELECT o.start_date FROM offers o
      WHERE o.enrolment_id = :e AND o.company_id = :c AND o.status = 'accepted') AS start_date,
    (SELECT MIN(st.occurred_at) FROM stage_transitions st
      WHERE st.enrolment_id = :e AND st.company_id = :c AND st.to_status = 'hired') AS hired_at
"""


async def employment_start(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID
) -> datetime | None:
    """When the job actually started: the accepted offer's ``start_date``,
    falling back to the hire date (the ledger's first move into ``hired``)
    when there is no offer or no start date on it. Indian notice periods of
    30-90 days mean the decision date alone lands "day 80" in the candidate's
    first weeks on the job — one function, used by ``due()``, the window info
    and the timing rule below, so none of them can use a different clock.
    """
    row = (
        await db.execute(text(_EMPLOYMENT_START_SQL), {"e": enrolment_id, "c": company_id})
    ).mappings().first()
    if row is None:
        return None
    if row["start_date"] is not None:
        return datetime.combine(row["start_date"], time.min, tzinfo=UTC)
    return row["hired_at"]


def _window_info(start: datetime | None, now: datetime) -> dict[str, Any]:
    """What the UI needs to explain the window (security review MEDIUM-1)."""
    if start is None:
        return {"start": None, "employed_from": None, "closes_at": None, "open": False}
    employed_from = start + timedelta(days=DUE_AFTER_DAYS)
    closes_at = start + timedelta(days=CHECKIN_WINDOW_MAX_DAYS)
    days = (now - start).days
    return {
        "start": start.isoformat(),
        "employed_from": employed_from.isoformat(),
        "closes_at": closes_at.isoformat(),
        "open": 0 <= days <= CHECKIN_WINDOW_MAX_DAYS,
    }


async def _assert_within_window(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID,
    employment: str, now: datetime,
) -> None:
    """The window: 'left' from day 0, 'employed' from day ``DUE_AFTER_DAYS``,
    both refused before day 0 (the job has not started — an accepted offer's
    ``start_date`` can be in the future during a notice period) and past day
    ``CHECKIN_WINDOW_MAX_DAYS``. An unknown start date counts as outside the
    window — there is no way to say it is inside one.
    """
    start = await employment_start(db, company_id=company_id, enrolment_id=enrolment_id)
    if start is None:
        raise CheckinError(409, _WINDOW_CLOSED_DETAIL)
    days = (now - start).days
    if days < 0:
        # An accepted offer's start_date can be in the future (a notice
        # period): 'left' had no lower bound at all before this, so someone
        # could record it against a job that has not started (security
        # re-review T1-A).
        raise CheckinError(
            409, "The job has not started yet; a check-in can be recorded from the "
                 "start date."
        )
    if days > CHECKIN_WINDOW_MAX_DAYS:
        raise CheckinError(409, _WINDOW_CLOSED_DETAIL)
    if employment == "employed" and days < DUE_AFTER_DAYS:
        raise CheckinError(
            409, f"This hire started on {start.date().isoformat()}, {days} day(s) ago. "
                 f"'employed' can only be recorded from day {DUE_AFTER_DAYS} onward — "
                 "record 'left' at any time up to day 180."
        )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
#: LEFT JOIN, not JOIN: the recorder may no longer exist (a removed staff
#: account), and CheckinOut.recorded_by_name is documented as null then — the
#: same COALESCE(full_name, email) display-name shape other HR screens use
#: for a staff actor (e.g. interviewer_scorecards' interviewer_name).
_ROW_SQL = (
    "SELECT hc.id, hc.enrolment_id, hc.kind, hc.employment, hc.left_reason, hc.performance,"
    " hc.recorded_by_user_id, hc.recorded_at, hc.supersedes_id, hc.superseded_at,"
    " hc.created_at, hc.updated_at,"
    " COALESCE(u.full_name, u.email) AS recorded_by_name"
    "  FROM hire_checkins hc"
    "  LEFT JOIN users u ON u.id = hc.recorded_by_user_id AND u.company_id = hc.company_id"
    " WHERE hc.id = :i AND hc.company_id = :c"
)


def _to_out(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "checkin_id": str(row["id"]),
        "enrolment_id": str(row["enrolment_id"]),
        "kind": row["kind"],
        "employment": row["employment"],
        "left_reason": row["left_reason"],
        "performance": row["performance"],
        "recorded_by_user_id": str(row["recorded_by_user_id"]),
        "recorded_by_name": row["recorded_by_name"],
        "recorded_at": row["recorded_at"].isoformat(),
        "supersedes_id": str(row["supersedes_id"]) if row["supersedes_id"] else None,
        "superseded": row["superseded_at"] is not None,
    }


async def list_for_enrolment(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID
) -> dict[str, Any]:
    """Every check-in for this application, oldest first, plus the window
    (security review MEDIUM-1) so the UI can explain why the form is or is
    not open."""
    exists = await db.scalar(
        text("SELECT 1 FROM enrolments WHERE id = :e AND company_id = :c AND deleted_at IS NULL"),
        {"e": enrolment_id, "c": company_id},
    )
    if not exists:
        raise CheckinError(404, "Application not found.")
    rows = (
        await db.execute(
            text(
                "SELECT hc.id, hc.enrolment_id, hc.kind, hc.employment, hc.left_reason,"
                " hc.performance, hc.recorded_by_user_id, hc.recorded_at, hc.supersedes_id,"
                " hc.superseded_at, hc.created_at, hc.updated_at,"
                " COALESCE(u.full_name, u.email) AS recorded_by_name"
                "  FROM hire_checkins hc"
                "  LEFT JOIN users u ON u.id = hc.recorded_by_user_id AND u.company_id = hc.company_id"
                " WHERE hc.enrolment_id = :e AND hc.company_id = :c"
                " ORDER BY hc.created_at"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().all()
    start = await employment_start(db, company_id=company_id, enrolment_id=enrolment_id)
    return {
        "checkins": [_to_out(dict(r)) for r in rows],
        "window": _window_info(start, datetime.now(tz=UTC)),
    }


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
async def record(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    enrolment_id: uuid.UUID,
    actor: uuid.UUID,
    employment: str,
    left_reason: str | None,
    performance: str | None,
    meta: RequestMeta,
) -> dict[str, Any]:
    """Record this hire's first 90-day check-in. Caller commits.

    409 if the hire does not stand, the candidate is erased, the check-in
    window has closed, or a live check-in already exists (``correct`` is the
    way to fix one). 422 on a vocabulary/iff violation.
    """
    _validate_payload(employment, left_reason, performance)

    # FOR SHARE: a reversal (final_decision takes FOR UPDATE on the enrolment)
    # cannot commit underneath this read; whichever is second waits, then sees
    # the other, exactly as interviewer_scorecards.assign does.
    await _assert_writable(
        db, company_id=company_id, enrolment_id=enrolment_id, for_share=True
    )

    live = await db.scalar(
        text(
            "SELECT id FROM hire_checkins WHERE enrolment_id = :e AND company_id = :c"
            "   AND kind = :k AND superseded_at IS NULL"
        ),
        {"e": enrolment_id, "c": company_id, "k": KIND_90_DAY},
    )
    if live is not None:
        raise CheckinError(
            409, "A check-in already exists for this hire. Use the correction flow "
                 "to change it."
        )

    now = datetime.now(tz=UTC)
    await _assert_within_window(
        db, company_id=company_id, enrolment_id=enrolment_id, employment=employment, now=now
    )

    checkin_id = uuid.uuid4()
    # A savepoint: two concurrent double-submits can both pass the live-check
    # read above and race on uq_hire_checkins_live at the database — the
    # interviewer_scorecards.assign precedent. Whichever loses gets a clean
    # 409, not a raw IntegrityError surfacing as a 500.
    savepoint = await db.begin_nested()
    try:
        await db.execute(
            text(
                "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind,"
                " employment, left_reason, performance, recorded_by_user_id, recorded_at,"
                " created_at, updated_at)"
                " VALUES (:id, :c, :e, :k, :emp, :lr, :perf, :by, :n, :n, :n)"
            ),
            {"id": checkin_id, "c": company_id, "e": enrolment_id, "k": KIND_90_DAY,
             "emp": employment, "lr": left_reason, "perf": performance, "by": actor, "n": now},
        )
        await savepoint.commit()
    except IntegrityError as exc:
        await savepoint.rollback()
        if "uq_hire_checkins_live" not in str(exc.orig):
            raise
        raise CheckinError(409, _DOUBLE_SUBMIT_DETAIL) from exc

    _audit(
        db, actor_id=actor, action="checkin.recorded", checkin_id=checkin_id,
        enrolment_id=enrolment_id, meta=meta,
    )
    log.info("checkin.recorded", checkin_id=str(checkin_id), enrolment_id=str(enrolment_id))
    row = (await db.execute(text(_ROW_SQL), {"i": checkin_id, "c": company_id})).mappings().first()
    return _to_out(dict(row))  # type: ignore[arg-type]


async def correct(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    checkin_id: uuid.UUID,
    actor: uuid.UUID,
    employment: str,
    left_reason: str | None,
    performance: str | None,
    meta: RequestMeta,
) -> dict[str, Any]:
    """Supersede the live check-in with a corrected one. Caller commits.

    Nothing is overwritten: the old row is marked superseded and a new row
    carries ``supersedes_id`` back to it, so both stay readable.
    """
    _validate_payload(employment, left_reason, performance)

    # FOR UPDATE OF hc, not a bare FOR UPDATE: _ROW_SQL LEFT JOINs users for
    # recorded_by_name, and Postgres refuses FOR UPDATE on the nullable side
    # of an outer join -- naming the table locks only the row that matters.
    row = (
        await db.execute(
            text(_ROW_SQL + " FOR UPDATE OF hc"), {"i": checkin_id, "c": company_id}
        )
    ).mappings().first()
    if row is None:
        raise CheckinError(404, "Check-in not found.")
    if row["superseded_at"] is not None:
        raise CheckinError(409, "This check-in was already corrected; open the latest version.")

    # Re-checked: the trigger refuses the new INSERT unless the hire still
    # stands, so a reversed hire or an erased candidate gets a clean 409 here
    # rather than a raw database exception.
    await _assert_writable(
        db, company_id=company_id, enrolment_id=row["enrolment_id"], for_share=True
    )

    now = datetime.now(tz=UTC)
    await _assert_within_window(
        db, company_id=company_id, enrolment_id=row["enrolment_id"],
        employment=employment, now=now,
    )

    new_id = uuid.uuid4()
    # Supersede FIRST, then insert: the live partial unique index refuses the
    # new row while the old one is still live, and the trigger's INSERT check
    # (migration 8f41cb18a299) requires the target it supersedes to already
    # read superseded_at IS NOT NULL.
    await db.execute(
        text(
            "UPDATE hire_checkins SET superseded_at = :n, updated_at = :n"
            " WHERE id = :old AND company_id = :c"
        ),
        {"n": now, "old": checkin_id, "c": company_id},
    )
    savepoint = await db.begin_nested()
    try:
        await db.execute(
            text(
                "INSERT INTO hire_checkins (id, company_id, enrolment_id, kind,"
                " employment, left_reason, performance, recorded_by_user_id, recorded_at,"
                " supersedes_id, created_at, updated_at)"
                " VALUES (:id, :c, :e, :k, :emp, :lr, :perf, :by, :n, :old, :n, :n)"
            ),
            {"id": new_id, "c": company_id, "e": row["enrolment_id"], "k": row["kind"],
             "emp": employment, "lr": left_reason, "perf": performance, "by": actor,
             "old": checkin_id, "n": now},
        )
        await savepoint.commit()
    except IntegrityError as exc:
        await savepoint.rollback()
        if "uq_hire_checkins_live" not in str(exc.orig) and (
            "uq_hire_checkins_supersedes" not in str(exc.orig)
        ):
            raise
        raise CheckinError(
            409, "This check-in was already corrected; open the latest version."
        ) from exc

    _audit(
        db, actor_id=actor, action="checkin.corrected", checkin_id=new_id,
        enrolment_id=row["enrolment_id"], extra={"corrects": str(checkin_id)}, meta=meta,
    )
    log.info("checkin.corrected", corrects=str(checkin_id), new=str(new_id))
    new_row = (await db.execute(text(_ROW_SQL), {"i": new_id, "c": company_id})).mappings().first()
    return _to_out(dict(new_row))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# due() — hires past the window with no live check-in
# ---------------------------------------------------------------------------
# The window (>= day 80, <= day 180) is filtered here in SQL, not only in
# Python, so a large backlog of hires outside the window never spends the
# candidate cap (security review MEDIUM-1). Erased applicants are excluded
# (LOW-7) the same way _candidate_erased checks a single one.
_DUE_CANDIDATES_SQL = f"""
SELECT e.id AS enrolment_id, e.applicant_id, a.full_name, e.target_job_title,
       COALESCE(off.start_date, hl.hired_date) AS start_date
  FROM enrolments e
  JOIN applicants a ON a.id = e.applicant_id
  LEFT JOIN LATERAL (
      SELECT o.start_date FROM offers o
       WHERE o.enrolment_id = e.id AND o.company_id = e.company_id AND o.status = 'accepted'
       LIMIT 1
  ) off ON true
  LEFT JOIN LATERAL (
      SELECT MIN(st.occurred_at)::date AS hired_date FROM stage_transitions st
       WHERE st.enrolment_id = e.id AND st.company_id = e.company_id AND st.to_status = 'hired'
  ) hl ON true
 WHERE e.company_id = :c AND e.deleted_at IS NULL
   AND {HIRE_STANDS_SQL}
   AND NOT (a.full_name = '[redacted]' AND a.email IS NULL)
   AND NOT EXISTS (SELECT 1 FROM erasure_requests er WHERE er.user_id = a.user_id)
   AND NOT EXISTS (
       SELECT 1 FROM hire_checkins hc
        WHERE hc.enrolment_id = e.id AND hc.company_id = e.company_id
          AND hc.kind = :k AND hc.superseded_at IS NULL
   )
   AND COALESCE(off.start_date, hl.hired_date) BETWEEN :earliest_start AND :latest_start
 ORDER BY start_date ASC
 LIMIT :cap
"""  # nosec B608 - interpolates the HIRE_STANDS_SQL module constant only


async def due(
    db: AsyncSession, *, company_id: uuid.UUID, as_of: datetime | None = None
) -> list[dict[str, Any]]:
    """Hires whose job started at least ``DUE_AFTER_DAYS`` days ago and at
    most ``CHECKIN_WINDOW_MAX_DAYS`` days ago, with no live check-in, oldest
    ``employment_start`` first, capped at ``DUE_LIMIT``.

    ``employment_start`` — not the hire decision date — is the clock (see
    ``employment_start``), so a candidate serving a long notice period is not
    marked due while they are still weeks from actually starting. The upper
    bound means a hire whose window has closed, or whose check-in was deleted
    by retention, does not come back onto this list.
    """
    as_of = as_of or datetime.now(tz=UTC)
    earliest_start = (as_of - timedelta(days=CHECKIN_WINDOW_MAX_DAYS)).date()
    latest_start = (as_of - timedelta(days=DUE_AFTER_DAYS)).date()
    candidates = (
        await db.execute(
            text(_DUE_CANDIDATES_SQL),
            {
                "c": company_id, "k": KIND_90_DAY, "cap": _DUE_CANDIDATE_CAP,
                "earliest_start": earliest_start, "latest_start": latest_start,
            },
        )
    ).mappings().all()

    out: list[dict[str, Any]] = []
    for row in candidates:
        start = datetime.combine(row["start_date"], time.min, tzinfo=UTC)
        days = (as_of - start).days
        if days < DUE_AFTER_DAYS or days > CHECKIN_WINDOW_MAX_DAYS:
            continue  # the SQL bound is a coarse (date-only) pre-filter; this is authoritative
        out.append(
            {
                "enrolment_id": str(row["enrolment_id"]),
                "applicant_id": str(row["applicant_id"]),
                "full_name": row["full_name"],
                "job_title": row["target_job_title"],
                "employment_start": start.isoformat(),
                "due_at": (start + timedelta(days=CHECKIN_TARGET_DAYS)).isoformat(),
                "days_since_start": days,
            }
        )
    out.sort(key=lambda r: r["employment_start"])
    return out[:DUE_LIMIT]


# ---------------------------------------------------------------------------
# Retention — deleted outright, 24 months after recorded_at
# ---------------------------------------------------------------------------
# Keyed on the CHAIN, not the row (security review LOW-3): a correction's
# ``supersedes_id`` means an individual row can only ever be deleted alongside
# the rest of its chain, in the SAME statement, or the self-referential
# foreign key refuses it. Grouping by (enrolment_id, kind) and keying off the
# chain's OWN earliest recorded_at is what lets a fresh correction on an
# old original still expire together, in one DELETE, once the chain's origin
# is old enough — the dry-run count below reuses this exact subquery, so the
# two can never disagree about which chains are due.
_PURGE_CHAINS_SQL = """
SELECT enrolment_id, kind FROM hire_checkins
 GROUP BY enrolment_id, kind
HAVING MIN(recorded_at) < :cutoff
 ORDER BY MIN(recorded_at)
 LIMIT :lim
"""

#: The full backlog, uncapped — cheap on this table (a handful of rows per
#: hire, nothing like the session/turn volume ``retention.py`` bounds
#: against), so it costs nothing to log alongside the capped batch count
#: either way (security re-review, point 3).
_PURGE_BACKLOG_CHAINS_SQL = """
SELECT count(*) FROM (
    SELECT 1 FROM hire_checkins GROUP BY enrolment_id, kind HAVING MIN(recorded_at) < :cutoff
) backlog
"""


async def purge(db: AsyncSession, *, retention_days: int, dry_run: bool) -> int:
    """Delete whole check-in chains outright ``retention_days`` after the
    chain's earliest ``recorded_at``, oldest chains first, at most
    ``RETENTION_PURGE_BATCH`` chains per call.

    There is no free text on this row and no ``applicant_id`` to redact
    against, so retention here is disposal, not redaction — the parameters
    (numbers) are kept, the accommodation/code-evidence/task-submission
    retention shape; only the mechanism (DELETE, not UPDATE) differs.

    The returned count, dry-run or live, is THIS RUN'S BATCH (at most
    ``RETENTION_PURGE_BATCH`` chains' worth of rows) — not the whole backlog,
    unlike ``retention.purge_expired_sessions``'s deliberately-uncapped
    dry-run. The full backlog size is cheap to ask for on this table and is
    logged alongside the batch count either way, so an operator reads it from
    the log rather than from what the dry-run return value means changing
    shape between dry and live. ``RETENTION_DRY_RUN`` (default true) reports
    both counts without deleting anything, honouring the same safety rail
    every other retention step in this service does.

    Caller commits.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
    backlog_chains = int(await db.scalar(text(_PURGE_BACKLOG_CHAINS_SQL), {"cutoff": cutoff}) or 0)
    if dry_run:
        count = await db.scalar(
            text(
                f"SELECT count(*) FROM hire_checkins h"  # nosec B608 - interpolates the module-level _PURGE_CHAINS_SQL fragment only
                f" JOIN ({_PURGE_CHAINS_SQL}) chains"
                " ON h.enrolment_id = chains.enrolment_id AND h.kind = chains.kind"
            ),
            {"cutoff": cutoff, "lim": RETENTION_PURGE_BATCH},
        )
        candidates = int(count or 0)
        log.info(
            "hire_checkin.retention", candidates=candidates, backlog_chains=backlog_chains,
            dry_run=True,
        )
        return candidates

    result = await db.execute(
        text(
            f"DELETE FROM hire_checkins h"  # nosec B608 - interpolates the module-level _PURGE_CHAINS_SQL fragment only
            f" USING ({_PURGE_CHAINS_SQL}) chains"
            " WHERE h.enrolment_id = chains.enrolment_id AND h.kind = chains.kind"
        ),
        {"cutoff": cutoff, "lim": RETENTION_PURGE_BATCH},
    )
    total = int(cast("CursorResult[Any]", result).rowcount or 0)
    log.info(
        "hire_checkin.retention", purged=total, backlog_chains=backlog_chains, dry_run=False,
    )
    return total
