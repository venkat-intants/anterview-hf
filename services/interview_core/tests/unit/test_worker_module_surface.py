"""IC-4: the split is invisible from outside ``app.worker.interview_worker``.

``interview_worker.py`` was 2,972 lines — 5.9x the project's 500-line threshold
— so the prompt builder, the DPDP consent watchdog, the session/turn writes and
the interview-shape constants moved into sibling modules. That refactor is only
safe if the module's public surface is unchanged, because two things depend on
it in ways a normal import error would not catch:

* **Every deployment** runs ``python -m app.worker.interview_worker`` (the
  Dockerfile, docker-compose, supervisord and the two ``Run:`` lines in the
  module docstring). A moved ``entrypoint``/``run`` would pass every unit test
  and fail at first boot.
* **The test suite patches by module path.** ``patch("app.worker.
  interview_worker.X")`` rebinds the name in THIS module's globals; a caller
  that has moved to a sibling reads its own globals instead and never sees the
  patch. Such a test does not error — it silently stops testing what it says it
  tests, which is the failure mode worth a pin of its own.

Identity (``is``), not mere presence: a re-export that produced a copy would
satisfy ``hasattr`` and still break both properties above.
"""

from __future__ import annotations

import pytest

import app.worker.interview_worker as wk
from app.worker import consent as consent_mod
from app.worker import constants as constants_mod
from app.worker import prompt as prompt_mod
from app.worker import session_store as store_mod

# (attribute name, module it now lives in). Each entry is either imported from
# ``interview_worker`` by a test, patched on it, or read as a module global by
# code that stayed behind.
_MOVED = [
    ("MAX_CANDIDATE_ANSWERS", constants_mod),
    ("MIN_ANSWERS_TO_SCORE", constants_mod),
    ("SESSION_WALL_CLOCK_CAP_SECONDS", constants_mod),
    ("_RESUME_PROMPT_CHAR_CAP", prompt_mod),
    ("_interviewer_instructions", prompt_mod),
    ("_CONSENT_RESOLVE_DB_ERROR", consent_mod),
    ("_RESOLVE_CONSENT_BACKOFF_SECONDS", consent_mod),
    ("_RESOLVE_CONSENT_MAX_ATTEMPTS", consent_mod),
    ("_lookup_candidate_user_id", consent_mod),
    ("_run_consent_watchdog", consent_mod),
    ("resolve_consent_user_id", consent_mod),
    ("_persist_injection_markers", store_mod),
    ("_persist_turns", store_mod),
    ("_read_session_status", store_mod),
    ("_update_session_status", store_mod),
]


@pytest.mark.parametrize(("name", "module"), _MOVED, ids=[n for n, _ in _MOVED])
def test_moved_symbol_is_still_reachable_from_the_worker_module(
    name: str, module: object
) -> None:
    """Re-exported, and the SAME object — not a copy that would drift."""
    assert hasattr(wk, name), (
        f"{name} moved out of interview_worker and was not re-exported. "
        "Callers pinned to the old path — tests and mock.patch targets among "
        "them — break, or worse, patch a name nothing reads."
    )
    assert getattr(wk, name) is getattr(module, name)


def test_entrypoint_and_run_stayed_put() -> None:
    """``python -m app.worker.interview_worker`` is what every deploy executes."""
    assert callable(wk.entrypoint)
    assert callable(wk.run)
    assert wk.entrypoint.__module__ == "app.worker.interview_worker"


def test_the_split_actually_shrank_the_file() -> None:
    """Guard the point of IC-4, not just its safety.

    Without this, the modules exist and the file grows straight back — which is
    precisely what happened between the first time IC-4 was raised and the
    second, when it came back 295 lines LARGER.
    """
    import pathlib

    src = pathlib.Path(wk.__file__)
    line_count = len(src.read_text(encoding="utf-8").splitlines())
    assert line_count < 2600, (
        f"interview_worker.py is back to {line_count} lines. It was 2,972 when "
        "IC-4 was re-raised and 2,521 after the split. New worker code that is "
        "not about running a LiveKit job belongs in a sibling module under "
        "app/worker/ — see the module map in its docstring. Raising this "
        "ceiling is a decision to record, not a way to make the test pass."
    )
