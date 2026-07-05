"""Unit tests for the email transport provider switch (SMTP vs Resend HTTPS).

Resend HTTP is mocked so these run offline. Context: HF Spaces blocks outbound
SMTP ports network-wide, so the Space must run EMAIL_PROVIDER=resend; local/VM
deploys keep the smtp default.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app import email_util
from app.email_util import ResendDeliveryError, deliver_email, deliver_smtp


class _FakeResp:
    def __init__(self, status_code: int, body: dict[str, Any]) -> None:
        self.status_code = status_code
        self.text = json.dumps(body)


class _FakeClient:
    """Records the request so tests can assert on the Resend payload."""

    last_url: str | None = None
    last_json: dict[str, Any] | None = None
    last_headers: dict[str, str] | None = None
    resp: _FakeResp = _FakeResp(200, {"id": "email_123"})

    def __init__(self, *_: Any, **__: Any) -> None:
        pass

    async def __aenter__(self) -> "_FakeClient":
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeResp:
        _FakeClient.last_url = url
        _FakeClient.last_json = json
        _FakeClient.last_headers = headers
        return _FakeClient.resp


@pytest.fixture()
def resend_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(email_util.settings, "email_provider", "resend")
    monkeypatch.setattr(email_util.settings, "resend_api_key", "re_test_key")
    monkeypatch.setattr(email_util.settings, "resend_api_url", "https://api.resend.com")
    monkeypatch.setattr(email_util.settings, "email_from", "noreply@example.com")
    monkeypatch.setattr(email_util.settings, "email_from_name", "Anterview")
    monkeypatch.setattr(email_util.settings, "email_reply_to", "")
    monkeypatch.setattr(email_util.httpx, "AsyncClient", _FakeClient)
    _FakeClient.resp = _FakeResp(200, {"id": "email_123"})
    _FakeClient.last_json = None


@pytest.mark.asyncio
async def test_resend_provider_posts_to_resend_api(resend_settings: None) -> None:
    await deliver_email(
        to="candidate@example.com",
        subject="Your interview",
        html="<p>Hello</p>",
        text="Hello",
    )
    assert _FakeClient.last_url == "https://api.resend.com/emails"
    assert _FakeClient.last_headers == {"Authorization": "Bearer re_test_key"}
    body = _FakeClient.last_json
    assert body is not None
    assert body["to"] == ["candidate@example.com"]
    assert body["subject"] == "Your interview"
    assert body["from"] == "Anterview <noreply@example.com>"
    assert body["html"] == "<p>Hello</p>"
    assert body["text"] == "Hello"


@pytest.mark.asyncio
async def test_resend_company_branded_from_name(resend_settings: None) -> None:
    """from_name overrides the display name (company-branded invites)."""
    await deliver_email(
        to="candidate@example.com",
        subject="s",
        html="<p>h</p>",
        from_name="Google",
    )
    assert _FakeClient.last_json is not None
    assert _FakeClient.last_json["from"] == "Google <noreply@example.com>"


@pytest.mark.asyncio
async def test_resend_api_error_raises_retriable(resend_settings: None) -> None:
    """A non-2xx from Resend must raise so the outbox worker schedules a retry."""
    _FakeClient.resp = _FakeResp(422, {"message": "domain is not verified"})
    with pytest.raises(ResendDeliveryError) as excinfo:
        await deliver_email(to="c@example.com", subject="s", html="<p>h</p>")
    assert "422" in str(excinfo.value)
    assert "domain is not verified" in str(excinfo.value)


@pytest.mark.asyncio
async def test_resend_without_key_raises(
    resend_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(email_util.settings, "resend_api_key", "")
    with pytest.raises(ResendDeliveryError):
        await deliver_email(to="c@example.com", subject="s", html="<p>h</p>")


@pytest.mark.asyncio
async def test_invalid_recipient_raises_value_error(resend_settings: None) -> None:
    """Bad recipients are never retriable — ValueError, not a transport error."""
    with pytest.raises(ValueError):
        await deliver_email(to="not-an-email", subject="s", html="<p>h</p>")


@pytest.mark.asyncio
async def test_smtp_provider_does_not_touch_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With the smtp default, the SMTP path runs and no Resend request is made."""
    monkeypatch.setattr(email_util.settings, "email_provider", "smtp")
    sent: dict[str, Any] = {}
    monkeypatch.setattr(email_util, "_send_sync", lambda msg: sent.update(msg=msg))
    monkeypatch.setattr(email_util.httpx, "AsyncClient", _FakeClient)
    _FakeClient.last_json = None

    await deliver_email(to="c@example.com", subject="s", html="<p>h</p>")
    assert "msg" in sent
    assert _FakeClient.last_json is None


def test_deliver_smtp_alias_points_at_deliver_email() -> None:
    """Back-compat: app.mailer imports deliver_smtp — must be the same callable."""
    assert deliver_smtp is deliver_email
