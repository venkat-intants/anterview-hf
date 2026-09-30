"""A provider error must never carry a credential to a user.

THE INCIDENT. ``last_error`` is interpolated into the exception
``post_with_retry`` raises; that exception's text becomes an HTTP 502 ``detail``;
the frontend renders the detail in a toast. So any credential inside a provider
error is shown to whoever is on the screen — an HR user, or a candidate
mid-exam.

It happened. A Groq key pasted with a line break inside it made httpx refuse the
request with

    Illegal header value b'Bearer gsk_<the whole live key>'

and the platform printed that key into a browser, from where it reached a
screenshot. The key had to be treated as public and rotated.

The redaction is exact-value, not pattern-based: at the point of failure we know
precisely which secrets we sent, so we remove those rather than guessing at
vendor key shapes we may not have thought of.
"""

from __future__ import annotations

import pytest

from shared.llm._recovery import REDACTED, redact_known_secrets

# Groq-SHAPED but wholly invented: this shares no bytes with any real key.
# The first draft of this file reused the prefix that appeared in the real
# error toast, which is exactly the material that must not enter a tracked
# file — the length and the `gsk_` shape are all the test needs.
KEY = "gsk_testonly000000000000000000000000notarealkey"
BEARER = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
URL = "https://api.groq.com/openai/v1/chat/completions"


def test_the_exact_message_that_leaked_a_live_key_is_redacted() -> None:
    """Reproduces the real string, verbatim in shape."""
    leaked = f"request error: Illegal header value b'Bearer {KEY}'"
    out = redact_known_secrets(leaked, headers=BEARER, url=URL)
    assert KEY not in out
    assert REDACTED in out
    # The diagnosis survives — that is the whole point of keeping the message.
    assert "Illegal header value" in out


def test_the_bare_token_is_caught_as_well_as_the_whole_header() -> None:
    """Providers echo either spelling, so both are removed."""
    for text in (f"...{KEY}...", f"...Bearer {KEY}..."):
        assert KEY not in redact_known_secrets(text, headers=BEARER, url=URL)


def test_a_key_echoed_back_in_the_provider_body_is_redacted() -> None:
    """The second vector: not our exception, the vendor's own error body."""
    body = f'HTTP 401: {{"error":{{"message":"Invalid API key: {KEY}"}}}}'
    out = redact_known_secrets(body, headers=BEARER, url=URL)
    assert KEY not in out
    assert "Invalid API key" in out


def test_a_key_carried_in_the_url_query_is_redacted() -> None:
    """Gemini puts the key in the URL, and httpx renders the full URL into its
    own exception text — so the header sweep alone would miss it."""
    gemini_key = "AIzaSyTestOnlyNotARealKeyValue123456"
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={gemini_key}"
    text = f"request error: ConnectError for url {url}"
    out = redact_known_secrets(text, headers={}, url=url)
    assert gemini_key not in out
    assert REDACTED in out


def test_x_api_key_and_x_goog_api_key_headers_are_covered() -> None:
    for header in ("X-API-Key", "x-goog-api-key", "api-key"):
        out = redact_known_secrets(
            f"denied for {KEY}", headers={header: KEY}, url=URL
        )
        assert KEY not in out, f"{header} not covered"


def test_ordinary_headers_and_prose_are_left_alone() -> None:
    """Redaction must not eat the diagnosis. `application/json` is not a secret,
    and a message with no credential in it comes back untouched."""
    text = "HTTP 429: rate limit exceeded, retry in 20s (content-type application/json)"
    assert redact_known_secrets(text, headers=BEARER, url=URL) == text


def test_a_short_header_value_is_never_blindly_replaced() -> None:
    """A placeholder like `none` must not turn every occurrence of that word in
    the message into a redaction marker."""
    out = redact_known_secrets(
        "HTTP 401: none of the supplied credentials matched",
        headers={"Authorization": "none"},
        url=URL,
    )
    assert out == "HTTP 401: none of the supplied credentials matched"


def test_empty_text_is_handled() -> None:
    assert redact_known_secrets("", headers=BEARER, url=URL) == ""


@pytest.mark.asyncio
async def test_the_raised_exception_cannot_contain_the_key(monkeypatch) -> None:
    """End to end through post_with_retry: the message that reaches the caller —
    and therefore the browser — is clean, not merely the helper's output."""
    import httpx

    from shared.llm import _recovery

    class _Boom:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            raise httpx.ConnectError(f"Illegal header value b'Bearer {KEY}'")

    monkeypatch.setattr(_recovery.httpx, "AsyncClient", lambda **k: _Boom())
    monkeypatch.setattr(_recovery, "MAX_ATTEMPTS", 1)

    with pytest.raises(RuntimeError) as exc:
        await _recovery.post_with_retry(
            URL,
            body={},
            headers=BEARER,
            timeout=1.0,
            model="m",
            provider="Groq",
            error_cls=RuntimeError,
        )
    assert KEY not in str(exc.value)
    assert REDACTED in str(exc.value)
