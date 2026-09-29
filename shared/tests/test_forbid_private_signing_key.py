"""``forbid_private_signing_key`` (AR-2): a non-issuer service must never hold
a JWT private key.

Only ``data_gateway``'s ``Settings`` class declares ``jwt_private_key`` at
all, so ``pydantic-settings``' ``extra="ignore"`` would otherwise make a stray
``JWT_PRIVATE_KEY`` on any other service silently do nothing. These tests pin
the loud version of that safety, and that it has no environment exemption —
unlike ``assert_strong_secrets``, this one fires in development too.
"""

from __future__ import annotations

import pytest

from shared.security import forbid_private_signing_key


def test_no_op_when_the_variable_is_absent() -> None:
    forbid_private_signing_key("interview_core", env={})


def test_no_op_when_the_variable_is_blank() -> None:
    forbid_private_signing_key("interview_core", env={"JWT_PRIVATE_KEY": ""})
    forbid_private_signing_key("interview_core", env={"JWT_PRIVATE_KEY": "   "})


def test_raises_when_the_variable_is_set() -> None:
    with pytest.raises(ValueError, match="JWT_PRIVATE_KEY"):
        forbid_private_signing_key(
            "interview_core", env={"JWT_PRIVATE_KEY": "some-key-material"}
        )


def test_error_names_the_offending_service() -> None:
    """An operator hit by this needs to know WHICH service's environment to
    fix — a generic message sends them hunting across all four."""
    with pytest.raises(ValueError, match="admin_ops"):
        forbid_private_signing_key("admin_ops", env={"JWT_PRIVATE_KEY": "x"})


def test_defaults_to_the_real_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``env=`` argument reads ``os.environ`` — the production code path,
    not just the test-friendly override."""
    monkeypatch.setenv("JWT_PRIVATE_KEY", "leaked-into-the-wrong-service")
    with pytest.raises(ValueError, match="JWT_PRIVATE_KEY"):
        forbid_private_signing_key("feedback_billing")


def test_real_environment_is_unaffected_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JWT_PRIVATE_KEY", raising=False)
    forbid_private_signing_key("feedback_billing")


def test_does_not_mind_being_called_for_data_gateway_itself() -> None:
    """data_gateway never calls this (it is the one service allowed to hold
    the key), but the function itself has no special-cased exception for it —
    the caller decides who calls it, not the function."""
    forbid_private_signing_key("data_gateway", env={})
    with pytest.raises(ValueError):
        forbid_private_signing_key("data_gateway", env={"JWT_PRIVATE_KEY": "x"})
