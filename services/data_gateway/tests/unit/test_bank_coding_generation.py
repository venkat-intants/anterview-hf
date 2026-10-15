"""AI-drafted CODING questions for a question bank.

What this feature must not get wrong, in the order it would hurt:

  1. Model output reaching a bank UNCHECKED. A coding question with no hidden
     test case can be passed by hard-coding the samples; one with an empty
     expected output is passed by a program that prints nothing. Both look
     like perfectly good questions in a preview.
  2. A reference solution presented as CORRECT. Nobody ran it. Every draft has
     to say so, in the data as well as on the screen.
  3. Generation that SAVES, or that approves. It is a preview; the drafts that
     HR keeps go through the same author-is-not-approver review as anything
     typed by hand.
  4. A second company's bank, or an unbounded bill.

No LLM, no database and no Redis are used anywhere in this file: the generator
client is replaced with an ``AsyncMock``, exactly where the router imports it.
"""

from __future__ import annotations

import ast
import inspect
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import bank_coding_generation as gen
from app import question_banks as svc
from app.config import settings
from app.dependencies import get_hr_company
from app.exam_ai_client import GENERATION_UNAVAILABLE, ExamGenerationError
from app.fake_ai import fake_coding_questions
from app.routers import question_banks as router

STATEMENT = (
    "Read two integers a and b from one line of standard input, separated by a "
    "space, and print their sum on one line. Constraints: -10^9 <= a, b <= 10^9."
)


def _case(stdin: str, out: str, *, sample: bool = False, weight: int = 1) -> dict[str, Any]:
    return {"stdin": stdin, "expected_output": out, "is_sample": sample, "weight": weight}


def _item(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "prompt": STATEMENT,
        "reference_solution": "a, b = map(int, input().split())\nprint(a + b)\n",
        "test_cases": [
            _case("2 3\n", "5\n", sample=True),
            _case("-4 10\n", "6\n"),
            _case("1000000000 1000000000\n", "2000000000\n", weight=2),
        ],
    }
    base.update(over)
    return base


def _validate(items: list[Any], *, requested: int = 5, existing: set[str] | None = None,
              languages: list[str] | None = None) -> gen.GenerationOutcome:
    return gen.validate_generated(
        items, requested=requested, difficulty="medium", language="en",
        allowed_languages=languages or ["python", "cpp"], existing_hashes=existing or set(),
    )


# ===========================================================================
# 1. A good item
# ===========================================================================
def test_a_well_formed_item_is_accepted_as_is() -> None:
    out = _validate([_item()])
    assert out.dropped == []
    (q,) = out.questions
    assert q.kind == "coding"
    assert q.prompt == STATEMENT
    assert q.allowed_languages == ["python", "cpp"]
    assert q.difficulty == "medium"
    assert q.language == "en"
    assert [tc.is_sample for tc in q.test_cases] == [True, False, False]
    assert q.test_cases[2].weight == 2
    assert q.time_limit_ms == gen.DEFAULT_TIME_LIMIT_MS
    assert q.points == gen.DEFAULT_POINTS
    assert q.warnings == []


def test_every_accepted_item_is_marked_unverified_in_the_data_it_will_be_saved_with() -> None:
    (q,) = _validate([_item()]).questions
    # Nobody ran this solution. The flag is for the preview...
    assert q.reference_solution_verified is False
    # ...and the tag is what is still on the row when a reviewer opens it.
    assert gen.UNVERIFIED_TAG in q.tags
    assert q.reference_solution_language == "python"


def test_the_reference_language_follows_the_generators_own_rule() -> None:
    assert gen.reference_language(["cpp", "python"]) == "python"
    assert gen.reference_language(["cpp", "java"]) == "cpp"
    (q,) = _validate([_item()], languages=["java", "go"]).questions
    assert q.reference_solution_language == "java"


