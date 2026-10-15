"""Client for feedback_billing's AI exam-question generator (HR MCQ authoring).

data_gateway owns exams but the Gemini generator lives in feedback_billing. We
mint a short internal JWT (data_gateway is the issuer; feedback_billing validates
it with the shared secret) and POST the generation params to
/internal/generate-exam. The generated questions are returned for the HR endpoint
to validate + persist. PII: nothing sensitive is sent — only topic/role text.
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog
from shared.auth.jwt import SERVICE_TOKEN_TTL_SECONDS, issue_access_token, resolve_signing_key

from app.config import settings
from app.fake_ai import fake_coding_questions, fake_mcq_questions
from app.remote import describe_unreachable

log = structlog.get_logger(__name__)


#: The ONLY sentence a browser is told when generation fails. The specifics
#: (service origin, how to start it, the provider's response body) are logged
#: server-side by this module and never put in an HTTP response.
GENERATION_UNAVAILABLE = (
    "The AI question generator is not available right now. Please try again in a minute."
)


class ExamGenerationError(Exception):
    """Raised when the AI generator cannot be reached or returns an error.

    ``str(exc)`` is the INTERNAL description (may hold an upstream body
    excerpt) — for logs, never for a response. ``hint`` is a short, body-free
    developer note (the unreachable-service description). Route code answers
    with :func:`client_message`.
    """

    def __init__(self, internal: str, *, hint: str | None = None) -> None:
        super().__init__(internal)
        self.hint = hint


def client_message(exc: ExamGenerationError) -> str:
    """What the HR browser is told. Fixed, plus the developer hint ONLY when
    ``APP_ENV`` is ``development`` (the origin and start command are of use
    to nobody else, and they describe our internal topology)."""
    if settings.app_env == "development" and exc.hint:
        return f"{GENERATION_UNAVAILABLE} (dev: {exc.hint})"
    return GENERATION_UNAVAILABLE


def _questions_from(resp: httpx.Response, *, what: str) -> list[dict[str, Any]]:
    """The ``questions`` list from a 200 response, or ``ExamGenerationError``.

    A 200 whose body is not JSON, is not an object, or whose ``questions`` is
    not a list of objects is a broken upstream, not a caller error: it used to
    raise ``JSONDecodeError`` / ``AttributeError`` out of the route as a 500.
    """
    try:
        data = resp.json()
    except ValueError as exc:  # json.JSONDecodeError and UnicodeDecodeError are ValueErrors
        log.error("exam_ai.bad_response", what=what, problem="not_json", error_type=type(exc).__name__)
        raise ExamGenerationError(f"{what} returned a body that is not JSON") from exc
    if not isinstance(data, dict):
        log.error("exam_ai.bad_response", what=what, problem="not_an_object")
        raise ExamGenerationError(f"{what} returned JSON that is not an object")
    questions = data.get("questions")
    if questions is None:
        return []
    if not isinstance(questions, list):
        log.error("exam_ai.bad_response", what=what, problem="questions_not_a_list")
        raise ExamGenerationError(f"{what} returned a 'questions' value of the wrong shape")
    objects = [q for q in questions if isinstance(q, dict)]
    if questions and not objects:
        log.error("exam_ai.bad_response", what=what, problem="questions_not_objects")
        raise ExamGenerationError(f"{what} returned 'questions' that are not objects")
    if len(objects) != len(questions):
        # One stray scalar must not cost the caller the good items beside it.
        log.warning("exam_ai.items_discarded", what=what, count=len(questions) - len(objects))
    return objects


def _unreachable(exc: httpx.RequestError, *, what: str, url: str) -> ExamGenerationError:
    detail = describe_unreachable(exc, what=what, url=url)
    # Specifics to the log (origin and error class; no secrets are in either).
    log.error("exam_ai.unreachable", what=what, error_type=type(exc).__name__, detail=detail)
    return ExamGenerationError(detail, hint=detail)


def _http_error(resp: httpx.Response, *, what: str) -> ExamGenerationError:
    # The body excerpt goes to the log only; it never reaches a response.
    log.error(
        "exam_ai.upstream_error", what=what, status=resp.status_code, body=resp.text[:160]
    )
    return ExamGenerationError(
        f"{what} returned HTTP {resp.status_code}: {resp.text[:160]}",
        hint=f"{what} returned HTTP {resp.status_code}",
    )


# The service identity this process presents to feedback_billing. It must be
# in feedback_billing's _ALLOWED_SERVICE_SUBS.
_SERVICE_SUB = "data_gateway"


def _internal_token(acting_user_id: str) -> str:
    """Mint the service token for a feedback_billing /internal/* call.

    `sub` is the SERVICE, not the human who triggered the call. It used to be
    the acting HR user's UUID, which made a service identity and a user
    identity indistinguishable on the wire — both signed with the same
    jwt_secret, differing only by a roles claim. feedback_billing pins the
    allowed subjects, and a per-user UUID cannot be pinned.

    The human is carried in `act_sub` purely for attribution in the callee's
    logs. It is NOT authoritative and must never be used for an authorisation
    decision — the caller controls it, and the caller is the one being
    authorised.
    """
    algorithm, key, kid = resolve_signing_key(settings)
    return issue_access_token(
        user_id=_SERVICE_SUB,
        roles=["service"],
        secret=key,
        algorithm=algorithm,
        issuer=settings.jwt_issuer,
        audience=settings.jwt_audience,
        extra_claims={"act_sub": acting_user_id},
        # 60s, not the 15-minute user default. A service token's `sub` is a
        # service name, so logout_all (which only ever writes
        # auth_epoch:<user-uuid>) cannot revoke it — its lifetime is its only
        # containment. interview_core's minter always used 60s; these three
        # silently used 900s because that was issue_access_token's default and
        # there was no parameter to say otherwise.
        ttl_seconds=SERVICE_TOKEN_TTL_SECONDS,
        kid=kid,
    )


async def generate_exam_questions_remote(
    *,
    topic: str,
    num_questions: int,
    difficulty: str,
    language: str,
    acting_user_id: str,
    job_title: str = "",
    experience_level: str = "mid",
    round_rubric: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate MCQs via feedback_billing. Raises ExamGenerationError on failure.

    Returns a list of {"prompt", "options", "correct_index"} dicts.
    """
    if settings.ai_fake_mode:
        return fake_mcq_questions(topic=topic, num_questions=num_questions)
    url = f"{settings.feedback_billing_url}/internal/generate-exam"
    token = _internal_token(acting_user_id)
    try:
        async with httpx.AsyncClient(timeout=100.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "topic": topic,
                    "num_questions": num_questions,
                    "difficulty": difficulty,
                    "language": language,
                    # Role context — lets the generator spread questions across
                    # the competencies this role is assessed on, matching the
                    # interview. Empty job_title = topic-only, as before.
                    "job_title": job_title,
                    "experience_level": experience_level,
                    # C3: the round's own criteria, when these questions
                    # are for a workflow round.
                    **({"round_rubric": round_rubric} if round_rubric else {}),
                },
            )
    except httpx.RequestError as exc:
        raise _unreachable(exc, what="Exam generation", url=url) from exc
    if resp.status_code != 200:
        raise _http_error(resp, what="exam generator")
    return _questions_from(resp, what="exam generator")


async def generate_coding_questions_remote(
    *,
    topic: str,
    num_questions: int,
    difficulty: str,
    language: str,
    allowed_languages: list[str],
    acting_user_id: str,
    job_title: str = "",
    experience_level: str = "mid",
    round_rubric: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Generate coding problems via feedback_billing. Raises ExamGenerationError.

    Returns a list of {"prompt", "reference_solution", "test_cases"} dicts where
    test_cases items are {"stdin", "expected_output", "is_sample", "weight"}.
    """
    if settings.ai_fake_mode:
        return fake_coding_questions(
            topic=topic, num_questions=num_questions, allowed_languages=allowed_languages
        )
    url = f"{settings.feedback_billing_url}/internal/generate-coding"
    token = _internal_token(acting_user_id)
    try:
        # Coding generation emits far more tokens than MCQs — allow extra time.
        async with httpx.AsyncClient(timeout=180.0) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "topic": topic,
                    "num_questions": num_questions,
                    "difficulty": difficulty,
                    "language": language,
                    "allowed_languages": allowed_languages,
                    "job_title": job_title,
                    "experience_level": experience_level,
                    # C3: the round's own criteria, when these questions
                    # are for a workflow round.
                    **({"round_rubric": round_rubric} if round_rubric else {}),
                },
            )
    except httpx.RequestError as exc:
        raise _unreachable(exc, what="Coding-question generation", url=url) from exc
    if resp.status_code != 200:
        raise _http_error(resp, what="coding generator")
    return _questions_from(resp, what="coding generator")
