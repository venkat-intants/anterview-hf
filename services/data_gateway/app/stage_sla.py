"""Stage owners, SLAs and exception paths — PH4-O1.

A stage is a round of a workflow version, or the final human decision after
the last one. It can have an OWNER — the HR manager answerable for moving
people through it — and an SLA in hours. An application's time in its stage is
measured from when it got there (``enrolment_stage_entered_at``: the latest
ledger entry that changed its status or round), and its state is derived when
it is read:

    on_track   less than 75% of the SLA used
    due_soon   75% or more used
    overdue    past the SLA

Nothing is stored about lateness, so nothing can go stale, and an application
in a stage with no SLA simply has no SLA state — existing workflows carry on
exactly as before.

EXCEPTIONS
An exception records that normal processing cannot proceed for one application
— a reschedule request, an absent interviewer — with a reason, an owner who has
to act, who raised it and when. Raising, reassigning and resolving one changes
no status and no round: it is information for a person, never a decision
(D-05). ``tests/unit/test_ph4_wave2.py`` holds that structurally. While an
exception is open the SLA shows it, so an overdue stage with a known reason
reads differently from one nobody has looked at.

Owners, SLAs and exceptions are operational, not part of the frozen rubric:
they live in their own tables, stay editable on a live version, and every
change is audited.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.notifications_util import create_notification

log = structlog.get_logger(__name__)

DUE_SOON_FRACTION = 0.75
SLA_MAX_HOURS = 8760
REASON_MIN, REASON_MAX = 10, 1000
DECISION_STAGE_LABEL = "Final decision"
#: Who may own a stage or an exception: the people who move candidates.
OWNER_ROLES: tuple[str, ...] = ("hr_manager",)


class StageError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# The clock — pure
# ---------------------------------------------------------------------------
def sla_state(
    entered_at: datetime | None, sla_hours: int | None, now: datetime | None = None
) -> dict[str, Any] | None:
    """Where an application stands against its stage SLA, or None with no SLA."""
    if not sla_hours or entered_at is None:
        return None
    now = now or datetime.now(tz=UTC)
    due = entered_at + timedelta(hours=int(sla_hours))
    elapsed = (now - entered_at).total_seconds() / 3600
    if now >= due:
        state = "overdue"
    elif elapsed >= DUE_SOON_FRACTION * sla_hours:
        state = "due_soon"
    else:
        state = "on_track"
    return {
        "state": state,
        "sla_hours": int(sla_hours),
        "entered_at": entered_at.isoformat(),
        "due_at": due.isoformat(),
        "hours_remaining": round((due - now).total_seconds() / 3600, 1),
    }


def _audit(
    db: AsyncSession, *, actor: uuid.UUID, action: str, resource_type: str,
    resource_id: uuid.UUID, details: dict[str, Any],
) -> None:
    db.add(
        AuditLog(
            actor_id=actor,
            actor_type="user",
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            details=details,
            event_ts=datetime.now(tz=UTC),
        )
    )


async def _assert_owner_eligible(
    db: AsyncSession, *, company_id: uuid.UUID, user_id: uuid.UUID
) -> None:
    ok = await db.scalar(
        text(
            "SELECT 1 FROM users u"
            "  JOIN user_roles ur ON ur.user_id = u.id"
            "  JOIN roles r ON r.id = ur.role_id AND r.name = ANY(:roles)"
            " WHERE u.id = :u AND u.company_id = :c AND u.deleted_at IS NULL AND u.is_active"
        ),
        {"u": user_id, "c": company_id, "roles": list(OWNER_ROLES)},
    )
    if not ok:
        raise StageError(422, "An owner must be an active HR manager in your company.")


# ---------------------------------------------------------------------------
# Stage settings — owner and SLA per stage
# ---------------------------------------------------------------------------
async def stage_settings(
    db: AsyncSession, *, company_id: uuid.UUID, workflow_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Every stage of a version — each round, then the final decision — with its settings."""
    rows = (
        await db.execute(
            text(
                "SELECT wr.id AS round_id, wr.title, wr.position, wr.kind,"
                "       s.owner_user_id, s.sla_hours, u.full_name AS owner_name"
                "  FROM workflow_rounds wr"
                "  LEFT JOIN workflow_stage_settings s"
                "         ON s.round_id = wr.id AND s.workflow_id = wr.workflow_id"
                "  LEFT JOIN users u ON u.id = s.owner_user_id"
                " WHERE wr.workflow_id = :w AND wr.company_id = :c AND wr.deleted_at IS NULL"
                " ORDER BY wr.position"
            ),
            {"w": workflow_id, "c": company_id},
        )
    ).mappings().all()
    decision = (
        await db.execute(
            text(
                "SELECT s.owner_user_id, s.sla_hours, u.full_name AS owner_name"
                "  FROM workflow_stage_settings s LEFT JOIN users u ON u.id = s.owner_user_id"
                " WHERE s.workflow_id = :w AND s.company_id = :c AND s.round_id IS NULL"
            ),
            {"w": workflow_id, "c": company_id},
        )
    ).mappings().first()
    out = [
        {"round_id": str(r["round_id"]), "stage": r["title"], "kind": r["kind"],
         "owner_user_id": str(r["owner_user_id"]) if r["owner_user_id"] else None,
         "owner_name": r["owner_name"], "sla_hours": r["sla_hours"]}
        for r in rows
    ]
    out.append({
        "round_id": None, "stage": DECISION_STAGE_LABEL, "kind": "decision",
        "owner_user_id": str(decision["owner_user_id"])
        if decision and decision["owner_user_id"] else None,
        "owner_name": decision["owner_name"] if decision else None,
        "sla_hours": decision["sla_hours"] if decision else None,
    })
    return out