def test_no_starter_code_is_ever_invented() -> None:
    """One ``starter_code`` string is pre-filled whichever language the
    candidate picks, so a single-language stub would be wrong for the rest."""
    (q,) = _validate([_item(starter_code="def solve():\n    pass\n")]).questions
    assert q.starter_code is None


def test_a_preview_item_is_accepted_verbatim_by_the_bulk_save_model() -> None:
    """The contract with the frontend: post it back unchanged."""
    (q,) = _validate([_item()]).questions
    body = router.BulkQuestionsIn(questions=[q.model_dump()])
    kwargs = router._question_kwargs(body.questions[0])
    assert kwargs["kind"] == "coding"
    assert kwargs["tags"] == [gen.UNVERIFIED_TAG]
    assert kwargs["starter_code"] is None
    assert len(kwargs["test_cases"]) == 3
    # Nothing in what is saved can carry a status or an origin: the service
    # decides both.
    assert "status" not in kwargs
    assert "origin" not in kwargs


# ===========================================================================
# 2. Each malformed case is refused, with a reason a person can read
# ===========================================================================
@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ("just a string", "not a question object"),
        (_item(prompt=None), "statement is missing"),
        (_item(prompt=12345), "statement is missing"),
        (_item(prompt="Add two numbers."), "shorter than"),
        (_item(prompt="x" * (gen.MAX_PROMPT_CHARS + 1)), "longer than"),
        (_item(prompt=STATEMENT + " See https://example.com/spec for details."), "contains a URL"),
        (_item(prompt=STATEMENT + " Docs at www.example.com."), "contains a URL"),
        (_item(prompt=STATEMENT + " key AKIAABCDEFGHIJKLMNOP"), "credential"),
        (_item(prompt=STATEMENT + "\x00"), "NUL"),
        (_item(test_cases=None), "no test cases"),
        (_item(test_cases="2 3 -> 5"), "no test cases"),
        (_item(test_cases=[_case("1 1\n", "2\n", sample=True)]), "fewer than 2"),
        (_item(test_cases=[_case("1 1\n", "2\n"), _case("2 2\n", "4\n")]), "no sample"),
        (
            _item(test_cases=[_case("1 1\n", "2\n", sample=True), _case("2 2\n", "4\n", sample=True)]),
            "no hidden",
        ),
        (
            _item(test_cases=[_case("http://evil.test/x\n", "1\n", sample=True), _case("2\n", "4\n")]),
            "test case contains a URL",
        ),
        (
            _item(reference_solution="import urllib.request\nurllib.request.urlopen('https://x.test')"),
            "reference solution contains a URL",
        ),
        (
            _item(reference_solution="KEY = '-----BEGIN RSA PRIVATE KEY-----'"),
            "reference solution contains something that looks like a credential",
        ),
    ],
)
def test_a_malformed_item_is_dropped_with_its_reason(raw: Any, fragment: str) -> None:
    out = _validate([raw])
    assert out.questions == []
    (dropped,) = out.dropped
    assert dropped.index == 0
    assert fragment in dropped.reason


def test_a_case_with_no_expected_output_never_survives() -> None:
    """Every program that prints nothing would pass it."""
    out = _validate([_item(test_cases=[
        _case("2 3\n", "5\n", sample=True), _case("0 0\n", "   \n"), _case("1 1\n", "2\n"),
    ])])
    (q,) = out.questions
    assert all(tc.expected_output.strip() for tc in q.test_cases)
    assert len(q.test_cases) == 2
    assert any("no expected output" in w for w in q.warnings)


def test_if_the_only_hidden_case_had_no_expected_output_the_question_is_dropped() -> None:
    out = _validate([_item(test_cases=[_case("2 3\n", "5\n", sample=True), _case("0 0\n", "")])])
    assert out.questions == []
    assert "fewer than 2" in out.dropped[0].reason


