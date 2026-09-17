"""Interview kits and interviewer notes — PH4-A5.

A kit is guidance for the person running a human interview: instructions, notes
from HR, and per criterion what to evaluate, what to look for, and suggested
probes. It exists so three interviewers run the same interview, not three
different ones.

THE KIT NEVER HOLDS THE RUBRIC
Criteria live in ``round_criteria``, frozen at publish. The kit keys its
guidance by ``competency_id`` and is validated against the round's frozen
criteria on every write, so it can neither duplicate a criterion nor introduce
one. What an interviewer sees as "Evaluation criteria" is read straight from the
frozen table — the same rows the scorecard scores against.

PROBES ARE GUIDANCE, NOT A SCRIPT
Two sources, kept apart so the interviewer can tell them apart: the probes
frozen into the published rubric, and whatever HR adds in the kit. Neither is
mandatory; the UI says so.

NO KIT IS FINE
Every read returns the criteria and their frozen probes whether or not HR has
written a kit. A round without one must still be interviewable — that is the
"existing scorecards keep working" criterion.

NOTES ARE PRIVATE
``interviewer_notes`` belong to the interviewer: never shown to HR, never part
of the submission, erased under DPDP. They are keyed on the interview rather
than the scorecard, so opening a correction does not strand them.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.interviewer_scorecards import (
    SCORABLE_ROUND_KINDS,
    RequestMeta,
    ScorecardError,
    _load_owned,
)
from app.models import AuditLog
from app.workflows import load_criteria

log = structlog.get_logger(__name__)

GUIDANCE_FIELDS: tuple[str, ...] = ("what_to_evaluate", "look_for", "probes")
MAX_ITEMS = 10
MAX_ITEM_CHARS = 300
MAX_LONG_TEXT = 8000
MAX_NOTES = 20000


@dataclass(frozen=True)
class KitCriterionIn:
    competency_id: str
    what_to_evaluate: list[str]
    look_for: list[str]
    probes: list[str]


def _clean_list(items: list[str] | None, *, field: str, name: str) -> list[str]:
    """Trimmed, de-blanked, bounded. Order is the author's."""
    out = [str(i).strip() for i in (items or []) if str(i).strip()]
    if len(out) > MAX_ITEMS:
        raise ScorecardError(422, f"{name}: at most {MAX_ITEMS} items in '{field}'.")
    for item in out:
        if len(item) > MAX_ITEM_CHARS:
            raise ScorecardError(
                422, f"{name}: each item in '{field}' is limited to {MAX_ITEM_CHARS} characters."
            )
    return out


def _clean_text(value: str | None, *, label: str, limit: int = MAX_LONG_TEXT) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if len(stripped) > limit:
        raise ScorecardError(422, f"{label} is limited to {limit} characters.")
    return stripped or None


