"""Code-review finding SH-6: the two PII key sets in ``shared/observability``.

``pii.py`` declared the canonical structlog deny-list and ``sentry.py`` restated
its own, under a comment claiming the two mirrored each other. They did not —
each missed names the other covered. The fix makes ``sentry.py`` *derive* its
set, so the parity claim is true by construction.

The consolidation is only safe if it lost nothing, and "nothing was lost" is a
property, not a diff you can eyeball across 50 string literals. So the two
pre-consolidation lists are frozen below and every name in them is asserted to
be scrubbed *behaviourally* — through the real processor and the real Sentry
``before_send`` — rather than by comparing set literals. A future edit that
quietly narrows either consumer fails here.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from shared.observability.pii import PII_FIELDS, redact_pii_processor
from shared.observability.sentry import (
    _PII_KEYS,
    _TRANSPORT_HEADERS,
    _before_send,
    _scrub,
)

# ---------------------------------------------------------------------------
# Frozen history — DO NOT EDIT
#
# The two sets exactly as they stood before this consolidation (parent commit
# 551f776). Their only job is to prove the merge was a superset. Adding a name
# to either of these makes the assertion weaker, not stronger; new coverage
# belongs in pii.py.
# ---------------------------------------------------------------------------

_PRE_MERGE_STRUCTLOG_FIELDS = frozenset(
    {
        "email",
        "password",
        "phone",
        "full_name",
        "candidate_name",
        "candidate_email",
        "transcript",
        "answer",
        "question",
        "text_content",
        "turn_text",
        "resume_text",
        "jd_text",
        "target_jd_text",
        "address",
        "token",
        "raw_token",
        "access_token",
        "refresh_token",
    }
)

_PRE_MERGE_SENTRY_KEYS = frozenset(
    {
        "email",
        "password",
        "phone",
        "full_name",
        "name",
        "ip",
        "ip_hash",
        "user_agent_hash",
        "authorization",
        "cookie",
        "token",
        "access_token",
        "refresh_token",
        "jwt",
        "transcript",
        "user_email",
        "candidate_email",
        "to_email",
        "candidate_name",
        "resume_text",
        "resume_excerpt",
        "jd_text",
        "api_key",
        "secret",
        "client_secret",
        "code_verifier",
        "plain",
        "password_hash",
        "csrf_token",
    }
)


# ---------------------------------------------------------------------------
# No coverage was lost by merging
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(_PRE_MERGE_SENTRY_KEYS))
def test_sentry_still_scrubs_every_key_it_scrubbed_before(key: str) -> None:
    """Every name the old hand-maintained Sentry list covered is still redacted."""
    assert _scrub({key: "candidate-data"}) == {key: "[redacted]"}


@pytest.mark.parametrize("key", sorted(_PRE_MERGE_STRUCTLOG_FIELDS))
def test_structlog_still_drops_every_field_it_dropped_before(key: str) -> None:
    """Every name the canonical structlog set covered is still dropped."""
    event: dict[str, Any] = {"event": "e", key: "candidate-data"}
    assert key not in redact_pii_processor(None, "info", event)


@pytest.mark.parametrize("key", sorted(_PRE_MERGE_STRUCTLOG_FIELDS - _PRE_MERGE_SENTRY_KEYS))
def test_sentry_now_covers_the_names_only_structlog_had(key: str) -> None:
    """The half of the drift Sentry was on the wrong side of.

    ``answer``/``question``/``text_content``/``turn_text`` are candidate speech
    and ``raw_token`` is a live magic-link credential — all of them could
    previously ride out of the process in a Sentry ``extra`` untouched.
    """
    assert _scrub({key: "candidate-data"}) == {key: "[redacted]"}


# The three names from the old Sentry list that deliberately did NOT move into
# the canonical set. Spelled out as literals rather than subtracted from
# `_TRANSPORT_HEADERS`, so this test states the intended split instead of
# restating whatever the code currently does.
#   authorization, cookie — HTTP request headers; a structlog event dict has no
#     way to acquire one, and they remain covered on the Sentry side.
#   name — ambiguous; this codebase logs it as a non-PII identifier
#     (`circuit_breaker.closed name=...`). See `_PII_KEYS` for the full reason.
_DELIBERATELY_SENTRY_LOCAL = frozenset({"authorization", "cookie", "name"})


@pytest.mark.parametrize(
    "key",
    sorted(_PRE_MERGE_SENTRY_KEYS - _PRE_MERGE_STRUCTLOG_FIELDS - _DELIBERATELY_SENTRY_LOCAL),
)
def test_structlog_now_covers_the_names_only_sentry_had(key: str) -> None:
    """The other half: credential and contact names now in the canonical set."""
    event: dict[str, Any] = {"event": "e", key: "candidate-data"}
    assert key not in redact_pii_processor(None, "info", event)


def test_sentry_key_set_is_a_superset_of_the_canonical_set() -> None:
    """The derivation itself: Sentry can never cover less than structlog does."""
    assert PII_FIELDS <= _PII_KEYS


def test_sentry_only_extras_are_wire_names_plus_the_documented_exception() -> None:
    """Pins the split rule so the Sentry-local set cannot grow into a second list.

    Anything Sentry-local must be a header a structlog event could not carry.
    ``name`` is the single documented exception. A future name added here
    instead of to ``pii.py`` fails this test, which is the point.
    """
    assert _TRANSPORT_HEADERS | {"name"} == _PII_KEYS - PII_FIELDS
    assert _TRANSPORT_HEADERS.isdisjoint(PII_FIELDS)


# ---------------------------------------------------------------------------
# Scrubbing behaviour
# ---------------------------------------------------------------------------


def test_scrub_matches_case_insensitively() -> None:
    """Header and JSON casing varies on the wire; matching lowercases the key."""
    assert _scrub({"Authorization": "Bearer x", "Email": "a@b.com"}) == {
        "Authorization": "[redacted]",
        "Email": "[redacted]",
    }


def test_scrub_recurses_through_nested_dicts_and_lists() -> None:
    """PII nested inside an ``extra`` payload is redacted at any depth."""
    payload = {
        "candidates": [
            {"user_id": "u1", "resume_text": "Curriculum Vitae..."},
            {"user_id": "u2", "meta": {"phone": "+91-9999999999"}},
        ]
    }
    assert _scrub(payload) == {
        "candidates": [
            {"user_id": "u1", "resume_text": "[redacted]"},
            {"user_id": "u2", "meta": {"phone": "[redacted]"}},
        ]
    }


@pytest.mark.parametrize(
    "key",
    [
        # The exact keys the four services' own redaction tests assert survive.
        # Widening the deny-list must not blank the fields operators debug with.
        "event",
        "user_id",
        "session_id",
        "request_id",
        "role",
        "language",
        "room",
        "scorecard_id",
        "composite_score",
        "model",
        "company_id",
        "status_code",
    ],
)
def test_operational_keys_survive_both_scrubbers(key: str) -> None:
    assert _scrub({key: "value"}) == {key: "value"}
    assert key in redact_pii_processor(None, "info", {"event": "e", key: "value"})


# ---------------------------------------------------------------------------
# before_send: the header sweep now reads from the same set
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("header", sorted(_TRANSPORT_HEADERS))
def test_before_send_redacts_every_transport_header(header: str) -> None:
    event = {"request": {"headers": {header.title(): "secret-value"}}}
    assert _before_send(event, {})["request"]["headers"][header.title()] == "[redacted]"


def test_before_send_keeps_benign_headers() -> None:
    """A 500 is only debuggable if the non-credential headers survive."""
    event = {"request": {"headers": {"x-request-id": "req-1", "user-agent": "curl/8"}}}
    headers = _before_send(event, {})["request"]["headers"]
    assert headers == {"x-request-id": "req-1", "user-agent": "curl/8"}


def test_before_send_scrubs_pii_in_extra_and_breadcrumbs() -> None:
    """Both recursive surfaces run through the merged key set."""
    event: dict[str, Any] = {
        "extra": {"answer": "I am a software engineer", "session_id": "s1"},
        "breadcrumbs": {"values": [{"data": {"turn_text": "hello there", "turn": 3}}]},
    }
    result = _before_send(event, {})
    assert result["extra"] == {"answer": "[redacted]", "session_id": "s1"}
    assert result["breadcrumbs"]["values"] == [
        {"data": {"turn_text": "[redacted]", "turn": 3}}
    ]


def test_before_send_never_raises_on_a_malformed_event() -> None:
    """Scrubbing must not break error reporting, whatever shape the event has."""
    assert _before_send({"request": "not-a-dict", "extra": None}, {}) is not None


# ---------------------------------------------------------------------------
# SEC-7 — the bare recipient keys
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["to", "recipient"])
def test_bare_recipient_keys_are_redacted_by_both_scrubbers(key: str) -> None:
    """``email_util.py:145`` logs a raw address as ``to=``.

    ``to_email`` was in the set and ``to`` was not; matching is exact, so the
    one name actually used as a log kwarg was the one not covered. An email
    address is personal data under DPDP §8 wherever it is spelled.
    """
    event: dict[str, Any] = {"event": "email.sent", key: "candidate@example.com"}
    assert key not in redact_pii_processor(None, "info", event)
    assert _scrub({key: "candidate@example.com"}) == {key: "[redacted]"}


# ---------------------------------------------------------------------------
# SEC-8 — transaction events go through the other hook
# ---------------------------------------------------------------------------


def test_transaction_span_data_and_tags_are_scrubbed() -> None:
    """A transaction keeps its payload in span ``data``/``tags``, not ``extra``.

    Wiring the scrubber to ``before_send_transaction`` is only a real fix if it
    reaches the surface transactions actually carry.
    """
    event: dict[str, Any] = {
        "type": "transaction",
        "spans": [
            {
                "op": "db.query",
                "data": {"candidate_email": "a@b.com", "rows": 3},
                "tags": {"api_key": "sk-live-xxxx", "route": "/sessions"},
            }
        ],
    }
    result = _before_send(event, {})
    span = result["spans"][0]
    assert span["data"] == {"candidate_email": "[redacted]", "rows": 3}
    assert span["tags"] == {"api_key": "[redacted]", "route": "/sessions"}


def test_transaction_keeps_its_name_and_span_structure() -> None:
    """``name`` is in ``_PII_KEYS`` and is also the transaction's route.

    Scrubbing the event top level (or a whole span dict) would blank it and
    make every performance event unreadable — which is why only ``data`` and
    ``tags`` are scrubbed. This test is what stops a future "just _scrub the
    event" simplification.
    """
    event: dict[str, Any] = {
        "type": "transaction",
        "name": "GET /api/v1/sessions/{id}",
        "transaction": "GET /api/v1/sessions/{id}",
        "spans": [{"op": "http.client", "description": "GET https://sarvam.ai/tts"}],
    }
    result = _before_send(event, {})
    assert result["name"] == "GET /api/v1/sessions/{id}"
    assert result["transaction"] == "GET /api/v1/sessions/{id}"
    assert result["spans"][0]["description"] == "GET https://sarvam.ai/tts"


def test_transaction_request_query_string_is_dropped() -> None:
    """The OAuth-code leak, on the tracing path.

    A sampled ``/auth/sso/google/callback?code=..`` transaction shipped the
    authorization code to a third-party SaaS, because ``before_send`` is not
    applied to transaction events at all.
    """
    event: dict[str, Any] = {
        "type": "transaction",
        "request": {
            "url": "https://api.intants.com/auth/sso/google/callback?code=4/0Ab_secret",
            "query_string": "code=4/0Ab_secret&state=xyz",
            "headers": {"Cookie": "session=abc"},
        },
    }
    req = _before_send(event, {})["request"]
    assert "query_string" not in req
    assert req["url"] == "https://api.intants.com/auth/sso/google/callback"
    assert req["headers"]["Cookie"] == "[redacted]"


def test_malformed_spans_do_not_break_scrubbing() -> None:
    """Scrubbing must never break reporting — including on a shape no real SDK
    emits, since ``_before_send`` also runs on hand-built events in tests."""
    event: dict[str, Any] = {"type": "transaction", "spans": ["not-a-dict", {"data": None}]}
    assert _before_send(event, {}) is not None


def test_both_sentry_hooks_receive_the_same_scrubber(monkeypatch: pytest.MonkeyPatch) -> None:
    """SEC-8 itself: the SDK dispatches errors and transactions through
    different hooks and applies neither to the other.

    Verified at the ``sentry_sdk.init`` call, since that is where the omission
    lived — every scrubbing test above would have passed unchanged while
    transactions left the process in the clear.
    """
    import shared.observability.sentry as sentry_mod

    captured: dict[str, Any] = {}
    fake_sdk = types.ModuleType("sentry_sdk")
    monkeypatch.setattr(fake_sdk, "init", lambda **kwargs: captured.update(kwargs), raising=False)
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sdk)

    assert sentry_mod.init_sentry(
        "https://key@o0.ingest.sentry.io/1", environment="test", service_name="svc"
    )

    assert captured["before_send"] is sentry_mod._before_send
    assert captured["before_send_transaction"] is sentry_mod._before_send
    # The other two PII guards at the same call site, pinned here because they
    # are just as invisible: send_default_pii would auto-attach the client IP,
    # and include_local_variables would ship the PLAINTEXT password held in
    # LocalAuthProvider._verify_password's `plain`.
    assert captured["send_default_pii"] is False
    assert captured["include_local_variables"] is False
