"""Reusable question banks — PH4-D1.

A company-scoped library of MCQ and coding questions, independent of any one
exam. A question is authored as a ``draft``, submitted for review, and
approved by another HR manager (or the company super admin) — never its own
author or submitter, at the database as well as here (migration
``d2a4c6e8f0b3``, trigger ``bank_questions_lifecycle``). A later version
follows an approved-or-retired one in the same lineage (``root_id``); at most
one version per lineage is ever ``approved`` (a partial unique index backstops
the approval race).

Copying a question into an exam is copy-on-add: ``add_to_section`` inserts an
independent ``exam_questions`` / ``coding_questions`` row, and nothing about a
later version of the bank question ever reaches an exam that already copied
an earlier one. The same lineage, at any version, is refused a second time in
one exam (a partial unique index on ``exam_id, source_bank_root_id``).

Callers commit — every function here does at most ``db.flush()``, so a router
can compose several of these calls (or one of these with an ``exam_locks``
check) in a single transaction and either commit all of it or none.

No agent or LLM path touches these tables directly: ``create_drafts_bulk`` is
where an AI-drafted question is SAVED, always as ``origin='ai_draft'`` and
always a plain HR-authored write from that point on — the generation itself
happens in ``app.exam_ai_client`` (via feedback_billing), which this module
never imports.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from shared.intelligence.taxonomy import FAMILIES
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app import exam_locks
from app.config import settings
from app.models import (
    AuditLog,
    BankQuestion,
    BankQuestionEvent,
    CodingQuestion,
    ExamQuestion,
    ExamSection,
    QuestionBank,
)
from app.utils.sql_like import like_literal

log = structlog.get_logger(__name__)

MAX_COMPETENCIES = 8
MAX_TAGS = 20
_COMPETENCY_ID_RE = re.compile(r"^[a-z0-9_]{1,80}$")
_WS_RE = re.compile(r"\s+")

#: Mirrors the T1 trigger's status transition table exactly, so a unit test
#: can pin the two together without a database.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"in_review"}),
    "in_review": frozenset({"draft", "approved"}),
    "approved": frozenset({"retired"}),
    "retired": frozenset(),
}


class QuestionBankError(Exception):
    """Refused. Carries the HTTP status and a sentence for a person."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------
def _normalise(value: str) -> str:
    return _WS_RE.sub(" ", value.strip().casefold())


def content_hash(
    *,
    kind: str,
    prompt: str,
    options: list[str] | None = None,
    correct_index: int | None = None,
    test_cases: list[dict[str, Any]] | None = None,
) -> str:
    """A stable fingerprint of what a question actually asks and expects —
    casefolded, whitespace-collapsed. Two questions that differ only in
    capitalisation or spacing hash the same, which is what the "skip an
    identical question" rule in ``add_to_section`` relies on.
    """
    parts = [kind, _normalise(prompt)]
    if kind == "mcq":
        parts.append(str(correct_index if correct_index is not None else ""))
        parts.extend(_normalise(o) for o in (options or []))
    else:
        for tc in test_cases or []:
            parts.append(_normalise(str(tc.get("stdin", ""))))
            parts.append(_normalise(str(tc.get("expected_output", ""))))
    blob = "\x1f".join(parts).encode()
    return hashlib.sha256(blob).hexdigest()


