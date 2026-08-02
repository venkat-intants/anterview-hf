"""Assemble one candidate's signals for the assessment panel.

Everything the panel needs comes from one company-scoped read. The important
work here is NORMALISATION: the four signals live on different scales and a
panel that compares them raw would invent contradictions that are pure unit
error.

    resume ATS   ats_overall            0-100  → as-is
    exam         score_percent          0-100  → as-is
    coding       score_percent          0-100  → as-is
    interview    composite_score         0-10  → ×10

Absence is preserved, never coerced. A candidate who has not sat the exam gets
``available=False``, not a zero — "did not sit" and "failed" are different
facts about a person and collapsing them would produce a confidently wrong
brief.
"""

from __future__ import annotations

import json
from typing import Any

import structlog
from shared.agents import CandidateEvidence, Citation, SignalEvidence
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger(__name__)

# Per-signal detail cap. Four specialists each carrying an unbounded transcript
# would blow both the context window and the per-assessment cost.
MAX_DETAIL_CHARS: int = 6000


class ApplicantNotFoundError(Exception):
    """The applicant does not exist in the caller's company."""


async def gather_candidate_evidence(
    db: AsyncSession, *, company_id: str, applicant_id: str
) -> CandidateEvidence:
    """Build ``CandidateEvidence`` for one applicant.

    Raises ``ApplicantNotFoundError`` when the id is unknown OR belongs to
    another company — deliberately the same error, so a caller cannot probe for
    the existence of records in other tenants.
    """
    applicant = (
        await db.execute(
            text(
                """
                SELECT id, full_name, target_job_title, target_level,
                       ats_overall, ats_summary, ats_strengths, ats_concerns,
                       resume_text
                FROM applicants
                WHERE id = CAST(:aid AS uuid) AND company_id = :cid
                  AND deleted_at IS NULL
                """
            ),
            {"aid": applicant_id, "cid": company_id},
        )
    ).first()
    if applicant is None:
        raise ApplicantNotFoundError(applicant_id)

    signals: list[SignalEvidence] = [
        _resume_signal(applicant),
        await _exam_signal(db, company_id, applicant_id),
        await _coding_signal(db, company_id, applicant_id),
        await _interview_signal(db, company_id, applicant_id, applicant.full_name),
    ]

    return CandidateEvidence(
        applicant_id=str(applicant.id),
        applicant_label=applicant.full_name,
        job_title=applicant.target_job_title,
        signals=signals,
        uncovered_competencies=_uncovered_competencies(signals),
    )


def _resume_signal(row: Any) -> SignalEvidence:
    parts: list[str] = []
    if row.ats_summary:
        parts.append(f"ATS summary: {row.ats_summary}")
    for label, blob in (("Strengths", row.ats_strengths), ("Concerns", row.ats_concerns)):
        if blob:
            items = blob if isinstance(blob, list) else [blob]
            parts.append(f"{label}: " + "; ".join(str(i) for i in items))
    if row.resume_text:
        parts.append(f"Resume text:\n{row.resume_text}")

    return SignalEvidence(
        signal="resume",
        available=row.ats_overall is not None or bool(row.resume_text),
        score_0_100=float(row.ats_overall) if row.ats_overall is not None else None,
        detail="\n\n".join(parts)[:MAX_DETAIL_CHARS],
        citations=[
            Citation(
                kind="applicant",
                id=str(row.id),
                label=row.full_name,
                href=f"/hr/applicants/{row.id}",
            )
        ],
    )


async def _exam_signal(db: AsyncSession, company_id: str, applicant_id: str) -> SignalEvidence:
    """Best submitted MCQ attempt, with per-question outcomes."""
    attempt = (
        await db.execute(
            text(
                """
                SELECT t.id, t.score_percent, t.passed, e.id AS exam_id, e.title
                FROM exam_attempts t
                JOIN exams e ON e.id = t.exam_id
                WHERE t.applicant_id = CAST(:aid AS uuid) AND t.company_id = :cid
                  AND t.status = 'submitted' AND t.deleted_at IS NULL
                  AND COALESCE(e.kind, 'mcq') <> 'coding'
                ORDER BY t.score_percent DESC NULLS LAST, t.submitted_at DESC
                LIMIT 1
                """
            ),
            {"aid": applicant_id, "cid": company_id},
        )
    ).first()
    if attempt is None:
        return SignalEvidence(signal="exam", available=False)

    # Per-question outcomes come from the attempt's two JSONB blobs, not a
    # responses table (there isn't one — see QUESTION_STATS_SQL in
    # watch_runner). ``answered`` is NULL when the candidate skipped, which the
    # rendering below reports as SKIPPED rather than as a wrong answer.
    responses = (
        await db.execute(
            text(
                """
                SELECT q.position, q.prompt,
                       NULLIF(t.answers->'mcq'->>(q.id::text), '')::int AS answered,
                       (t.graded_snapshot->'mcq'->(q.id::text)
                        ->>'correct_index')::int AS correct_index
                FROM exam_questions q
                JOIN exam_attempts t ON t.id = CAST(:tid AS uuid)
                WHERE q.exam_id = t.exam_id AND q.company_id = t.company_id
                  AND q.deleted_at IS NULL
                  AND jsonb_exists(
                      COALESCE(t.graded_snapshot->'mcq', '{}'::jsonb), q.id::text
                  )
                ORDER BY q.position
                LIMIT 40
                """
            ),
            {"tid": str(attempt.id)},
        )
    ).all()

    def _outcome(row: Any) -> str:
        if row.answered is None:
            return "SKIPPED"
        return "correct" if row.answered == row.correct_index else "WRONG"

    detail = f"Exam: {attempt.title}\nScore: {attempt.score_percent}% (passed={attempt.passed})\n\n"
    detail += "\n".join(
        f"Q{r.position} [{_outcome(r)}]: {str(r.prompt)[:180]}" for r in responses
    )

    return SignalEvidence(
        signal="exam",
        available=True,
        score_0_100=float(attempt.score_percent) if attempt.score_percent is not None else None,
        detail=detail[:MAX_DETAIL_CHARS],
        citations=[
            Citation(
                kind="exam_attempt",
                id=str(attempt.id),
                label=f"Exam attempt — {attempt.title}",
                href=f"/hr/exams/{attempt.exam_id}",
            )
        ],
    )


