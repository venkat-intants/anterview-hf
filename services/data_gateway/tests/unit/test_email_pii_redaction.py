"""SEC-7 — the legacy email shim logs a raw recipient under the bare key ``to``.

``email_util.send_email`` binds ``to=to`` on both its success and its failure log
line (``:145``, ``:148``). The canonical ``PII_FIELDS`` set covered ``email``,
``user_email`` and ``to_email``, and matching is EXACT, so none of the three
covered ``to`` — a candidate's address went to the log stream in cleartext.

``shared/observability/pii.py`` now carries ``to`` and ``recipient``. What is
verified here is the half that lives in this service: that the redaction is
actually REACHED from this call site. A shared deny-list is worth nothing on a
logger whose processor chain omits the processor, and this module's logger is a
plain ``structlog.get_logger`` that inherits whatever ``app/main.py`` configured
— so the coupling is real and untested until now.

The finding calls the shim dead. It is public API in a live module, and "no
caller today" is a property of the current tree, not a control.
"""

from __future__ import annotations

from typing import Any

import pytest
import structlog
from shared.observability.pii import PII_FIELDS, redact_pii_processor

from app import email_util


def _capturing_chain(sink: list[dict[str, Any]]) -> None:
    """Configure structlog exactly as app/main.py does, but capture the result.

    ``structlog.testing.capture_logs`` cannot be used: it replaces the whole
    processor chain, so it would report the event dict BEFORE redaction and pass
    while the real chain leaked.
    """

    def _sink(logger: Any, method: str, event_dict: dict[str, Any]) -> str:
        sink.append(dict(event_dict))
        return ""

    structlog.configure(
        processors=[redact_pii_processor, _sink],
        wrapper_class=structlog.make_filtering_bound_logger(0),
    )


@pytest.fixture
def captured() -> Any:
    sink: list[dict[str, Any]] = []
    _capturing_chain(sink)
    yield sink
    structlog.reset_defaults()


def test_the_canonical_set_covers_the_bare_recipient_keys() -> None:
    assert "to" in PII_FIELDS
    assert "recipient" in PII_FIELDS


@pytest.mark.asyncio
async def test_a_successful_send_does_not_log_the_recipient(
    captured: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _ok(**kwargs: Any) -> None:
        return None

    monkeypatch.setattr(email_util, "deliver_smtp", _ok)

    assert await email_util.send_email(
        to="candidate@example.com", subject="Interview invite", html="<p>hi</p>"
    )

    events = [e for e in captured if e.get("event") == "email.sent"]
    assert events, "the shim must still emit its delivery log line"
    assert "to" not in events[0]
    assert "candidate@example.com" not in str(events[0])


@pytest.mark.asyncio
async def test_a_failed_send_does_not_log_the_recipient_either(
    captured: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure branch is the one an operator actually reads, so it is the one
    most likely to be copied into a ticket or a screenshot."""

    async def _boom(**kwargs: Any) -> None:
        raise RuntimeError("relay refused")

    monkeypatch.setattr(email_util, "deliver_smtp", _boom)

    assert not await email_util.send_email(
        to="candidate@example.com", subject="Interview invite", html="<p>hi</p>"
    )

    events = [e for e in captured if e.get("event") == "email.send_failed"]
    assert events
    assert "to" not in events[0]
    assert "candidate@example.com" not in str(events[0])


def test_the_service_chain_installs_the_shared_processor() -> None:
    """If app/main.py ever drops the processor, every test above still passes —
    they configure their own chain. This is the assertion that does not.

    Checked structurally rather than by reloading ``app.main``: a reload would
    rebuild the module-level ``app`` object that other tests hold a reference to,
    making this file's pass/fail depend on collection order.
    """
    from pathlib import Path

    import app.main as main_module

    assert main_module._redact_pii_processor is redact_pii_processor

    source = Path(main_module.__file__).read_text(encoding="utf-8")
    configure_block = source.split("structlog.configure(", 1)[1].split("wrapper_class", 1)[0]
    assert "_redact_pii_processor" in configure_block
    # Order is the control: anything added to the event dict after the renderer
    # would never be redacted.
    assert configure_block.index("_redact_pii_processor") < configure_block.index(
        "JSONRenderer"
    )
