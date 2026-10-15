"""Four small leftovers from the 2026-10 security / code review.

1. Internal detail through the scoring + embedding clients (and their routes).
2. Exam-editor AI generation: connection released before the model call, and the
   AI budget charged only for a request that will reach the model.
3. ``list_banks`` costs a constant number of queries.

No database, Redis, network or LLM: sessions are mocks, Redis is a fake, the
HTTP client is a stub.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from structlog.testing import capture_logs

from app import embedding_client, scoring_client
from app import question_banks as svc
from app.config import settings
from app.routers import hr_applicants, hr_coding, hr_exams

COMPANY = uuid.uuid4()
UID = uuid.uuid4()


# ===========================================================================
# 1. Scoring + embedding clients: fixed sentence, specifics in the log only
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


def _stub(monkeypatch: pytest.MonkeyPatch, module: Any, **kw: Any) -> None:
    monkeypatch.setattr(settings, "ai_fake_mode", False)
    monkeypatch.setattr(module, "_internal_token", lambda _u: "t")
    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_: _Client(**kw))


_FAILURES = (
    {"resp": httpx.Response(500, text="Traceback: key sk-LEAK quota for project 1234")},
    {"exc": httpx.ConnectError("refused")},
    {"exc": httpx.ReadTimeout("slow")},
)
_LEAKS = ("sk-LEAK", "uvicorn", "8003", "localhost", "127.0.0.1", "feedback_billing", "Traceback")


async def _score() -> Any:
    return await scoring_client.score_resume_remote(
        resume_text="r", job_title="j", level="mid", jd_text=None, acting_user_id="u",
    )


async def _embed() -> Any:
    return await embedding_client.embed_texts_remote(
        texts=["a"], task_type="query", acting_user_id="u"
    )


async def _why() -> Any:
    return await embedding_client.why_match_remote(resume_text="r", query="q", acting_user_id="u")


@pytest.mark.parametrize("kw", _FAILURES)
async def test_scoring_message_is_fixed_in_production(
    monkeypatch: pytest.MonkeyPatch, kw: dict[str, Any]
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    _stub(monkeypatch, scoring_client, **kw)
    with pytest.raises(scoring_client.ResumeScoreError) as ei:
        await _score()
    msg = scoring_client.client_message(ei.value)
    assert msg == scoring_client.SCORING_UNAVAILABLE
    assert msg == "Scoring is not available right now. Please try again in a minute."
    assert not any(leak in msg for leak in _LEAKS)


@pytest.mark.parametrize("call", [_embed, _why])
@pytest.mark.parametrize("kw", _FAILURES)
async def test_embedding_message_is_fixed_in_production(
    monkeypatch: pytest.MonkeyPatch, kw: dict[str, Any], call: Any
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    _stub(monkeypatch, embedding_client, **kw)
    with pytest.raises(embedding_client.EmbeddingError) as ei:
        await call()
    for fixed in (embedding_client.SEARCH_UNAVAILABLE, embedding_client.EXPLANATION_UNAVAILABLE):
        msg = embedding_client.client_message(ei.value, fixed=fixed)
        assert msg == fixed
        assert not any(leak in msg for leak in _LEAKS)
    assert embedding_client.client_message(ei.value) == embedding_client.SEARCH_UNAVAILABLE


async def test_dev_hint_is_development_only_and_never_carries_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub(monkeypatch, scoring_client, resp=httpx.Response(500, text="sk-LEAK body"))
    with pytest.raises(scoring_client.ResumeScoreError) as ei:
        await _score()
    monkeypatch.setattr(settings, "app_env", "development")
    dev = scoring_client.client_message(ei.value)
    assert dev.startswith(scoring_client.SCORING_UNAVAILABLE) and "HTTP 500" in dev
    assert "sk-LEAK" not in dev
    monkeypatch.setattr(settings, "app_env", "staging")
    assert scoring_client.client_message(ei.value) == scoring_client.SCORING_UNAVAILABLE

    # An unreachable service: the start command is the dev hint, in dev only.
    _stub(monkeypatch, embedding_client, exc=httpx.ConnectError("refused"))
    with pytest.raises(embedding_client.EmbeddingError) as ei2:
        await _embed()
    monkeypatch.setattr(settings, "app_env", "development")
    assert "uvicorn" in embedding_client.client_message(ei2.value)
    monkeypatch.setattr(settings, "app_env", "production")
    assert "uvicorn" not in embedding_client.client_message(ei2.value)


async def test_specifics_reach_the_server_log(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub(monkeypatch, scoring_client, resp=httpx.Response(500, text="provider said: quota"))
    with capture_logs() as logs, pytest.raises(scoring_client.ResumeScoreError):
        await _score()
    assert any(e.get("body") == "provider said: quota" for e in logs)
    _stub(monkeypatch, embedding_client, exc=httpx.ConnectError("refused"))
    with capture_logs() as logs2, pytest.raises(embedding_client.EmbeddingError):
        await _embed()
    assert any(e["event"] == "embedding.unreachable" for e in logs2)


async def test_the_two_routes_answer_the_fixed_sentences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "app_env", "production")
    owned = MagicMock(resume_text="cv", target_job_title="t", target_level="mid", target_jd_text=None)
    monkeypatch.setattr(hr_applicants, "_get_owned", AsyncMock(return_value=owned))

    db = AsyncMock()
    res = MagicMock()
    res.mappings.return_value.first.return_value = None
    db.execute = AsyncMock(return_value=res)
    monkeypatch.setattr(
        hr_applicants, "score_resume_remote",
        AsyncMock(side_effect=scoring_client.ResumeScoreError(
            "Resume scoring needs the feedback_billing service on http://x:8003 sk-LEAK",
            hint="h",
        )),
    )
    with pytest.raises(HTTPException) as e1:
        await hr_applicants.rescore_applicant(
            uuid.uuid4(), (UID, COMPANY), db, enrolment_id=None
        )
    assert e1.value.status_code == 502
    assert e1.value.detail == scoring_client.SCORING_UNAVAILABLE

    monkeypatch.setattr(
        hr_applicants, "why_match_remote",
        AsyncMock(side_effect=embedding_client.EmbeddingError("http://x:8003 sk-LEAK", hint="h")),
    )
    with pytest.raises(HTTPException) as e2:
        await hr_applicants.why_match_applicant(uuid.uuid4(), (UID, COMPANY), db, "q")
    assert e2.value.status_code == 502
    assert e2.value.detail == embedding_client.EXPLANATION_UNAVAILABLE


# ===========================================================================
# 2. Exam-editor generation: release the connection, charge late
# ===========================================================================
class _Order:
    def __init__(self) -> None:
        self.events: list[str] = []


def _wire(monkeypatch: pytest.MonkeyPatch, mod: Any, gen_name: str, order: _Order,
          *, owned: Any = None) -> MagicMock:
    db = MagicMock()

    async def _rollback() -> None:
        order.events.append("rollback")

    db.rollback = _rollback

    async def _get_owned(*a: Any, **k: Any) -> Any:
        order.events.append("owned")
        if owned is not None:
            raise owned
        return MagicMock()

    async def _rubric(*a: Any, **k: Any) -> None:
        order.events.append("rubric")
        return None

    async def _budget(*a: Any, **k: Any) -> None:
        order.events.append("budget")
        assert k["fail_closed"] is True
        assert k["bucket"] == "exam_generate"

    async def _gen(*a: Any, **k: Any) -> list[dict[str, Any]]:
        order.events.append("generate")
        return []   # no usable questions -> 502 after the call; the order is what we test

    monkeypatch.setattr(mod, "_get_owned_exam", _get_owned)
    monkeypatch.setattr(mod, "rubric_for_exam", _rubric)
    monkeypatch.setattr(mod, "enforce_company_budget", _budget)
    monkeypatch.setattr(mod, gen_name, _gen)
    return db


async def _run_mcq(db: Any) -> Any:
    return await hr_exams.generate_questions(
        uuid.uuid4(), hr_exams.GenerateQuestionsIn(topic="sql"), (UID, COMPANY), db
    )


async def _run_coding(db: Any) -> Any:
    return await hr_coding.generate_coding_questions(
        uuid.uuid4(),
        hr_coding.GenerateCodingQuestionsIn(topic="sql", allowed_languages=["python"]),
        (UID, COMPANY), db,
    )


_CASES = [
    (hr_exams, "generate_exam_questions_remote", _run_mcq),
    (hr_coding, "generate_coding_questions_remote", _run_coding),
]


@pytest.mark.parametrize(("mod", "gen_name", "run"), _CASES)
async def test_connection_is_released_before_the_model_call_and_budget_is_charged_after_reads(
    monkeypatch: pytest.MonkeyPatch, mod: Any, gen_name: str, run: Any
) -> None:
    order = _Order()
    db = _wire(monkeypatch, mod, gen_name, order)
    with pytest.raises(HTTPException) as ei:
        await run(db)
    assert ei.value.status_code == 502
    assert order.events == ["owned", "rubric", "budget", "rollback", "generate"]


@pytest.mark.parametrize(("mod", "gen_name", "run"), _CASES)
async def test_an_exam_the_caller_does_not_own_costs_no_budget(
    monkeypatch: pytest.MonkeyPatch, mod: Any, gen_name: str, run: Any
) -> None:
    order = _Order()
    db = _wire(monkeypatch, mod, gen_name, order,
               owned=HTTPException(status_code=404, detail="Exam not found."))
    with pytest.raises(HTTPException) as ei:
        await run(db)
    assert ei.value.status_code == 404
    assert order.events == ["owned"]          # no budget, no model call


def test_the_routes_no_longer_charge_as_a_dependency() -> None:
    for r, path in ((hr_exams.router, "/exams/{exam_id}/questions/generate"),
                    (hr_coding.router, "/exams/{exam_id}/coding-questions/generate")):
        (route,) = (x for x in r.routes if getattr(x, "path", "").endswith(path))
        assert len(route.dependencies) == 0


async def test_the_budget_charge_is_still_fail_closed_and_per_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app import rate_limit

    class _Down:
        async def incr(self, key: str) -> int:
            raise ConnectionError("down")

    monkeypatch.setattr(rate_limit, "get_redis", lambda: _Down())
    with pytest.raises(HTTPException) as ei:
        await rate_limit.enforce_company_budget(
            COMPANY, bucket="exam_generate", limit=1, fail_closed=True
        )
    assert ei.value.status_code == 503


# ===========================================================================
# 3. list_banks: constant queries, identical payload
# ===========================================================================
def _bank(name: str, created: int = 0) -> Any:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    b = MagicMock()
    b.id, b.name, b.description = uuid.uuid4(), name, None
    b.created_at = b.updated_at = now.replace(minute=created % 60)
    return b


class _Result:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def scalars(self) -> _Result:
        return self

    def tuples(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return self._rows


class _ListDb:
    """Answers list_banks' three statements in order, recording each one."""

    def __init__(self, banks: list[Any], grouped: list[Any], usage: list[Any]) -> None:
        self._answers = [banks, grouped, usage]
        self.statements: list[Any] = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, stmt: Any, params: Any = None) -> _Result:
        self.statements.append((stmt, params))
        return _Result(self._answers[len(self.statements) - 1])