def validate_competencies(items: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """At most 8, each a ``{id, name}`` pair; ``id`` matches ``^[a-z0-9_]{1,80}$``."""
    items = items or []
    if len(items) > MAX_COMPETENCIES:
        raise QuestionBankError(422, f"At most {MAX_COMPETENCIES} competencies.")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        cid = str(item.get("id", "")).strip().lower()
        name = str(item.get("name", "")).strip()
        if not _COMPETENCY_ID_RE.match(cid):
            raise QuestionBankError(422, f"'{cid or '(blank)'}' is not a valid competency id.")
        if not name:
            raise QuestionBankError(422, "Each competency needs a name.")
        if cid in seen:
            continue
        seen.add(cid)
        out.append({"id": cid, "name": name})
    return out


def _clean_tags(tags: list[str] | None) -> list[str] | None:
    if tags is None:
        return None
    out: list[str] = []
    for t in tags:
        t = t.strip().lower()
        if t and t not in out:
            out.append(t)
    return out[:MAX_TAGS] or None


def picker_skip_reason(
    bq: BankQuestion, *, section_kind: str, existing_roots: set[uuid.UUID],
    existing_hashes: set[str],
) -> str | None:
    """Why ``add_to_section`` would skip this bank question, or ``None`` to add
    it. Pure — the picker's row-disable logic and the endpoint's own decision
    are one function, so they can never silently drift apart."""
    if bq.status != "approved":
        return "not approved"
    if bq.kind != section_kind:
        return f"this section is {section_kind}, not {bq.kind}"
    if bq.root_id in existing_roots:
        return "already in this exam"
    if bq.content_hash in existing_hashes:
        return "an identical question is already in this exam"
    return None


def question_out(q: BankQuestion) -> dict[str, Any]:
    """The HR-facing shape of one bank question, at any status."""
    return {
        "id": str(q.id), "bank_id": str(q.bank_id), "root_id": str(q.root_id), "version": q.version,
        "kind": q.kind, "prompt": q.prompt, "points": q.points,
        "options": list(q.options) if q.options is not None else None,
        "correct_index": q.correct_index,
        "starter_code": q.starter_code, "reference_solution": q.reference_solution,
        "allowed_languages": list(q.allowed_languages) if q.allowed_languages is not None else None,
        "test_cases": list(q.test_cases) if q.test_cases is not None else None,
        "time_limit_ms": q.time_limit_ms,
        "difficulty": q.difficulty, "language": q.language,
        "competencies": list(q.competencies) if q.competencies is not None else [],
        "tags": list(q.tags) if q.tags is not None else [],
        "status": q.status, "origin": q.origin, "content_hash": q.content_hash,
        "created_by_user_id": str(q.created_by_user_id) if q.created_by_user_id else None,
        "submitted_by_user_id": str(q.submitted_by_user_id) if q.submitted_by_user_id else None,
        "submitted_at": q.submitted_at.isoformat() if q.submitted_at else None,
        "reviewed_by_user_id": str(q.reviewed_by_user_id) if q.reviewed_by_user_id else None,
        "reviewed_at": q.reviewed_at.isoformat() if q.reviewed_at else None,
        "review_note": q.review_note,
        "retired_by_user_id": str(q.retired_by_user_id) if q.retired_by_user_id else None,
        "retired_at": q.retired_at.isoformat() if q.retired_at else None,
        "created_at": q.created_at.isoformat(), "updated_at": q.updated_at.isoformat(),
    }


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------
def _event(
    db: AsyncSession, *, company_id: uuid.UUID, bank_question_id: uuid.UUID, action: str,
    actor: uuid.UUID | None, details: dict[str, Any] | None = None,
) -> None:
    db.add(BankQuestionEvent(
        id=uuid.uuid4(), company_id=company_id, bank_question_id=bank_question_id,
        action=action, actor_user_id=actor, details=details or {}, created_at=datetime.now(tz=UTC),
    ))


def _audit(
    db: AsyncSession, *, actor: uuid.UUID | None, action: str, resource_id: uuid.UUID,
    details: dict[str, Any],
) -> None:
    db.add(AuditLog(
        actor_id=actor, actor_type="user", action=action, resource_type="bank_question",
        resource_id=resource_id, details=details, event_ts=datetime.now(tz=UTC),
    ))


# ---------------------------------------------------------------------------
# Lookups (tenant isolation lives here)
# ---------------------------------------------------------------------------
async def _get_bank(db: AsyncSession, company_id: uuid.UUID, bank_id: uuid.UUID) -> QuestionBank:
    bank = await db.scalar(
        select(QuestionBank).where(
            QuestionBank.id == bank_id, QuestionBank.company_id == company_id,
            QuestionBank.archived_at.is_(None),
        )
    )
    if bank is None:
        raise QuestionBankError(404, "Question bank not found.")
    return bank


async def _get_question(db: AsyncSession, company_id: uuid.UUID, qid: uuid.UUID) -> BankQuestion:
    q = await db.scalar(
        select(BankQuestion).where(BankQuestion.id == qid, BankQuestion.company_id == company_id)
    )
    if q is None:
        raise QuestionBankError(404, "Question not found.")
    return q


async def _used_in_any_exam(db: AsyncSession, bank_question_id: uuid.UUID) -> bool:
    return bool(
        await db.scalar(
            text(
                "SELECT EXISTS ("
                " SELECT 1 FROM exam_questions WHERE source_bank_question_id = :q"
                " UNION ALL"
                " SELECT 1 FROM coding_questions WHERE source_bank_question_id = :q"
                ")"
            ),
            {"q": bank_question_id},
        )
    )


# ---------------------------------------------------------------------------
# Banks
# ---------------------------------------------------------------------------
async def create_bank(
    db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID, name: str,
    description: str | None = None,
) -> QuestionBank:
    name = name.strip()
    if not (1 <= len(name) <= 120):
        raise QuestionBankError(422, "Name must be 1-120 characters.")
    clash = await db.scalar(
        select(QuestionBank.id).where(
            QuestionBank.company_id == company_id, QuestionBank.archived_at.is_(None),
            func.lower(QuestionBank.name) == name.casefold(),
        )
    )
    if clash is not None:
        raise QuestionBankError(409, "A bank with this name already exists.")
    now = datetime.now(tz=UTC)
    bank = QuestionBank(
        id=uuid.uuid4(), company_id=company_id, name=name, description=description,
        created_by_user_id=actor, created_at=now, updated_at=now,
    )
    db.add(bank)
    await db.flush()
    return bank


async def update_bank(
    db: AsyncSession, *, company_id: uuid.UUID, bank_id: uuid.UUID,
    name: str | None = None, description: str | None = None,
) -> QuestionBank:
    bank = await _get_bank(db, company_id, bank_id)
    if name is not None:
        name = name.strip()
        if not (1 <= len(name) <= 120):
            raise QuestionBankError(422, "Name must be 1-120 characters.")
        clash = await db.scalar(
            select(QuestionBank.id).where(
                QuestionBank.company_id == company_id, QuestionBank.archived_at.is_(None),
                QuestionBank.id != bank_id, func.lower(QuestionBank.name) == name.casefold(),
            )
        )
        if clash is not None:
            raise QuestionBankError(409, "A bank with this name already exists.")
        bank.name = name
    if description is not None:
        bank.description = description
    bank.updated_at = datetime.now(tz=UTC)
    return bank


async def archive_bank(db: AsyncSession, *, company_id: uuid.UUID, bank_id: uuid.UUID) -> QuestionBank:
    bank = await _get_bank(db, company_id, bank_id)
    bank.archived_at = datetime.now(tz=UTC)
    return bank


async def _bank_summary(db: AsyncSession, *, company_id: uuid.UUID, bank: QuestionBank) -> dict[str, Any]:
    counts: dict[str, int] = dict(
        (
            await db.execute(
                select(BankQuestion.status, func.count()).where(
                    BankQuestion.bank_id == bank.id, BankQuestion.company_id == company_id,
                ).group_by(BankQuestion.status)
            )
        ).tuples().all()
    )
    usage = await db.scalar(
        text(
            "SELECT count(*) FROM ("
            " SELECT 1 FROM exam_questions e JOIN bank_questions q ON q.id = e.source_bank_question_id"
            "  WHERE q.bank_id = :b AND e.deleted_at IS NULL"
            " UNION ALL"
            " SELECT 1 FROM coding_questions c JOIN bank_questions q"
            "   ON q.id = c.source_bank_question_id"
            "  WHERE q.bank_id = :b AND c.deleted_at IS NULL"
            ") x"
        ),
        {"b": bank.id},
    )
    return {
        "id": str(bank.id), "name": bank.name, "description": bank.description,
        "counts": {k: int(v) for k, v in counts.items()},
        "used_in_exams": int(usage or 0),
        "created_at": bank.created_at.isoformat(), "updated_at": bank.updated_at.isoformat(),
    }


async def bank_summary(db: AsyncSession, *, company_id: uuid.UUID, bank_id: uuid.UUID) -> dict[str, Any]:
    bank = await _get_bank(db, company_id, bank_id)
    return await _bank_summary(db, company_id=company_id, bank=bank)


async def list_banks(db: AsyncSession, *, company_id: uuid.UUID) -> list[dict[str, Any]]:
    banks = (
        await db.execute(
            select(QuestionBank)
            .where(QuestionBank.company_id == company_id, QuestionBank.archived_at.is_(None))
            .order_by(QuestionBank.created_at.desc())
        )
    ).scalars().all()
    return [await _bank_summary(db, company_id=company_id, bank=b) for b in banks]


# ---------------------------------------------------------------------------
# Questions — authoring
# ---------------------------------------------------------------------------
async def create_question(
    db: AsyncSession, *, company_id: uuid.UUID, bank_id: uuid.UUID, actor: uuid.UUID | None,
    kind: str, prompt: str, points: int, difficulty: str, language: str = "en",
    competencies: list[dict[str, str]] | None = None, tags: list[str] | None = None,
    options: list[str] | None = None, correct_index: int | None = None,
    starter_code: str | None = None, reference_solution: str | None = None,
    allowed_languages: list[str] | None = None, test_cases: list[dict[str, Any]] | None = None,
    time_limit_ms: int | None = None, origin: str = "authored",
) -> BankQuestion:
    bank = await _get_bank(db, company_id, bank_id)
    comps = validate_competencies(competencies)
    now = datetime.now(tz=UTC)
    qid = uuid.uuid4()
    q = BankQuestion(
        id=qid, company_id=company_id, bank_id=bank.id, root_id=qid, version=1, kind=kind,
        prompt=prompt.strip(), points=points, options=options, correct_index=correct_index,
        starter_code=starter_code, reference_solution=reference_solution,
        allowed_languages=allowed_languages, test_cases=test_cases, time_limit_ms=time_limit_ms,
        difficulty=difficulty, language=language, competencies=comps or None,
        competency_ids=[c["id"] for c in comps] or None, tags=_clean_tags(tags),
        status="draft", origin=origin,
        content_hash=content_hash(
            kind=kind, prompt=prompt, options=options, correct_index=correct_index,
            test_cases=test_cases,
        ),
        created_by_user_id=actor, created_at=now, updated_at=now,
    )
    db.add(q)
    await db.flush()
    _event(db, company_id=company_id, bank_question_id=q.id, action="created", actor=actor)
    return q


async def create_drafts_bulk(
    db: AsyncSession, *, company_id: uuid.UUID, bank_id: uuid.UUID, actor: uuid.UUID | None,
    items: list[dict[str, Any]],
) -> list[BankQuestion]:
    """An AI preview, saved as ``origin='ai_draft'`` — always a plain draft
    from this point on, reviewed exactly like anything HR typed by hand."""
    return [
        await create_question(db, company_id=company_id, bank_id=bank_id, actor=actor,
                              origin="ai_draft", **item)
        for item in items
    ]


async def update_question(
    db: AsyncSession, *, company_id: uuid.UUID, qid: uuid.UUID, actor: uuid.UUID | None,
    **fields: Any,
) -> BankQuestion:
    q = await _get_question(db, company_id, qid)
    if q.status != "draft":
        raise QuestionBankError(409, "Only a draft question can be edited.")
    if fields.get("prompt") is not None:
        q.prompt = fields["prompt"].strip()
    for key in ("points", "options", "correct_index", "starter_code", "reference_solution",
               "allowed_languages", "test_cases", "time_limit_ms", "difficulty", "language"):
        if fields.get(key) is not None:
            setattr(q, key, fields[key])
    if fields.get("competencies") is not None:
        comps = validate_competencies(fields["competencies"])
        q.competencies = comps or None
        q.competency_ids = [c["id"] for c in comps] or None
    if fields.get("tags") is not None:
        q.tags = _clean_tags(fields["tags"])
    q.content_hash = content_hash(
        kind=q.kind, prompt=q.prompt, options=q.options, correct_index=q.correct_index,
        test_cases=q.test_cases,
    )
    q.updated_at = datetime.now(tz=UTC)
    _event(db, company_id=company_id, bank_question_id=q.id, action="edited", actor=actor)
    return q


async def delete_question(db: AsyncSession, *, company_id: uuid.UUID, qid: uuid.UUID) -> None:
    q = await _get_question(db, company_id, qid)
    if q.status != "draft":
        raise QuestionBankError(409, "Only a draft question can be deleted.")
    if await _used_in_any_exam(db, q.id):
        raise QuestionBankError(409, "This question has been copied into an exam and cannot be deleted.")
    await db.delete(q)


async def submit(db: AsyncSession, *, company_id: uuid.UUID, qid: uuid.UUID, actor: uuid.UUID) -> BankQuestion:
    q = await _get_question(db, company_id, qid)
    if q.status != "draft":
        raise QuestionBankError(409, "Only a draft can be submitted for review.")
    q.status = "in_review"
    q.submitted_by_user_id = actor
    q.submitted_at = datetime.now(tz=UTC)
    _event(db, company_id=company_id, bank_question_id=q.id, action="submitted", actor=actor)
    _audit(db, actor=actor, action="bank_question.submitted", resource_id=q.id,
          details={"company_id": str(company_id)})
    return q


async def withdraw(db: AsyncSession, *, company_id: uuid.UUID, qid: uuid.UUID, actor: uuid.UUID) -> BankQuestion:
    q = await _get_question(db, company_id, qid)
    if q.status != "in_review":
        raise QuestionBankError(409, "Only a submitted question can be withdrawn.")
    if q.submitted_by_user_id != actor:
        raise QuestionBankError(403, "Only the person who submitted this question can withdraw it.")
    q.status = "draft"
    q.reviewed_by_user_id = None
    q.reviewed_at = None
    q.review_note = None
    _event(db, company_id=company_id, bank_question_id=q.id, action="withdrawn", actor=actor)
    _audit(db, actor=actor, action="bank_question.withdrawn", resource_id=q.id,
          details={"company_id": str(company_id)})
    return q


async def review(
    db: AsyncSession, *, company_id: uuid.UUID, qid: uuid.UUID, actor: uuid.UUID, role: str,
    action: str, note: str | None = None,
) -> BankQuestion:
    """``action`` is ``approve`` or ``request_changes``. ``role`` is
    ``hr_manager`` or ``super_admin`` — recorded on the audit row only; the
    database enforces "not the author, not the submitter" the same way either
    way, and approval alone additionally excludes the author (request_changes
    does not — an author who did not submit their own question may ask for
    changes to it)."""
    if action not in ("approve", "request_changes"):
        raise QuestionBankError(422, "action must be approve or request_changes.")
    q = await _get_question(db, company_id, qid)
    if q.status != "in_review":
        raise QuestionBankError(409, "Only a submitted question can be reviewed.")
    # 409, not 403: this is a conflict with the question's own review state
    # (who may act on it next), the same family as every other lock refusal
    # here — not a missing permission, which the role gate already covers.
    if action == "approve" and actor in (q.created_by_user_id, q.submitted_by_user_id):
        raise QuestionBankError(
            409, "You wrote or submitted this question — another reviewer must approve it."
        )
    if action == "request_changes" and actor == q.submitted_by_user_id:
        raise QuestionBankError(
            409, "You submitted this question — another reviewer must request changes."
        )
    now = datetime.now(tz=UTC)
    if action == "approve":
        prior = await db.scalar(
            select(BankQuestion).where(
                BankQuestion.root_id == q.root_id, BankQuestion.company_id == company_id,
                BankQuestion.status == "approved", BankQuestion.id != q.id,
            )
        )
        if prior is not None:
            prior.status = "retired"
            prior.retired_by_user_id = actor
            prior.retired_at = now
            await db.flush()
            _event(db, company_id=company_id, bank_question_id=prior.id, action="retired",
                  actor=actor, details={"superseded_by": str(q.id)})
        q.status = "approved"
        q.reviewed_by_user_id = actor
        q.reviewed_at = now
        q.review_note = note
        _event(db, company_id=company_id, bank_question_id=q.id, action="approved", actor=actor)
    else:
        if note is None or len(note.strip()) < 5:
            raise QuestionBankError(422, "Say what needs to change.")
        q.status = "draft"
        q.reviewed_by_user_id = actor
        q.reviewed_at = now
        q.review_note = note.strip()
        _event(db, company_id=company_id, bank_question_id=q.id, action="changes_requested",
              actor=actor, details={"has_note": True})
    _audit(db, actor=actor, action=f"bank_question.{action}", resource_id=q.id,
          details={"company_id": str(company_id), "role": role})
    return q


async def new_version(
    db: AsyncSession, *, company_id: uuid.UUID, qid: uuid.UUID, actor: uuid.UUID | None,
    **fields: Any,
) -> BankQuestion:
    src = await _get_question(db, company_id, qid)
    if src.status not in ("approved", "retired"):
        raise QuestionBankError(409, "A new version follows an approved or retired question.")
    next_version = int(
        (await db.scalar(select(func.max(BankQuestion.version)).where(BankQuestion.root_id == src.root_id)))
        or 0
    ) + 1
    comps = (
        validate_competencies(fields["competencies"])
        if fields.get("competencies") is not None
        else (src.competencies or [])
    )
    prompt = fields.get("prompt") or src.prompt
    now = datetime.now(tz=UTC)
    new_id = uuid.uuid4()
    nq = BankQuestion(
        id=new_id, company_id=company_id, bank_id=src.bank_id, root_id=src.root_id,
        version=next_version, kind=src.kind, prompt=prompt.strip(),
        points=fields.get("points") if fields.get("points") is not None else src.points,
        options=fields.get("options") if fields.get("options") is not None else src.options,
        correct_index=(
            fields.get("correct_index") if fields.get("correct_index") is not None
            else src.correct_index
        ),
        starter_code=(
            fields.get("starter_code") if fields.get("starter_code") is not None else src.starter_code
        ),
        reference_solution=(
            fields.get("reference_solution") if fields.get("reference_solution") is not None
            else src.reference_solution
        ),
        allowed_languages=(
            fields.get("allowed_languages") if fields.get("allowed_languages") is not None
            else src.allowed_languages
        ),
        test_cases=fields.get("test_cases") if fields.get("test_cases") is not None else src.test_cases,
        time_limit_ms=(
            fields.get("time_limit_ms") if fields.get("time_limit_ms") is not None
            else src.time_limit_ms
        ),
        difficulty=fields.get("difficulty") or src.difficulty,
        language=fields.get("language") or src.language,
        competencies=comps or None, competency_ids=[c["id"] for c in comps] or None,
        tags=_clean_tags(fields["tags"]) if fields.get("tags") is not None else src.tags,
        status="draft", origin=src.origin,
        content_hash=content_hash(
            kind=src.kind, prompt=prompt,
            options=fields.get("options") if fields.get("options") is not None else src.options,
            correct_index=(
                fields.get("correct_index") if fields.get("correct_index") is not None
                else src.correct_index
            ),
            test_cases=fields.get("test_cases") if fields.get("test_cases") is not None else src.test_cases,
        ),
        created_by_user_id=actor, created_at=now, updated_at=now,
    )
    db.add(nq)
    await db.flush()
    _event(db, company_id=company_id, bank_question_id=nq.id, action="versioned", actor=actor,
          details={"root_id": str(src.root_id), "from_version": src.version, "to_version": next_version})
    _audit(db, actor=actor, action="bank_question.versioned", resource_id=nq.id,
          details={"company_id": str(company_id), "root_id": str(src.root_id)})
    return nq


async def retire(db: AsyncSession, *, company_id: uuid.UUID, qid: uuid.UUID, actor: uuid.UUID) -> BankQuestion:
    q = await _get_question(db, company_id, qid)
    if q.status != "approved":
        raise QuestionBankError(409, "Only an approved question can be retired.")
    q.status = "retired"
    q.retired_by_user_id = actor
    q.retired_at = datetime.now(tz=UTC)
    _event(db, company_id=company_id, bank_question_id=q.id, action="retired", actor=actor)
    _audit(db, actor=actor, action="bank_question.retired", resource_id=q.id,
          details={"company_id": str(company_id)})
    return q


async def get_question(db: AsyncSession, *, company_id: uuid.UUID, qid: uuid.UUID) -> dict[str, Any]:
    q = await _get_question(db, company_id, qid)
    versions = (
        await db.execute(
            select(BankQuestion)
            .where(BankQuestion.root_id == q.root_id, BankQuestion.company_id == company_id)
            .order_by(BankQuestion.version.asc())
        )
    ).scalars().all()
    usage = (
        await db.execute(
            text(
                "SELECT e.exam_id AS exam_id, x.title AS exam_title FROM exam_questions e"
                " JOIN exams x ON x.id = e.exam_id"
                " WHERE e.source_bank_root_id = :r AND e.company_id = :c AND e.deleted_at IS NULL"
                " UNION"
                " SELECT c.exam_id AS exam_id, x.title AS exam_title FROM coding_questions c"
                " JOIN exams x ON x.id = c.exam_id"
                " WHERE c.source_bank_root_id = :r AND c.company_id = :c AND c.deleted_at IS NULL"
            ),
            {"r": q.root_id, "c": company_id},
        )
    ).mappings().all()
    return {
        "question": question_out(q),
        "versions": [question_out(v) for v in versions],
        "used_in_exams": [{"exam_id": str(r["exam_id"]), "exam_title": r["exam_title"]} for r in usage],
    }


# ---------------------------------------------------------------------------
# Search, the review queue, and the competency catalogue
# ---------------------------------------------------------------------------
async def search(
    db: AsyncSession, *, company_id: uuid.UUID, bank_id: uuid.UUID | None = None,
    q: str | None = None, kind: str | None = None, difficulty: str | None = None,
    language: str | None = None, competency: str | None = None, tag: str | None = None,
    status: str | None = "approved", exclude_exam_id: uuid.UUID | None = None,
) -> list[dict[str, Any]]:
    stmt = (
        select(BankQuestion, QuestionBank.name.label("bank_name"))
        .join(QuestionBank, QuestionBank.id == BankQuestion.bank_id)
        .where(BankQuestion.company_id == company_id, QuestionBank.archived_at.is_(None))
    )
    if status:
        stmt = stmt.where(BankQuestion.status == status)
    if bank_id:
        stmt = stmt.where(BankQuestion.bank_id == bank_id)
    if kind:
        stmt = stmt.where(BankQuestion.kind == kind)
    if difficulty:
        stmt = stmt.where(BankQuestion.difficulty == difficulty)
    if language:
        stmt = stmt.where(BankQuestion.language == language)
    if competency:
        stmt = stmt.where(BankQuestion.competency_ids.contains([competency]))
    if tag:
        stmt = stmt.where(BankQuestion.tags.contains([tag]))
    if q:
        stmt = stmt.where(BankQuestion.prompt.ilike(f"%{like_literal(q)}%", escape="\\"))
    stmt = stmt.order_by(BankQuestion.updated_at.desc()).limit(200)
    rows = (await db.execute(stmt)).all()

    already: set[uuid.UUID] = set()
    if exclude_exam_id:
        already = set(
            (
                await db.execute(
                    text(
                        "SELECT source_bank_root_id FROM exam_questions"
                        " WHERE exam_id = :e AND deleted_at IS NULL"
                        "   AND source_bank_root_id IS NOT NULL"
                        " UNION"
                        " SELECT source_bank_root_id FROM coding_questions"
                        " WHERE exam_id = :e AND deleted_at IS NULL"
                        "   AND source_bank_root_id IS NOT NULL"
                    ),
                    {"e": exclude_exam_id},
                )
            ).scalars().all()
        )
    return [
        {**question_out(bq), "bank_name": name, "already_in_exam": bq.root_id in already}
        for bq, name in rows
    ]


async def review_queue(db: AsyncSession, *, company_id: uuid.UUID, actor: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            select(BankQuestion, QuestionBank.name.label("bank_name"))
            .join(QuestionBank, QuestionBank.id == BankQuestion.bank_id)
            .where(BankQuestion.company_id == company_id, BankQuestion.status == "in_review")
            .order_by(BankQuestion.submitted_at.asc())
        )
    ).all()
    out: list[dict[str, Any]] = []
    for bq, name in rows:
        own = actor in (bq.created_by_user_id, bq.submitted_by_user_id)
        out.append({
            **question_out(bq), "bank_name": name, "own_submission": own,
            "reason": "You wrote or submitted this question." if own else None,
        })
    return out


