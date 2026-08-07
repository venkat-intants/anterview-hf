"""LIKE/ILIKE pattern escaping — one implementation, four call sites.

Lived in ``app/routers/hr_applicants.py`` until a second copy was nearly written
for the copilot tool layer (DG-4). It is small enough that a second copy would
have looked correct, and wrong in the same invisible way the first one was.
"""

from __future__ import annotations

# Escape character for LIKE patterns. Backslash is Postgres's default, but every
# call site passes ESCAPE '\' explicitly so the behaviour does not depend on
# standard_conforming_strings or on the SQLAlchemy dialect's defaults.
LIKE_ESCAPE = "\\"


def like_literal(value: str) -> str:
    """Escape LIKE wildcards so *value* matches itself and nothing else.

    Not an injection fix — every call site already binds the value as a
    parameter, so it can never break out of the string. The bug is subtler: `%`
    and `_` are wildcards inside a LIKE pattern, so a caller passing ``job=%``
    matched EVERY row and ``job=_`` matched any single character. The filter
    widened its own result set beyond what the caller asked to narrow to.

    Tenancy is unaffected either way (``company_id`` is a separate predicate), so
    this widens results within the caller's own company rather than across
    companies — a least-privilege and correctness fix, not a breach fix. That
    distinction matters most on the copilot path, where the "caller" choosing the
    pattern is a language model that a resume can talk to.

    The escape character must be escaped first, or escaping `%` would re-escape
    the backslash it just introduced.
    """
    return (
        value.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
        .replace("%", f"{LIKE_ESCAPE}%")
        .replace("_", f"{LIKE_ESCAPE}_")
    )
