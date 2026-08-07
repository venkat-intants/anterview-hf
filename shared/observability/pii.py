"""One PII redaction set for every service's structlog chain (DPDP §8).

Defence in depth: no code should be binding a candidate's transcript to a log
event in the first place, but "should not" is not a control. This processor drops
known PII field names from every event dict before rendering, so a mistake in one
log call fails safe.

Why shared rather than per-service
----------------------------------
It was per-service, and it drifted. ``data_gateway`` redacted eleven fields;
``interview_core``, ``feedback_billing`` and ``admin_ops`` redacted four
(``email``, ``password``, ``phone``, ``full_name``) while their comments claimed
parity. The service that missed ``transcript``, ``text_content``, ``answer`` and
``question`` was ``interview_core`` — the one service whose entire job is
handling interview transcripts.

Adding a field here covers all four at once, which is the property that was
missing. A field name only ever needs adding, never removing: over-redacting a
log line costs debugging convenience, under-redacting writes a candidate's CV to
the log stream.

This set is also the base of the Sentry scrubber (``observability/sentry.py``),
which derives ``_PII_KEYS`` from it rather than restating it. That file used to
carry its own list under a comment claiming the two mirrored each other; they
did not, and the credential/contact names it covered alone have been folded in
here. One place to add a field, two consumers — the drift this module was
written to kill had simply reappeared one file over.

Limitation worth knowing: this matches on KEY NAME only. It cannot redact PII
embedded in a free-text ``event`` string or nested inside a dict value, so
"never put PII in a log message" remains the actual rule — this is the net, not
the policy.
"""

from __future__ import annotations

from collections.abc import MutableMapping
from typing import Any

__all__ = ["PII_FIELDS", "redact_pii_processor"]

PII_FIELDS: frozenset[str] = frozenset(
    {
        # --- identity ---
        # Matching is exact, so "email" alone covers neither the "user_email"
        # the auth routers use nor the "to_email" the Resend client passes.
        "email",
        "user_email",
        "to_email",
        # The bare recipient names. ``email_util.py:145`` binds a raw address as
        # ``to=`` (``log.info("email.sent", to=to, ...)``) — "to_email" above
        # does not cover it, because matching is exact. "recipient" is the same
        # field under the other name a mailer helper naturally reaches for.
        # Nothing in this repo logs either as a non-PII value, so the only cost
        # of covering them is that a future ops field called "to" would be
        # blanked — which is the trade this whole set is built on.
        "to",
        "recipient",
        "password",
        "phone",
        "full_name",
        "candidate_name",
        "candidate_email",
        # --- voice / interview transcript content (candidate speech) ---
        "transcript",
        "answer",
        "question",
        "text_content",
        "turn_text",
        # --- document PII (resume / JD free text) ---
        "resume_text",
        "resume_excerpt",
        "jd_text",
        "target_jd_text",
        # --- contact / geo ---
        "address",
        # A raw IP is personal data under DPDP. The salted hashes are
        # pseudonymous, not anonymous — they belong in the consent evidence
        # ledger, which is access-controlled, not in the log stream, which is
        # not. Neither name is used as a log kwarg anywhere in the repo, so
        # covering them costs no operational signal.
        "ip",
        "ip_hash",
        "user_agent_hash",
        # --- credentials and link material ---
        # A magic-link token in a log line is a live credential for anyone who
        # can read logs; the Caddy access-log fix (45ec2c2) covered the URL side.
        "token",
        "raw_token",
        "access_token",
        "refresh_token",
        "jwt",
        "csrf_token",
        "api_key",
        "secret",
        "client_secret",
        "code_verifier",
        # The two names this codebase actually binds password material to:
        # "plain" is the PLAINTEXT candidate password inside
        # LocalAuthProvider._verify_password, "password_hash" the stored
        # verifier. Generic "password" covers neither.
        "plain",
        "password_hash",
    }
)


def redact_pii_processor(
    logger: Any, method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: drop known PII keys before rendering.

    Install immediately before the renderer — anything added to the event dict
    after this runs is not covered.
    """
    for field in PII_FIELDS:
        event_dict.pop(field, None)
    return event_dict
