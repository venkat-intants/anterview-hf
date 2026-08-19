"""Deep health checks for interview_core.

Response-body rule (S-6, CWE-209): ``/health/deep`` is UNAUTHENTICATED, so a
failing check may return only the exception *type*. Driver exceptions embed the
thing that failed to connect — asyncpg puts the Neon host, port, user and
database in ``str(exc)``; botocore names the R2 endpoint and bucket; httpx
echoes the full request URL, which for Gemini carries ``?key=<API key>``. Full
detail goes to structlog, where operators can see it and the internet cannot.
This mirrors the convention already in services/admin_ops/app/health.py.
"""

import asyncio
from typing import Any

import boto3
import httpx
import redis.asyncio as aioredis
import structlog
from anthropic import AsyncAnthropic
from botocore.client import Config as BotoConfig
from fastapi import APIRouter
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.sql import text

from app.config import settings

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/health", tags=["health"])


async def _check_postgres() -> dict[str, Any]:
    try:
        engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            row = result.scalar()
            ext_result = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname='vector'")
            )
            has_vector = ext_result.first() is not None
        await engine.dispose()
        return {"ok": row == 1, "pgvector": has_vector}
    except Exception as exc:
        log.warning("health.postgres.fail", exc_type=type(exc).__name__, exc_msg=str(exc))
        return {"ok": False, "error": type(exc).__name__}


async def _check_redis() -> dict[str, Any]:
    try:
        client = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
        pong = await client.ping()
        await client.aclose()
        return {"ok": pong is True}
    except Exception as exc:
        log.warning("health.redis.fail", exc_type=type(exc).__name__, exc_msg=str(exc))
        return {"ok": False, "error": type(exc).__name__}


async def _check_s3() -> dict[str, Any]:
    try:
        s3 = await asyncio.to_thread(
            lambda: boto3.client(
                "s3",
                endpoint_url=settings.s3_endpoint or None,
                region_name=settings.s3_region,
                aws_access_key_id=settings.s3_access_key_id,
                aws_secret_access_key=settings.s3_secret_access_key,
                use_ssl=settings.s3_use_ssl,
                config=BotoConfig(signature_version="s3v4"),
            )
        )
        head = await asyncio.to_thread(lambda: s3.head_bucket(Bucket=settings.s3_bucket_name))
        return {
            "ok": head["ResponseMetadata"]["HTTPStatusCode"] == 200,
            "bucket": settings.s3_bucket_name,
        }
    except Exception as exc:
        log.warning("health.s3.fail", exc_type=type(exc).__name__, exc_msg=str(exc))
        return {"ok": False, "error": type(exc).__name__}


async def _check_anthropic() -> dict[str, Any]:
    if not settings.anthropic_api_key:
        return {"ok": False, "error": "ANTHROPIC_API_KEY not set"}
    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await client.messages.create(
            model=settings.anthropic_model,
            max_tokens=16,
            messages=[{"role": "user", "content": "Reply with the single word: PING"}],
        )
        text_out = "".join(b.text for b in resp.content if hasattr(b, "text"))
        return {"ok": "PING" in text_out.upper(), "model": settings.anthropic_model}
    except Exception as exc:
        log.warning("health.anthropic.fail", exc_type=type(exc).__name__, exc_msg=str(exc))
        return {"ok": False, "error": type(exc).__name__}


async def _check_gemini() -> dict[str, Any]:
    if not settings.gemini_api_key:
        return {"ok": False, "error": "GEMINI_API_KEY not set"}
    try:
        url = (
            f"{settings.gemini_api_base_url}/models/"
            f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
        )
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                url,
                json={
                    "contents": [{"parts": [{"text": "Reply with the single word: PING"}]}],
                    "generationConfig": {"maxOutputTokens": settings.gemini_max_tokens},
                },
            )
        if r.status_code != 200:
            # Same rule as the except blocks: the upstream body is a third-party
            # error document that has already been observed to echo the request
            # (and the request URL carries ?key=<GEMINI_API_KEY>). Status code is
            # enough to triage from an unauthenticated endpoint; the body goes to
            # the log.
            log.warning("health.gemini.http_error", status=r.status_code, body=r.text[:300])
            return {"ok": False, "status": r.status_code}
        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            log.warning("health.gemini.no_candidates", body=str(data)[:300])
            return {"ok": False, "error": "no candidates in response"}
        finish_reason = candidates[0].get("finishReason")
        if finish_reason == "MAX_TOKENS":
            usage = data.get("usageMetadata", {})
            return {
                "ok": False,
                "error": "MAX_TOKENS — model used all output budget on thoughts",
                "thoughtsTokens": usage.get("thoughtsTokenCount"),
                "candidatesTokens": usage.get("candidatesTokenCount"),
                "hint": "Increase gemini_max_tokens (current: "
                + str(settings.gemini_max_tokens) + ")",
            }
        parts = candidates[0].get("content", {}).get("parts") or []
        text_out = "".join(p.get("text", "") for p in parts)
        if not text_out:
            return {"ok": False, "error": f"empty output, finishReason={finish_reason}"}
        return {"ok": "PING" in text_out.upper(), "model": settings.gemini_model}
    except Exception as exc:
        log.warning("health.gemini.fail", exc_type=type(exc).__name__, exc_msg=str(exc))
        return {"ok": False, "error": type(exc).__name__}


