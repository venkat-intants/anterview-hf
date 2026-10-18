"""AI-drafted CODING questions for a question bank — the trust boundary.

The generation itself is not here. It is the same generator the exam coding
round already uses (``feedback_billing`` ``/internal/generate-coding``, reached
through ``app.exam_ai_client.generate_coding_questions_remote``), so it follows
``LLM_PROVIDER`` and the role engine's allocator with no second prompt to keep
in step. This module is what stands between that model output and a library
that feeds real assessments: it re-validates every item, drops what cannot be
graded, and says why.

Three things it deliberately does NOT do:

* **It never executes anything.** A model wrote the reference solution and the
  expected outputs; neither has been run. Nothing in this service runs a
  reference solution at authoring time (the exam generator does not either), so
  every item carries ``reference_solution_verified = False`` and the
  ``reference-unverified`` tag. The tag is stored with the question, so it is
  still on the row a second reviewer reads before approving.
* **It never saves.** The output is a preview. ``POST .../questions/bulk``
  stores what HR keeps, as ``origin='ai_draft'`` drafts, and those go through
  the ordinary author-is-not-approver review.
* **It never invents starter code.** A bank (and exam) coding question has ONE
  ``starter_code`` string, and the candidate editor pre-fills it whichever
  language the candidate picks — so a Python stub on a question that also allows
  C++ would be wrong for every C++ candidate. Generated questions therefore
  carry ``starter_code = None``, exactly like the exam coding generator's.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr, ValidationError

from app.config import settings
from app.question_banks import UNVERIFIED_TAG as _UNVERIFIED_TAG
from app.question_banks import content_hash
from app.routers.hr_coding import CodingQuestionIn, TestCaseIn

#: The per-request ceiling. It is the cost bound: the generator budgets
#: ``4096 + 2048 * n`` output tokens, so five problems is at most 14,336.
MAX_GENERATED_QUESTIONS = 5
#: A statement shorter than this cannot specify a task, an input format and an
#: output format; it is a title, not a problem.
MIN_PROMPT_CHARS = 40
MAX_PROMPT_CHARS = 20_000
MAX_SOLUTION_CHARS = 40_000
MAX_CASE_CHARS = 20_000
#: One sample for the candidate's Run button and one hidden case to grade on.
MIN_TEST_CASES = 2
DEFAULT_TIME_LIMIT_MS = 5000
DEFAULT_POINTS = 100
#: Stored on the question, so the "nobody has run this" fact survives the
#: preview and is in front of the reviewer.
#: Defined once, in ``app.question_banks`` (the save and edit paths enforce it
#: there); re-exported so generation and tests can say ``gen.UNVERIFIED_TAG``.
UNVERIFIED_TAG = _UNVERIFIED_TAG

# A link in a statement shown to candidates, or in code somebody may later run,
# has no business coming out of a model. Applied to test data too: the samples
# are shown to candidates.
#
# Three shapes: a scheme (``http://``, ``hxxp``-style defanging), ``www.`` plus
# a name, and a BARE ``domain.tld/path``. The last needs a TLD from a short
# allowlist AND a path, so ``a.b``, ``obj.method/2``, ``v1.2.3``, ``file.txt``
# and ``os.path.join`` stay clean; TLDs that are also common attribute names
# (``.in``, ``.app``, ``.dev``, ``.co``, ``.me``, ``.to``, ``.sh``, ``.ai``) are
# deliberately left out of the bare form for the same reason.
_BARE_TLDS = "com|org|net|io|info|biz|xyz|edu|gov|ru|cn|tk|ly|top|site|online|club"
_URL_RE = re.compile(
    r"(?i)(?:\b(?:https?|ftp|wss?|file)://"
    r"|\bhxxps?\b"
    r"|\bwww\.[a-z0-9-]+\.[a-z]{2,}"
    rf"|(?<![\w.-])(?:[a-z0-9-]+\.)+(?:{_BARE_TLDS})/[^\s/]"
    r")"
)
#: Invisible characters that split a token so a pattern no longer matches it
#: while a person still reads it: zero-width space/joiners/marks and
#: directional marks (U+200B-U+200F), the embedding/override controls
#: (U+202A-U+202E), word joiner and invisible operators (U+2060-U+2064), BOM,
#: and the soft hyphen.
#: Written as \u escapes, NOT as the characters themselves. Spelled
#: literally this class is a row of invisible glyphs: unreadable in review,
#: meaningless in a diff, and it makes this a file carrying bidirectional
#: controls — which is the one thing bandit's B613/trojansource check exists
#: to flag, source that does not read the way it runs. The class is
#: unchanged; test_invisible_re_escapes.py pins the two forms equal.
_INVISIBLE_RE = re.compile(
    "[\u200b-\u200f\u202a-\u202e\u2060-\u2064\ufeff\u00ad]"
)


def _scan_form(text: str) -> str:
    """What the tripwires match against: NFKC-folded (full-width ``ｈｔｔｐ：／／``
    becomes ``http://``) with the invisible characters removed."""
    return _INVISIBLE_RE.sub("", unicodedata.normalize("NFKC", text))

# Shapes of real credentials. Not a secret scanner — a tripwire for a model that
# has echoed something it should never have seen.
_SECRET_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),
)


