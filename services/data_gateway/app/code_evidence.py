"""PH4-D3 — code quality + similarity EVIDENCE. A signal is never a finding.

WHO WRITES WHAT
``analyse_pending`` (the sweep, ``app/reminders.py``) and ``analyse_attempt``
(on demand) are the only writers of ``code_quality_reports``,
``code_fingerprints`` and ``code_similarity_signals`` — SYSTEM evidence,
immutable once written (the database refuses UPDATE). ``record_finding`` is
the only writer of ``code_integrity_findings`` — the human judgement call,
always against a named ``hr_manager`` and a mandatory rationale.

WHAT THIS MODULE NEVER DOES
It never writes ``exam_attempts.status``, ``score_*``, ``passed`` or
``enrolments`` of any kind, and it never calls ``workflow_runner.record_result``
or anything that advances an application. The ONE exception, in ``purge``, is
a REDACTION of ``exam_attempts.answers``/``graded_snapshot`` (never their
status/score), which the ``exam_attempts_submission_frozen`` trigger permits
in exactly one shape: this module and DPDP erasure's step 5h are the only two
places in the schema allowed to perform it. See that trigger's migration
docstring for why a redaction-only UPDATE is not the lifecycle mutation this
module's other rules are written to forbid.

ANALYSIS FAILURE NEVER INVALIDATES A SUBMISSION
``exam_take.py`` does not import this module (grep-verified, AST-tested) — a
submit can never fail because analysis did. Every analysis path here catches
broadly and stores ``status='failed'`` with only the exception's TYPE NAME
(``error_class``), never the source, and moves on to the next submission.

TENANT ISOLATION
Every read and write here carries ``company_id`` explicitly, on top of the
composite FKs the schema itself enforces — belt and braces, not either/or.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.code_quality import ANALYSER_VERSION, analyse
from app.code_sandbox import (
    AnalysisFailedError,
    AnalysisTimeoutError,
    SandboxUnavailableError,
    run_isolated,
)
from app.code_similarity import (
    ALGORITHM_VERSION,
    PYGMENTS_LANGUAGE_ALIASES,
    Fingerprint,
    compare,
    fingerprint_source,
    regions,
)
from app.config import settings
from app.interviewer_scorecards import RequestMeta
from app.models import AuditLog

log = structlog.get_logger(__name__)


class CodeEvidenceError(Exception):
    """Refused. Carries the HTTP status and a sentence a person can act on."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


_NO_COVERAGE: dict[str, Any] = {
    "available": False,
    "reason": (
        "Coverage requires instrumented execution. This analyser is pure static "
        "analysis and never runs candidate code."
    ),
}


def _audit(
    db: AsyncSession, *, actor: uuid.UUID | None, action: str, resource_id: uuid.UUID,
    details: dict[str, Any], meta: RequestMeta,
) -> None:
    db.add(AuditLog(
        actor_id=actor, actor_type="user", action=action, resource_type="code_evidence",
        resource_id=resource_id, details=details, ip_address=meta.ip_address,
        user_agent=meta.user_agent, event_ts=datetime.now(tz=UTC),
    ))


# ---------------------------------------------------------------------------
# The sweep — SYSTEM evidence only
# ---------------------------------------------------------------------------
_PENDING_SQL = """
SELECT a.id AS attempt_id, a.company_id, a.exam_id,
       kv.key AS coding_question_id, kv.value AS answer
  FROM exam_attempts a
  CROSS JOIN LATERAL jsonb_each(COALESCE(a.answers -> 'coding', '{}'::jsonb)) AS kv(key, value)
 WHERE a.status IN ('submitted', 'expired')
   AND a.deleted_at IS NULL
   AND a.code_redacted_at IS NULL
   AND a.submitted_at IS NOT NULL
   AND a.submitted_at >= :cutoff
   AND NOT EXISTS (
     SELECT 1 FROM code_quality_reports r
      WHERE r.attempt_id = a.id AND r.coding_question_id = (kv.key)::uuid
        AND r.analyser_version = :qv
   )
 ORDER BY a.submitted_at
 LIMIT :lim
"""

_INSERT_REPORT_SQL = """
INSERT INTO code_quality_reports
  (id, company_id, attempt_id, coding_question_id, exam_id, language, analyser,
   analyser_version, status, metrics, findings, coverage, error_class, source_sha256, created_at)
VALUES
  (:id, :company_id, :attempt_id, :coding_question_id, :exam_id, :language, :analyser,
   :analyser_version, :status, CAST(:metrics AS jsonb), CAST(:findings AS jsonb),
   CAST(:coverage AS jsonb), :error_class, :source_sha256, now())
ON CONFLICT (attempt_id, coding_question_id, analyser_version) DO NOTHING
"""

_INSERT_FINGERPRINT_SQL = """
INSERT INTO code_fingerprints
  (id, company_id, attempt_id, coding_question_id, hashes, lines, token_count,
   algorithm_version, created_at)
VALUES
  (:id, :company_id, :attempt_id, :coding_question_id, :hashes, :lines, :token_count,
   :algorithm_version, now())
ON CONFLICT (attempt_id, coding_question_id, algorithm_version) DO NOTHING
"""


@dataclass(frozen=True)
class SweepResult:
    attempts_scanned: int = 0
    reports_written: int = 0
    fingerprints_written: int = 0
    signals_written: int = 0


