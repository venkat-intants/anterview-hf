"""Regression suite for ``shared.text``.

Two bugs are pinned here, both of them mistakes the apply-door work made and a
reviewer caught:

* the NUL / lone-surrogate classes being the ONLY two Postgres refuses, which
  was measured against a real database through asyncpg across twelve candidate
  classes (U+0001, U+001F, U+007F, U+0085, U+200B, U+FFFE, U+FFFF, U+FDD0,
  U+10FFFF, stacked combining marks and the two that fail) — so this module
  must not grow a third class on a hunch;
* valid surrogate PAIRS surviving, which the first version destroyed.
"""

from __future__ import annotations

import pytest

from shared.text import strip_unstorable

NUL = chr(0)
HIGH = chr(0xD83D)
LOW = chr(0xDE00)
ASTRAL = chr(0x1F600)  # what HIGH + LOW encodes


def test_clean_text_is_returned_unchanged() -> None:
    """The common case must not allocate or alter anything."""
    for value in ("Priya Sharma", "", "Zo\u00eb O'Neill-M\u00fcller",
                  "\u092a\u094d\u0930\u093f\u092f\u093e", "tab\tnewline\n"):
        assert strip_unstorable(value) == value


def test_a_valid_surrogate_pair_survives_as_its_character() -> None:
    """THE BUG THE FIRST VERSION HAD, and it was silent.

    pypdf does not return an astral-plane character as one code point: a CMap
    mapping to U+1F600 comes back as the two surrogates U+D83D and U+DE00. A
    naive "drop every code point in D800-DFFF" removed both, so every emoji,
    mathematical alphanumeric and historic-script character in a candidate's
    CV vanished from the text the scorer reads, with nobody told. Indian BMP
    scripts were unaffected, which is exactly why it could have sat there.
    """
    assert strip_unstorable("hi " + HIGH + LOW + " there") == "hi " + ASTRAL + " there"
    assert strip_unstorable(ASTRAL) == ASTRAL
    assert strip_unstorable("a" + NUL + HIGH + LOW + "b") == "a" + ASTRAL + "b"


@pytest.mark.parametrize(
    ("label", "value", "expected"),
    [
        ("NUL", "a" + NUL + "b", "ab"),
        ("lone high surrogate", "a" + HIGH + "b", "ab"),
        ("lone low surrogate", "a" + LOW + "b", "ab"),
        ("pair then lone high", HIGH + LOW + HIGH, ASTRAL),
        ("lone low then pair", LOW + HIGH + LOW, ASTRAL),
        ("only a NUL", NUL, ""),
    ],
)
def test_what_postgres_refuses_is_removed(label: str, value: str, expected: str) -> None:
    assert strip_unstorable(value) == expected, label


@pytest.mark.parametrize(
    "value",
    [
        "a" + NUL + "b",
        "a" + HIGH + "b",
        "hi " + HIGH + LOW + " there",
        NUL + HIGH + LOW + NUL,
    ],
)
def test_the_result_always_encodes_to_utf8(value: str) -> None:
    """A lone surrogate is a UTF-8 encode error, which is how it reaches the
    database as one. Whatever comes out of here must be sendable."""
    strip_unstorable(value).encode("utf-8")


def test_nothing_in_the_keep_set_is_touched() -> None:
    """Classes a reviewer MEASURED as storable, so they must survive.

    Listed explicitly because the temptation when hunting a database error is
    to widen the strip, and every one of these stores fine in `text` and
    `jsonb`. Widening this function is how a candidate's CV quietly loses
    content.
    """
    for cp in (0x0001, 0x001F, 0x007F, 0x0085, 0x200B, 0xFFFE, 0xFFFF, 0xFDD0,
               0x10FFFF):
        ch = chr(cp)
        assert strip_unstorable("a" + ch + "b") == "a" + ch + "b", f"U+{cp:04X}"
