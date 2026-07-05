"""Pure, deterministic MCQ grading (HR workflow Phase 2).

No I/O, no DB, no MediaPipe — just the scoring math, so it is trivially
unit-testable and identical everywhere. The authoritative question rows (with
``correct_index`` + ``points``) come from the SERVER; the applicant only supplies
chosen indices. ``score_max`` is summed from the server questions, never from the
payload, so a client cannot shrink the denominator.

SECURITY: ``grade_breakdown`` (per-question correctness) is a SEPARATE function
used ONLY by the HR results endpoint — it is never constructed on the applicant
take path, so correctness can't leak into a candidate-facing response.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class GradeQuestion:
    """One authoritative question: its id, the correct option, and its weight."""

    question_id: str
    correct_index: int
    points: int


@dataclass(frozen=True)
class GradeInput:
    questions: list[GradeQuestion]  # authoritative, server-side
    answers: dict[str, int]  # {question_id: chosen_index} from the applicant


@dataclass(frozen=True)
class GradeResult:
    score_raw: int
    score_max: int
    score_percent: int  # floor(100 * raw / max); 0 when max == 0
    passed: bool


def _is_correct(q: GradeQuestion, answers: dict[str, int]) -> bool:
    """True iff the applicant selected exactly the correct option for this question.

    Unanswered, unknown question, or out-of-range index all count as wrong
    (never raises) — defensive against malformed/forged answer payloads.
    """
    chosen = answers.get(q.question_id)
    return chosen is not None and chosen == q.correct_index


def grade_exam(data: GradeInput, pass_threshold: int) -> GradeResult:
    """Grade an attempt. ``passed`` is the ONLY authority on pass/fail.

    score_max is summed from the server questions (not the payload). percent uses
    math.floor so a candidate is never rounded up across the threshold. passed is
    ``score_percent >= pass_threshold`` (>=, so a threshold of 60 passes at 60).
    """
    score_max = sum(q.points for q in data.questions)
    score_raw = sum(q.points for q in data.questions if _is_correct(q, data.answers))
    score_percent = math.floor(100 * score_raw / score_max) if score_max > 0 else 0
    return GradeResult(
        score_raw=score_raw,
        score_max=score_max,
        score_percent=score_percent,
        passed=score_percent >= pass_threshold,
    )


def grade_breakdown(data: GradeInput) -> dict[str, bool]:
    """HR-ONLY per-question correctness {question_id: was_correct}.

    Constructed solely by the HR results/breakdown endpoint — NEVER on the
    applicant take path (that response carries no correctness at all).
    """
    return {q.question_id: _is_correct(q, data.answers) for q in data.questions}