async def _round(db: AsyncSession, *, company_id: uuid.UUID, round_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await db.execute(
            text(
                "SELECT id, title, kind FROM workflow_rounds"
                " WHERE id = :r AND company_id = :c AND deleted_at IS NULL"
            ),
            {"r": round_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise ScorecardError(404, "Round not found.")
    return dict(row)


def _shape(
    round_title: str, kit: dict[str, Any] | None, criteria: list[dict[str, Any]]
) -> dict[str, Any]:
    guidance: dict[str, Any] = (kit or {}).get("guidance") or {}
    return {
        "round_title": round_title,
        "instructions": (kit or {}).get("instructions"),
        "interviewer_notes_from_hr": (kit or {}).get("interviewer_notes"),
        "has_custom_kit": kit is not None,
        "updated_at": kit["updated_at"].isoformat() if kit else None,
        "criteria": [
            {
                "competency_id": c["competency_id"],
                "competency_name": c["competency_name"],
                "weight": c["weight"],
                "anchors": c.get("anchors"),
                "frozen_probes": [str(p) for p in (c.get("probes") or [])],
                **{
                    f: [str(v) for v in (guidance.get(c["competency_id"], {}).get(f) or [])]
                    for f in GUIDANCE_FIELDS
                },
            }
            for c in criteria
        ],
    }


async def _load_kit(db: AsyncSession, round_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await db.execute(
            text(
                "SELECT instructions, interviewer_notes, guidance, updated_at"
                "  FROM interview_kits WHERE round_id = :r"
            ),
            {"r": round_id},
        )
    ).mappings().first()
    return dict(row) if row else None


async def get_kit(
    db: AsyncSession, *, company_id: uuid.UUID, round_id: uuid.UUID
) -> dict[str, Any]:
    """The kit for a round, for HR. Human interview rounds only."""
    round_ = await _round(db, company_id=company_id, round_id=round_id)
    if round_["kind"] not in SCORABLE_ROUND_KINDS:
        raise ScorecardError(
            409, f"'{round_['title']}' is scored by the system; interview kits are for "
                 "human interview rounds."
        )
    criteria = (await load_criteria(db, [round_id])).get(str(round_id), [])
    return _shape(round_["title"], await _load_kit(db, round_id), criteria)


async def update_kit(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    round_id: uuid.UUID,
    actor: uuid.UUID,
    instructions: str | None,
    interviewer_notes: str | None,
    criteria: list[KitCriterionIn],
    meta: RequestMeta,
) -> dict[str, Any]:
    """Replace a round's kit. Caller commits.

    Audited with WHAT changed — "System Design guidance", "instructions" — not
    with the text itself: the audit log answers "who changed the kit, when, and
    which part", and the kit row already holds the current wording.
    """
    round_ = await _round(db, company_id=company_id, round_id=round_id)
    if round_["kind"] not in SCORABLE_ROUND_KINDS:
        raise ScorecardError(
            409, f"'{round_['title']}' is scored by the system; interview kits are for "
                 "human interview rounds."
        )
    frozen = (await load_criteria(db, [round_id])).get(str(round_id), [])
    names = {c["competency_id"]: c["competency_name"] for c in frozen}

    guidance: dict[str, dict[str, list[str]]] = {}
    seen: set[str] = set()
    for item in criteria:
        if item.competency_id not in names:
            # The kit cannot add a criterion, and it says so rather than
            # silently dropping the entry.
            raise ScorecardError(
                422, "The kit can only give guidance on this round's existing criteria; "
                     "criteria themselves are changed by publishing a new workflow version."
            )
        # Tracked separately from `guidance`, which only keeps entries with
        # content — checking membership there let a duplicate through whenever
        # its first occurrence was empty.
        if item.competency_id in seen:
            raise ScorecardError(422, f"{names[item.competency_id]} appears twice.")
        seen.add(item.competency_id)
        name = names[item.competency_id]
        entry = {
            "what_to_evaluate": _clean_list(item.what_to_evaluate, field="what to evaluate", name=name),
            "look_for": _clean_list(item.look_for, field="look for", name=name),
            "probes": _clean_list(item.probes, field="probes", name=name),
        }
        if any(entry.values()):
            guidance[item.competency_id] = entry

    new_instructions = _clean_text(instructions, label="Instructions")
    new_notes = _clean_text(interviewer_notes, label="Notes for interviewers")

    current = await _load_kit(db, round_id)
    old_guidance: dict[str, Any] = (current or {}).get("guidance") or {}
    changed: list[str] = []
    if (current or {}).get("instructions") != new_instructions:
        changed.append("instructions")
    if (current or {}).get("interviewer_notes") != new_notes:
        changed.append("notes for interviewers")
    for cid in sorted(set(guidance) | set(old_guidance)):
        if guidance.get(cid) != old_guidance.get(cid):
            changed.append(f"{names.get(cid, cid)} guidance")

    if not changed and current is not None:
        return _shape(round_["title"], current, frozen)

    await db.execute(
        text(
            "INSERT INTO interview_kits (id, company_id, round_id, instructions, interviewer_notes,"
            " guidance, updated_by_user_id, created_at, updated_at)"
            " VALUES (:id, :c, :r, :ins, :notes, CAST(:g AS jsonb), :by, now(), now())"
            " ON CONFLICT (round_id) DO UPDATE SET"
            "   instructions = EXCLUDED.instructions,"
            "   interviewer_notes = EXCLUDED.interviewer_notes,"
            "   guidance = EXCLUDED.guidance,"
            "   updated_by_user_id = EXCLUDED.updated_by_user_id,"
            "   updated_at = now()"
        ),
        {"id": uuid.uuid4(), "c": company_id, "r": round_id, "ins": new_instructions,
         "notes": new_notes, "g": json.dumps(guidance), "by": actor},
    )
    db.add(
        AuditLog(
            actor_id=actor,
            actor_type="user",
            action="interview_kit.updated",
            resource_type="workflow_round",
            resource_id=round_id,
            details={"company_id": str(company_id), "round_title": round_["title"],
                     "changed": changed or ["created"]},
            ip_address=meta.ip_address,
            user_agent=meta.user_agent,
            event_ts=datetime.now(tz=UTC),
        )
    )
    await db.flush()
    log.info("interview_kit.updated", round_id=str(round_id), changed=changed)
    return _shape(round_["title"], await _load_kit(db, round_id), frozen)


async def kit_for_scorecard(
    db: AsyncSession,
    *,
    scorecard_id: uuid.UUID,
    interviewer_user_id: uuid.UUID,
    company_id: uuid.UUID,
) -> dict[str, Any]:
    """The kit for an interview assigned to the caller — and only theirs."""
    card = await _load_owned(
        db, scorecard_id=scorecard_id, interviewer_user_id=interviewer_user_id,
        company_id=company_id,
    )
    round_id = uuid.UUID(str(card["round_id"]))
    criteria = (await load_criteria(db, [round_id])).get(str(round_id), [])
    return _shape(card["round_title"], await _load_kit(db, round_id), criteria)


async def get_notes(
    db: AsyncSession,
    *,
    scorecard_id: uuid.UUID,
    interviewer_user_id: uuid.UUID,
    company_id: uuid.UUID,
) -> dict[str, Any]:
    card = await _load_owned(
        db, scorecard_id=scorecard_id, interviewer_user_id=interviewer_user_id,
        company_id=company_id,
    )
    row = (
        await db.execute(
            text(
                "SELECT notes, updated_at FROM interviewer_notes"
                " WHERE round_id = :r AND enrolment_id = :e AND interviewer_user_id = :iv"
            ),
            {"r": card["round_id"], "e": card["enrolment_id"], "iv": interviewer_user_id},
        )
    ).mappings().first()
    return {
        "notes": row["notes"] if row else "",
        "updated_at": row["updated_at"].isoformat() if row else None,
    }


async def save_notes(
    db: AsyncSession,
    *,
    scorecard_id: uuid.UUID,
    interviewer_user_id: uuid.UUID,
    company_id: uuid.UUID,
    notes: str,
) -> dict[str, Any]:
    """Store the caller's private notes. Caller commits.

    Not audited, deliberately: the audit log is readable by the platform owner,
    and recording each save of someone's private working notes there would be a
    record of their content's rhythm for no purpose anyone has asked for. The
    scorecard — the thing that is evidence — is fully audited.
    """
    if len(notes) > MAX_NOTES:
        raise ScorecardError(422, f"Notes are limited to {MAX_NOTES} characters.")
    card = await _load_owned(
        db, scorecard_id=scorecard_id, interviewer_user_id=interviewer_user_id,
        company_id=company_id,
    )
    now = datetime.now(tz=UTC)
    await db.execute(
        text(
            "INSERT INTO interviewer_notes (id, company_id, enrolment_id, round_id,"
            " interviewer_user_id, notes, updated_at)"
            " VALUES (:id, :c, :e, :r, :iv, :notes, :n)"
            " ON CONFLICT (round_id, enrolment_id, interviewer_user_id)"
            " DO UPDATE SET notes = EXCLUDED.notes, updated_at = EXCLUDED.updated_at"
        ),
        {"id": uuid.uuid4(), "c": company_id, "e": card["enrolment_id"], "r": card["round_id"],
         "iv": interviewer_user_id, "notes": notes, "n": now},
    )
    return {"updated_at": now.isoformat()}


async def copy_kits(
    db: AsyncSession, *, company_id: uuid.UUID, id_map: dict[str, uuid.UUID]
) -> int:
    """Carry kits forward when a published workflow is cloned for editing.

    A new workflow version gets new round ids; without this, every kit HR wrote
    would silently disappear the moment someone edited the workflow.
    """
    copied = 0
    for old_id, new_id in id_map.items():
        result = await db.execute(
            text(
                "INSERT INTO interview_kits (id, company_id, round_id, instructions,"
                " interviewer_notes, guidance, updated_by_user_id, created_at, updated_at)"
                " SELECT :nid, company_id, :new_round, instructions, interviewer_notes, guidance,"
                "        updated_by_user_id, now(), now()"
                "   FROM interview_kits WHERE round_id = :old_round AND company_id = :c"
                " ON CONFLICT (round_id) DO NOTHING"
            ),
            {"nid": uuid.uuid4(), "new_round": new_id, "old_round": uuid.UUID(old_id),
             "c": company_id},
        )
        copied += getattr(result, "rowcount", 0) or 0
    return copied
