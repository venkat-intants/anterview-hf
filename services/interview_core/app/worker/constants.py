"""Interview-shape constants shared by the worker and its sibling modules.

These four answer "how many questions, for how long, and how often do we
re-check consent". They lived in ``interview_worker.py``, which is also where
everything that reads them lived — until IC-4 split the prompt builder and the
consent watchdog into their own modules. A constant that two siblings need
cannot stay in one of them without making the other import it back and creating
an import cycle, so it lives here: no imports, nothing to cycle with.

Deliberately NOT in ``app/config.py``. Their scope is this worker's interview
protocol, not deployment configuration — an operator has no business changing
"ten questions" through an environment variable, because the number is enforced
in code (``InterviewState``), stated in the prompt, and assumed by the scorer's
minimum-answers rule. Anything genuinely per-deployment (drain timeout,
concurrency ceiling, heartbeat interval) is in ``Settings`` already.
"""

from __future__ import annotations

# Exactly 10 candidate answers before the interview closes (code-enforced).
MAX_CANDIDATE_ANSWERS: int = 10

# Safety wall-clock cap in seconds (12 minutes). Whichever fires first:
# 10th answer OR this cap.
SESSION_WALL_CLOCK_CAP_SECONDS: int = 12 * 60  # 720 s

# Minimum candidate answers required before we bother scoring. If the candidate
# disconnects before this, we mark the session 'abandoned' and skip the scorer.
MIN_ANSWERS_TO_SCORE: int = 2

# DPDP §11 — how often to re-check that the candidate's recording consent is still
# active DURING a live session (not just at join). On withdrawal we end the
# interview within this window. Kept short enough to honour withdrawal promptly,
# long enough to be a negligible DB load (one indexed SELECT per tick).
CONSENT_RECHECK_INTERVAL_SECONDS: int = 15