async def analyse_pending(db: AsyncSession, *, limit: int | None = None) -> SweepResult:
    """The sweep stage (``app/reminders.py``). No-ops when disabled.

    Batched by (attempt, coding-question) pair, oldest submission first.
    Idempotent by construction: the UNIQUE constraint on
    ``(attempt_id, coding_question_id, analyser_version)`` plus
    ``ON CONFLICT DO NOTHING`` means a pair analysed twice (a re-run, an
    overlapping sweep) writes at most one report — the write itself is the
    concurrency guard, so this never takes a row lock on ``exam_attempts``
    across the (multi-second) sandboxed analysis call, which would hold a
    lock on a table candidates are actively submitting against.
    """
    if not settings.code_analysis_enabled:
        return SweepResult()
    cutoff = datetime.now(tz=UTC) - timedelta(days=settings.code_analysis_lookback_days)
    rows = (
        await db.execute(
            text(_PENDING_SQL),
            {"cutoff": cutoff, "qv": ANALYSER_VERSION, "lim": limit or settings.code_analysis_batch},
        )
    ).mappings().all()

    touched: set[tuple[uuid.UUID, uuid.UUID, uuid.UUID]] = set()  # (company, exam, question)
    reports_written = 0
    fingerprints_written = 0
    for row in rows:
        entry = row["answer"] if isinstance(row["answer"], dict) else {}
        language = str(entry.get("language") or "")
        source = str(entry.get("source") or "")
        qid = uuid.UUID(str(row["coding_question_id"]))
        report = await _analyse_one(
            db, company_id=row["company_id"], attempt_id=row["attempt_id"],
            coding_question_id=qid, exam_id=row["exam_id"], language=language, source=source,
        )
        reports_written += 1 if report else 0
        if language in PYGMENTS_LANGUAGE_ALIASES and len(source) > 0:
            starter = await _starter_code(db, row["company_id"], qid)
            wrote_fp = await _fingerprint_one(
                db, company_id=row["company_id"], attempt_id=row["attempt_id"],
                coding_question_id=qid, language=language, source=source, starter_code=starter,
            )
            fingerprints_written += 1 if wrote_fp else 0
        touched.add((row["company_id"], row["exam_id"], qid))
    await db.commit()

    signals_written = 0
    for company_id, exam_id, qid in touched:
        signals_written += await _compare_question(db, company_id=company_id, exam_id=exam_id,
                                                    coding_question_id=qid)
    await db.commit()
    log.info(
        "code_evidence.sweep", attempts_scanned=len(rows), reports_written=reports_written,
        fingerprints_written=fingerprints_written, signals_written=signals_written,
    )
    return SweepResult(
        attempts_scanned=len(rows), reports_written=reports_written,
        fingerprints_written=fingerprints_written, signals_written=signals_written,
    )


async def _starter_code(db: AsyncSession, company_id: uuid.UUID, coding_question_id: uuid.UUID) -> str | None:
    return await db.scalar(
        text("SELECT starter_code FROM coding_questions WHERE id = :q AND company_id = :c"),
        {"q": coding_question_id, "c": company_id},
    )


async def _analyse_one(
    db: AsyncSession, *, company_id: uuid.UUID, attempt_id: uuid.UUID,
    coding_question_id: uuid.UUID, exam_id: uuid.UUID, language: str, source: str,
) -> bool:
    """One (attempt, question) pair -> one row in ``code_quality_reports``.

    Never raises. A failure caused by THIS INPUT -- too slow, or the analyser
    raised, or its process died on it -- becomes ``status='failed'`` with only
    a class name recorded. A sandbox that could not start at all is
    infrastructure, not the input, and writes NO report, so the sweep picks
    the submission up again next pass. (Returns False in that case.)

    Recording infrastructure failures as permanent was how one dead worker
    used to turn every later submission, across every tenant, into an
    un-retried ``failed`` until the service restarted.
    """
    source_sha256 = hashlib.sha256(source.encode("utf-8", errors="surrogatepass")).hexdigest()
    analyser = "python-ast" if language == "python" else "pygments-tokens"
    status = "unsupported"
    metrics: dict[str, Any] = {}
    findings: list[Any] = []
    coverage: dict[str, Any] = _NO_COVERAGE
    error_class: str | None = None
    try:
        result = await run_isolated(analyse, language, source, None)
        analyser = result["analyser"]
        status = result["status"]
        metrics = result["metrics"]
        findings = result["findings"]
        coverage = result["coverage"]
    except SandboxUnavailableError:
        log.warning(
            "code_evidence.sandbox_unavailable", attempt_id=str(attempt_id),
            coding_question_id=str(coding_question_id),
        )
        return False  # no report: retried on the next pass
    except AnalysisTimeoutError:
        status, error_class = "failed", "TimeoutError"
    except AnalysisFailedError as exc:
        status, error_class = "failed", exc.error_class
        log.warning(
            "code_evidence.analysis_failed", attempt_id=str(attempt_id),
            coding_question_id=str(coding_question_id), error_class=error_class,
        )
    except Exception as exc:  # noqa: BLE001 -- analysis failure must never bubble up
        status, error_class = "failed", type(exc).__name__
        log.warning(
            "code_evidence.analysis_failed", attempt_id=str(attempt_id),
            coding_question_id=str(coding_question_id), error_class=error_class,
        )
    await db.execute(
        text(_INSERT_REPORT_SQL),
        {
            "id": uuid.uuid4(), "company_id": company_id, "attempt_id": attempt_id,
            "coding_question_id": coding_question_id, "exam_id": exam_id, "language": language,
            "analyser": analyser, "analyser_version": ANALYSER_VERSION, "status": status,
            "metrics": json.dumps(metrics), "findings": json.dumps(findings),
            "coverage": json.dumps(coverage), "error_class": error_class,
            "source_sha256": source_sha256,
        },
    )
    return True