def test_malformed_and_duplicate_cases_are_removed_and_reported() -> None:
    out = _validate([_item(test_cases=[
        _case("2 3\n", "5\n", sample=True),
        _case("2 3\n", "5\n", sample=True),          # duplicate
        {"stdin": 7, "expected_output": "7\n"},      # stdin is not text
        {"stdin": "1\n"},                            # no expected_output at all
        {"stdin": "1\n", "expected_output": "1\n", "is_sample": "yes"},  # not a bool
        {"stdin": "1\n", "expected_output": "1\n", "weight": 0},         # out of range
        "nonsense",
        _case("-1 1\n", "0\n"),
    ])])
    (q,) = out.questions
    assert [(tc.stdin, tc.is_sample) for tc in q.test_cases] == [("2 3\n", True), ("-1 1\n", False)]
    assert "5 malformed test cases removed" in q.warnings
    assert "1 duplicate test case removed" in q.warnings


def test_test_cases_are_capped_at_the_exam_limit() -> None:
    cap = settings.code_max_test_cases
    cases = [_case("0 0\n", "0\n", sample=True)] + [
        _case(f"{i} 1\n", f"{i + 1}\n") for i in range(cap + 4)
    ]
    (q,) = _validate([_item(test_cases=cases)]).questions
    assert len(q.test_cases) == cap
    assert any("beyond the limit" in w for w in q.warnings)


def test_an_oversized_case_is_removed_not_truncated() -> None:
    big = "9" * (gen.MAX_CASE_CHARS + 1)
    (q,) = _validate([_item(test_cases=[
        _case("2 3\n", "5\n", sample=True), _case(big, "1\n"), _case("1 1\n", "2\n"),
    ])]).questions
    assert len(q.test_cases) == 2
    assert "1 malformed test case removed" in q.warnings


@pytest.mark.parametrize("reference", [None, "", "   ", 42])
def test_a_question_without_a_reference_solution_is_kept_but_says_so(reference: Any) -> None:
    (q,) = _validate([_item(reference_solution=reference)]).questions
    assert q.reference_solution is None
    assert q.reference_solution_language is None
    # Still unverified: the expected OUTPUTS are the model's too and nothing was
    # run, solution or not (review 2026-10: the tag was solution-conditional).
    assert q.tags == [gen.UNVERIFIED_TAG]
    assert any("no reference solution" in w for w in q.warnings)


def test_an_oversized_reference_solution_is_removed() -> None:
    (q,) = _validate([_item(reference_solution="x = 1\n" * 10_000)]).questions
    assert q.reference_solution is None
    assert any("too long" in w for w in q.warnings)


def test_surplus_items_are_not_returned() -> None:
    items = [_item(prompt=f"{STATEMENT} Variant {i}.") for i in range(4)]
    out = _validate(items, requested=2)
    assert len(out.questions) == 2
    assert [d.reason for d in out.dropped] == ["more than was asked for"] * 2


def test_a_bad_item_does_not_cost_the_good_ones_after_it() -> None:
    out = _validate(
        [_item(prompt="too short"), _item(), _item(prompt=f"{STATEMENT} Again.", test_cases=[])],
        requested=3,
    )
    assert len(out.questions) == 1
    assert [d.index for d in out.dropped] == [0, 2]


def test_a_question_already_in_the_bank_is_not_offered_again() -> None:
    (q,) = _validate([_item()]).questions
    digest = svc.content_hash(
        kind="coding", prompt=q.prompt, test_cases=[tc.model_dump() for tc in q.test_cases]
    )
    out = _validate([_item()], existing={digest})
    assert out.questions == []
    assert "already in this bank" in out.dropped[0].reason


def test_the_same_question_twice_in_one_response_is_kept_once() -> None:
    out = _validate([_item(), _item(prompt=STATEMENT.upper())])
    assert len(out.questions) == 1
    assert "duplicate of another question" in out.dropped[0].reason


def test_validation_never_raises_on_garbage() -> None:
    garbage: list[Any] = [None, 3, [], {}, {"prompt": {}}, {"prompt": STATEMENT, "test_cases": [None]}]
    out = _validate(garbage, requested=5)
    assert out.questions == []
    assert len(out.dropped) == len(garbage)


