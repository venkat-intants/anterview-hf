"""Which roles are candidate roles — the single definition candidate-only flows use.

FAIL CLOSED, BY CONSTRUCTION
Google and Naipunyam sign-in are for candidates only, and onboarding's wizard is
a candidate screen. Each of them used to decide that by EXCLUDING a hard-coded
list of staff roles: ``('hr_manager', 'super_admin', 'platform_owner', 'admin')``.

A deny-list fails open. When PH4-A1 added ``interviewer`` — company staff who
can read named candidates, kits and private notes, and submit hiring evidence —
it was on none of those lists. Anyone holding a Google account with a verified
address matching an interviewer's could sign in with no password, and because
``/auth/refresh`` reloads roles from the database, within fifteen minutes that
candidate session carried ``interviewer``. Nobody made a mistake in the SSO
code; a new role simply was not on a list nobody knew to update.

So candidate-only flows ask the opposite question: does this account hold
ANYTHING that is not a candidate role? The next staff role to be added is then
refused by default, and has to be deliberately made a candidate role to get in.

This is deliberately NOT applied to allow-lists (agent tools, JD writers, the
AI scorecard viewer). An allow-list already fails closed for a new role, which
is the correct outcome there.
"""

from __future__ import annotations

from collections.abc import Iterable

#: Every role that may use a candidate-only flow. Anything else is staff.
CANDIDATE_ROLES: frozenset[str] = frozenset({"candidate", "guest_candidate"})


def holds_non_candidate_role(roles: Iterable[str]) -> bool:
    """True when any role is not a candidate role — i.e. the account is staff."""
    return bool(set(roles) - CANDIDATE_ROLES)


def is_candidate_only(roles: Iterable[str]) -> bool:
    """True when every role is a candidate role. An account with no roles yet
    (mid-provisioning) is treated as a candidate, which is what it will become."""
    return not holds_non_candidate_role(roles)
