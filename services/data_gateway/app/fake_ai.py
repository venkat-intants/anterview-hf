"""AI_FAKE_MODE — deterministic stand-ins for data_gateway's model calls.

For the browser end-to-end suite and other local runs, where a real model call
costs money, takes seconds and answers differently each time. With
``AI_FAKE_MODE=true`` the five calls data_gateway makes to feedback_billing —
resume scoring, MCQ and coding generation, embeddings and the match reason —
return these instead, shaped exactly like the real responses.

Never in production: Settings refuses to start with AI_FAKE_MODE on when APP_ENV
is production or staging, so a stray environment variable cannot quietly replace
real assessments with fixed answers.

Everything here is a pure function of its inputs.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

# The same width the embedding column stores (vector(3072)).
FAKE_EMBEDDING_DIMENSIONS = 3072

# A CV can pin its own score — "E2E-SCORE: 85" — so a spec decides whether a
# candidate clears the shortlist bar instead of inferring it from keywords.
_PINNED_SCORE = re.compile(r"E2E-SCORE:\s*(\d{1,3})", re.IGNORECASE)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_WORD = re.compile(r"[a-z][a-z0-9+#.]{2,}")
_STOPWORDS = frozenset({
    "and", "the", "for", "with", "you", "your", "our", "are", "will", "who", "that",
    "this", "from", "have", "has", "role", "team", "work", "working", "job", "senior",
    "junior", "mid", "level", "engineer", "developer", "manager", "experience", "years",
})


def _keywords(*texts: str | None) -> set[str]:
    words: set[str] = set()
    for t in texts:
        words.update(w for w in _WORD.findall((t or "").lower()) if w not in _STOPWORDS)
    return words


def _recommendation(overall: int) -> str:
    if overall >= 75:
        return "strong_fit"
    if overall >= 50:
        return "moderate_fit"
    return "weak_fit"


def fake_resume_score(
    *, resume_text: str, job_title: str, level: str, jd_text: str | None
) -> dict[str, Any]:
    """A ResumeScoreResponse-shaped score.

    A pinned ``E2E-SCORE: NN`` wins. Otherwise the score is how many of the
    role's keywords (title and JD) the CV mentions, so a relevant CV scores high
    and an unrelated one low — the same direction a real scorer would take.
    """
    pinned = _PINNED_SCORE.search(resume_text or "")
    if pinned:
        overall = max(0, min(100, int(pinned.group(1))))
    else:
        wanted = _keywords(job_title, jd_text)
        have = _keywords(resume_text)
        share = len(wanted & have) / len(wanted) if wanted else 0.0
        overall = int(round(20 + 70 * share))

    first_line = next((ln.strip() for ln in (resume_text or "").splitlines() if ln.strip()), "")
    name = first_line if 0 < len(first_line) <= 80 and "@" not in first_line else ""
    email = _EMAIL.search(resume_text or "")
    recommendation = _recommendation(overall)
    return {
        "candidate_name": name,
        "candidate_email": email.group(0) if email else "",
        "overall": overall,
        "breakdown": {
            "skills_match": overall,
            "experience": max(0, overall - 5),
            "education": min(100, overall + 5),
            "projects": overall,
        },
        "strengths": [f"[fake AI] Relevant to {job_title} ({level})"] if overall >= 50 else [],
        "concerns": [] if overall >= 50 else [f"[fake AI] Little overlap with {job_title}"],
        "recommendation": recommendation,
        "summary": f"[fake AI] Scored {overall}/100 against {job_title} — {recommendation}.",
    }


def fake_mcq_questions(*, topic: str, num_questions: int) -> list[dict[str, Any]]:
    """MCQs whose correct option is always the one reading "Correct answer".

    Its position rotates, so a spec that always picks the first option does not
    pass by accident.
    """
    out: list[dict[str, Any]] = []
    for i in range(max(0, num_questions)):
        wrong = [f"Wrong answer {chr(65 + k)}" for k in range(3)]
        idx = i % 4
        options = [*wrong[:idx], "Correct answer", *wrong[idx:]]
        out.append({
            "prompt": f"[fake AI] {topic} — question {i + 1}: which option is correct?",
            "options": options,
            "correct_index": idx,
            "points": 1,
        })
    return out


_SUM_SOLUTION = "a, b = map(int, input().split())\nprint(a + b)\n"


def fake_coding_questions(
    *, topic: str, num_questions: int, allowed_languages: list[str]
) -> list[dict[str, Any]]:
    """A "sum two integers" problem with sample and hidden tests, and a solution."""
    languages = allowed_languages or ["python"]
    return [
        {
            "prompt": (
                f"[fake AI] {topic} — problem {i + 1}: read two integers a and b from one "
                "line of standard input, separated by a space, and print a + b."
            ),
            "allowed_languages": languages,
            "starter_code": "a, b = map(int, input().split())\n",
            "reference_solution": _SUM_SOLUTION,
            "test_cases": [
                {"stdin": "2 3\n", "expected_output": "5\n", "is_sample": True, "weight": 1},
                {"stdin": "-4 10\n", "expected_output": "6\n", "is_sample": False, "weight": 1},
                {"stdin": "1000000 2000000\n", "expected_output": "3000000\n",
                 "is_sample": False, "weight": 1},
            ],
            "time_limit_ms": 5000,
            "points": 100,
        }
        for i in range(max(0, num_questions))
    ]


def fake_embeddings(texts: list[str]) -> list[list[float]]:
    """A unit vector per text, derived from its hash: equal texts, equal vectors."""
    vectors: list[list[float]] = []
    for text in texts:
        seed = hashlib.sha256((text or "").encode("utf-8")).digest()
        raw = [
            ((seed[i % len(seed)] ^ (i * 131 & 0xFF)) - 127.5) / 127.5
            for i in range(FAKE_EMBEDDING_DIMENSIONS)
        ]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        vectors.append([v / norm for v in raw])
    return vectors


def fake_why_match(*, query: str) -> str:
    return f"[fake AI] Matched on skills related to “{query}”."