def test_the_fake_ai_generator_passes_validation() -> None:
    """AI_FAKE_MODE drives the browser suite; if it stops validating, the
    panel's e2e path silently returns a 502."""
    raw = fake_coding_questions(topic="arrays", num_questions=3, allowed_languages=["python"])
    out = _validate(raw, requested=3, languages=["python"])
    assert len(out.questions) == 3
    assert out.dropped == []


def test_summarise_dropped_groups_by_reason() -> None:
    dropped = [gen.DroppedItem(index=i, reason=r) for i, r in enumerate(["a", "b", "a"])]
    assert gen.summarise_dropped(dropped) == "3 dropped: a (2); b (1)"
    assert gen.summarise_dropped([]) == ""


# ===========================================================================
# 3. The request: bounds, and no way to name a company
# ===========================================================================
def _body(**over: Any) -> router.BankGenerateCodingIn:
    fields: dict[str, Any] = {"topic": "two pointers", "allowed_languages": ["python"]}
    fields.update(over)
    return router.BankGenerateCodingIn(**fields)


def test_defaults_match_the_mcq_generators_where_they_share_a_field() -> None:
    body = _body()
    mcq = router.BankGenerateIn(topic="two pointers")
    assert (body.difficulty, body.language, body.job_title, body.experience_level) == (
        mcq.difficulty, mcq.language, mcq.job_title, mcq.experience_level,
    )
    assert body.num_questions == 2


@pytest.mark.parametrize("count", [0, -1, gen.MAX_GENERATED_QUESTIONS + 1, 100])
def test_the_count_is_bounded(count: int) -> None:
    with pytest.raises(ValidationError):
        _body(num_questions=count)


def test_five_is_the_ceiling_and_that_is_the_token_budget() -> None:
    assert gen.MAX_GENERATED_QUESTIONS == 5
    assert _body(num_questions=5).num_questions == 5
    # feedback_billing budgets 4096 + 2048 per problem for a coding generation.
    assert 4096 + 2048 * gen.MAX_GENERATED_QUESTIONS == 14_336


@pytest.mark.parametrize(
    "over",
    [
        {"topic": "x"},
        {"topic": "y" * 201},
        {"difficulty": "mixed"},  # a bank question has ONE difficulty (DB CHECK)
        {"difficulty": "extreme"},
        {"language": "fr"},
        {"allowed_languages": []},
        {"allowed_languages": ["cobol"]},
        {"allowed_languages": ["python", "brainfuck"]},
        {"job_title": "z" * 201},
    ],
)
def test_a_malformed_request_is_refused(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _body(**over)


@pytest.mark.parametrize("language", ["en", "hi", "te"])
def test_the_statement_language_is_one_of_the_day_one_three(language: str) -> None:
    assert _body(language=language).language == language


def test_programming_languages_are_normalised_and_deduped() -> None:
    assert _body(allowed_languages=["Python", "python", " CPP "]).allowed_languages == [
        "python", "cpp",
    ]


def test_the_request_has_no_field_that_could_name_a_tenant() -> None:
    fields = set(router.BankGenerateCodingIn.model_fields)
    assert not fields & {"company_id", "bank_id", "user_id", "actor", "status", "origin"}
    # And one smuggled in is discarded rather than honoured.
    body = _body(company_id=str(uuid.uuid4()))
    assert not hasattr(body, "company_id")


# ===========================================================================
# 4. The endpoint — generator, database and Redis all replaced
# ===========================================================================
UID = uuid.uuid4()
COMPANY = uuid.uuid4()
BANK = uuid.uuid4()


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    remote = AsyncMock(return_value=[_item()])
    summary = AsyncMock(return_value={"id": str(BANK)})
    hashes = AsyncMock(return_value=set())
    monkeypatch.setattr(router, "generate_coding_questions_remote", remote)
    monkeypatch.setattr(svc, "bank_summary", summary)
    monkeypatch.setattr(svc, "bank_content_hashes", hashes)
    db = MagicMock()
    db.rollback = AsyncMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    # The limiter is real; only Redis is replaced (never a real connection).
    redis = _FakeRedis([1] * 100)
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: redis)
    return {"remote": remote, "summary": summary, "hashes": hashes, "db": db, "redis": redis}


