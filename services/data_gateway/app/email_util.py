"""Low-level email transport (SMTP relay or Resend HTTPS API).

``EMAIL_PROVIDER`` selects the wire protocol:
  * ``smtp`` (default) — direct relay via ``smtp_*`` / ``email_from``. Local dev
    points at a catch-all like Mailpit (localhost:1025); VM deploys use a real
    relay; production is AWS SES Mumbai. Uses stdlib ``smtplib`` in a worker
    thread (``asyncio.to_thread``) so we add no dependency and never block the
    event loop.
  * ``resend`` — POST https://api.resend.com/emails over HTTPS. REQUIRED on
    HF Spaces: HF blocks outbound SMTP ports (25/465/587) network-wide
    ("[Errno 101] Network is unreachable"), so only an HTTPS API can deliver.

Two entry points:
  * ``deliver_email`` — RAISES on failure. This is what the outbox worker
    (app.mailer) calls so it can record the error and schedule a retry.
    (``deliver_smtp`` remains as a back-compat alias.)
  * ``send_email`` — legacy BEST-EFFORT wrapper (returns bool, never raises).
    New code should enqueue via ``app.mailer.enqueue_email`` instead, which gives
    durable retry + a delivery log. Kept for back-compat.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

import httpx
import structlog

from app.config import settings

log = structlog.get_logger(__name__)


def _send_sync(msg: EmailMessage) -> None:
    """Blocking SMTP send (runs in a worker thread)."""
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
        return
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)


def _build_message(
    to: str, subject: str, html: str, text: str | None, from_name: str | None = None
) -> EmailMessage:
    msg = EmailMessage()
    # The envelope address is fixed (the single authenticated SMTP relay account),
    # but the DISPLAY NAME is per-email: company-scoped invites show the company
    # (e.g. "Google", "CDPR") so the candidate's inbox reads as that company, not
    # the generic platform brand. Falls back to the platform brand when unset.
    display = (from_name or "").strip() or settings.email_from_name
    msg["From"] = formataddr((display, settings.email_from))
    msg["To"] = to
    msg["Subject"] = subject
    if settings.email_reply_to:
        msg["Reply-To"] = settings.email_reply_to
    # Plain-text fallback first, then the HTML alternative.
    msg.set_content(text or _strip_html(html))
    msg.add_alternative(html, subtype="html")
    return msg


class ResendDeliveryError(Exception):
    """Non-2xx from the Resend API. Retriable by the outbox worker."""


async def _send_resend(
    to: str, subject: str, html: str, text: str | None, from_name: str | None
) -> None:
    """POST one message to the Resend HTTPS API, raising on any failure."""
    if not settings.resend_api_key:
        raise ResendDeliveryError(
            "EMAIL_PROVIDER=resend but RESEND_API_KEY is not set"
        )
    display = (from_name or "").strip() or settings.email_from_name
    payload: dict[str, object] = {
        # Resend expects "Display Name <address>" — same semantics as the SMTP
        # From header (company-branded display name, fixed sender address).
        "from": formataddr((display, settings.email_from)),
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text or _strip_html(html),
    }
    if settings.email_reply_to:
        payload["reply_to"] = settings.email_reply_to
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.resend_api_url.rstrip('/')}/emails",
            json=payload,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        )
    if resp.status_code >= 400:
        # Body carries Resend's error message (e.g. unverified domain,
        # sandbox recipient restriction) — surface it into email_events.
        raise ResendDeliveryError(
            f"Resend API HTTP {resp.status_code}: {resp.text[:300]}"
        )


async def deliver_email(
    *, to: str, subject: str, html: str, text: str | None = None, from_name: str | None = None
) -> None:
    """Send one email via the configured provider, RAISING on any failure.

    The outbox worker relies on the raised exception to mark the row failed and
    schedule a retry. ``ValueError`` for an obviously invalid recipient (never
    retriable); transport errors propagate as-is (retriable).

    ``from_name`` overrides the sender display name (company-branded invites);
    None falls back to the platform brand.
    """
    if not to or "@" not in to:
        raise ValueError(f"invalid recipient: {to!r}")
    if settings.email_provider.strip().lower() == "resend":
        await _send_resend(to, subject, html, text, from_name)
        return
    msg = _build_message(to, subject, html, text, from_name=from_name)
    await asyncio.to_thread(_send_sync, msg)


# Back-compat alias — app.mailer and older call sites import deliver_smtp.
deliver_smtp = deliver_email


async def send_email(
    *, to: str, subject: str, html: str, text: str | None = None
) -> bool:
    """Best-effort send (returns True/False, never raises). Legacy shim.

    Prefer ``app.mailer.enqueue_email`` for durable retry + a delivery log.
    """
    try:
        await deliver_smtp(to=to, subject=subject, html=html, text=text)
        log.info("email.sent", to=to, subject=subject)
        return True
    except Exception as exc:  # noqa: BLE001 - smtplib raises many types; best-effort
        log.warning("email.send_failed", to=to, subject=subject, error=str(exc))
        return False


def _strip_html(html: str) -> str:
    """Crude HTML→text fallback for the plain-text part (no external dep)."""
    import re

    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"</p\s*>", "\n\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()