async def competency_catalog(db: AsyncSession, *, company_id: uuid.UUID) -> list[dict[str, str]]:
    """The role engine's baseline competencies, plus whatever this company's
    own rounds already use — so a tag on a bank question uses the same words
    a round's rubric does."""
    seen: dict[str, str] = {}
    for family in FAMILIES.values():
        for comp in family.competencies():
            seen.setdefault(comp.id, comp.name)
    rows = (
        await db.execute(
            text(
                "SELECT DISTINCT competency_id, competency_name FROM round_criteria"
                " WHERE company_id = :c"
            ),
            {"c": company_id},
        )
    ).all()
    for cid, name in rows:
        seen.setdefault(cid, name)
    return [{"id": k, "name": v} for k, v in sorted(seen.items(), key=lambda kv: kv[1].lower())]


# ---------------------------------------------------------------------------
# Copying into an exam
# ---------------------------------------------------------------------------
async def add_to_section(
    db: AsyncSession, *, company_id: uuid.UUID, exam_id: uuid.UUID, section_id: uuid.UUID,
    ids: list[uuid.UUID], actor: uuid.UUID | None,
) -> dict[str, Any]:
    section = await db.scalar(
        select(ExamSection).where(
            ExamSection.id == section_id, ExamSection.exam_id == exam_id,
            ExamSection.company_id == company_id, ExamSection.deleted_at.is_(None),
        )
    )
    if section is None:
        raise QuestionBankError(404, "Section not found.")
    # The T2/T3 lock — checked here, not just left to the trigger, so a locked
    # round's picker gives a sentence instead of a raw conflict.
    await exam_locks.assert_round_editable_by_id(db, company_id, section.round_id)

    existing_hashes: set[str] = set(
        (
            await db.execute(
                text(
                    "SELECT bq.content_hash FROM bank_questions bq"
                    " JOIN exam_questions e ON e.source_bank_question_id = bq.id"
                    " WHERE e.exam_id = :x AND e.deleted_at IS NULL"
                    " UNION"
                    " SELECT bq.content_hash FROM bank_questions bq"
                    " JOIN coding_questions c ON c.source_bank_question_id = bq.id"
                    " WHERE c.exam_id = :x AND c.deleted_at IS NULL"
                ),
                {"x": exam_id},
            )
        ).scalars().all()
    )
    existing_roots: set[uuid.UUID] = set(
        (
            await db.execute(
                text(
                    "SELECT source_bank_root_id FROM exam_questions"
                    " WHERE exam_id = :x AND deleted_at IS NULL AND source_bank_root_id IS NOT NULL"
                    " UNION"
                    " SELECT source_bank_root_id FROM coding_questions"
                    " WHERE exam_id = :x AND deleted_at IS NULL AND source_bank_root_id IS NOT NULL"
                ),
                {"x": exam_id},
            )
        ).scalars().all()
    )

    added = 0
    skipped: list[dict[str, str]] = []
    for qid in ids:
        bq = await db.scalar(
            select(BankQuestion).where(BankQuestion.id == qid, BankQuestion.company_id == company_id)
        )
        if bq is None:
            skipped.append({"id": str(qid), "reason": "not found"})
            continue
        reason = picker_skip_reason(
            bq, section_kind=section.kind, existing_roots=existing_roots,
            existing_hashes=existing_hashes,
        )
        if reason is not None:
            skipped.append({"id": str(qid), "reason": reason})
            continue
        if section.kind == "coding":
            count = await db.scalar(
                select(func.count()).select_from(CodingQuestion).where(
                    CodingQuestion.exam_id == exam_id, CodingQuestion.deleted_at.is_(None),
                )
            )
            if int(count or 0) >= settings.code_max_questions_per_exam:
                skipped.append({"id": str(qid), "reason": "this exam has reached its question limit"})
                continue

        now = datetime.now(tz=UTC)
        if section.kind == "mcq":
            max_pos = await db.scalar(
                select(func.max(ExamQuestion.position)).where(
                    ExamQuestion.section_id == section.id, ExamQuestion.deleted_at.is_(None),
                )
            )
            db.add(ExamQuestion(
                id=uuid.uuid4(), exam_id=exam_id, section_id=section.id, company_id=company_id,
                prompt=bq.prompt, options=list(bq.options or []), correct_index=bq.correct_index,
                points=bq.points, position=(int(max_pos) + 1) if max_pos is not None else 0,
                created_at=now, updated_at=now,
                source_bank_question_id=bq.id, source_bank_root_id=bq.root_id,
                source_bank_version=bq.version,
            ))
        else:
            max_pos = await db.scalar(
                select(func.max(CodingQuestion.position)).where(
                    CodingQuestion.section_id == section.id, CodingQuestion.deleted_at.is_(None),
                )
            )
            db.add(CodingQuestion(
                id=uuid.uuid4(), exam_id=exam_id, section_id=section.id, company_id=company_id,
                prompt=bq.prompt, starter_code=bq.starter_code,
                reference_solution=bq.reference_solution,
                allowed_languages=list(bq.allowed_languages or []),
                test_cases=list(bq.test_cases or []), time_limit_ms=bq.time_limit_ms,
                points=bq.points, position=(int(max_pos) + 1) if max_pos is not None else 0,
                created_at=now, updated_at=now,
                source_bank_question_id=bq.id, source_bank_root_id=bq.root_id,
                source_bank_version=bq.version,
            ))
        await db.flush()
        existing_roots.add(bq.root_id)
        existing_hashes.add(bq.content_hash)
        added += 1
        _event(db, company_id=company_id, bank_question_id=bq.id, action="copied_to_exam",
              actor=actor, details={"exam_id": str(exam_id), "section_id": str(section.id)})
    return {"added": added, "skipped": skipped}