async def _fingerprint_one(
    db: AsyncSession, *, company_id: uuid.UUID, attempt_id: uuid.UUID,
    coding_question_id: uuid.UUID, language: str, source: str, starter_code: str | None,
) -> bool:
    try:
        fp: Fingerprint = await run_isolated(
            fingerprint_source, language, source, starter_code=starter_code
        )
    except Exception as exc:  # noqa: BLE001 -- fingerprinting failure must not block the report
        log.warning(
            "code_evidence.fingerprint_failed", attempt_id=str(attempt_id),
            error_class=type(exc).__name__,
        )
        return False
    if fp.token_count < settings.code_similarity_min_tokens or not fp.hashes:
        return False
    await db.execute(
        text(_INSERT_FINGERPRINT_SQL),
        {
            "id": uuid.uuid4(), "company_id": company_id, "attempt_id": attempt_id,
            "coding_question_id": coding_question_id, "hashes": fp.hashes, "lines": fp.lines,
            "token_count": fp.token_count, "algorithm_version": ALGORITHM_VERSION,
        },
    )
    return True


@dataclass(frozen=True)
class _FpRow:
    attempt_id: uuid.UUID
    hashes: list[int]
    token_count: int


_FINGERPRINTS_FOR_QUESTION_SQL = """
SELECT attempt_id, hashes, token_count FROM code_fingerprints
 WHERE company_id = :c AND coding_question_id = :q AND algorithm_version = :v
"""

_INSERT_SIGNAL_SQL = """
INSERT INTO code_similarity_signals
  (id, company_id, coding_question_id, exam_id, attempt_low_id, attempt_high_id,
   reference_kind, containment_low, containment_high, jaccard, shared_fingerprints,
   tokens_low, tokens_high, matched_regions, thresholds, algorithm_version, created_at)
VALUES
  (:id, :company_id, :coding_question_id, :exam_id, :low, :high, :kind, :clow, :chigh,
   :jaccard, :shared, :tlow, :thigh, CAST(:regions AS jsonb), CAST(:thresholds AS jsonb),
   :algorithm_version, now())
ON CONFLICT DO NOTHING
"""


async def _compare_question(
    db: AsyncSession, *, company_id: uuid.UUID, exam_id: uuid.UUID, coding_question_id: uuid.UUID,
) -> int:
    """Pairwise comparison across every fingerprinted submission for one
    coding question, plus a reference-solution comparison. Writes a signal
    when containment and shared-fingerprint thresholds are both met.

    A fingerprint shared by more than ``code_similarity_max_df`` of this
    question's OWN submissions is treated as boilerplate every candidate
    reaches independently and excluded before comparing — the reason many
    candidates using the same AI assistant does not, on its own, produce a
    wall of signals.
    """
    rows = (
        await db.execute(
            text(_FINGERPRINTS_FOR_QUESTION_SQL),
            {"c": company_id, "q": coding_question_id, "v": ALGORITHM_VERSION},
        )
    ).all()
    fp_rows = [_FpRow(r.attempt_id, list(r.hashes), r.token_count) for r in rows]
    if not fp_rows:
        return 0

    doc_freq: Counter[int] = Counter()
    for fr in fp_rows:
        doc_freq.update(set(fr.hashes))
    n = len(fp_rows)
    df_ceiling = max(1, math.ceil(settings.code_similarity_max_df * n))
    boilerplate = {h for h, c in doc_freq.items() if c > df_ceiling}

    filtered: dict[uuid.UUID, set[int]] = {
        fr.attempt_id: set(fr.hashes) - boilerplate for fr in fp_rows
    }
    tokens_by_attempt = {fr.attempt_id: fr.token_count for fr in fp_rows}

    thresholds = {
        "min_containment": settings.code_similarity_min_containment,
        "min_shared": settings.code_similarity_min_shared,
        "max_df": settings.code_similarity_max_df,
        "k": 15, "w": 8,
    }

    written = 0
    for i in range(len(fp_rows)):
        for j in range(i + 1, len(fp_rows)):
            a, b = fp_rows[i], fp_rows[j]
            sim = compare(list(filtered[a.attempt_id]), list(filtered[b.attempt_id]))
            if sim.shared < settings.code_similarity_min_shared:
                continue
            if max(sim.containment_a, sim.containment_b) < settings.code_similarity_min_containment:
                continue
            # Python's uuid.UUID orders by .int (the 16 bytes as one big-endian
            # integer), the same order Postgres's `uuid <` operator uses — so
            # this matches ck_code_similarity_signals_pair_order exactly.
            low_id, high_id = sorted((a.attempt_id, b.attempt_id))
            containment_low, containment_high = (
                (sim.containment_a, sim.containment_b) if low_id == a.attempt_id
                else (sim.containment_b, sim.containment_a)
            )
            tokens_low = tokens_by_attempt[low_id]
            tokens_high = tokens_by_attempt[high_id]
            await db.execute(
                text(_INSERT_SIGNAL_SQL),
                {
                    "id": uuid.uuid4(), "company_id": company_id,
                    "coding_question_id": coding_question_id, "exam_id": exam_id,
                    "low": low_id, "high": high_id, "kind": "submission",
                    "clow": containment_low, "chigh": containment_high, "jaccard": sim.jaccard,
                    "shared": sim.shared, "tlow": tokens_low, "thigh": tokens_high,
                    "regions": json.dumps([]), "thresholds": json.dumps(thresholds),
                    "algorithm_version": ALGORITHM_VERSION,
                },
            )
            written += 1

    written += await _compare_reference_solution(
        db, company_id=company_id, exam_id=exam_id, coding_question_id=coding_question_id,
        filtered=filtered, tokens_by_attempt=tokens_by_attempt, thresholds=thresholds,
    )
    return written


