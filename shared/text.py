"""Text normalisation shared by every service that writes a ``text`` column.

Why this module exists
----------------------
Round 13 of PH3-B4b. Postgres cannot store two things in a ``text`` or ``jsonb``
value, and both of them reach the database from user-supplied bytes:

* **U+0000.** No ``text`` column accepts a NUL. The cast to ``jsonb`` refuses it
  too, with "\\\\u0000 cannot be converted to text".
* **Lone surrogates, U+D800 to U+DFFF.** These encode happily in JSON and fail
  at the UTF-8 boundary instead — the same defect one layer down.

Why it is a privacy control and not a tidiness one
--------------------------------------------------
``POST /apply/{requisition_id}`` and ``POST /apply/draft/submit`` are ANONYMOUS,
and must answer identically whether the submitted address has a live
application, is in a rejection cooldown, was rejected and the window elapsed,
was rejected with an override, or has never applied here
(``docs/ACCEPTED-RISKS.md`` AR-10). The ``applicants`` INSERT runs only on the
branch that CREATES an applicant — so a value that parses but fails at the
database turned "this address has never applied here" into a 503 while the
other four states answered 201. One anonymous request, no timing, no
concurrency, byte-identical bodies within each group.

Round 12 closed that for typed answers and for ``full_name``. Round 13 found it
still open in five sibling form fields and, worse, through the CV PARSER — a
PDF whose content stream carries an octal escape is pure ASCII to look at, and
pypdf returns the NUL verbatim. Both reviewers reached the same conclusion
about the shape of the fix: stop guarding fields one at a time and normalise at
the producers, so every present and future consumer inherits it.

STRIP, DO NOT REFUSE — here. This module is for text a MACHINE produced: the
PDF parser's output, and the defensive pass inside ``_clean``. Nobody typed it,
so there is nothing to retype, and refusing an application because pypdf read a
NUL out of the candidate's own CV would turn a storage limitation into a
rejection — and on HR's bulk upload, into a denial of service. A value a person
typed is refused instead, at the request boundary, where they can fix it; see
``public_apply._unstorable``.

Deliberately dependency-free: ``shared/`` is COPY'd into all four service
images, so this must not import anything a service might not have.
"""

from __future__ import annotations

__all__ = ["strip_unstorable"]

# Spelled as code points rather than escapes on purpose. An escape written into
# a module's source builds the character itself, and a lone surrogate then makes
# the module unencodable — which is how the first attempt at this fix broke its
# own import.
_NUL = 0x0000
_SURROGATE_FIRST = 0xD800
_SURROGATE_LAST = 0xDFFF


def strip_unstorable(value: str) -> str:
    """Return *value* without the code points Postgres cannot hold.

    Returns the input unchanged when there is nothing to remove, which is the
    overwhelmingly common case — a scan of the string with no allocation beyond
    the generator when it is clean.
    """
    if not any(
        ord(ch) == _NUL or _SURROGATE_FIRST <= ord(ch) <= _SURROGATE_LAST
        for ch in value
    ):
        return value
    return "".join(
        ch
        for ch in value
        if ord(ch) != _NUL
        and not (_SURROGATE_FIRST <= ord(ch) <= _SURROGATE_LAST)
    )