async def save_exam_question_to_bank(
    db: AsyncSession, *, company_id: uuid.UUID, exam_id: uuid.UUID, question_id: uuid.UUID,
    bank_id: uuid.UUID, actor: uuid.UUID | None, kind: str, difficulty: str, language: str = "en",
    competencies: list[dict[str, str]] | None = None, tags: list[str] | None = None,
) -> BankQuestion:
    await _get_bank(db, company_id, bank_id)
    if kind == "mcq":
        src_q = await db.scalar(
            select(ExamQuestion).where(
                ExamQuestion.id == question_id, ExamQuestion.exam_id == exam_id,
                ExamQuestion.company_id == company_id, ExamQuestion.deleted_at.is_(None),
            )
        )
        if src_q is None:
            raise QuestionBankError(404, "Question not found.")
        q = await create_question(
            db, company_id=company_id, bank_id=bank_id, actor=actor, kind="mcq",
            prompt=src_q.prompt, points=src_q.points, difficulty=difficulty, language=language,
            competencies=competencies, tags=tags, options=list(src_q.options or []),
            correct_index=src_q.correct_index, origin="from_exam",
        )
    else:
        src_c = await db.scalar(
            select(CodingQuestion).where(
                CodingQuestion.id == question_id, CodingQuestion.exam_id == exam_id,
                CodingQuestion.company_id == company_id, CodingQuestion.deleted_at.is_(None),
            )
        )
        if src_c is None:
            raise QuestionBankError(404, "Question not found.")
        q = await create_question(
            db, company_id=company_id, bank_id=bank_id, actor=actor, kind="coding",
            prompt=src_c.prompt, points=src_c.points, difficulty=difficulty, language=language,
            competencies=competencies, tags=tags, starter_code=src_c.starter_code,
            reference_solution=src_c.reference_solution,
            allowed_languages=list(src_c.allowed_languages or []),
            test_cases=list(src_c.test_cases or []), time_limit_ms=src_c.time_limit_ms,
            origin="from_exam",
        )
    _event(db, company_id=company_id, bank_question_id=q.id, action="saved_from_exam", actor=actor,
          details={"exam_id": str(exam_id), "exam_question_id": str(question_id)})
    return q