async def _coding_signal(db: AsyncSession, company_id: str, applicant_id: str) -> SignalEvidence:
    """Best submitted coding attempt. Separate from the MCQ signal on purpose.

    Merging them would hide the most informative disagreement in the whole
    pipeline — strong code, weak conversation about that code.
    """
    attempt = (
        await db.execute(
            text(
                """
                SELECT t.id, t.score_percent, t.passed, e.id AS exam_id, e.title
                FROM exam_attempts t
                JOIN exams e ON e.id = t.exam_id
                WHERE t.applicant_id = CAST(:aid AS uuid) AND t.company_id = :cid
                  AND t.status = 'submitted' AND t.deleted_at IS NULL
                  AND e.kind = 'coding'
                ORDER BY t.score_percent DESC NULLS LAST, t.submitted_at DESC
                LIMIT 1
                """
            ),
            {"aid": applicant_id, "cid": company_id},
        )
    ).first()
    if attempt is None:
        return SignalEvidence(signal="coding", available=False)

    return SignalEvidence(
        signal="coding",
        available=True,
        score_0_100=float(attempt.score_percent) if attempt.score_percent is not None else None,
        detail=(
            f"Coding test: {attempt.title}\n"
            f"Hidden-test pass rate: {attempt.score_percent}% (passed={attempt.passed})"
        ),
        citations=[
            Citation(
                kind="exam_attempt",
                id=str(attempt.id),
                label=f"Coding attempt — {attempt.title}",
                href=f"/hr/exams/{attempt.exam_id}",
            )
        ],
    )


async def _interview_signal(
    db: AsyncSession, company_id: str, applicant_id: str, name: str
) -> SignalEvidence:
    """Most recent interview scorecard, with per-axis rationale."""
    row = (
        await db.execute(
            text(
                """
                SELECT sc.scorecard_id, sc.scores, sc.composite_score, sc.summary,
                       sc.rationale
                FROM interview_invites i
                JOIN scorecards sc ON sc.session_id = i.session_id
                WHERE i.applicant_id = CAST(:aid AS uuid) AND i.company_id = :cid
                  AND i.deleted_at IS NULL
                ORDER BY sc.created_at DESC LIMIT 1
                """
            ),
            {"aid": applicant_id, "cid": company_id},
        )
    ).first()
    if row is None:
        return SignalEvidence(signal="interview", available=False)

    parts = [f"Interview summary: {row.summary}"]
    if isinstance(row.scores, dict):
        parts.append(
            "Axis scores (0-10): "
            + ", ".join(f"{k} {v}" for k, v in row.scores.items())
        )
    if isinstance(row.rationale, dict):
        # Underscore-prefixed keys are intelligence-layer metadata
        # (_role_profile_id, _competencies), not per-axis prose.
        parts.extend(
            f"{axis}: {why}"
            for axis, why in row.rationale.items()
            if not axis.startswith("_") and why
        )

    return SignalEvidence(
        signal="interview",
        available=True,
        # Composite is 0-10; the panel compares everything on 0-100.
        score_0_100=(
            float(row.composite_score) * 10.0 if row.composite_score is not None else None
        ),
        detail="\n".join(parts)[:MAX_DETAIL_CHARS],
        citations=[
            Citation(
                kind="scorecard",
                id=str(row.scorecard_id),
                label=f"Interview scorecard — {name}",
            )
        ],
    )


def _uncovered_competencies(signals: list[SignalEvidence]) -> list[str]:
    """Role competencies the interview never actually probed.

    The scorer writes a ``_competencies`` breakdown into the scorecard's
    rationale JSONB (intelligence layer). Anything scored there with no
    supporting evidence was inferred rather than observed, and the panel says
    so rather than letting it pass as a measurement.
    """
    interview = next((s for s in signals if s.signal == "interview" and s.available), None)
    if interview is None:
        return []

    # The breakdown is embedded in the detail text we just built; re-reading it
    # from the raw rationale would need another query, so it is parsed from the
    # marker the scorer writes.
    marker = '"_competencies"'
    if marker not in interview.detail:
        return []
    try:
        start = interview.detail.index(marker)
        blob = json.loads(interview.detail[start + len(marker) + 1 :])
    except (ValueError, json.JSONDecodeError):
        return []

    return [
        str(entry.get("name", cid))
        for cid, entry in blob.items()
        if isinstance(entry, dict) and not str(entry.get("evidence", "")).strip()
    ]
