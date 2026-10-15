"""Hardening of the AI question-bank generation path (review findings 1-8).

No database, Redis, network or LLM: the session is a mock, Redis a fake, and
the generator's HTTP client a stub.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from app import bank_coding_generation as gen
from app import exam_ai_client
from app import question_banks as svc
from app.config import settings
from app.models import CodingQuestion
from app.routers import hr_coding, hr_exams
from app.routers import question_banks as router

COMPANY = uuid.uuid4()
BANK = uuid.uuid4()
UID = uuid.uuid4()

STATEMENT = (
    "Read two integers a and b from one line of standard input, separated by a "
    "space, and print their sum on one line. Constraints: -10^9 <= a, b <= 10^9."
)


def _case(stdin: str, out: str, *, sample: bool = False) -> dict[str, Any]:
    return {"stdin": stdin, "expected_output": out, "is_sample": sample, "weight": 1}


def _item(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "prompt": STATEMENT,
        "reference_solution": "a, b = map(int, input().split())\nprint(a + b)\n",
        "test_cases": [_case("2 3\n", "5\n", sample=True), _case("-4 10\n", "6\n")],
    }
    base.update(over)
    return base


def _validate(items: list[Any]) -> gen.GenerationOutcome:
    return gen.validate_generated(
        items, requested=5, difficulty="medium", language="en",
        allowed_languages=["python"], existing_hashes=set(),
    )


# ===========================================================================
# 1. The unverified marker is server-derived
# ===========================================================================
def test_one_shared_constant() -> None:
    assert gen.UNVERIFIED_TAG is svc.UNVERIFIED_TAG
    assert svc.UNVERIFIED_TAG == "reference-unverified"


def test_a_draft_with_no_reference_solution_is_marked_unverified_too() -> None:
    (q,) = _validate([_item(reference_solution=None)]).questions
    assert q.reference_solution is None
    assert q.tags == [svc.UNVERIFIED_TAG]
    assert q.reference_solution_verified is False


async def _bulk(monkeypatch: pytest.MonkeyPatch, items: list[dict[str, Any]]) -> list[Any]:
    async def _get_bank(_db: Any, _company: uuid.UUID, bank_id: uuid.UUID) -> Any:
        return MagicMock(id=bank_id)

    monkeypatch.setattr(svc, "_get_bank", _get_bank)
    db = MagicMock()
    db.flush = AsyncMock()
    return await svc.create_drafts_bulk(
        db, company_id=COMPANY, bank_id=BANK, actor=UID, items=items,
    )


def _coding_kwargs(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "kind": "coding", "prompt": STATEMENT, "points": 100, "difficulty": "medium",
        "language": "en", "tags": [], "allowed_languages": ["python"],
        "test_cases": [_case("2 3\n", "5\n", sample=True), _case("1 1\n", "2\n")],
        "time_limit_ms": 5000,
    }
    base.update(over)
    return base


async def test_bulk_save_stamps_the_tag_whatever_the_client_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = await _bulk(monkeypatch, [
        _coding_kwargs(tags=[]),                                   # client removed it
        _coding_kwargs(prompt=STATEMENT + " B", tags=["graphs"]),  # never had it
        _coding_kwargs(prompt=STATEMENT + " C",
                       tags=["Reference-Unverified", "x"]),        # already there
    ])
    assert saved[0].tags == [svc.UNVERIFIED_TAG]
    assert saved[1].tags == ["graphs", svc.UNVERIFIED_TAG]
    assert saved[2].tags.count(svc.UNVERIFIED_TAG) == 1   # deduplicated
    assert all(q.origin == "ai_draft" for q in saved)


async def test_bulk_save_does_not_stamp_an_mcq(monkeypatch: pytest.MonkeyPatch) -> None:
    (q,) = await _bulk(monkeypatch, [{
        "kind": "mcq", "prompt": "What is 2+2?", "points": 1, "difficulty": "easy",
        "language": "en", "tags": [], "options": ["3", "4"], "correct_index": 1,
    }])
    assert q.tags is None or svc.UNVERIFIED_TAG not in q.tags


def _stored(**over: Any) -> Any:
    q = MagicMock()
    q.company_id, q.root_id, q.kind, q.origin = COMPANY, uuid.uuid4(), "coding", "ai_draft"
    q.status, q.tags = "draft", [svc.UNVERIFIED_TAG]
    q.prompt, q.options, q.correct_index, q.test_cases = STATEMENT, None, None, []
    for k, v in over.items():
        setattr(q, k, v)
    return q


async def _update(monkeypatch: pytest.MonkeyPatch, q: Any, *, approved_in_lineage: bool,
                  **fields: Any) -> Any:
    async def _get_question(_db: Any, _c: uuid.UUID, _qid: uuid.UUID) -> Any:
        return q

    monkeypatch.setattr(svc, "_get_question", _get_question)
    db = MagicMock()
    db.scalar = AsyncMock(return_value=uuid.uuid4() if approved_in_lineage else None)
    return await svc.update_question(db, company_id=COMPANY, qid=uuid.uuid4(), actor=UID, **fields)


async def test_the_tag_cannot_be_removed_from_a_never_approved_ai_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q = await _update(monkeypatch, _stored(), approved_in_lineage=False, tags=[])
    assert svc.UNVERIFIED_TAG in q.tags
    q = await _update(monkeypatch, _stored(), approved_in_lineage=False, tags=["a"])
    assert q.tags == ["a", svc.UNVERIFIED_TAG]


async def test_a_save_that_sends_no_tags_still_restores_a_missing_tag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q = await _update(monkeypatch, _stored(tags=[]), approved_in_lineage=False, points=5)
    assert q.tags == [svc.UNVERIFIED_TAG]


async def test_the_tag_may_be_dropped_once_a_version_has_been_approved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q = await _update(monkeypatch, _stored(), approved_in_lineage=True, tags=[])
    assert svc.UNVERIFIED_TAG not in (q.tags or [])


async def test_the_rule_is_only_for_ai_drafted_coding_questions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    q = await _update(monkeypatch, _stored(origin="authored"), approved_in_lineage=False, tags=[])
    assert svc.UNVERIFIED_TAG not in (q.tags or [])
    q = await _update(monkeypatch, _stored(kind="mcq"), approved_in_lineage=False, tags=[])
    assert svc.UNVERIFIED_TAG not in (q.tags or [])


def test_the_tag_is_a_label_not_a_gate() -> None:
    import inspect

    for fn in (svc.submit, svc.review):
        assert "UNVERIFIED" not in inspect.getsource(fn)
    assert svc.ALLOWED_TRANSITIONS["in_review"] == frozenset({"draft", "approved"})


def test_a_copied_exam_question_has_no_tags_column_so_the_link_is_the_provenance() -> None:
    """No migration: ``coding_questions`` carries no tags/metadata, so the exam
    editor reads ``source_bank_question_id`` (already returned) and looks the
    bank question up."""
    cols = {c.name for c in CodingQuestion.__table__.columns}
    assert not cols & {"tags", "metadata", "origin", "provenance"}
    assert "source_bank_question_id" in cols
    assert "source_bank_question_id" in hr_coding.CodingQuestionOut.model_fields


# ===========================================================================
# 2 + 3. Upstream failures: fixed sentence, specifics logged, no 500s
# ===========================================================================
class _Client:
    def __init__(self, *, resp: httpx.Response | None = None, exc: Exception | None = None):
        self._resp, self._exc = resp, exc

    async def __aenter__(self) -> _Client:
        return self

    async def __aexit__(self, *a: object) -> None:
        return None

    async def post(self, *a: Any, **k: Any) -> httpx.Response:
        if self._exc:
            raise self._exc
        assert self._resp is not None
        return self._resp


def _stub(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> None:
    monkeypatch.setattr(settings, "ai_fake_mode", False)
    monkeypatch.setattr(exam_ai_client, "_internal_token", lambda _u: "t")
    monkeypatch.setattr(exam_ai_client.httpx, "AsyncClient", lambda **_: _Client(**kw))


_CALLS = ("mcq", "coding")


async def _call(kind: str) -> Any:
    if kind == "mcq":
        return await exam_ai_client.generate_exam_questions_remote(
            topic="t", num_questions=1, difficulty="easy", language="en", acting_user_id="u",
        )
    return await exam_ai_client.generate_coding_questions_remote(
        topic="t", num_questions=1, difficulty="easy", language="en",
        allowed_languages=["python"], acting_user_id="u",
    )


@pytest.mark.parametrize("kind", _CALLS)
@pytest.mark.parametrize(
    "body",
    [b"<html>oops</html>", b"", b"[1, 2]", b'"str"', b'{"questions": "nope"}',
     b'{"questions": {"a": 1}}', b'{"questions": [1, 2]}', b"\xff\xfe"],
)
async def test_a_malformed_200_is_a_generation_error_not_a_500(
    monkeypatch: pytest.MonkeyPatch, kind: str, body: bytes,
) -> None:
    _stub(monkeypatch, resp=httpx.Response(200, content=body))
    with pytest.raises(exam_ai_client.ExamGenerationError):
        await _call(kind)


@pytest.mark.parametrize("kind", _CALLS)
async def test_good_and_empty_bodies_still_work(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    _stub(monkeypatch, resp=httpx.Response(200, json={"questions": [{"prompt": "p"}, 5]}))
    assert await _call(kind) == [{"prompt": "p"}]      # a stray scalar costs only itself
    _stub(monkeypatch, resp=httpx.Response(200, json={}))
    assert await _call(kind) == []
    _stub(monkeypatch, resp=httpx.Response(200, json={"questions": None}))
    assert await _call(kind) == []


@pytest.mark.parametrize("kind", _CALLS)
async def test_the_browser_message_is_fixed_and_leaks_nothing_in_production(
    monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    secret_body = "Traceback: provider key sk-LEAK quota exceeded for project 1234"
    for kw in (
        {"resp": httpx.Response(500, text=secret_body)},
        {"exc": httpx.ConnectError("refused")},
        {"exc": httpx.ReadTimeout("slow")},
    ):
        _stub(monkeypatch, **kw)
        with pytest.raises(exam_ai_client.ExamGenerationError) as ei:
            await _call(kind)
        msg = exam_ai_client.client_message(ei.value)
        assert msg == exam_ai_client.GENERATION_UNAVAILABLE
        assert "sk-LEAK" not in msg and "uvicorn" not in msg and "8003" not in msg


async def test_the_developer_hint_is_development_only_and_never_has_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, resp=httpx.Response(500, text="sk-LEAK body"))
    with pytest.raises(exam_ai_client.ExamGenerationError) as ei:
        await _call("coding")
    monkeypatch.setattr(settings, "app_env", "development")
    dev = exam_ai_client.client_message(ei.value)
    assert dev.startswith(exam_ai_client.GENERATION_UNAVAILABLE) and "HTTP 500" in dev
    assert "sk-LEAK" not in dev
    monkeypatch.setattr(settings, "app_env", "staging")
    assert exam_ai_client.client_message(ei.value) == exam_ai_client.GENERATION_UNAVAILABLE


async def test_the_upstream_body_reaches_the_server_log_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from structlog.testing import capture_logs

    _stub(monkeypatch, resp=httpx.Response(500, text="provider said: quota"))
    with capture_logs() as logs, pytest.raises(exam_ai_client.ExamGenerationError):
        await _call("mcq")
    assert any(e.get("body") == "provider said: quota" for e in logs)


async def test_all_four_generation_routes_answer_the_fixed_sentence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    err = exam_ai_client.ExamGenerationError("internal http://x:8003 body", hint="h")
    # bank MCQ
    db = MagicMock()
    db.rollback = AsyncMock()
    monkeypatch.setattr(svc, "bank_summary", AsyncMock(return_value={}))
    monkeypatch.setattr("app.rate_limit.get_redis", lambda: _Redis())
    monkeypatch.setattr(router, "generate_exam_questions_remote", AsyncMock(side_effect=err))
    with pytest.raises(HTTPException) as e1:
        await router.generate_bank_questions(
            BANK, router.BankGenerateIn(topic="sql"), (UID, COMPANY), db
        )
    assert e1.value.status_code == 502 and e1.value.detail == exam_ai_client.GENERATION_UNAVAILABLE
    # exam MCQ + exam coding
    for mod, name, body in (
        (hr_exams, "generate_exam_questions_remote", hr_exams.GenerateQuestionsIn(topic="sql")),
        (hr_coding, "generate_coding_questions_remote",
         hr_coding.GenerateCodingQuestionsIn(topic="sql", allowed_languages=["python"])),
    ):
        monkeypatch.setattr(mod, "_get_owned_exam", AsyncMock())
        monkeypatch.setattr(mod, "rubric_for_exam", AsyncMock(return_value=None))
        monkeypatch.setattr(mod, name, AsyncMock(side_effect=err))
        # The budget is charged in the handler now (after the reads), and the
        # connection is released before the call: both are awaited.
        monkeypatch.setattr(mod, "enforce_company_budget", AsyncMock())
        fn = (hr_exams.generate_questions if mod is hr_exams
              else hr_coding.generate_coding_questions)
        with pytest.raises(HTTPException) as e:
            await fn(uuid.uuid4(), body, (UID, COMPANY), AsyncMock())
        assert e.value.status_code == 502
        assert e.value.detail == exam_ai_client.GENERATION_UNAVAILABLE


class _Redis:
    async def incr(self, key: str) -> int:
        return 1

    async def expire(self, key: str, seconds: int) -> None:
        return None


def test_the_exam_generate_routes_charge_a_fail_closed_budget_in_the_handler() -> None:
    """Moved from a route dependency to the handler body (2026-10-13) so a 404 or
    a 422 does not spend the company's AI budget; the ordering and the
    ``fail_closed=True`` argument are asserted in test_review_leftovers_2026_10_13."""
    import inspect

    from app.rate_limit import rate_limit_company

    for r, path in ((hr_exams.router, "/exams/{exam_id}/questions/generate"),
                    (hr_coding.router, "/exams/{exam_id}/coding-questions/generate")):
        (route,) = (x for x in r.routes if getattr(x, "path", "").endswith(path))
        assert len(route.dependencies) == 0
    for fn in (hr_exams.generate_questions, hr_coding.generate_coding_questions):
        src = inspect.getsource(fn)
        assert "enforce_company_budget" in src and "fail_closed=True" in src
    assert rate_limit_company("b", 1, fail_closed=True).dependency is not None


async def test_fail_closed_dependency_answers_503_and_default_stays_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.rate_limit import rate_limit_company

    class _Down:
        async def incr(self, key: str) -> int:
            raise ConnectionError("down")

    monkeypatch.setattr("app.rate_limit.get_redis", lambda: _Down())
    with pytest.raises(HTTPException) as ei:
        await rate_limit_company("b", 1, fail_closed=True).dependency(ctx=(UID, COMPANY))
    assert ei.value.status_code == 503
    await rate_limit_company("b", 1).dependency(ctx=(UID, COMPANY))   # fails open, as before


# ===========================================================================
# 6. URL / secret tripwire
# ===========================================================================
@pytest.mark.parametrize("text", [
    "see http://evil.example/x",
    "see ｈｔｔｐ：／／evil.example",                     # full-width (NFKC)
    "see ht​tp://evil.example",                       # zero-width space
    "see h­ttps://evil.example",                      # soft hyphen
    "see ﻿https://evil.example",
    "see hxxp://evil.example and hxxps://x",
    "visit evil.com/payload now",
    "visit sub.evil.net/a/b",
    "visit EVIL.ORG/x",
    "ev​il.com/payload",
    "key AKIA​ABCDEFGHIJKLMNOP",                      # secret split by ZWSP
    "www.evil.com",
])
def test_obfuscated_and_bare_links_are_caught(text: str) -> None:
    assert gen._unsafe_reason(text) is not None, text


@pytest.mark.parametrize("text", [
    "a.b", "print(obj.method/2)", "x = self.count/3", "version 1.2.3 and v2.0/4",
    "read file.txt then write out.csv", "import os.path; os.path.join(a, b)",
    "ratio a.b/c.d", "use math.floor/ceil here", "x.in/y", "data.json and notes.md",
    "Divide the total by 2.5 and print", "user@example.com is an email-like token",
    "the file is data.txt/ in 3.5/2",
])
def test_ordinary_programming_text_is_not_flagged(text: str) -> None:
    assert gen._unsafe_reason(text) is None, text


def test_the_tripwire_drops_an_item_through_validate_generated() -> None:
    out = _validate([_item(prompt=STATEMENT + " Docs: ev​il.com/payload")])
    assert out.questions == []
    assert out.dropped[0].reason == "the problem statement contains a URL"


# ===========================================================================
# 8. BankOut
# ===========================================================================
async def test_bank_out_matches_exactly_what_the_summary_returns() -> None:
    now = datetime.now(tz=UTC)
    bank = MagicMock()
    bank.id, bank.name, bank.description, bank.created_at, bank.updated_at = (
        uuid.uuid4(), "B", None, now, now,
    )
    res = MagicMock()
    res.tuples.return_value.all.return_value = [("approved", "mcq", 3), ("draft", "coding", 1)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    db.scalar = AsyncMock(return_value=2)
    raw = await svc._bank_summary(db, company_id=COMPANY, bank=bank)
    assert set(router.BankOut.model_fields) == set(raw)          # no key added, none dropped
    assert router.BankOut(**raw).model_dump() == raw             # and every value survives


def test_bank_out_is_attached_to_list_create_and_patch() -> None:
    wanted = {
        ("/hr/question-banks", "GET"), ("/hr/question-banks", "POST"),
        ("/hr/question-banks/{bank_id}", "PATCH"),
    }
    found = {
        (r.path, m) for r in router.hr_router.routes
        for m in getattr(r, "methods", set())
        if (r.path, m) in wanted and r.response_model is not None
    }
    assert found == wanted
