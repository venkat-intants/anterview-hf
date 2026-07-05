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
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _check_redis() -> dict[str, Any]:
    try:
        client = aioredis.from_url(settings.redis_url, decode_responses=True)  # type: ignore[no-untyped-call]
        pong = await client.ping()
        await client.aclose()
        return {"ok": pong is True}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


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
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


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
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


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
            return {"ok": False, "status": r.status_code, "body": r.text[:300]}
        data = r.json()
        candidates = data.get("candidates") or []
        if not candidates:
            return {"ok": False, "error": "no candidates in response", "body": str(data)[:300]}
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
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


async def _check_llm() -> dict[str, Any]:
    if settings.llm_provider == "gemini":
        return await _check_gemini()
    if settings.llm_provider == "anthropic":
        return await _check_anthropic()
    return {"ok": False, "error": f"Unknown LLM_PROVIDER: {settings.llm_provider}"}


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
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


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