async def _call(wired: dict[str, Any], **over: Any) -> router.BankGenerateCodingOut:
    return await router.generate_bank_coding_questions(
        BANK, _body(**over), (UID, COMPANY), wired["db"]
    )


async def test_it_returns_a_preview_and_saves_nothing(wired: dict[str, Any]) -> None:
    out = await _call(wired, num_questions=1)
    assert len(out.questions) == 1
    assert out.requested == 1
    assert out.dropped == []
    assert out.not_returned == 0
    db = wired["db"]
    db.add.assert_not_called()
    db.commit.assert_not_awaited()
    db.flush.assert_not_awaited()


async def test_the_company_comes_from_the_session_and_scopes_both_lookups(
    wired: dict[str, Any],
) -> None:
    await _call(wired)
    assert wired["summary"].await_args.kwargs == {"company_id": COMPANY, "bank_id": BANK}
    assert wired["hashes"].await_args.kwargs == {"company_id": COMPANY, "bank_id": BANK}


async def test_another_companys_bank_is_a_404_and_no_model_call_is_made(
    wired: dict[str, Any],
) -> None:
    wired["summary"].side_effect = svc.QuestionBankError(404, "Question bank not found.")
    with pytest.raises(HTTPException) as exc:
        await _call(wired)
    assert exc.value.status_code == 404
    assert exc.value.detail == "Question bank not found."
    wired["remote"].assert_not_awaited()


async def test_the_generator_receives_the_request_and_the_acting_user(
    wired: dict[str, Any],
) -> None:
    await _call(
        wired, topic="binary search", num_questions=3, difficulty="hard", language="te",
        allowed_languages=["java", "python"], job_title="Backend Engineer",
        experience_level="senior",
    )
    assert wired["remote"].await_args.kwargs == {
        "topic": "binary search", "num_questions": 3, "difficulty": "hard", "language": "te",
        "allowed_languages": ["java", "python"], "acting_user_id": str(UID),
        "job_title": "Backend Engineer", "experience_level": "senior",
    }


async def test_the_chosen_statement_language_is_stamped_on_every_draft(
    wired: dict[str, Any],
) -> None:
    out = await _call(wired, language="hi", difficulty="easy")
    assert [(q.language, q.difficulty) for q in out.questions] == [("hi", "easy")]


async def test_a_generator_failure_is_a_clean_502(wired: dict[str, Any]) -> None:
    wired["remote"].side_effect = ExamGenerationError("coding generator returned HTTP 502: x")
    with pytest.raises(HTTPException) as exc:
        await _call(wired)
    assert exc.value.status_code == 502
    # A fixed sentence — nothing of the upstream error ("HTTP 502: x") leaks.
    assert exc.value.detail == GENERATION_UNAVAILABLE
    assert "HTTP" not in str(exc.value.detail)


async def test_partial_success_returns_the_good_ones_and_says_what_was_dropped(
    wired: dict[str, Any],
) -> None:
    wired["remote"].return_value = [
        _item(),
        _item(prompt=f"{STATEMENT} Second.", test_cases=[_case("1 1\n", "2\n", sample=True)]),
        _item(prompt=f"{STATEMENT} Third, see http://example.com."),
    ]
    out = await _call(wired, num_questions=5)
    assert len(out.questions) == 1
    assert out.requested == 5
    assert [(d.index, d.reason) for d in out.dropped] == [
        (1, "it has fewer than 2 usable test cases"),
        (2, "the problem statement contains a URL"),
    ]
    # Asked for five, the generator sent three: two it never returned.
    assert out.not_returned == 2


