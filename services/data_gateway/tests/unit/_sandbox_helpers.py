"""Stand-ins for an analyser that misbehaves, for tests/unit/test_ph4_d3_sandbox.py.

They live in their own importable module because the sandbox SPAWNS a fresh
interpreter per task and hands it the function by reference -- so the child
must be able to import it, which a function defined inside a test cannot
offer. Nothing here is ever given candidate code.
"""

from __future__ import annotations

import os
import time


def doubled(x: int) -> int:
    return x * 2


def dies_without_answering() -> None:
    """An abrupt exit -- what SIGXCPU, the OOM killer or a crash in a C
    extension looks like from the parent: the process is gone and it said
    nothing."""
    os._exit(3)


def never_finishes() -> None:
    time.sleep(120)


def raises_with_a_message() -> None:
    raise ValueError("def secret_candidate_function(): pass")


def reports_its_environment() -> dict[str, str]:
    return dict(os.environ)
