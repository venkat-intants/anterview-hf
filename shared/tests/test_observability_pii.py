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