def _per_bank_db(rows: list[tuple[str, str, int]], usage: int) -> AsyncMock:
    res = MagicMock()
    res.tuples.return_value.all.return_value = rows
    db = AsyncMock()
    db.execute = AsyncMock(return_value=res)
    db.scalar = AsyncMock(return_value=usage)
    return db


async def test_list_banks_equals_the_per_bank_summaries_for_a_mixed_fixture() -> None:
    mixed, empty, retired_only, coding = (_bank("mixed", 1), _bank("empty", 2),
                                          _bank("retired", 3), _bank("coding", 4))
    rows = {
        mixed.id: [("approved", "mcq", 3), ("draft", "coding", 2), ("retired", "mcq", 5)],
        empty.id: [],
        retired_only.id: [("retired", "mcq", 4), ("retired", "coding", 1)],
        coding.id: [("in_review", "coding", 2)],
    }
    usage = {mixed.id: 7, coding.id: 1}
    banks = [mixed, empty, retired_only, coding]

    grouped = [(bid, st, k, n) for bid, rs in rows.items() for st, k, n in rs]
    db = _ListDb(banks, grouped, list(usage.items()))
    got = await svc.list_banks(db, company_id=COMPANY)  # type: ignore[arg-type]

    expected = [
        await svc._bank_summary(
            _per_bank_db(rows[b.id], usage.get(b.id, 0)), company_id=COMPANY, bank=b  # type: ignore[arg-type]
        )
        for b in banks
    ]
    assert got == expected
    # spot-check the shapes that matter
    by_name = {g["name"]: g for g in got}
    assert by_name["empty"]["counts"] == {} and by_name["empty"]["kind_counts"] == {
        "mcq": 0, "coding": 0}
    assert by_name["empty"]["used_in_exams"] == 0
    assert by_name["retired"]["counts"] == {"retired": 5}
    assert by_name["retired"]["kind_counts"] == {"mcq": 0, "coding": 0}
    assert by_name["mixed"]["used_in_exams"] == 7
    from app.routers.question_banks import BankOut

    for g in got:
        assert BankOut(**g).model_dump() == g