async def test_nothing_usable_is_a_502_that_says_why(wired: dict[str, Any]) -> None:
    wired["remote"].return_value = [_item(test_cases=[]), _item(prompt="short")]
    with pytest.raises(HTTPException) as exc:
        await _call(wired)
    assert exc.value.status_code == 502
    detail = str(exc.value.detail)
    assert "no usable coding questions" in detail
    assert "2 dropped" in detail


@pytest.mark.parametrize("payload", [[], None, {"questions": []}, "oops"])
async def test_an_empty_or_misshapen_generator_answer_is_a_502(
    wired: dict[str, Any], payload: Any,
) -> None:
    wired["remote"].return_value = payload
    with pytest.raises(HTTPException) as exc:
        await _call(wired)
    assert exc.value.status_code == 502


async def test_a_question_the_bank_already_holds_is_dropped_by_the_endpoint(
    wired: dict[str, Any],
) -> None:
    first = await _call(wired)
    q = first.questions[0]
    wired["hashes"].return_value = {svc.content_hash(
        kind="coding", prompt=q.prompt, test_cases=[tc.model_dump() for tc in q.test_cases],
    )}
    wired["remote"].return_value = [_item(), _item(prompt=f"{STATEMENT} A new one.")]
    out = await _call(wired)
    assert [x.prompt for x in out.questions] == [f"{STATEMENT} A new one."]
    assert "already in this bank" in out.dropped[0].reason


# ===========================================================================
# 5. Structure: role gate, rate limit, no execution, no second way to write
# ===========================================================================
def _route() -> Any:
    path = "/hr/question-banks/{bank_id}/questions/generate-coding"
    found = [r for r in router.hr_router.routes if getattr(r, "path", "") == path]
    assert len(found) == 1
    return found[0]


def test_the_route_is_post_only_and_sits_beside_the_mcq_generator() -> None:
    route = _route()
    assert route.methods == {"POST"}
    paths = [getattr(r, "path", "") for r in router.hr_router.routes]
    assert "/hr/question-banks/{bank_id}/questions/generate" in paths


def test_the_handler_takes_its_tenant_from_the_hr_session_dependency() -> None:
    hints = inspect.signature(router.generate_bank_coding_questions).parameters
    assert "HrCtxDep" in str(hints["ctx"].annotation)
    # The only way in is through get_hr_company — which requires hr_manager.
    assert any(d.call is get_hr_company for d in _route().dependant.dependencies)


class _FakeRedis:
    def __init__(self, counts: list[int] | None = None, *, broken: bool = False) -> None:
        self.keys: list[str] = []
        self._counts = counts or []
        self._broken = broken

    async def incr(self, key: str) -> int:
        if self._broken:
            raise ConnectionError("redis down")
        self.keys.append(key)
        return self._counts.pop(0)

    async def expire(self, key: str, seconds: int) -> None:
        assert seconds in (60, 86_400)


async def test_the_rate_limit_is_per_company_and_refuses_past_the_cap(
    wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    cap = settings.bank_generate_coding_per_minute
    fake = _FakeRedis([1, 1, cap, 1, cap + 1])
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    await _call(wired)          # minute=1, day=1
    await _call(wired)          # minute=cap, day=1
    with pytest.raises(HTTPException) as exc:
        await _call(wired)      # minute=cap+1 -> refused before the day is charged
    assert exc.value.status_code == 429
    # Keyed on the company from the session — many HR seats share one budget.
    assert fake.keys == [
        f"rl:bank_generate_coding:{COMPANY}", f"rl:bank_generate_daily:{COMPANY}",
        f"rl:bank_generate_coding:{COMPANY}", f"rl:bank_generate_daily:{COMPANY}",
        f"rl:bank_generate_coding:{COMPANY}",
    ]
    assert wired["remote"].await_count == 2


async def test_the_daily_cap_refuses_and_is_shared_by_mcq_and_coding(
    wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = settings.bank_generate_daily_per_company
    fake = _FakeRedis([1, day + 1])
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake)
    with pytest.raises(HTTPException) as exc:
        await _call(wired)
    assert exc.value.status_code == 429
    assert "today" in str(exc.value.detail)
    wired["remote"].assert_not_awaited()
    # The MCQ generator charges the SAME daily key.
    fake2 = _FakeRedis([1, 1])
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: fake2)
    monkeypatch.setattr(router, "generate_exam_questions_remote", AsyncMock(return_value=[]))
    with pytest.raises(HTTPException):
        await router.generate_bank_questions(BANK, router.BankGenerateIn(topic="sql"), (UID, COMPANY), wired["db"])
    assert f"rl:bank_generate_daily:{COMPANY}" in fake2.keys