async def _compare_reference_solution(
    db: AsyncSession, *, company_id: uuid.UUID, exam_id: uuid.UUID, coding_question_id: uuid.UUID,
    filtered: dict[uuid.UUID, set[int]], tokens_by_attempt: dict[uuid.UUID, int],
    thresholds: dict[str, Any],
) -> int:
    row = (
        await db.execute(
            text(
                "SELECT reference_solution, starter_code, allowed_languages"
                "  FROM coding_questions WHERE id = :q AND company_id = :c"
            ),
            {"q": coding_question_id, "c": company_id},
        )
    ).first()
    if row is None or not row[0] or not row[0].strip():
        return 0
    reference_solution, starter_code, allowed_languages = row
    # The schema does not record which language the reference solution is
    # written in. Documented assumption: the FIRST allowed language — the
    # one HR authors it against in practice.
    languages = list(allowed_languages or [])
    if not languages or languages[0] not in PYGMENTS_LANGUAGE_ALIASES:
        return 0
    try:
        ref_fp = await run_isolated(
            fingerprint_source, languages[0], reference_solution, starter_code=starter_code
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("code_evidence.reference_fingerprint_failed", error_class=type(exc).__name__)
        return 0
    if not ref_fp.hashes:
        return 0

    written = 0
    for attempt_id, hashes in filtered.items():
        sim = compare(list(hashes), ref_fp.hashes)
        if sim.shared < settings.code_similarity_min_shared:
            continue
        if sim.containment_a < settings.code_similarity_min_containment:
            continue
        await db.execute(
            text(_INSERT_SIGNAL_SQL),
            {
                "id": uuid.uuid4(), "company_id": company_id, "coding_question_id": coding_question_id,
                "exam_id": exam_id, "low": attempt_id, "high": None, "kind": "reference_solution",
                "clow": sim.containment_a, "chigh": None, "jaccard": sim.jaccard,
                "shared": sim.shared, "tlow": tokens_by_attempt[attempt_id],
                "thigh": ref_fp.token_count, "regions": json.dumps([]),
                "thresholds": json.dumps(thresholds), "algorithm_version": ALGORITHM_VERSION,
            },
        )
        written += 1
    return written


async def analyse_attempt(db: AsyncSession, *, company_id: uuid.UUID, attempt_id: uuid.UUID) -> SweepResult:
    """On-demand analysis for one attempt (an older submission outside the
    sweep's lookback window, or a re-run after a fix). Same idempotent
    ``ON CONFLICT DO NOTHING`` writes as the sweep."""
    row = (
        await db.execute(
            text(
                "SELECT id, company_id, exam_id, answers FROM exam_attempts"
                " WHERE id = :a AND company_id = :c AND deleted_at IS NULL"
                "   AND status IN ('submitted', 'expired')"
            ),
            {"a": attempt_id, "c": company_id},
        )
    ).mappings().first()
    if row is None:
        raise CodeEvidenceError(404, "Attempt not found.")
    coding = (row["answers"] or {}).get("coding") if isinstance(row["answers"], dict) else None
    if not isinstance(coding, dict) or not coding:
        return SweepResult()
    reports = fingerprints_ = 0
    touched: set[uuid.UUID] = set()
    for qid_str, entry in coding.items():
        qid = uuid.UUID(qid_str)
        language = str((entry or {}).get("language") or "")
        source = str((entry or {}).get("source") or "")
        reports += 1 if await _analyse_one(
            db, company_id=row["company_id"], attempt_id=row["id"], coding_question_id=qid,
            exam_id=row["exam_id"], language=language, source=source,
        ) else 0
        if language in PYGMENTS_LANGUAGE_ALIASES and source:
            starter = await _starter_code(db, row["company_id"], qid)
            fingerprints_ += 1 if await _fingerprint_one(
                db, company_id=row["company_id"], attempt_id=row["id"], coding_question_id=qid,
                language=language, source=source, starter_code=starter,
            ) else 0
        touched.add(qid)
    await db.commit()
    signals = 0
    for qid in touched:
        signals += await _compare_question(
            db, company_id=row["company_id"], exam_id=row["exam_id"], coding_question_id=qid
        )
    await db.commit()
    return SweepResult(
        attempts_scanned=1, reports_written=reports, fingerprints_written=fingerprints_,
        signals_written=signals,
    )


# ---------------------------------------------------------------------------
# Reads — HR only (enforced by the router), source reads and compares audited
# ---------------------------------------------------------------------------
async def evidence_for_attempt(
    db: AsyncSession, *, company_id: uuid.UUID, exam_id: uuid.UUID, attempt_id: uuid.UUID,
) -> dict[str, Any]:
    """Reports, test results (from ``graded_snapshot`` — labelled RESULTS, not
    coverage), an integrity summary, similarity signals and findings — for
    every coding question in this attempt."""
    attempt = (
        await db.execute(
            text(
                "SELECT id, graded_snapshot, integrity_score, proctoring_summary, code_redacted_at"
                "  FROM exam_attempts"
                " WHERE id = :a AND company_id = :c AND exam_id = :x AND deleted_at IS NULL"
            ),
            {"a": attempt_id, "c": company_id, "x": exam_id},
        )
    ).mappings().first()
    if attempt is None:
        raise CodeEvidenceError(404, "Attempt not found.")

    reports = (
        await db.execute(
            text(
                "SELECT coding_question_id, language, analyser, analyser_version, status,"
                "       metrics, findings, coverage, error_class, source_sha256, created_at"
                "  FROM code_quality_reports"
                " WHERE company_id = :c AND attempt_id = :a"
                " ORDER BY created_at"
            ),
            {"c": company_id, "a": attempt_id},
        )
    ).mappings().all()

    integrity_events = (
        await db.execute(
            text(
                "SELECT event_type, count(*) AS n FROM exam_integrity_events"
                " WHERE company_id = :c AND attempt_id = :a GROUP BY event_type"
            ),
            {"c": company_id, "a": attempt_id},
        )
    ).all()

    signals = (
        await db.execute(
            text(
                "SELECT id, coding_question_id, attempt_low_id, attempt_high_id, reference_kind,"
                "       containment_low, containment_high, jaccard, shared_fingerprints,"
                "       tokens_low, tokens_high, algorithm_version, created_at"
                "  FROM code_similarity_signals"
                " WHERE company_id = :c AND (attempt_low_id = :a OR attempt_high_id = :a)"
                " ORDER BY created_at DESC"
            ),
            {"c": company_id, "a": attempt_id},
        )
    ).mappings().all()

    findings = (
        await db.execute(
            text(
                "SELECT id, coding_question_id, signal_id, outcome, rationale,"
                "       recorded_by_user_id, created_at, redacted_at"
                "  FROM code_integrity_findings"
                " WHERE company_id = :c AND attempt_id = :a AND superseded_at IS NULL"
                " ORDER BY created_at DESC"
            ),
            {"c": company_id, "a": attempt_id},
        )
    ).mappings().all()

    graded_snapshot = attempt["graded_snapshot"] or {}
    coding_results = graded_snapshot.get("coding", {}) if isinstance(graded_snapshot, dict) else {}

    return {
        "attempt_id": str(attempt_id),
        "code_redacted": attempt["code_redacted_at"] is not None,
        "reports": [
            {
                "coding_question_id": str(r["coding_question_id"]), "language": r["language"],
                "analyser": r["analyser"], "analyser_version": r["analyser_version"],
                "status": r["status"], "metrics": r["metrics"], "findings": r["findings"],
                "coverage": r["coverage"], "error_class": r["error_class"],
                "source_sha256": r["source_sha256"], "created_at": r["created_at"].isoformat(),
            }
            for r in reports
        ],
        # Existing sandbox TEST RESULTS, clearly labelled as results, never as
        # coverage — checklist #6.
        "test_results": coding_results,
        "integrity": {
            "integrity_score": attempt["integrity_score"],
            "proctoring_summary": attempt["proctoring_summary"],
            "event_counts": {et: int(n) for et, n in integrity_events},
        },
        "similarity_signals": [
            {
                "id": str(s["id"]), "coding_question_id": str(s["coding_question_id"]),
                "attempt_low_id": str(s["attempt_low_id"]),
                "attempt_high_id": str(s["attempt_high_id"]) if s["attempt_high_id"] else None,
                "reference_kind": s["reference_kind"],
                "containment_low": float(s["containment_low"]),
                "containment_high": float(s["containment_high"]) if s["containment_high"] is not None else None,
                "jaccard": float(s["jaccard"]), "shared_fingerprints": s["shared_fingerprints"],
                "tokens_low": s["tokens_low"], "tokens_high": s["tokens_high"],
                "algorithm_version": s["algorithm_version"], "created_at": s["created_at"].isoformat(),
                "caption": "Automated, unreviewed — similar code is not evidence of "
                           "misconduct on its own. Many candidates independently using the "
                           "same tools or references can look alike.",
            }
            for s in signals
        ],
        "findings": [
            {
                "id": str(f["id"]), "coding_question_id": str(f["coding_question_id"]),
                "signal_id": str(f["signal_id"]) if f["signal_id"] else None,
                "outcome": f["outcome"],
                "rationale": f["rationale"] if f["redacted_at"] is None else None,
                "recorded_by_user_id": str(f["recorded_by_user_id"]),
                "created_at": f["created_at"].isoformat(),
                "redacted": f["redacted_at"] is not None,
            }
            for f in findings
        ],
    }


async def source_for(
    db: AsyncSession, *, company_id: uuid.UUID, exam_id: uuid.UUID, attempt_id: uuid.UUID,
    coding_question_id: uuid.UUID, actor: uuid.UUID, meta: RequestMeta,
) -> dict[str, Any]:
    """The candidate's own submitted source — audited every time, per D3 #24."""
    row = (
        await db.execute(
            text(
                "SELECT answers, code_redacted_at FROM exam_attempts"
                " WHERE id = :a AND company_id = :c AND exam_id = :x AND deleted_at IS NULL"
            ),
            {"a": attempt_id, "c": company_id, "x": exam_id},
        )
    ).mappings().first()
    if row is None:
        raise CodeEvidenceError(404, "Attempt not found.")
    coding = (row["answers"] or {}).get("coding") if isinstance(row["answers"], dict) else None
    entry = (coding or {}).get(str(coding_question_id))
    if entry is None:
        raise CodeEvidenceError(404, "No submission for this question.")
    _audit(
        db, actor=actor, action="code_evidence.source_viewed", resource_id=attempt_id,
        details={"company_id": str(company_id), "coding_question_id": str(coding_question_id)},
        meta=meta,
    )
    await db.commit()
    if row["code_redacted_at"] is not None:
        return {"language": entry.get("language"), "source": None, "redacted": True}
    return {"language": entry.get("language"), "source": entry.get("source"), "redacted": False}


async def compare_view(
    db: AsyncSession, *, company_id: uuid.UUID, signal_id: uuid.UUID, actor: uuid.UUID,
    meta: RequestMeta,
) -> dict[str, Any]:
    """Excerpts of each matched region, ±3 lines, at most 200 lines per side.
    Audited every time (D3 #24)."""
    signal = (
        await db.execute(
            text(
                "SELECT id, coding_question_id, attempt_low_id, attempt_high_id, reference_kind"
                "  FROM code_similarity_signals WHERE id = :i AND company_id = :c"
            ),
            {"i": signal_id, "c": company_id},
        )
    ).mappings().first()
    if signal is None:
        raise CodeEvidenceError(404, "Signal not found.")

    low_fp = await _fingerprint_for(db, company_id, signal["attempt_low_id"], signal["coding_question_id"])
    low_source = await _raw_source(db, company_id, signal["attempt_low_id"], signal["coding_question_id"])
    if signal["reference_kind"] == "reference_solution":
        ref = (
            await db.execute(
                text("SELECT reference_solution FROM coding_questions WHERE id = :q AND company_id = :c"),
                {"q": signal["coding_question_id"], "c": company_id},
            )
        ).scalar()
        high_source = {"language": None, "text": ref or ""}
        high_fp = None
    else:
        high_fp = await _fingerprint_for(
            db, company_id, signal["attempt_high_id"], signal["coding_question_id"]
        )
        high_source = await _raw_source(
            db, company_id, signal["attempt_high_id"], signal["coding_question_id"]
        )

    matched = regions(low_fp, high_fp) if (low_fp and high_fp) else []
    _audit(
        db, actor=actor, action="code_similarity.viewed", resource_id=signal_id,
        details={"company_id": str(company_id), "coding_question_id": str(signal["coding_question_id"])},
        meta=meta,
    )
    await db.commit()
    return {
        "signal_id": str(signal_id),
        "low": {"language": low_source["language"], "excerpt": _excerpt(low_source["text"])},
        "high": {"language": high_source["language"], "excerpt": _excerpt(high_source["text"])},
        "matched_regions": matched,
        "caption": "Automated, unreviewed — similar code is not evidence of misconduct "
                   "on its own.",
    }


async def _fingerprint_for(
    db: AsyncSession, company_id: uuid.UUID, attempt_id: uuid.UUID | None, coding_question_id: uuid.UUID,
) -> Fingerprint | None:
    if attempt_id is None:
        return None
    row = (
        await db.execute(
            text(
                "SELECT hashes, lines FROM code_fingerprints"
                " WHERE company_id = :c AND attempt_id = :a AND coding_question_id = :q"
                " ORDER BY created_at DESC LIMIT 1"
            ),
            {"c": company_id, "a": attempt_id, "q": coding_question_id},
        )
    ).first()
    if row is None:
        return None
    return Fingerprint(hashes=list(row[0]), lines=list(row[1]), token_count=len(row[0]))


async def _raw_source(
    db: AsyncSession, company_id: uuid.UUID, attempt_id: uuid.UUID | None, coding_question_id: uuid.UUID,
) -> dict[str, Any]:
    if attempt_id is None:
        return {"language": None, "text": ""}
    row = (
        await db.execute(
            text("SELECT answers, code_redacted_at FROM exam_attempts WHERE id = :a AND company_id = :c"),
            {"a": attempt_id, "c": company_id},
        )
    ).mappings().first()
    if row is None or row["code_redacted_at"] is not None:
        return {"language": None, "text": ""}
    coding = (row["answers"] or {}).get("coding") if isinstance(row["answers"], dict) else None
    entry = (coding or {}).get(str(coding_question_id)) or {}
    return {"language": entry.get("language"), "text": entry.get("source") or ""}


_MAX_EXCERPT_LINES = 200


def _excerpt(text_: str | None) -> str:
    lines = (text_ or "").splitlines()
    return "\n".join(lines[:_MAX_EXCERPT_LINES])


# ---------------------------------------------------------------------------
# Writes — HUMAN evidence
# ---------------------------------------------------------------------------
async def record_finding(
    db: AsyncSession, *, company_id: uuid.UUID, attempt_id: uuid.UUID, coding_question_id: uuid.UUID,
    outcome: str, rationale: str, actor: uuid.UUID, meta: RequestMeta,
    signal_id: uuid.UUID | None = None, enrolment_id: uuid.UUID | None = None,
    supersedes_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Record a NAMED person's judgement call. ``rationale`` is mandatory
    (20-2000 chars, enforced by the CHECK constraint too) — there is no path
    to record a finding without one."""
    if outcome not in ("no_concern", "follow_up", "confirmed"):
        raise CodeEvidenceError(422, "outcome must be no_concern, follow_up or confirmed.")
    if len(rationale.strip()) < 20:
        raise CodeEvidenceError(422, "Give a rationale of at least 20 characters.")
    fid = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO code_integrity_findings"
            " (id, company_id, attempt_id, coding_question_id, signal_id, enrolment_id, outcome,"
            "  rationale, recorded_by_user_id, supersedes_id, created_at)"
            " VALUES (:id, :c, :a, :q, :s, :e, :o, :r, :actor, :sup, now())"
        ),
        {
            "id": fid, "c": company_id, "a": attempt_id, "q": coding_question_id, "s": signal_id,
            "e": enrolment_id, "o": outcome, "r": rationale.strip(), "actor": actor, "sup": supersedes_id,
        },
    )
    if supersedes_id is not None:
        await db.execute(
            text(
                "UPDATE code_integrity_findings SET superseded_at = now()"
                " WHERE id = :old AND company_id = :c AND superseded_at IS NULL"
            ),
            {"old": supersedes_id, "c": company_id},
        )
    _audit(
        db, actor=actor, action="code_integrity.finding_recorded", resource_id=fid,
        details={
            "company_id": str(company_id), "attempt_id": str(attempt_id),
            "coding_question_id": str(coding_question_id), "outcome": outcome,
            "has_reason": True, "reason_chars": len(rationale.strip()),
        },
        meta=meta,
    )
    await db.commit()
    log.info("code_integrity.finding_recorded", finding_id=str(fid), outcome=outcome)
    return fid


async def signals_for_exam(db: AsyncSession, *, company_id: uuid.UUID, exam_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = (
        await db.execute(
            text(
                "SELECT id, coding_question_id, attempt_low_id, attempt_high_id, reference_kind,"
                "       containment_low, containment_high, jaccard, shared_fingerprints, created_at"
                "  FROM code_similarity_signals WHERE company_id = :c AND exam_id = :x"
                " ORDER BY created_at DESC"
            ),
            {"c": company_id, "x": exam_id},
        )
    ).mappings().all()
    return [
        {
            "id": str(r["id"]), "coding_question_id": str(r["coding_question_id"]),
            "attempt_low_id": str(r["attempt_low_id"]),
            "attempt_high_id": str(r["attempt_high_id"]) if r["attempt_high_id"] else None,
            "reference_kind": r["reference_kind"], "containment_low": float(r["containment_low"]),
            "containment_high": float(r["containment_high"]) if r["containment_high"] is not None else None,
            "jaccard": float(r["jaccard"]), "shared_fingerprints": r["shared_fingerprints"],
            "created_at": r["created_at"].isoformat(),
        }
        for r in rows
    ]


async def summary_for_enrolments(
    db: AsyncSession, *, company_id: uuid.UUID, enrolment_ids: list[uuid.UUID],
) -> dict[str, dict[str, int]]:
    """Counts only, for the decision-queue and applicant-drawer evidence chips
    — never the content."""
    if not enrolment_ids:
        return {}
    rows = (
        await db.execute(
            text(
                "SELECT asg.enrolment_id,"
                "       count(DISTINCT s.id) AS signal_count,"
                "       count(DISTINCT f.id) FILTER (WHERE f.superseded_at IS NULL"
                "                                       AND f.redacted_at IS NULL) AS finding_count"
                "  FROM exam_assignments asg"
                "  JOIN exam_attempts a ON a.assignment_id = asg.id AND a.company_id = asg.company_id"
                "  LEFT JOIN code_similarity_signals s ON s.company_id = a.company_id"
                "                                     AND (s.attempt_low_id = a.id OR s.attempt_high_id = a.id)"
                "  LEFT JOIN code_integrity_findings f ON f.company_id = a.company_id"
                "                                     AND f.attempt_id = a.id"
                " WHERE asg.company_id = :c AND asg.enrolment_id = ANY(:ids)"
                " GROUP BY asg.enrolment_id"
            ),
            {"c": company_id, "ids": enrolment_ids},
        )
    ).all()
    return {
        str(enrolment_id): {"signal_count": int(signal_count), "finding_count": int(finding_count)}
        for enrolment_id, signal_count, finding_count in rows
    }


# ---------------------------------------------------------------------------
# Retention — the same redaction erasure's step 5h performs, on a timer
# ---------------------------------------------------------------------------
def redact_coding_answers(answers: dict[str, Any] | None) -> dict[str, Any]:
    """Pure: strip ``source`` from every coding answer, marking each entry
    ``source_redacted``. A second, independently-written copy of this
    transform lives in ``services/admin_ops/app/code_redaction.py`` — that
    service cannot import this one."""
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
    """Pure: strip ``actual_output``/``stderr`` from every coding test result.
    Scores (``raw``, ``points``, ``passed``) are untouched."""
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


_PURGEABLE_ATTEMPTS_SQL = """
SELECT a.id, a.company_id, a.answers, a.graded_snapshot
  FROM exam_attempts a
  LEFT JOIN exam_assignments asg ON asg.id = a.assignment_id AND asg.company_id = a.company_id
  LEFT JOIN enrolments e ON e.id = asg.enrolment_id AND e.company_id = a.company_id
 WHERE a.code_redacted_at IS NULL
   AND a.status IN ('submitted', 'expired')
   AND a.deleted_at IS NULL
   AND a.answers -> 'coding' IS NOT NULL
   AND (
     (e.id IS NULL AND a.submitted_at IS NOT NULL AND a.submitted_at < :cutoff)
     OR (
       -- Only a DECIDED application, and only once the decision is older than
       -- the cutoff. The first version wrapped the decision time in
       -- COALESCE(..., 'epoch'), so an application with NO decision read
       -- 'epoch' < cutoff -- true -- and the first real run would have
       -- stripped the source and deleted the evidence of every candidate
       -- still waiting on HR. Now: no decision is NULL, and NULL < cutoff is
       -- not true. The current status is checked as well, so a decision that
       -- was later reversed does not count as one.
       e.id IS NOT NULL
       AND e.status IN ('hired', 'rejected')
       AND (SELECT max(t.occurred_at) FROM stage_transitions t
             WHERE t.enrolment_id = e.id AND t.to_status IN ('hired', 'rejected')) < :cutoff
     )
   )
 LIMIT :lim
"""

_PURGE_BATCH = 500


async def purge(db: AsyncSession, *, retention_days: int, dry_run: bool) -> int:
    """Redact candidate source and program output ``retention_days`` after the
    application was decided (or after ``submitted_at`` when there is no
    application — a hand-assigned exam). Scores are kept. Deletes this
    attempt's reports/fingerprints/signals and redacts any finding's
    rationale once its evidence is gone. Honours ``RETENTION_DRY_RUN``.

    The redaction UPDATE touches only ``answers``/``graded_snapshot``/
    ``code_redacted_at`` on ``exam_attempts`` — never status or score — which
    is the one exception ``exam_attempts_submission_frozen`` permits.
    """
    cutoff = datetime.now(tz=UTC) - timedelta(days=retention_days)
    rows = (
        await db.execute(text(_PURGEABLE_ATTEMPTS_SQL), {"cutoff": cutoff, "lim": _PURGE_BATCH})
    ).all()
    if dry_run or not rows:
        log.info("code_evidence.retention", candidates=len(rows), dry_run=dry_run)
        return len(rows)

    for attempt_id, company_id, answers, graded_snapshot in rows:
        new_answers = redact_coding_answers(answers)
        new_snapshot = redact_graded_snapshot(graded_snapshot)
        await db.execute(
            text(
                "UPDATE exam_attempts SET answers = CAST(:a AS jsonb),"
                " graded_snapshot = CAST(:g AS jsonb), code_redacted_at = now(),"
                " updated_at = now()"
                " WHERE id = :id AND company_id = :c AND code_redacted_at IS NULL"
            ),
            {"a": json.dumps(new_answers), "g": json.dumps(new_snapshot), "id": attempt_id, "c": company_id},
        )
        # MUST run before the DELETE below: code_integrity_findings.signal_id
        # is RESTRICT, not SET NULL, because a composite FK's ON DELETE
        # SET NULL would null company_id (NOT NULL) along with it. A finding
        # can sit on either attempt of the pair, not just this one, so the
        # match is on signal membership, not on attempt_id.
        await db.execute(
            text(
                "UPDATE code_integrity_findings SET signal_id = NULL"
                " WHERE company_id = :c AND signal_id IN ("
                "   SELECT id FROM code_similarity_signals"
                "    WHERE company_id = :c AND (attempt_low_id = :a OR attempt_high_id = :a)"
                " )"
            ),
            {"c": company_id, "a": attempt_id},
        )
        await db.execute(
            text("DELETE FROM code_similarity_signals WHERE company_id = :c"
                 " AND (attempt_low_id = :a OR attempt_high_id = :a)"),
            {"c": company_id, "a": attempt_id},
        )
        await db.execute(
            text("DELETE FROM code_fingerprints WHERE company_id = :c AND attempt_id = :a"),
            {"c": company_id, "a": attempt_id},
        )
        await db.execute(
            text("DELETE FROM code_quality_reports WHERE company_id = :c AND attempt_id = :a"),
            {"c": company_id, "a": attempt_id},
        )
        await db.execute(
            text(
                "UPDATE code_integrity_findings SET rationale = '[redacted]', redacted_at = now()"
                " WHERE company_id = :c AND attempt_id = :a AND redacted_at IS NULL"
            ),
            {"c": company_id, "a": attempt_id},
        )
    log.info("code_evidence.retention", purged=len(rows))
    return len(rows)
