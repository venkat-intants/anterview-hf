"""AI_FAKE_MODE and the test hooks — local end-to-end testing switches.

Two things matter, in this order:
1. Neither can be on where real users are: Settings refuses to start.
2. When on locally, the fakes are shaped exactly like the real responses and
   are deterministic, and the hooks demand their token.
"""

from __future__ import annotations

import math
import re
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.config import Settings
from app.fake_ai import (
    FAKE_EMBEDDING_DIMENSIONS,
    fake_coding_questions,
    fake_embeddings,
    fake_mcq_questions,
    fake_resume_score,
)

# Every value explicit, as in test_config.py: a developer's .env must not
# satisfy a test invisibly.
_BASE_ENV: dict[str, object] = {
    "database_url": "postgresql+asyncpg://u:p@localhost/db",
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret": "a" * 48,
    "exam_link_secret": "b" * 48,
    "interview_link_secret": "c" * 48,
    "consent_ip_salt": "d" * 48,
    "ai_fake_mode": False,
    "test_hooks_enabled": False,
    "test_hooks_token": "",
}
_PROD_ENV: dict[str, object] = {"auth_cookie_secure": True, "database_ssl": "require"}
_TOKEN = "t" * 40


def _settings(**overrides: object) -> Settings:
    return Settings(**{**_BASE_ENV, **overrides})  # type: ignore[arg-type]


# ===========================================================================
# 1. Never where real users are
# ===========================================================================
@pytest.mark.parametrize("env", ["production", "staging", "prod", "LIVE"])
@pytest.mark.parametrize(
    ("switch", "extra"),
    [("ai_fake_mode", {}), ("test_hooks_enabled", {"test_hooks_token": _TOKEN})],
)
def test_a_testing_switch_stops_production_from_starting(
    env: str, switch: str, extra: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="local end-to-end testing only"):
        _settings(app_env=env, **_PROD_ENV, **{switch: True}, **extra)


def test_production_starts_with_both_switches_off() -> None:
    s = _settings(app_env="production", **_PROD_ENV)
    assert s.ai_fake_mode is False and s.test_hooks_enabled is False


def test_both_are_off_by_default() -> None:
    s = Settings(**{k: v for k, v in _BASE_ENV.items()  # type: ignore[arg-type]
                    if k not in {"ai_fake_mode", "test_hooks_enabled", "test_hooks_token"}})
    assert s.ai_fake_mode is False and s.test_hooks_enabled is False


@pytest.mark.parametrize("token", ["", "short", "x" * 31])
def test_hooks_need_a_long_token(token: str) -> None:
    with pytest.raises(ValidationError, match="TEST_HOOKS_TOKEN of at least 32"):
        _settings(app_env="development", test_hooks_enabled=True, test_hooks_token=token)


def test_hooks_start_locally_with_a_token() -> None:
    s = _settings(app_env="development", test_hooks_enabled=True, test_hooks_token=_TOKEN)
    assert s.test_hooks_enabled is True


def test_the_hooks_router_is_not_mounted_unless_enabled() -> None:
    from app.config import settings
    from app.main import app

    if settings.test_hooks_enabled:  # a developer's .env turned it on
        pytest.skip("TEST_HOOKS_ENABLED is set in this environment")
    paths = {getattr(r, "path", "") for r in app.routes}
    assert not any(p.startswith("/test-hooks") for p in paths)


