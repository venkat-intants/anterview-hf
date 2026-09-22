"""PH4-D3 — code similarity via winnowing (the MOSS approach). PURE: stdlib +
Pygments only. Never executes candidate code — tokenising with a Pygments
lexer only inspects syntax, exactly like ``ast.parse``.

Renaming a variable, adding a comment, or reindenting must not change a
fingerprint, so every token stream is normalised first: identifiers become
``V``, string literals ``S``, number literals ``N``; comments and whitespace
are dropped; keywords, operators, punctuation and built-ins are kept as-is
(``print`` is not the same signal as an arbitrary user function).

Then 15-token k-grams are hashed (blake2b, 8 bytes — stable across processes,
unlike Python's salted ``hash()``) and winnowed with a window of 8: any run of
at least ``k + w - 1 = 22`` shared tokens is guaranteed to produce at least one
shared fingerprint (the winnowing guarantee — Schleimer/Wilkerson/Aiken 2003).

WHAT THIS CANNOT SEE: a semantic rewrite, translation into another language,
or many candidates independently using the same AI assistant — that last one
produces high similarity with NO collusion at all. This is why a similarity
signal is evidence for a human reviewer, never a finding on its own
(``app/code_evidence.py`` never writes a signal into anything that reaches a
hiring decision).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Name, Number, String
from pygments.token import Text as _TextToken
from pygments.util import ClassNotFound

# Our language slug (app/piston_client.py::SUPPORTED_LANGUAGES) -> the
# Pygments lexer alias to tokenise it with. Kept explicit, never guessed, so a
# renamed Pygments alias fails loudly here instead of silently mis-tokenising
# candidate code.
PYGMENTS_LANGUAGE_ALIASES: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "java": "java",
    "cpp": "cpp",
    "c": "c",
    "go": "go",
    "csharp": "csharp",
    "ruby": "ruby",
    "rust": "rust",
}

# Deliberately NOT bumped by the 2026-09-22 overflow fix — see _STORABLE.
ALGORITHM_VERSION = "sim-winnow-1.0"

K_GRAM = 15
WINDOW = 8
MIN_TOKENS = 50


class UnsupportedLanguageError(ValueError):
    """Raised when ``language`` is not one of ``PYGMENTS_LANGUAGE_ALIASES``."""


_LEXER_CACHE: dict[str, Any] = {}


def get_lexer(language: str) -> Any:
    """The Pygments lexer for our language slug. Cached — building one is not
    free and this is called once per submission analysed."""
    alias = PYGMENTS_LANGUAGE_ALIASES.get(language)
    if alias is None:
        raise UnsupportedLanguageError(language)
    lexer = _LEXER_CACHE.get(alias)
    if lexer is None:
        try:
            lexer = get_lexer_by_name(alias, stripnl=False, stripall=False)
        except ClassNotFound as exc:
            raise UnsupportedLanguageError(language) from exc
        _LEXER_CACHE[alias] = lexer
    return lexer


def _is_kept_name(ttype: Any) -> bool:
    """True for a ``Name.*`` token whose literal text is signal, not an
    arbitrary identifier — built-ins, exceptions and decorators. Plain
    identifiers (variables, functions the candidate named, classes) become
    the ``V`` placeholder instead."""
    return (
        ttype in Name.Builtin
        or ttype in Name.Builtin.Pseudo
        or ttype in Name.Exception
        or ttype in Name.Decorator
    )


def tokenize_with_lines(language: str, source: str) -> list[tuple[str, int]]:
    """``(normalised_token, source_line)`` pairs, comments/whitespace dropped.

    The line counter advances through EVERY token Pygments emits, including
    the ones we then skip — a comment or a run of blank lines still consumes
    real source lines, and skipping the counter along with the token would
    misattribute every line number after it.
    """
    lexer = get_lexer(language)
    line = 1
    out: list[tuple[str, int]] = []
    for ttype, value in lexer.get_tokens(source):
        start_line = line
        line += value.count("\n")
        if ttype in Comment:
            continue
        text = value.strip()
        if not text:
            continue
        if ttype in String:
            out.append(("S", start_line))
        elif ttype in Number:
            out.append(("N", start_line))
        elif _is_kept_name(ttype):
            out.append((text, start_line))
        elif ttype in Name:
            out.append(("V", start_line))
        elif ttype in _TextToken:
            # Non-whitespace Token.Text (rare — some lexers emit stray
            # punctuation this way). Kept literally, like an operator.
            out.append((text, start_line))
        else:
            out.append((text, start_line))
    return out


def normalised_tokens(language: str, source: str) -> list[str]:
    """The normalised token stream alone — what two submissions are compared
    on. Renaming every identifier in ``source`` produces the same list."""
    return [t for t, _ in tokenize_with_lines(language, source)]


def _hash_gram(tokens: tuple[str, ...]) -> int:
    """blake2b, not Python's ``hash()`` — stable across processes and PYTHONHASHSEED,
    which matters because analysis runs in a short-lived worker process
    (``app/code_sandbox.py``), never the request-serving one."""
    digest = hashlib.blake2b(
        "\x1f".join(tokens).encode("utf-8", errors="surrogatepass"), digest_size=8
    ).digest()
    return int.from_bytes(digest, "big")


# code_fingerprints.hashes is a SIGNED 64-bit array, and these hashes are
# unsigned 64-bit. Any selected hash at or above 2**63 made the whole INSERT
# fail — 58% of realistic submissions, measured on 2,000 generated Python
# sources on 2026-09-22 — so almost nothing was ever stored.
#
# Fixed at the point of RETURNING a selection, not in _hash_gram, and that
# placement is the point. Winnowing still orders and picks on the full 64-bit
# value, so it chooses exactly the positions it always did; only a chosen
# value at or above 2**63 loses its top bit. Every hash that could ever have
# been stored before was already below 2**63 and comes out UNCHANGED, so no
# fingerprint on record goes stale and ALGORITHM_VERSION stays put. That
# matters more than it looks: the sweep picks work by quality report, not by
# fingerprint, so a submission already reported would never be fingerprinted
# again — a version bump would have silently dropped every one of them out of
# similarity checks for good.
_STORABLE = (1 << 63) - 1


def fingerprints(
    tokens: list[str], *, k: int = K_GRAM, w: int = WINDOW
) -> tuple[list[int], list[int]]:
    """Winnowing over a normalised token stream.

    Returns ``(hashes, positions)`` — ``positions[i]`` is the index into
    ``tokens`` where the k-gram hashed as ``hashes[i]`` starts. Any run of at
    least ``k + w - 1`` shared tokens between two submissions is guaranteed to
    select at least one shared hash (the winnowing guarantee); ties within a
    window break to the RIGHTMOST occurrence, per Schleimer/Wilkerson/Aiken —
    the choice that gives the guarantee its bound.
    """
    if len(tokens) < k:
        return [], []
    grams: list[tuple[int, int]] = [
        (_hash_gram(tuple(tokens[i : i + k])), i) for i in range(len(tokens) - k + 1)
    ]
    window = min(w, len(grams))
    if window <= 0:
        return [], []
    selected: dict[int, int] = {}
    last_pos = -1
    for start in range(len(grams) - window + 1):
        chunk = grams[start : start + window]
        min_hash = min(h for h, _ in chunk)
        # Rightmost occurrence of the minimum in this window.
        pos = max(p for h, p in chunk if h == min_hash)
        if pos != last_pos:
            selected[pos] = min_hash
            last_pos = pos
    positions = sorted(selected)
    return [selected[p] & _STORABLE for p in positions], positions


@dataclass(frozen=True)
class Fingerprint:
    """One submission's fingerprint set, ready to store in ``code_fingerprints``
    and to compare via ``compare()``/``regions()``."""

    hashes: list[int]
    lines: list[int]
    token_count: int


def fingerprint_source(
    language: str, source: str, *, starter_code: str | None = None,
    k: int = K_GRAM, w: int = WINDOW,
) -> Fingerprint:
    """The full pipeline: tokenise, normalise, winnow, then subtract the
    question's own ``starter_code`` fingerprints — boilerplate every
    candidate starts from is not a similarity signal. Never executes
    ``source`` or ``starter_code``."""
    pairs = tokenize_with_lines(language, source)
    tokens = [t for t, _ in pairs]
    lines_by_pos = [ln for _, ln in pairs]
    hashes, positions = fingerprints(tokens, k=k, w=w)
    lines = [lines_by_pos[p] for p in positions]

    if starter_code and starter_code.strip():
        starter_tokens = normalised_tokens(language, starter_code)
        starter_hashes, _ = fingerprints(starter_tokens, k=k, w=w)
        exclude = set(starter_hashes)
        if exclude:
            kept = [(h, ln) for h, ln in zip(hashes, lines, strict=True) if h not in exclude]
            hashes = [h for h, _ in kept]
            lines = [ln for _, ln in kept]

    return Fingerprint(hashes=hashes, lines=lines, token_count=len(tokens))


@dataclass(frozen=True)
class Similarity:
    shared: int
    containment_a: float
    containment_b: float
    jaccard: float


def compare(hashes_a: list[int], hashes_b: list[int]) -> Similarity:
    """Containment from each side's own perspective, plus the symmetric
    Jaccard index. Pure set arithmetic over the (already-deduplicated-by-
    winnowing) hash lists."""
    set_a, set_b = set(hashes_a), set(hashes_b)
    shared = len(set_a & set_b)
    union = len(set_a | set_b)
    return Similarity(
        shared=shared,
        containment_a=(shared / len(set_a)) if set_a else 0.0,
        containment_b=(shared / len(set_b)) if set_b else 0.0,
        jaccard=(shared / union) if union else 0.0,
    )


def _merge_lines(lines: list[int], *, gap: int = 2) -> list[tuple[int, int]]:
    """Sorted line numbers -> contiguous ranges, allowing a small gap so one
    matched block interrupted by an unrelated line is still one region."""
    if not lines:
        return []
    ranges: list[list[int]] = [[lines[0], lines[0]]]
    for ln in lines[1:]:
        if ln - ranges[-1][1] <= gap:
            ranges[-1][1] = ln
        else:
            ranges.append([ln, ln])
    return [(lo, hi) for lo, hi in ranges]  # noqa: C416 -- converts list[int] pairs to tuples


def regions(
    a: Fingerprint, b: Fingerprint, *, max_regions: int = 20,
) -> list[dict[str, int]]:
    """Matched line ranges on each side, for the HR compare view.

    An APPROXIMATION: shared hashes are turned into line ranges independently
    on each side and paired positionally (both ascending), which is right
    when the two submissions keep the same statement order and only
    approximate when they do not — two submissions can share logic while
    reordering it, and this will under-report the pairing in that case
    without under-reporting THAT a match exists (``compare()`` already
    reports the raw counts).
    """
    shared_hashes = set(a.hashes) & set(b.hashes)
    if not shared_hashes:
        return []
    a_lines = sorted({ln for h, ln in zip(a.hashes, a.lines, strict=True) if h in shared_hashes})
    b_lines = sorted({ln for h, ln in zip(b.hashes, b.lines, strict=True) if h in shared_hashes})
    a_ranges = _merge_lines(a_lines)
    b_ranges = _merge_lines(b_lines)
    out = []
    for (al, ah), (bl, bh) in zip(a_ranges, b_ranges, strict=False):
        out.append({"low_start": al, "low_end": ah, "high_start": bl, "high_end": bh})
        if len(out) >= max_regions:
            break
    return out
