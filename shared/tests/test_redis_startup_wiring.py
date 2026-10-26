"""Every service pings Redis once at startup (review F7).

Redis-backed controls all fail OPEN, so a Redis that cannot be reached - or a TLS
handshake rejected by hostname verification - used to switch them off with only
per-call warnings. Each service's lifespan must call ``verify_redis_connection``
right after ``init_redis()``. Checked on the source so it cannot decay unnoticed;
the behaviour of the helper itself is tested in test_redis_factory.py.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_SERVICES = ("data_gateway", "interview_core", "feedback_billing", "admin_ops")
_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _calls_in_lifespan(service: str) -> list[str]:
    tree = ast.parse((_ROOT / "services" / service / "app" / "main.py").read_text("utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "lifespan":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    func = sub.func
                    names.append(func.id if isinstance(func, ast.Name) else getattr(func, "attr", ""))
    return names


@pytest.mark.parametrize("service", _SERVICES)
def test_the_lifespan_verifies_redis_right_after_initialising_it(service: str) -> None:
    calls = _calls_in_lifespan(service)
    assert "init_redis" in calls
    assert "verify_redis_connection" in calls, f"{service} never pings Redis at startup"
    assert calls.index("verify_redis_connection") > calls.index("init_redis")