class _RawTestCase(BaseModel):
    """One test case exactly as the generator must send it. Strict: a number
    where the text should be is a malformed case, not something to coerce."""

    model_config = ConfigDict(extra="ignore")

    stdin: StrictStr = Field(default="", max_length=MAX_CASE_CHARS)
    expected_output: StrictStr = Field(max_length=MAX_CASE_CHARS)
    is_sample: StrictBool = False
    weight: int = Field(default=1, ge=1, le=100)


class GeneratedBankCodingQuestion(BaseModel):
    """One draft, shaped so it can be posted to ``questions/bulk`` unchanged.

    ``reference_solution_language``, ``reference_solution_verified`` and
    ``warnings`` are for the preview only; the bulk endpoint ignores them. What
    persists of "unverified" is the tag.
    """

    kind: Literal["coding"] = "coding"
    prompt: str
    points: int
    difficulty: str
    language: str
    allowed_languages: list[str]
    starter_code: None = None
    reference_solution: str | None
    reference_solution_language: str | None
    reference_solution_verified: Literal[False] = False
    test_cases: list[TestCaseIn]
    time_limit_ms: int
    tags: list[str]
    warnings: list[str]


class DroppedItem(BaseModel):
    """A generated item that was not usable. ``index`` is its 0-based position
    in what the generator returned."""

    index: int
    reason: str


class GenerationOutcome(BaseModel):
    questions: list[GeneratedBankCodingQuestion]
    dropped: list[DroppedItem]


def reference_language(allowed_languages: list[str]) -> str:
    """The language the generator is told to write the reference solution in —
    the same rule as ``feedback_billing.exam_generator``: Python when it is
    allowed (the most reviewable), otherwise the first allowed language."""
    return "python" if "python" in allowed_languages else allowed_languages[0]