def test_the_daily_cap_is_configured_and_sensible() -> None:
    assert 1 <= settings.bank_generate_daily_per_company <= 1000
    assert settings.bank_generate_daily_per_company > settings.bank_generate_coding_per_minute


async def test_the_rate_limit_fails_closed_with_503_when_redis_is_down(
    wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """This route spends LLM budget: 'limiter down' must not mean 'unlimited'."""
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: _FakeRedis(broken=True))
    with pytest.raises(HTTPException) as exc:
        await _call(wired)
    assert exc.value.status_code == 503
    wired["remote"].assert_not_awaited()
    # The MCQ twin too.
    with pytest.raises(HTTPException) as exc2:
        await router.generate_bank_questions(
            BANK, router.BankGenerateIn(topic="sql"), (UID, COMPANY), wired["db"]
        )
    assert exc2.value.status_code == 503


async def test_a_404_or_a_422_spends_no_budget(wired: dict[str, Any]) -> None:
    wired["summary"].side_effect = svc.QuestionBankError(404, "Question bank not found.")
    with pytest.raises(HTTPException):
        await _call(wired)
    assert wired["redis"].keys == []          # unknown bank: nothing charged
    # A 422 is raised by body validation before any handler runs at all, so the
    # limiter (now in the handler) cannot be reached by an invalid body.
    with pytest.raises(ValidationError):
        _body(num_questions=0)
    assert wired["redis"].keys == []


def test_the_coding_route_has_no_decorator_limiter_that_would_run_before_the_checks() -> None:
    assert _route().dependencies == []


def test_the_fail_open_default_is_unchanged_for_other_callers() -> None:
    import inspect as _i

    from app.rate_limit import enforce_company_budget, rate_limit_company
    assert _i.signature(rate_limit_company).parameters["fail_closed"].default is False
    assert _i.signature(enforce_company_budget).parameters["fail_closed"].default is False


def test_the_cap_is_small() -> None:
    assert 1 <= settings.bank_generate_coding_per_minute <= 10


# --- the DB connection is handed back before the model is awaited -----------
async def test_the_session_is_released_before_the_generator_is_awaited(
    wired: dict[str, Any],
) -> None:
    order: list[str] = []
    wired["db"].rollback = AsyncMock(side_effect=lambda: order.append("release"))
    wired["remote"].side_effect = lambda **_: (order.append("generate"), [_item()])[1]
    await _call(wired)
    assert order == ["release", "generate"]


