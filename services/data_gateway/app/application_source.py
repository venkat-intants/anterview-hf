"""Where an application came from — PH3-B1.

One vocabulary, one normaliser. The value is written once when the enrolment is
created and never rewritten, so it survives every workflow transition without
anything having to carry it forward.

WHY A CLOSED VOCABULARY
The value arrives in a query parameter on a URL that anyone may construct, and
it ends up in an analytics ``GROUP BY`` (PH5-C1). An open string there gives the
open web a way to invent dimensions in a company's own reporting, and a funnel
chart with four thousand one-row channels in it is not a funnel chart. So the
channel is drawn from a fixed set; anything unrecognised becomes ``other`` and
the raw value is kept, sanitised, in ``source_detail``.

WHY UNRECOGNISED IS NOT REFUSED
A malformed tracking parameter is the recruiter's mistake, never the candidate's.
Refusing the application — or even refusing the *page* — would lose a real
applicant over a typo in a campaign URL. It is recorded as ``other`` and the
application proceeds.

WHY ``unknown`` AND ``direct`` ARE DIFFERENT THINGS
``unknown`` means nobody was tracking: every enrolment created before this story
existed, and anything HR imports without saying where it came from. ``direct``
means we *were* tracking and the person arrived on the apply link with no
campaign attached. Collapsing them would make the day this shipped look like a
sudden surge in direct applications. PH5-C1 requires missing source to read as
Unknown/Untracked rather than be silently discarded, which is why ``unknown`` is
a real value in the vocabulary and not a NULL.

PH6-I2 (job boards) will ingest applications from external boards. Those arrive
as ``job_board`` with the board named in ``source_detail`` — which is why the
alias table below already maps the common board names rather than leaving the
next author to invent a second vocabulary.
"""

from __future__ import annotations

import re

#: Every channel an application may be attributed to.
#:
#: Additions are cheap; renames are not — the value is stored on the enrolment
#: and read by analytics over historical cohorts, so a rename silently splits
#: one channel into two in every chart that spans the change.
SOURCES: frozenset[str] = frozenset({
    "unknown",        # never tracked: pre-PH3-B1 rows, and imports that say nothing
    "direct",         # arrived on the apply link with no campaign attached
    "careers_site",   # the company's own board
    "job_board",      # an external aggregator; which one goes in source_detail
    "referral",
    "social",
    "email_campaign",
    "agency",
    "campus",
    "qr_code",
    "internal",       # created by HR (bulk or single upload), not a candidate arrival
    "other",          # a src we did not recognise, kept rather than dropped
})

#: The value a row gets when nobody was tracking. Also the column default, so
#: every historical enrolment reads as this rather than as NULL.
UNTRACKED = "unknown"

#: What a public application with no ``?src=`` is attributed to.
DIRECT = "direct"

#: What HR-created applicants are attributed to.
INTERNAL = "internal"

#: Spellings a recruiter will plausibly put in a URL, mapped to the channel and
#: the detail they imply. Keeps "?src=linkedin" from becoming a channel of its
#: own while still recording which board it was.
_ALIASES: dict[str, tuple[str, str | None]] = {
    "careers": ("careers_site", None),
    "career": ("careers_site", None),
    "website": ("careers_site", None),
    "site": ("careers_site", None),
    "linkedin": ("job_board", "linkedin"),
    "naukri": ("job_board", "naukri"),
    "indeed": ("job_board", "indeed"),
    "shine": ("job_board", "shine"),
    "monster": ("job_board", "monster"),
    "glassdoor": ("job_board", "glassdoor"),
    "instahyre": ("job_board", "instahyre"),
    "foundit": ("job_board", "foundit"),
    "jobboard": ("job_board", None),
    "refer": ("referral", None),
    "employee_referral": ("referral", None),
    "twitter": ("social", "twitter"),
    "x": ("social", "twitter"),
    "facebook": ("social", "facebook"),
    "instagram": ("social", "instagram"),
    "whatsapp": ("social", "whatsapp"),
    "telegram": ("social", "telegram"),
    "youtube": ("social", "youtube"),
    "email": ("email_campaign", None),
    "newsletter": ("email_campaign", None),
    "campaign": ("email_campaign", None),
    "consultant": ("agency", None),
    "vendor": ("agency", None),
    "college": ("campus", None),
    "university": ("campus", None),
    "placement": ("campus", None),
    "qr": ("qr_code", None),
    "poster": ("qr_code", None),
}

#: What may survive into ``source_detail``. Deliberately narrow: the value comes
#: off a public URL and is rendered in an HR console and grouped on in SQL.
_DETAIL_OK = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: Bound on what we will even look at. A 4 KB query parameter is not a campaign
#: name, and normalising it would be work done on an attacker's behalf.
_MAX_RAW = 120


def _fold(raw: str) -> str:
    """Lower-case, trim, and treat -, spaces and dots as underscores."""
    return re.sub(r"[\s.\-]+", "_", raw.strip().lower()).strip("_")


def normalise_source(raw: str | None, *, default: str = DIRECT) -> tuple[str, str | None]:
    """Turn a ``?src=`` value into ``(source, source_detail)``.

    Never raises and never refuses: an unusable value yields ``(default, None)``
    and an unrecognised but well-formed one yields ``("other", <the value>)``,
    so a campaign nobody added to the vocabulary is still countable afterwards.

    ``default`` is what an absent value means, which differs by caller — a
    public application with no parameter is ``direct``, an HR import with none
    is ``internal``, and a backfilled historical row is ``unknown``.
    """
    if raw is None:
        return default, None
    if len(raw) > _MAX_RAW:
        # Too long to be a campaign name. Not recorded as `other` either: that
        # would let a caller fill the `other` bucket with noise.
        return default, None

    folded = _fold(raw)
    if not folded:
        return default, None

    if folded in SOURCES:
        # `unknown` is ours to assign, not the caller's to claim — a URL that
        # says "I am untracked" would otherwise hide a tracked arrival.
        return (default, None) if folded == UNTRACKED else (folded, None)

    if folded in _ALIASES:
        return _ALIASES[folded]

    return ("other", folded) if _DETAIL_OK.match(folded) else (default, None)


def normalise_detail(raw: str | None) -> str | None:
    """Sanitise a sub-channel supplied alongside an already-known source.

    Used by callers that set the channel themselves (PH6-I2's board connectors)
    and only need the detail cleaned.
    """
    if raw is None or len(raw) > _MAX_RAW:
        return None
    folded = _fold(raw)
    return folded if _DETAIL_OK.match(folded) else None