@pytest.mark.parametrize("n", [1, 30])
async def test_query_count_does_not_grow_with_the_number_of_banks(n: int) -> None:
    banks = [_bank(f"b{i}", i) for i in range(n)]
    grouped = [(b.id, "approved", "mcq", 1) for b in banks]
    db = _ListDb(banks, grouped, [(banks[0].id, 2)])
    out = await svc.list_banks(db, company_id=COMPANY)  # type: ignore[arg-type]
    assert len(out) == n
    assert db.execute.await_count == 3        # banks + grouped counts + exam usage, always


async def test_no_banks_is_one_query() -> None:
    db = _ListDb([], [], [])
    assert await svc.list_banks(db, company_id=COMPANY) == []  # type: ignore[arg-type]
    assert db.execute.await_count == 1


async def test_every_statement_is_scoped_to_the_company_and_the_bank_ids() -> None:
    banks = [_bank("a", 1), _bank("b", 2)]
    db = _ListDb(banks, [], [])
    other_company = uuid.uuid4()
    await svc.list_banks(db, company_id=COMPANY)  # type: ignore[arg-type]
    banks_stmt, _ = db.statements[0]
    grouped_stmt, _ = db.statements[1]
    usage_stmt, usage_params = db.statements[2]

    for stmt in (banks_stmt, grouped_stmt):
        compiled = stmt.compile()
        assert COMPANY in compiled.params.values()
        assert other_company not in compiled.params.values()
        assert "company_id" in str(compiled)
    assert "bank_id IN" in str(grouped_stmt.compile())
    assert usage_params["c"] == COMPANY
    assert set(usage_params["ids"]) == {b.id for b in banks}
    assert "q.company_id = :c" in str(usage_stmt)
    assert "q.bank_id IN (__[POSTCOMPILE_ids])" in str(usage_stmt)   # expanding bind, parameterised