async def set_stage_setting(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    workflow_id: uuid.UUID,
    round_id: uuid.UUID | None,
    owner_user_id: uuid.UUID | None,
    sla_hours: int | None,
    actor: uuid.UUID,
) -> dict[str, Any]:
    """Set (or clear) a stage's owner and SLA. Editable on a live version. Caller commits."""
    wf = await db.scalar(
        text(
            "SELECT 1 FROM workflows WHERE id = :w AND company_id = :c AND deleted_at IS NULL"
        ),
        {"w": workflow_id, "c": company_id},
    )
    if not wf:
        raise StageError(404, "Workflow not found.")
    stage = DECISION_STAGE_LABEL
    if round_id is not None:
        title = await db.scalar(
            text(
                "SELECT title FROM workflow_rounds"
                " WHERE id = :r AND workflow_id = :w AND deleted_at IS NULL"
            ),
            {"r": round_id, "w": workflow_id},
        )
        if title is None:
            raise StageError(404, "That stage is not part of this workflow.")
        stage = title
    if sla_hours is not None and not 1 <= sla_hours <= SLA_MAX_HOURS:
        raise StageError(422, f"An SLA is between 1 and {SLA_MAX_HOURS} hours.")
    if owner_user_id is not None:
        await _assert_owner_eligible(db, company_id=company_id, user_id=owner_user_id)

    before = (
        await db.execute(
            text(
                "SELECT owner_user_id, sla_hours FROM workflow_stage_settings"
                " WHERE workflow_id = :w AND round_id IS NOT DISTINCT FROM :r"
            ),
            {"w": workflow_id, "r": round_id},
        )
    ).mappings().first()
    now = datetime.now(tz=UTC)
    if before is None:
        await db.execute(
            text(
                "INSERT INTO workflow_stage_settings (id, company_id, workflow_id, round_id,"
                " owner_user_id, sla_hours, updated_by_user_id, created_at, updated_at)"
                " VALUES (:i, :c, :w, :r, :o, :h, :u, :n, :n)"
            ),
            {"i": uuid.uuid4(), "c": company_id, "w": workflow_id, "r": round_id,
             "o": owner_user_id, "h": sla_hours, "u": actor, "n": now},
        )
    else:
        await db.execute(
            text(
                "UPDATE workflow_stage_settings SET owner_user_id = :o, sla_hours = :h,"
                " updated_by_user_id = :u, updated_at = :n"
                " WHERE workflow_id = :w AND round_id IS NOT DISTINCT FROM :r"
            ),
            {"o": owner_user_id, "h": sla_hours, "u": actor, "n": now,
             "w": workflow_id, "r": round_id},
        )
    old_owner = before["owner_user_id"] if before else None
    old_sla = before["sla_hours"] if before else None
    changed = []
    if old_owner != owner_user_id:
        changed.append("owner")
    if old_sla != sla_hours:
        changed.append("sla")
    if changed:
        _audit(
            db, actor=actor, action="stage.settings.updated", resource_type="workflow",
            resource_id=workflow_id,
            details={"company_id": str(company_id), "stage": stage,
                     "round_id": str(round_id) if round_id else None, "changed": changed,
                     "owner_before": str(old_owner) if old_owner else None,
                     "owner_after": str(owner_user_id) if owner_user_id else None,
                     "sla_hours_before": old_sla, "sla_hours_after": sla_hours},
        )
        if owner_user_id and old_owner != owner_user_id:
            await create_notification(
                db, user_id=owner_user_id, kind="stage_owner_assigned",
                title=f"You now own the '{stage}' stage",
                body="You'll be told when applications in it run past their SLA.",
                link="/hr",
            )
    return {"round_id": str(round_id) if round_id else None, "stage": stage,
            "owner_user_id": str(owner_user_id) if owner_user_id else None,
            "sla_hours": sla_hours, "changed": changed}


