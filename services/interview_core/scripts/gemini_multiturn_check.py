"""Day-1 model compat gate (Sprint 1 retro action item).

Runs a 3-turn synthetic interview against gemini-2.5-flash via the REST
generateContent endpoint to confirm multi-turn conversation works on the free
tier with our current MAX_TOKENS budget before we wire LLM calls into the
LangGraph nodes in S2-005.

USAGE
-----
    cd services/interview_core
    poetry run python scripts/gemini_multiturn_check.py

Exits 0 on success, 1 on any failure (empty output, MAX_TOKENS truncation,
HTTP error, exception). On failure the founder must choose:
  (a) enable Gemini billing,
  (b) switch to ``gemini-flash-lite-latest`` (non-thinking, smaller quality),
  (c) hardcoded question sequence fallback for the demo.

DO NOT silently switch models inside this script — the failure mode itself
is the signal product needs.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx

# Make ``app.config`` importable when run as ``python scripts/...``
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

# The configured env default (1024) is a floor for production prompts where the
# system prompt is short. The Day-1 gate uses a generous budget so we can
# distinguish "model is fundamentally broken for multi-turn" from "prompt was
# too cramped". Tune the env value upward separately once real prompts land.
MAX_OUTPUT_TOKENS_FOR_CHECK: int = max(2048, settings.gemini_max_tokens)


SCRIPTED_USER_TURNS: list[str] = [
    "I'm a candidate applying for a Junior Java Developer role. "
    "Interview me with one question.",
    "I have 1 year of experience using Spring Boot in college projects.",
    "I used JPA with PostgreSQL.",
]


def _extract_text(candidate: dict[str, Any]) -> str:
    parts = candidate.get("content", {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict))


async def run_multiturn() -> int:
    if not settings.gemini_api_key:
        print("FAIL: GEMINI_API_KEY not set in environment", file=sys.stderr)
        return 1

    url = (
        f"{settings.gemini_api_base_url}/models/"
        f"{settings.gemini_model}:generateContent?key={settings.gemini_api_key}"
    )

    # Persistent chat history in Gemini ``contents`` format. After each turn we
    # append the model's reply so the next request carries the full transcript.
    contents: list[dict[str, Any]] = []
    failures: list[str] = []

    print(f"Model:       {settings.gemini_model}")
    print(f"MaxTokens:   {MAX_OUTPUT_TOKENS_FOR_CHECK} (env default: {settings.gemini_max_tokens})")
    print(f"Endpoint:    {settings.gemini_api_base_url}/models/<model>:generateContent")
    print("-" * 78)

    async with httpx.AsyncClient(timeout=30.0) as client:
        for turn_idx, user_text in enumerate(SCRIPTED_USER_TURNS, start=1):
            contents.append({"role": "user", "parts": [{"text": user_text}]})

            payload = {
                "contents": contents,
                "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS_FOR_CHECK},
            }

            try:
                response = await client.post(url, json=payload)
            except httpx.HTTPError as exc:
                msg = f"turn {turn_idx}: HTTP transport error: {type(exc).__name__}: {exc}"
                print(f"FAIL: {msg}", file=sys.stderr)
                failures.append(msg)
                break

            if response.status_code != 200:
                msg = (
                    f"turn {turn_idx}: HTTP {response.status_code} "
                    f"body={response.text[:400]}"
                )
                print(f"FAIL: {msg}", file=sys.stderr)
                failures.append(msg)
                break

            data = response.json()
            candidates = data.get("candidates") or []
            usage = data.get("usageMetadata", {}) or {}

            prompt_tokens = usage.get("promptTokenCount")
            candidates_tokens = usage.get("candidatesTokenCount")
            thoughts_tokens = usage.get("thoughtsTokenCount")

            if not candidates:
                msg = (
                    f"turn {turn_idx}: no candidates in response "
                    f"body={json.dumps(data)[:400]}"
                )
                print(f"FAIL: {msg}", file=sys.stderr)
                failures.append(msg)
                break

            candidate = candidates[0]
            finish_reason = candidate.get("finishReason")
            text_out = _extract_text(candidate)

            print(f"Turn {turn_idx}")
            print(f"  user:              {user_text}")
            print(f"  prompt_tokens:     {prompt_tokens}")
            print(f"  candidates_tokens: {candidates_tokens}")
            print(f"  thoughts_tokens:   {thoughts_tokens}")
            print(f"  finish_reason:     {finish_reason}")
            print(f"  output:            {text_out!r}")
            print()

            if finish_reason == "MAX_TOKENS":
                msg = (
                    f"turn {turn_idx}: finishReason=MAX_TOKENS — "
                    f"model spent budget on thoughts; raise GEMINI_MAX_TOKENS"
                )
                print(f"FAIL: {msg}", file=sys.stderr)
                failures.append(msg)
                break

            if not text_out.strip():
                msg = (
                    f"turn {turn_idx}: empty output, finishReason={finish_reason}"
                )
                print(f"FAIL: {msg}", file=sys.stderr)
                failures.append(msg)
                break

            # Append the model's reply so subsequent turns see the full convo.
            contents.append({"role": "model", "parts": [{"text": text_out}]})

    if failures:
        print("-" * 78)
        print(f"RESULT: FAIL ({len(failures)} failure(s))")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("-" * 78)
    print("RESULT: PASS — gemini-2.5-flash multi-turn works on free tier.")
    return 0


def main() -> None:
    rc = asyncio.run(run_multiturn())
    sys.exit(rc)


if __name__ == "__main__":
    main()