# ===========================================================================
# 4. A board status move on a person with no application records its actor
# ===========================================================================
async def _board_move(
    monkeypatch: pytest.MonkeyPatch, *, current: str, to: str
) -> tuple[Any, Any]:
    applicant = MagicMock()
    applicant.id, applicant.status, applicant.full_name = uuid.uuid4(), current, "A"
    app_ = MagicMock(ambiguous=False, enrolment_id=None, status=current, is_latest=True, title="T")
    monkeypatch.setattr(hr_applicants, "_get_owned", AsyncMock(return_value=applicant))
    monkeypatch.setattr(hr_applicants, "choose_application", AsyncMock(return_value=app_))
    monkeypatch.setattr(hr_applicants, "email_applicant_decision", AsyncMock())
    monkeypatch.setattr(hr_applicants, "extract_client_ip", lambda _r: "203.0.113.9")
    monkeypatch.setattr(hr_applicants, "extract_user_agent", lambda _r: "ua")
    monkeypatch.setattr(hr_applicants, "_to_out", lambda a: a)
    db = MagicMock()
    db.commit = AsyncMock()
    await hr_applicants.update_applicant_status(
        applicant.id, hr_applicants.StatusUpdate(status=to), MagicMock(), (UID, COMPANY), db
    )
    return db, applicant


async def test_a_status_move_with_no_application_leaves_an_audit_row_naming_the_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, applicant = await _board_move(monkeypatch, current="new", to="interviewed")
    (row,) = (c.args[0] for c in db.add.call_args_list)
    assert row.actor_id == UID and row.actor_type == "user"
    assert row.action == "applicant.status.interviewed"
    assert row.resource_id == applicant.id and row.event_ts is not None
    assert row.details["previous_status"] == "new" and row.details["status"] == "interviewed"
    assert row.details["enrolment_id"] is None


async def test_a_resave_with_no_change_writes_no_audit_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db, _ = await _board_move(monkeypatch, current="interviewed", to="interviewed")
    assert db.add.call_count == 0