def test_a_wrong_or_missing_token_is_a_plain_404(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings
    from app.routers import test_hooks

    monkeypatch.setattr(settings, "test_hooks_token", _TOKEN)
    for supplied in (None, "", "wrong", _TOKEN[:-1]):
        with pytest.raises(HTTPException) as exc:
            test_hooks.require_test_hooks_token(supplied)
        assert exc.value.status_code == 404
    test_hooks.require_test_hooks_token(_TOKEN)  # the right one passes


def test_an_unset_token_refuses_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import settings
    from app.routers import test_hooks

    monkeypatch.setattr(settings, "test_hooks_token", "")
    with pytest.raises(HTTPException):
        test_hooks.require_test_hooks_token("")


# ===========================================================================
# 2. Fakes shaped like the real thing, and deterministic
# ===========================================================================
def test_a_fake_resume_score_has_every_field_the_real_one_has() -> None:
    from app.fake_ai import fake_resume_score as f

    score = f(resume_text="Asha\nasha@example.com\nPython FastAPI", job_title="Python Developer",
              level="mid", jd_text="Python FastAPI PostgreSQL")
    real_fields = {"candidate_name", "candidate_email", "overall", "breakdown", "strengths",
                   "concerns", "recommendation", "summary"}
    assert set(score) == real_fields
    assert score["candidate_name"] == "Asha" and score["candidate_email"] == "asha@example.com"
    assert score["recommendation"] in {"strong_fit", "moderate_fit", "weak_fit"}
    assert set(score["breakdown"]) == {"skills_match", "experience", "education", "projects"}


@pytest.mark.parametrize(("pin", "overall", "rec"),
                         [(85, 85, "strong_fit"), (60, 60, "moderate_fit"), (5, 5, "weak_fit"),
                          (250, 100, "strong_fit")])
def test_a_cv_can_pin_its_own_score(pin: int, overall: int, rec: str) -> None:
    score = fake_resume_score(resume_text=f"Someone\nE2E-SCORE: {pin}", job_title="Any",
                              level="mid", jd_text="")
    assert score["overall"] == overall and score["recommendation"] == rec


def test_an_unpinned_score_rewards_the_roles_keywords() -> None:
    kw: dict[str, Any] = {"job_title": "Python Developer", "level": "mid",
                          "jd_text": "Python FastAPI PostgreSQL pytest Docker"}
    relevant = fake_resume_score(resume_text="Python FastAPI PostgreSQL pytest Docker", **kw)
    unrelated = fake_resume_score(resume_text="Canva Instagram flyers bakery", **kw)
    assert relevant["overall"] > unrelated["overall"]
    assert relevant == fake_resume_score(resume_text="Python FastAPI PostgreSQL pytest Docker", **kw)


def test_fake_mcqs_mark_the_correct_answer_in_a_rotating_position() -> None:
    qs = fake_mcq_questions(topic="Aptitude", num_questions=8)
    assert len(qs) == 8
    assert all(q["options"][q["correct_index"]] == "Correct answer" for q in qs)
    assert {q["correct_index"] for q in qs} == {0, 1, 2, 3}
    assert all(len(q["options"]) == 4 for q in qs)


def test_the_fake_coding_problem_is_solved_by_its_reference_solution() -> None:
    (q,) = fake_coding_questions(topic="Python", num_questions=1, allowed_languages=["python"])
    assert any(tc["is_sample"] for tc in q["test_cases"])
    assert any(not tc["is_sample"] for tc in q["test_cases"])
    for tc in q["test_cases"]:
        a, b = map(int, tc["stdin"].split())
        assert tc["expected_output"].strip() == str(a + b)
    assert re.search(r"print\(a \+ b\)", q["reference_solution"])


def test_fake_embeddings_fit_the_column_and_repeat() -> None:
    a, b, a2 = fake_embeddings(["python developer", "nurse", "python developer"])
    assert len(a) == FAKE_EMBEDDING_DIMENSIONS == 3072
    assert a == a2 and a != b
    assert math.isclose(math.sqrt(sum(v * v for v in a)), 1.0, rel_tol=1e-9)


# ===========================================================================
# The five call sites use the fakes, and make no request, when the mode is on
# ===========================================================================
class _NoNetwork:
    def __init__(self, *_: object, **__: object) -> None:
        raise AssertionError("AI_FAKE_MODE made a network call")


@pytest.mark.asyncio
async def test_fake_mode_short_circuits_every_model_call(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app import embedding_client, exam_ai_client, scoring_client
    from app.config import settings

    monkeypatch.setattr(settings, "ai_fake_mode", True)
    monkeypatch.setattr(httpx, "AsyncClient", _NoNetwork)

    score = await scoring_client.score_resume_remote(
        resume_text="E2E-SCORE: 80", job_title="QA", level="mid", jd_text=None, acting_user_id="u")
    assert score["overall"] == 80
    mcqs = await exam_ai_client.generate_exam_questions_remote(
        topic="QA", num_questions=3, difficulty="medium", language="en", acting_user_id="u")
    assert len(mcqs) == 3
    coding = await exam_ai_client.generate_coding_questions_remote(
        topic="QA", num_questions=1, difficulty="easy", language="en",
        allowed_languages=["python"], acting_user_id="u")
    assert coding[0]["reference_solution"]
    vectors = await embedding_client.embed_texts_remote(
        texts=["a", "b"], task_type="document", acting_user_id="u")
    assert len(vectors) == 2
    reason = await embedding_client.why_match_remote(resume_text="x", query="python", acting_user_id="u")
    assert "python" in reason