async def _check_groq() -> dict[str, Any]:
    """Verify the key AND that the configured model exists on this account.

    Both halves matter, and the second is the one that bites. Model
    availability on Groq is per-account and the catalogue changes; a model id
    that the account cannot reach returns 404 on every call. In the interview
    turn loop that surfaces as an avatar which simply never speaks, while the
    room, the video and the audio pipeline all look perfectly healthy — so the
    health endpoint is the only place that can name the real cause.
    """
    if not settings.groq_api_key:
        return {"ok": False, "error": "GROQ_API_KEY not set"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                json={
                    "model": settings.groq_model,
                    "messages": [{"role": "user", "content": "Reply with: PING"}],
                    # Generous on purpose. gpt-oss-* are REASONING models: they
                    # spend output tokens thinking before emitting any text, so
                    # a tight cap returns finish_reason=length with an empty
                    # string — indistinguishable from a broken model. Measured:
                    # gpt-oss-120b needs ~186 tokens before its first character
                    # at default effort. reasoning_effort=low cuts that to ~41
                    # and is what a latency-bound health probe wants.
                    "max_tokens": 512,
                    "reasoning_effort": "low",
                },
            )
        if resp.status_code == 401:
            return {"ok": False, "error": "invalid GROQ_API_KEY", "model": settings.groq_model}
        if resp.status_code == 404:
            return {
                "ok": False,
                "error": "model not available on this Groq account",
                "model": settings.groq_model,
                "hint": "GET /openai/v1/models to list what this key can reach",
            }
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}", "model": settings.groq_model}
        choices = resp.json().get("choices") or []
        text_out = choices[0].get("message", {}).get("content", "") if choices else ""
        if not text_out.strip():
            # gpt-oss-20b does this: 200 OK with an empty completion, which the
            # turn loop cannot distinguish from a model that had nothing to say.
            return {"ok": False, "error": "empty completion", "model": settings.groq_model}
        return {"ok": True, "model": settings.groq_model}
    except Exception as exc:
        log.warning("health.groq.fail", exc_type=type(exc).__name__, exc_msg=str(exc))
        return {"ok": False, "error": type(exc).__name__}


async def _check_llm() -> dict[str, Any]:
    """Check the provider this deployment actually talks to.

    NOTE: the live interview turn loop wires Groq DIRECTLY in
    ``worker/interview_worker.py`` and does not consult ``LLM_PROVIDER`` — so
    with any other value set, this check covers a path the interview does not
    use. Groq is checked unconditionally for that reason.
    """
    checks: dict[str, Any] = {}

    # Always checked: the turn loop uses it no matter what LLM_PROVIDER says.
    checks["groq"] = await _check_groq()

    if settings.llm_provider == "gemini":
        checks["gemini"] = await _check_gemini()
    elif settings.llm_provider == "anthropic":
        checks["anthropic"] = await _check_anthropic()
    elif settings.llm_provider != "groq":
        checks["provider"] = {
            "ok": False,
            "error": f"Unknown LLM_PROVIDER: {settings.llm_provider}",
        }

    if len(checks) == 1:
        return checks["groq"]
    return {"ok": all(c.get("ok") for c in checks.values()), **checks}


async def _check_sarvam() -> dict[str, Any]:
    if not settings.sarvam_api_key:
        return {"ok": False, "error": "SARVAM_API_KEY not set"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(
                "https://api.sarvam.ai/text-to-speech",
                headers={"api-subscription-key": settings.sarvam_api_key},
                json={
                    "inputs": ["hi"],
                    "target_language_code": "en-IN",
                    "model": settings.sarvam_tts_model,
                },
            )
        return {"ok": r.status_code in (200, 201), "status": r.status_code}
    except Exception as exc:
        log.warning("health.sarvam.fail", exc_type=type(exc).__name__, exc_msg=str(exc))
        return {"ok": False, "error": type(exc).__name__}


@router.get("/live")
async def liveness() -> dict[str, str]:
    return {"status": "alive"}


@router.get("/deep")
async def deep_health() -> dict[str, Any]:
    postgres, redis_res, s3, llm, sarvam = await asyncio.gather(
        _check_postgres(),
        _check_redis(),
        _check_s3(),
        _check_llm(),
        _check_sarvam(),
    )
    checks = {
        "postgres": postgres,
        "redis": redis_res,
        "s3": s3,
        f"llm.{settings.llm_provider}": llm,
        "sarvam": sarvam,
    }
    all_ok = all(c.get("ok") for c in checks.values())
    return {"status": "healthy" if all_ok else "degraded", "checks": checks}
