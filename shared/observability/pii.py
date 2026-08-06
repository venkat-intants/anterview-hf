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
        "email",
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
        "jd_text",
        "target_jd_text",
        # --- contact / geo ---
        "address",
        # --- credentials and link material ---
        # A magic-link token in a log line is a live credential for anyone who
        # can read logs; the Caddy access-log fix (45ec2c2) covered the URL side.
        "token",
        "raw_token",
        "access_token",
        "refresh_token",
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
