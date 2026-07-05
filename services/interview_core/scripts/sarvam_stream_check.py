"""Manual check: Sarvam streaming STT connect + handshake (S4-004 diagnosis).

Run:  cd services/interview_core && .venv/Scripts/python.exe scripts/sarvam_stream_check.py

Uses SARVAM_API_KEY / SARVAM_STT_MODEL from .env. Exercises the REAL
SarvamStreamingSTT adapter against the live endpoint and prints the exact
connect result / error message (the WS handler only logs the error *type*, so
this surfaces the underlying HTTP status / reason).
"""

from __future__ import annotations

import asyncio

from app.config import settings
from app.speech.sarvam_stt_stream import SARVAM_STT_WS_URL, SarvamStreamingSTT


async def main() -> None:
    print(f"URL base : {SARVAM_STT_WS_URL}")
    key = settings.sarvam_api_key
    print(f"key set  : {bool(key)} (len={len(key)})")
    print(f"model    : {settings.sarvam_stt_model}")

    stream = SarvamStreamingSTT(api_key=key, model=settings.sarvam_stt_model)

    # 1. Connect / handshake — the part currently failing.
    try:
        await stream.start(language="en")
        print("CONNECT  : OK")
    except Exception as exc:  # noqa: BLE001 - diagnostic
        print(f"CONNECT FAILED: {type(exc).__name__}: {exc}")
        return

    # 2. Send ~0.2s of silence and finalize to confirm the audio-frame contract.
    try:
        await stream.send_audio(b"\x00\x00" * 3200)  # 0.2s @ 16kHz mono s16le
        final = await stream.finalize()
        print(f"STREAM   : OK (transcript_len={len(final)})")
    except Exception as exc:  # noqa: BLE001 - diagnostic
        print(f"STREAM FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
