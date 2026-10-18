r"""``_INVISIBLE_RE`` is spelled with ``\u`` escapes, and still matches the set.

The class strips characters that split a token so a tripwire stops matching it
while a human still reads the word — the homoglyph/zero-width trick. It used to
be written with the characters THEMSELVES, which worked and was unreviewable: a
row of invisible glyphs, meaningless in a diff, in a file that therefore carried
bidirectional control characters. Bandit flags that as B613/trojansource, and
the check is right in principle even where the use is benign — the whole class
of problem is source that does not read the way it runs.

Escaping is only safe if the set is unchanged, so the set is pinned here by
codepoint rather than by re-typing the pattern. Two kinds of assertion, because
either alone is weak: that every intended codepoint matches, and that the
neighbours just outside each range do NOT — a widened class would silently
start deleting ordinary text from a candidate's answer.
"""

from __future__ import annotations

import pytest

from app.bank_coding_generation import _INVISIBLE_RE, _scan_form

#: Exactly what the comment above the pattern claims: zero-width space,
#: joiners and directional marks; the embedding/override controls; word joiner
#: and the invisible operators; the BOM; and the soft hyphen.
INVISIBLE = [
    *range(0x200B, 0x2010),  # U+200B-U+200F
    *range(0x202A, 0x202F),  # U+202A-U+202E
    *range(0x2060, 0x2065),  # U+2060-U+2064
    0xFEFF,
    0x00AD,
]

#: One codepoint either side of every range, plus characters that look like
#: candidates for inclusion and deliberately are not.
VISIBLE_NEIGHBOURS = [
    0x200A,  # hair space — a real space, not invisible
    0x2010,  # hyphen
    0x2029,  # paragraph separator
    0x202F,  # narrow no-break space
    0x205F,  # medium mathematical space
    0x2065,  # unassigned
    0xFEFE,
    0xFF00,
    0x0020,  # plain space
    0x002D,  # plain hyphen-minus — the soft hyphen's visible cousin
    0x0041,  # 'A'
]


@pytest.mark.parametrize("cp", INVISIBLE)
def test_every_intended_codepoint_is_stripped(cp: int) -> None:
    assert _INVISIBLE_RE.fullmatch(chr(cp)), f"U+{cp:04X} is no longer matched"


@pytest.mark.parametrize("cp", VISIBLE_NEIGHBOURS)
def test_the_neighbours_are_left_alone(cp: int) -> None:
    """A widened class would quietly delete real characters from a candidate's
    submitted answer, which is the expensive direction to get wrong."""
    assert not _INVISIBLE_RE.fullmatch(chr(cp)), f"U+{cp:04X} is now being stripped"


def test_the_set_is_exactly_this_size() -> None:
    """Guards the two parametrised tests: both pass against a class that gained
    a codepoint nobody listed."""
    matched = [c for c in range(0x1, 0x11000) if _INVISIBLE_RE.fullmatch(chr(c))]
    assert len(matched) == len(INVISIBLE) == 17
    assert matched == sorted(INVISIBLE)


def test_the_source_carries_no_literal_invisibles() -> None:
    """The point of the rewrite. A file whose own bytes contain bidi controls
    cannot be reviewed honestly, whatever its intent."""
    import pathlib

    from app import bank_coding_generation

    src = pathlib.Path(bank_coding_generation.__file__).read_text(encoding="utf-8")
    offenders = sorted({f"U+{ord(c):04X}" for c in src if ord(c) in set(INVISIBLE)})
    assert not offenders, (
        f"literal invisible characters are back in the source: {offenders}. "
        r"Write them as \u escapes - see this module's docstring."
    )


def test_it_still_does_its_job() -> None:
    """End to end, on the trick it exists to defeat: a URL broken up by
    zero-width characters so a link tripwire misses it."""
    hidden = "htt​ps://ev‌il.exa‍mple.com"
    assert "https://evil.example.com" in _scan_form(hidden)