async def copy_stage_settings(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    source_workflow_id: uuid.UUID,
    target_workflow_id: uuid.UUID,
    id_map: dict[str, uuid.UUID],
) -> int:
    """Carry owners and SLAs to a new version (clone_for_edit). Caller commits."""
    rows = (
        await db.execute(
            text(
                "SELECT round_id, owner_user_id, sla_hours FROM workflow_stage_settings"
                " WHERE workflow_id = :w AND company_id = :c"
            ),
            {"w": source_workflow_id, "c": company_id},
        )
    ).mappings().all()
    copied = 0
    now = datetime.now(tz=UTC)
    for r in rows:
        if r["round_id"] is not None and str(r["round_id"]) not in id_map:
            continue
        await db.execute(
            text(
                "INSERT INTO workflow_stage_settings (id, company_id, workflow_id, round_id,"
                " owner_user_id, sla_hours, created_at, updated_at)"
                " VALUES (:i, :c, :w, :r, :o, :h, :n, :n)"
            ),
            {"i": uuid.uuid4(), "c": company_id, "w": target_workflow_id,
             "r": id_map[str(r["round_id"])] if r["round_id"] else None,
             "o": r["owner_user_id"], "h": r["sla_hours"], "n": now},
        )
        copied += 1
    return copied


# ---------------------------------------------------------------------------
# The board — every application's SLA, computed now
# ---------------------------------------------------------------------------
# Ordered by when each stage falls due BEFORE the cap, so a company with more
# than 2000 open applications still sees the most overdue ones.
_BOARD_SQL = """
SELECT * FROM (
SELECT e.id AS enrolment_id, e.requisition_id, e.status, e.current_round_id,
       a.full_name, jr.title AS opening_title,
       COALESCE(cur.title, 'Final decision') AS stage,
       enrolment_stage_entered_at(e.id, e.created_at) AS entered_at,
       s.sla_hours, s.owner_user_id, u.full_name AS owner_name,
       (SELECT count(*) FROM stage_exceptions x
         WHERE x.enrolment_id = e.id AND x.status = 'open') AS open_exceptions
  FROM enrolments e
  JOIN applicants a ON a.id = e.applicant_id AND a.deleted_at IS NULL
  JOIN job_requisitions jr ON jr.id = e.requisition_id
  LEFT JOIN workflow_rounds cur ON cur.id = e.current_round_id
  JOIN workflow_stage_settings s
    ON s.workflow_id = e.workflow_id
   AND s.round_id IS NOT DISTINCT FROM e.current_round_id
  LEFT JOIN users u ON u.id = s.owner_user_id
 WHERE e.company_id = :c AND e.deleted_at IS NULL
   AND e.status NOT IN ('hired', 'rejected')
   AND s.sla_hours IS NOT NULL
   AND (e.current_round_id IS NOT NULL OR enrolment_awaits_human(e.status, e.current_round_id))
   AND (CAST(:r AS uuid) IS NULL OR e.requisition_id = CAST(:r AS uuid))
) b
 ORDER BY b.entered_at + b.sla_hours * interval '1 hour', b.enrolment_id
 LIMIT 2000
"""