async def test_the_mcq_session_is_released_before_the_generator_is_awaited(
    wired: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []
    wired["db"].rollback = AsyncMock(side_effect=lambda: order.append("release"))

    async def _gen(**_: Any) -> list[dict[str, Any]]:
        order.append("generate")
        return [{"prompt": "Q?", "options": ["a", "b"], "correct_index": 0}]

    monkeypatch.setattr(router, "generate_exam_questions_remote", _gen)
    await router.generate_bank_questions(
        BANK, router.BankGenerateIn(topic="sql"), (UID, COMPANY), wired["db"]
    )
    assert order == ["release", "generate"]


def test_the_handlers_do_not_touch_the_session_after_releasing_it() -> None:
    for fn in (router.generate_bank_coding_questions, router.generate_bank_questions):
        src = inspect.getsource(fn)
        tail = src.split("await db.rollback()", 1)[1]
        assert "db." not in tail and "svc." not in tail


def test_nothing_on_this_path_executes_code() -> None:
    """The sandbox client must not be reachable from generation: the reference
    solution is model output, and running it server-side at authoring time is a
    decision somebody has to make on purpose."""
    for source in (
        inspect.getsource(gen),
        inspect.getsource(router.generate_bank_coding_questions),
    ):
        assert "run_code" not in source
        assert "piston" not in source.lower()
        assert "jdoodle" not in source.lower()


def test_the_validator_module_talks_to_no_model_and_no_database() -> None:
    # Imports, not text: the module's docstring names the client it is NOT.
    imported: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(gen))):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported == {
        "__future__", "re", "typing", "unicodedata", "pydantic",
        "app.config", "app.question_banks", "app.routers.hr_coding",
    }
    assert not any(isinstance(n, ast.AsyncFunctionDef) for n in ast.walk(ast.parse(inspect.getsource(gen))))


def test_the_endpoint_writes_nothing_and_approves_nothing() -> None:
    source = inspect.getsource(router.generate_bank_coding_questions)
    for needle in ("db.add", "commit", "create_question", "create_drafts_bulk", "review(",
                   "approve", "submit("):
        assert needle not in source


def test_no_agent_tool_can_reach_the_generator() -> None:
    """An HR authoring endpoint, not a copilot tool."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[4]
    for folder in (root / "shared" / "agents", root / "services" / "data_gateway" / "app" / "agents"):
        for path in folder.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "generate_coding_questions_remote" not in text, path
            assert "bank_coding_generation" not in text, path


# ===========================================================================
# 6. Saving: a generated question lands as a DRAFT, never approved
# ===========================================================================
async def test_a_kept_question_is_saved_as_an_ai_draft_awaiting_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (q,) = _validate([_item()]).questions
    body = router.BulkQuestionsIn(questions=[q.model_dump()])

    async def _get_bank(_db: Any, _company: uuid.UUID, bank_id: uuid.UUID) -> Any:
        return MagicMock(id=bank_id)

    monkeypatch.setattr(svc, "_get_bank", _get_bank)
    db = MagicMock()
    db.flush = AsyncMock()
    (saved,) = await svc.create_drafts_bulk(
        db, company_id=COMPANY, bank_id=BANK, actor=UID,
        items=[router._question_kwargs(body.questions[0])],
    )
    assert saved.status == "draft"
    assert saved.origin == "ai_draft"
    assert saved.kind == "coding"
    assert saved.company_id == COMPANY
    assert saved.created_by_user_id == UID
    assert saved.reviewed_by_user_id is None
    assert saved.submitted_by_user_id is None
    assert saved.tags == [gen.UNVERIFIED_TAG]
    assert saved.starter_code is None
    # The DB's coding-shape CHECK: test cases, languages and a time limit.
    assert saved.test_cases and saved.allowed_languages and saved.time_limit_ms >= 100


def test_a_draft_can_only_move_to_review_never_straight_to_approved() -> None:
    assert svc.ALLOWED_TRANSITIONS["draft"] == frozenset({"in_review"})


def test_the_picker_refuses_a_generated_question_until_someone_else_approves_it() -> None:
    bq = MagicMock(status="draft", kind="coding", root_id=uuid.uuid4(), content_hash="h")
    assert svc.picker_skip_reason(
        bq, section_kind="coding", existing_roots=set(), existing_hashes=set()
    ) == "not approved"
    bq.status = "approved"
    assert svc.picker_skip_reason(
        bq, section_kind="coding", existing_roots=set(), existing_hashes=set()
    ) is None
