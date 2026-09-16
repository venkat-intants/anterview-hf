"""What a CV says about its owner, read without a model — PH3-B5 / PH3-B5b.

WHY NOT THE ATS SCORER
The scorer is a language-model call. ``public_apply``'s own module docstring
says why nothing in that request path may wait on one: "a candidate pressing
Submit should not wait on a language model, and an applicant lost because the
model was down would be the worst possible failure for this particular
endpoint." A confirmation screen shown DURING the application is the same path,
and the candidate is going to correct whatever we show them anyway — so the bar
is "good enough to be worth correcting", not "good enough to trust", and a
deterministic read clears it at zero cost and zero latency.

The scorer still runs afterwards, in the reconciler, for ATS scoring. This does
not replace it and does not compete with it: this is what the candidate is asked
to confirm, and the candidate's answer wins over both.

WHAT IT COSTS
Every pattern is bounded and the input is truncated to ``_MAX_SCAN_CHARS``, so
the work is linear and capped regardless of what is uploaded. The caller runs it
off the event loop (``asyncio.to_thread``) as well — belt and braces, because
this is reachable by an anonymous request and the alternative is one upload
stalling every other request on the worker.

WHAT IT WILL NOT DO
Guess. Every extractor here returns None rather than a low-confidence value,
because a wrong pre-filled field is worse than an empty one: a person skimming a
confirmation screen corrects what is obviously wrong and accepts what looks
plausible. An invented employer that looks plausible is how bad data gets
confirmed by a real human and becomes authoritative.
"""

from __future__ import annotations

import re
from typing import Any

# A pragmatic address pattern. Not RFC 5322 — that matches things no CV
# contains and is famously unreadable. Anything this finds is validated by
# pydantic before it is used.
#
# Bounded repetition, not open-ended. The `X+@Y+\.Z` shape backtracks linearly
# per start offset, so searching it over a whole CV is O(n^2): measured at 0.72s
# on 32 KB of 'a', 1.6s for the profile patterns, and a CV can be hundreds of KB.
# Since this runs on the request path for an anonymous upload, that was a denial
# of service. Explicit upper bounds make the failure path finite.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]{1,64}@[A-Za-z0-9\-]{1,63}(?:\.[A-Za-z0-9\-]{1,63}){1,4}")

# Indian mobile numbers with or without +91, and generic 10-15 digit runs with
# common separators. Deliberately anchored on a word boundary so it does not
# pick up the middle of a long identifier.
_PHONE = re.compile(
    r"(?<![\d])(?:\+?91[\s\-]?)?(?:\d[\s\-]?){9,14}\d(?![\d])"
)

_LINKEDIN = re.compile(
    r"(?:https?://)?(?:[\w\-]{1,63}\.){0,3}linkedin\.com/in/[\w\-%]{1,100}/?", re.I
)
_GITHUB = re.compile(r"(?:https?://)?(?:www\.)?github\.com/[\w\-.]{1,100}/?", re.I)

# Lines that are obviously not a person's name, however early they appear.
_NOT_A_NAME = re.compile(
    r"(curriculum\s+vitae|r[ée]sum[ée]|profile|summary|objective|contact|"
    r"phone|email|address|www\.|@|\d{4})",
    re.I,
)

#: How much of the document to look at for the header fields. A name and a
#: phone number are at the top; scanning further finds a referee's.
_HEADER_CHARS = 1200

#: The hard ceiling on how much text is examined AT ALL.
#:
#: Every pattern here is now bounded, but bounded is not free — and this runs on
#: an anonymous upload. A 5 MB PDF (the accepted ceiling) can yield megabytes of
#: text, and there is no answer worth finding in the last megabyte of a CV: the
#: email, the phone number and the profile links are in the first page. So the
#: work is capped rather than merely made cheaper.
_MAX_SCAN_CHARS = 20_000


def _first_email(text: str) -> str | None:
    match = _EMAIL.search(text)
    return match.group(0).strip().lower()[:320] if match else None


def _first_phone(text: str) -> str | None:
    for candidate in _PHONE.finditer(text[:_HEADER_CHARS]):
        digits = re.sub(r"\D", "", candidate.group(0))
        # 10 digits is an Indian mobile; 12 is one with the country code. Reject
        # anything outside a plausible range — a long run of digits in a CV is
        # far more often a date range or an employee id than a phone number.
        if 10 <= len(digits) <= 13:
            return candidate.group(0).strip()[:40]
    return None


def _name(text: str) -> str | None:
    """The first line that looks like a person's name.

    Deliberately conservative: two to four capitalised words, no digits, no
    punctuation beyond a dot or apostrophe, and not one of the header words a
    CV template puts at the top. Anything else returns None and the candidate
    types their own name, which they were going to be asked to check regardless.
    """
    for raw in text[:_HEADER_CHARS].splitlines():
        line = " ".join(raw.split())
        if not (4 <= len(line) <= 60):
            continue
        if _NOT_A_NAME.search(line):
            continue
        words = line.split()
        if not (2 <= len(words) <= 4):
            continue
        if not all(re.fullmatch(r"[A-Za-z][A-Za-z.'\-]*", w) for w in words):
            continue
        # ALL CAPS is common in CV headers and is still a name.
        if not all(w[0].isupper() for w in words):
            continue
        return line[:200]
    return None


def _profile(text: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.search(text)
    if not match:
        return None
    url = match.group(0).rstrip("/")
    return (url if url.startswith("http") else f"https://{url}")[:500]


def extract_contact_details(resume_text: str | None) -> dict[str, Any]:
    """Everything a confirmation screen can honestly pre-fill.

    Only keys whose value was actually found are present. An absent key renders
    as an empty editable field, which PH3-B5 Task 4 requires: a candidate must
    not be blocked because an optional field could not be extracted.
    """
    if not resume_text:
        return {}
    # Truncate FIRST. Everything below is linear in this length, and the caller
    # hands us whatever pypdf extracted from a file a stranger uploaded.
    text = resume_text[:_MAX_SCAN_CHARS].replace("\r\n", "\n")
    found: dict[str, Any] = {
        "full_name": _name(text),
        "email": _first_email(text),
        "phone": _first_phone(text),
        "linkedin_url": _profile(text, _LINKEDIN),
        "github_url": _profile(text, _GITHUB),
    }
    return {k: v for k, v in found.items() if v}