def _unsafe_reason(text: str) -> str | None:
    if "\x00" in text:
        return "contains a NUL character"
    text = _scan_form(text)
    if "\x00" in text:
        return "contains a NUL character"
    if _URL_RE.search(text):
        return "contains a URL"
    if any(p.search(text) for p in _SECRET_RES):
        return "contains something that looks like a credential"
    return None


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _validate_one(
    raw: Any, *, difficulty: str, language: str, allowed_languages: list[str],
) -> GeneratedBankCodingQuestion | str:
    """One generated item -> a validated draft, or the sentence saying why not."""
    if not isinstance(raw, dict):
        return "not a question object"

    prompt_raw = raw.get("prompt")
    if not isinstance(prompt_raw, str):
        return "the problem statement is missing"
    prompt = prompt_raw.strip()
    if len(prompt) < MIN_PROMPT_CHARS:
        return f"the problem statement is shorter than {MIN_PROMPT_CHARS} characters"
    if len(prompt) > MAX_PROMPT_CHARS:
        # Refused, not truncated: a statement cut off mid-sentence still parses.
        return f"the problem statement is longer than {MAX_PROMPT_CHARS} characters"
    unsafe = _unsafe_reason(prompt)
    if unsafe:
        return f"the problem statement {unsafe}"

    cases_raw = raw.get("test_cases")
    if not isinstance(cases_raw, list):
        return "it has no test cases"

    warnings: list[str] = []
    cases: list[TestCaseIn] = []
    seen_cases: set[tuple[str, str]] = set()
    malformed = empty = duplicate = 0
    for item in cases_raw:
        try:
            tc = _RawTestCase.model_validate(item)
        except ValidationError:
            malformed += 1
            continue
        # A case with no expected output cannot grade anything: every program
        # that prints nothing would pass it.
        if not tc.expected_output.strip():
            empty += 1
            continue
        unsafe = _unsafe_reason(tc.stdin) or _unsafe_reason(tc.expected_output)
        if unsafe:
            return f"a test case {unsafe}"
        key = (tc.stdin, tc.expected_output)
        if key in seen_cases:
            duplicate += 1
            continue
        seen_cases.add(key)
        cases.append(TestCaseIn(
            stdin=tc.stdin, expected_output=tc.expected_output,
            is_sample=tc.is_sample, weight=tc.weight,
        ))
    if malformed:
        warnings.append(f"{_plural(malformed, 'malformed test case')} removed")
    if empty:
        warnings.append(f"{_plural(empty, 'test case')} with no expected output removed")
    if duplicate:
        warnings.append(f"{_plural(duplicate, 'duplicate test case')} removed")
    if len(cases) > settings.code_max_test_cases:
        over = len(cases) - settings.code_max_test_cases
        cases = cases[: settings.code_max_test_cases]
        warnings.append(
            f"{_plural(over, 'test case')} beyond the limit of "
            f"{settings.code_max_test_cases} removed"
        )

    if len(cases) < MIN_TEST_CASES:
        return f"it has fewer than {MIN_TEST_CASES} usable test cases"
    if not any(tc.is_sample for tc in cases):
        return "it has no sample test case to show the candidate"
    if all(tc.is_sample for tc in cases):
        return "it has no hidden test case, so the visible samples would be the whole grade"

    reference: str | None = None
    reference_raw = raw.get("reference_solution")
    if isinstance(reference_raw, str) and reference_raw.strip():
        reference = reference_raw.strip()
        if len(reference) > MAX_SOLUTION_CHARS:
            reference = None
            warnings.append("the reference solution was too long and was removed")
        else:
            unsafe = _unsafe_reason(reference)
            if unsafe:
                return f"the reference solution {unsafe}"
    elif reference_raw is not None and not isinstance(reference_raw, str):
        warnings.append("the reference solution was not text and was removed")
    if reference is None:
        warnings.append("no reference solution - write one before sending this for review")

    # Last, the SAME model the exam and bank authoring routes validate with, so
    # nothing reaches the preview that `questions/bulk` would then refuse.
    try:
        checked = CodingQuestionIn(
            prompt=prompt, allowed_languages=allowed_languages, starter_code=None,
            reference_solution=reference, test_cases=cases,
            time_limit_ms=DEFAULT_TIME_LIMIT_MS, points=DEFAULT_POINTS,
        )
    except ValidationError as exc:
        errs = exc.errors()
        return str(errs[0].get("msg", "invalid question")) if errs else "invalid question"

    return GeneratedBankCodingQuestion(
        prompt=checked.prompt, points=checked.points, difficulty=difficulty, language=language,
        allowed_languages=checked.allowed_languages,
        reference_solution=checked.reference_solution,
        reference_solution_language=(
            reference_language(checked.allowed_languages) if checked.reference_solution else None
        ),
        test_cases=checked.test_cases, time_limit_ms=checked.time_limit_ms,
        # Every draft, with or without a reference solution: the expected
        # outputs are the model's too, and nothing was executed either way.
        tags=[UNVERIFIED_TAG],
        warnings=warnings,
    )


def validate_generated(
    raw_items: list[Any], *, requested: int, difficulty: str, language: str,
    allowed_languages: list[str], existing_hashes: set[str],
) -> GenerationOutcome:
    """Turn the generator's raw list into drafts plus the reasons for the rest.

    ``existing_hashes`` is the bank's ``content_hash`` set: a problem the bank
    already holds (at any status, retired included) is dropped here rather than
    offered again. The set is updated as items are accepted, so the same problem
    twice in one response is kept once.

    Never raises and never calls anything: pure over its inputs.
    """
    seen = set(existing_hashes)
    questions: list[GeneratedBankCodingQuestion] = []
    dropped: list[DroppedItem] = []
    for index, raw in enumerate(raw_items):
        if len(questions) >= requested:
            dropped.append(DroppedItem(index=index, reason="more than was asked for"))
            continue
        result = _validate_one(
            raw, difficulty=difficulty, language=language, allowed_languages=allowed_languages,
        )
        if isinstance(result, str):
            dropped.append(DroppedItem(index=index, reason=result))
            continue
        digest = content_hash(
            kind="coding", prompt=result.prompt,
            test_cases=[tc.model_dump() for tc in result.test_cases],
        )
        if digest in existing_hashes:
            dropped.append(DroppedItem(index=index, reason="an identical question is already in this bank"))
            continue
        if digest in seen:
            dropped.append(DroppedItem(index=index, reason="a duplicate of another question in this batch"))
            continue
        seen.add(digest)
        questions.append(result)
    return GenerationOutcome(questions=questions, dropped=dropped)


def summarise_dropped(dropped: list[DroppedItem]) -> str:
    """``"2 dropped: it has no hidden test case (2)"`` — for the one error line
    the UI shows when nothing at all was usable."""
    if not dropped:
        return ""
    counts: dict[str, int] = {}
    for d in dropped:
        counts[d.reason] = counts.get(d.reason, 0) + 1
    parts = "; ".join(f"{reason} ({n})" for reason, n in counts.items())
    return f"{len(dropped)} dropped: {parts}"
