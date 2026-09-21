"""PH4-D3 erasure step 5h — the pure JSON transform, redacting candidate
coding SOURCE and program stdout/stderr while keeping the score.

A second, independently-written copy of this exact transform lives in
``services/data_gateway/app/code_evidence.py`` (``redact_coding_answers`` /
``redact_graded_snapshot``), used by that service's own retention timer.
admin_ops cannot import data_gateway (they are separate deployables), so this
is not code reached from two places — it is the same RULE implemented twice,
each with its own unit test, so a change to one is not silently a change to
the other.
"""

from __future__ import annotations

from typing import Any


def redact_coding_answers(answers: dict[str, Any] | None) -> dict[str, Any]:
    """Strip ``source`` from every ``answers.coding[*]`` entry, marking each
    ``source_redacted: true``. Anything that is not a coding answer (MCQ, or
    a malformed entry) passes through untouched."""
    if not answers:
        return answers or {}
    coding = answers.get("coding")
    if not isinstance(coding, dict):
        return answers
    return {
        **answers,
        "coding": {
            qid: ({**entry, "source": None, "source_redacted": True} if isinstance(entry, dict) else entry)
            for qid, entry in coding.items()
        },
    }


def redact_graded_snapshot(snapshot: dict[str, Any] | None) -> dict[str, Any]:
    """Strip ``actual_output``/``stderr`` from every test result under
    ``graded_snapshot.coding[*].tests[]``. Scores (``raw``, ``points``,
    ``passed``, ``timed_out``, ``index``) are untouched — the redaction
    removes what a program PRINTED, never what it SCORED."""
    if not snapshot:
        return snapshot or {}
    coding = snapshot.get("coding")
    if not isinstance(coding, dict):
        return snapshot
    new_coding: dict[str, Any] = {}
    for qid, entry in coding.items():
        if not isinstance(entry, dict):
            new_coding[qid] = entry
            continue
        tests = entry.get("tests")
        if isinstance(tests, list):
            new_coding[qid] = {
                **entry,
                "tests": [
                    ({**t, "actual_output": None, "stderr": None} if isinstance(t, dict) else t)
                    for t in tests
                ],
            }
        else:
            new_coding[qid] = entry
    return {**snapshot, "coding": new_coding}