async def sla_board(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    requisition_id: uuid.UUID | None = None,
    states: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Applications in a stage with an SLA, worst first. HR's "at risk" view.

    The decision stage (``current_round_id`` NULL) only counts once the
    application is actually waiting on a person — not while it is still new
    and nobody has shortlisted it.
    """
    rows = (
        await db.execute(text(_BOARD_SQL), {"c": company_id, "r": requisition_id})
    ).mappings().all()
    now = datetime.now(tz=UTC)
    out = []
    for r in rows:
        sla = sla_state(r["entered_at"], r["sla_hours"], now)
        if sla is None or (states and sla["state"] not in states):
            continue
        out.append({
            "enrolment_id": str(r["enrolment_id"]),
            "requisition_id": str(r["requisition_id"]),
            "opening_title": r["opening_title"],
            "full_name": r["full_name"],
            "stage": r["stage"],
            "owner_user_id": str(r["owner_user_id"]) if r["owner_user_id"] else None,
            "owner_name": r["owner_name"],
            "open_exceptions": int(r["open_exceptions"] or 0),
            **sla,
        })
    rank = {"overdue": 0, "due_soon": 1, "on_track": 2}
    return sorted(out, key=lambda x: (rank[x["state"]], x["hours_remaining"]))


async def sla_for_enrolments(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_ids: list[uuid.UUID]
) -> dict[str, dict[str, Any]]:
    """SLA state keyed by enrolment id — for list views that already have their rows."""
    if not enrolment_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT e.id, enrolment_stage_entered_at(e.id, e.created_at) AS entered_at,"
                "       s.sla_hours, u.full_name AS owner_name, s.owner_user_id,"
                "       (SELECT count(*) FROM stage_exceptions x"
                "         WHERE x.enrolment_id = e.id AND x.status = 'open') AS open_exceptions"
                "  FROM enrolments e"
                # LEFT: an exception can be raised on a stage nobody has set an
                # SLA for, and its count must still reach the list.
                "  LEFT JOIN workflow_stage_settings s"
                "    ON s.workflow_id = e.workflow_id"
                "   AND s.round_id IS NOT DISTINCT FROM e.current_round_id"
                "  LEFT JOIN users u ON u.id = s.owner_user_id"
                " WHERE e.company_id = :c AND e.id = ANY(:ids)"
            ),
            {"c": company_id, "ids": enrolment_ids},
        )
    ).mappings().all()
    now = datetime.now(tz=UTC)
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        sla = sla_state(r["entered_at"], r["sla_hours"], now)
        out[str(r["id"])] = {
            "sla": sla,
            "owner_name": r["owner_name"],
            "owner_user_id": str(r["owner_user_id"]) if r["owner_user_id"] else None,
            "open_exceptions": int(r["open_exceptions"] or 0),
        }
    return out


async def notify_overdue_stages(db: AsyncSession) -> int:
    """Tell each stage owner once when an application in their stage runs overdue.

    Run by the reminder sweep. Deduplicated per application, stage and entry
    time, so a candidate who moves on and comes back is announced again, and
    one who stays overdue for a week is announced once. Caller commits.
    """
    rows = (
        await db.execute(
            text(
                "SELECT e.id, e.company_id, e.requisition_id, a.full_name,"
                "       COALESCE(cur.title, 'Final decision') AS stage,"
                "       enrolment_stage_entered_at(e.id, e.created_at) AS entered_at,"
                "       s.sla_hours, s.owner_user_id"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id AND a.deleted_at IS NULL"
                "  LEFT JOIN workflow_rounds cur ON cur.id = e.current_round_id"
                "  JOIN workflow_stage_settings s"
                "    ON s.workflow_id = e.workflow_id"
                "   AND s.round_id IS NOT DISTINCT FROM e.current_round_id"
                " WHERE e.deleted_at IS NULL AND e.status NOT IN ('hired', 'rejected')"
                "   AND s.sla_hours IS NOT NULL AND s.owner_user_id IS NOT NULL"
                "   AND (e.current_round_id IS NOT NULL"
                "        OR enrolment_awaits_human(e.status, e.current_round_id))"
                "   AND enrolment_stage_entered_at(e.id, e.created_at)"
                "       + make_interval(hours => s.sla_hours) < now()"
                " LIMIT 500"
            )
        )
    ).mappings().all()
    sent = 0
    for r in rows:
        ok = await create_notification(
            db, user_id=r["owner_user_id"], kind="stage_overdue",
            title=f"{r['full_name']} is overdue at {r['stage']}",
            body=f"The {r['sla_hours']}-hour SLA for this stage has passed.",
            link=f"/hr/requisitions/{r['requisition_id']}/decisions",
            dedupe_key=f"sla-overdue:{r['id']}:{r['stage']}:{r['entered_at'].isoformat()}",
        )
        sent += int(ok)
    return sent


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
def _exception_out(r: Any) -> dict[str, Any]:
    return {
        "exception_id": str(r["id"]),
        "enrolment_id": str(r["enrolment_id"]),
        "stage": r["stage_label"],
        "reason": r["reason"],
        "status": r["status"],
        "owner_user_id": str(r["owner_user_id"]) if r["owner_user_id"] else None,
        "owner_name": r["owner_name"],
        "raised_by_name": r["raised_by_name"],
        "raised_at": r["raised_at"].isoformat(),
        "resolved_by_name": r["resolved_by_name"],
        "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
        "resolution_note": r["resolution_note"],
    }


_EXCEPTIONS_SQL = """
SELECT x.id, x.enrolment_id, x.stage_label, x.reason, x.status, x.owner_user_id,
       x.raised_at, x.resolved_at, x.resolution_note,
       o.full_name AS owner_name, rb.full_name AS raised_by_name,
       sb.full_name AS resolved_by_name
  FROM stage_exceptions x
  LEFT JOIN users o ON o.id = x.owner_user_id
  LEFT JOIN users rb ON rb.id = x.raised_by_user_id
  LEFT JOIN users sb ON sb.id = x.resolved_by_user_id
 WHERE x.company_id = :c AND x.enrolment_id = :e
 ORDER BY x.raised_at DESC
"""


async def list_exceptions(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_id: uuid.UUID
) -> list[dict[str, Any]]:
    rows = (
        await db.execute(text(_EXCEPTIONS_SQL), {"c": company_id, "e": enrolment_id})
    ).mappings().all()
    return [_exception_out(r) for r in rows]


def _clean_reason(reason: str) -> str:
    cleaned = (reason or "").strip()
    if not REASON_MIN <= len(cleaned) <= REASON_MAX:
        raise StageError(
            422, f"Say what is blocking this ({REASON_MIN}-{REASON_MAX} characters)."
        )
    return cleaned


async def raise_exception(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    enrolment_id: uuid.UUID,
    reason: str,
    owner_user_id: uuid.UUID | None,
    actor: uuid.UUID,
) -> dict[str, Any]:
    """Record that an application cannot proceed normally. Changes nothing else. Caller commits."""
    why = _clean_reason(reason)
    e = (
        await db.execute(
            text(
                "SELECT e.id, e.status, e.current_round_id, e.requisition_id,"
                "       COALESCE(cur.title, 'Final decision') AS stage, a.full_name,"
                "       ((a.full_name = '[redacted]' AND a.email IS NULL)"
                "        OR EXISTS (SELECT 1 FROM erasure_requests er"
                "                    WHERE er.user_id = a.user_id)) AS candidate_erased"
                "  FROM enrolments e"
                "  JOIN applicants a ON a.id = e.applicant_id"
                "  LEFT JOIN workflow_rounds cur ON cur.id = e.current_round_id"
                " WHERE e.id = :e AND e.company_id = :c AND e.deleted_at IS NULL"
            ),
            {"e": enrolment_id, "c": company_id},
        )
    ).mappings().first()
    if e is None:
        raise StageError(404, "Application not found.")
    if e["candidate_erased"]:
        raise StageError(
            409, "This candidate's personal data has been erased or is being erased, so "
                 "nothing more can be recorded about them."
        )
    if e["status"] in ("hired", "rejected"):
        raise StageError(409, "A final decision is already recorded for this application.")
    owner = owner_user_id or actor
    await _assert_owner_eligible(db, company_id=company_id, user_id=owner)
    exc_id = uuid.uuid4()
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO stage_exceptions (id, company_id, enrolment_id, round_id, stage_label,"
            " reason, owner_user_id, raised_by_user_id, raised_at, status, updated_at)"
            " VALUES (:i, :c, :e, :r, :st, :why, :o, :u, :n, 'open', :n)"
        ),
        {"i": exc_id, "c": company_id, "e": enrolment_id, "r": e["current_round_id"],
         "st": e["stage"], "why": why, "o": owner, "u": actor, "n": now},
    )
    # The reason stays on the row, where erasure can redact it; the audit log
    # keeps only that one was given and how long.
    _audit(db, actor=actor, action="stage.exception.raised", resource_type="enrolment",
           resource_id=enrolment_id,
           details={"company_id": str(company_id), "exception_id": str(exc_id),
                    "stage": e["stage"], "owner_user_id": str(owner),
                    "reason_chars": len(why)})
    if owner != actor:
        await create_notification(
            db, user_id=owner, kind="stage_exception",
            title=f"An exception needs you: {e['full_name']} at {e['stage']}",
            body="Open the application to see what is blocking it.",
            link=f"/hr/requisitions/{e['requisition_id']}/decisions",
        )
    return {"exception_id": str(exc_id), "status": "open", "stage": e["stage"],
            "owner_user_id": str(owner)}


async def _load_exception(
    db: AsyncSession, *, company_id: uuid.UUID, exception_id: uuid.UUID
) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT x.id, x.enrolment_id, x.status, x.owner_user_id, x.stage_label,"
                "       x.redacted_at,"
                "       ((a.full_name = '[redacted]' AND a.email IS NULL)"
                "        OR EXISTS (SELECT 1 FROM erasure_requests er"
                "                    WHERE er.user_id = a.user_id)) AS candidate_erased"
                "  FROM stage_exceptions x"
                "  JOIN enrolments e ON e.id = x.enrolment_id"
                "  JOIN applicants a ON a.id = e.applicant_id"
                " WHERE x.id = :i AND x.company_id = :c FOR UPDATE OF x"
            ),
            {"i": exception_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise StageError(404, "Exception not found.")
    return dict(row)


async def resolve_exception(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    exception_id: uuid.UUID,
    note: str | None,
    actor: uuid.UUID,
) -> dict[str, Any]:
    """Close an exception with how it was dealt with. Changes nothing else. Caller commits."""
    cleaned = (note or "").strip() or None
    if cleaned and len(cleaned) > REASON_MAX:
        raise StageError(422, f"A resolution note is at most {REASON_MAX} characters.")
    row = await _load_exception(db, company_id=company_id, exception_id=exception_id)
    if row["status"] != "open":
        raise StageError(409, "This exception is already resolved.")
    if row["redacted_at"] is not None or row["candidate_erased"]:
        # The exception can still be closed, but nothing more is written about
        # a person whose data has been (or is being) erased.
        cleaned = None
    await db.execute(
        text(
            "UPDATE stage_exceptions SET status = 'resolved', resolved_by_user_id = :u,"
            " resolved_at = now(), resolution_note = :note, updated_at = now() WHERE id = :i"
        ),
        {"u": actor, "note": cleaned, "i": exception_id},
    )
    _audit(db, actor=actor, action="stage.exception.resolved", resource_type="enrolment",
           resource_id=row["enrolment_id"],
           details={"company_id": str(company_id), "exception_id": str(exception_id),
                    "stage": row["stage_label"], "note_chars": len(cleaned or "")})
    return {"exception_id": str(exception_id), "status": "resolved"}


async def reassign_exception(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    exception_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    actor: uuid.UUID,
) -> dict[str, Any]:
    """Give an open exception to someone else. Caller commits."""
    row = await _load_exception(db, company_id=company_id, exception_id=exception_id)
    if row["status"] != "open":
        raise StageError(409, "A resolved exception cannot be reassigned.")
    await _assert_owner_eligible(db, company_id=company_id, user_id=owner_user_id)
    if row["owner_user_id"] == owner_user_id:
        return {"exception_id": str(exception_id), "owner_user_id": str(owner_user_id)}
    await db.execute(
        text("UPDATE stage_exceptions SET owner_user_id = :o, updated_at = now() WHERE id = :i"),
        {"o": owner_user_id, "i": exception_id},
    )
    _audit(db, actor=actor, action="stage.exception.reassigned", resource_type="enrolment",
           resource_id=row["enrolment_id"],
           details={"company_id": str(company_id), "exception_id": str(exception_id),
                    "owner_before": str(row["owner_user_id"]) if row["owner_user_id"] else None,
                    "owner_after": str(owner_user_id)})
    await create_notification(
        db, user_id=owner_user_id, kind="stage_exception",
        title=f"An exception at {row['stage_label']} was passed to you",
        body="Open the application to see what is blocking it.", link="/hr",
    )
    return {"exception_id": str(exception_id), "owner_user_id": str(owner_user_id)}


async def eligible_owners(db: AsyncSession, *, company_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT u.id, u.full_name, u.email FROM users u"
                "  JOIN user_roles ur ON ur.user_id = u.id"
                "  JOIN roles r ON r.id = ur.role_id AND r.name = ANY(:roles)"
                " WHERE u.company_id = :c AND u.deleted_at IS NULL AND u.is_active"
                " ORDER BY u.full_name"
            ),
            {"c": company_id, "roles": list(OWNER_ROLES)},
        )
    ).mappings().all()
    return [{"user_id": str(r["id"]), "full_name": r["full_name"] or r["email"],
             "email": r["email"]} for r in rows]
