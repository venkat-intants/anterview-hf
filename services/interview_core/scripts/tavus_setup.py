"""Tavus setup helper — list replicas and create the echo-mode persona.

Usage (from the interview_core service root):

    # List available replicas so you can pick a face:
    poetry run python -m scripts.tavus_setup list

    # Create the Intants Interviewer echo persona (do this ONCE):
    poetry run python -m scripts.tavus_setup create-persona

After running create-persona, add the returned IDs to your .env:

    AVATAR_PROVIDER=tavus
    TAVUS_API_KEY=<your key>
    TAVUS_REPLICA_ID=<id from 'list' output>
    TAVUS_PERSONA_ID=<id returned by create-persona>

IMPORTANT: The persona MUST use pipeline_mode="echo" with transport_type="livekit".
This tells Tavus to act as a pure lip-sync renderer — it will NOT run its own
STT / LLM / TTS. Our Sarvam audio drives the lip-sync directly.

This script is intentionally standalone (no FastAPI / SQLAlchemy / LiveKit
imports) so it can run from any shell without starting the full service.

No key is hardcoded. TAVUS_API_KEY is read from the environment / .env file
via pydantic-settings (the same Settings class used by the worker).
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import httpx


def _get_settings() -> tuple[str, str]:
    """Return (api_key, api_url) from the service Settings (reads .env)."""
    # Import here so the module can be imported without a .env present (CI).
    from app.config import settings  # noqa: PLC0415

    api_key = settings.tavus_api_key
    api_url = settings.tavus_api_url.rstrip("/")
    return api_key, api_url


async def _list_replicas() -> None:
    """GET /v2/replicas — print available replicas with id, name, and status."""
    api_key, api_url = _get_settings()
    if not api_key:
        print(
            "ERROR: TAVUS_API_KEY is not set. Add it to your .env file and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    url = f"{api_url}/v2/replicas"
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, headers=headers)

    if response.status_code != 200:
        print(
            f"ERROR: Tavus API returned HTTP {response.status_code}:\n{response.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    data: Any = response.json()
    replicas: list[dict[str, Any]] = data if isinstance(data, list) else data.get("data", [])

    if not replicas:
        print("No replicas found. Create one in the Tavus dashboard first.")
        return

    print(f"Found {len(replicas)} replica(s):\n")
    for r in replicas:
        replica_id = r.get("replica_id") or r.get("id", "<unknown>")
        name = r.get("replica_name") or r.get("name", "<unnamed>")
        status = r.get("status", "<unknown>")
        thumbnail = r.get("thumbnail_video_url") or r.get("thumbnail", "")
        print(f"  replica_id : {replica_id}")
        print(f"  name       : {name}")
        print(f"  status     : {status}")
        if thumbnail:
            print(f"  thumbnail  : {thumbnail}")
        print()

    print("Put your chosen replica_id into .env as:  TAVUS_REPLICA_ID=<replica_id>")


async def _create_persona() -> None:
    """POST /v2/personas — create the Intants Interviewer echo-mode persona.

    pipeline_mode="echo" + transport_type="livekit" means Tavus ONLY lip-syncs
    audio we push into the room — it does NOT run its own STT/LLM/TTS.

    This call is idempotent-safe to run multiple times; each run creates a NEW
    persona. Keep the returned persona_id; do not run this again if you already
    have one.
    """
    api_key, api_url = _get_settings()
    if not api_key:
        print(
            "ERROR: TAVUS_API_KEY is not set. Add it to your .env file and retry.",
            file=sys.stderr,
        )
        sys.exit(1)

    url = f"{api_url}/v2/personas"
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    body: dict[str, Any] = {
        "persona_name": "Intants Interviewer",
        "pipeline_mode": "echo",
        "layers": {
            "transport": {
                "transport_type": "livekit",
            },
        },
    }

    print("Creating persona...")
    print(f"POST {url}")
    print(f"Body: {json.dumps(body, indent=2)}\n")

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=body, headers=headers)

    if response.status_code not in (200, 201):
        print(
            f"ERROR: Tavus API returned HTTP {response.status_code}:\n{response.text}",
            file=sys.stderr,
        )
        sys.exit(1)

    data: dict[str, Any] = response.json()
    persona_id: str = data.get("persona_id") or data.get("id", "<check response below>")

    print("Persona created successfully!")
    print(f"\n  persona_id : {persona_id}\n")
    print("Full response:")
    print(json.dumps(data, indent=2))
    print()
    print("Add these to your .env:")
    print(f"  TAVUS_PERSONA_ID={persona_id}")
    print("  TAVUS_REPLICA_ID=<replica_id from 'list' command>")
    print("  AVATAR_PROVIDER=tavus")


def _usage() -> None:
    print(
        "Usage:\n"
        "  poetry run python -m scripts.tavus_setup list\n"
        "  poetry run python -m scripts.tavus_setup create-persona\n",
        file=sys.stderr,
    )
    sys.exit(1)


async def _main() -> None:
    if len(sys.argv) < 2:
        _usage()

    command = sys.argv[1].lower()
    if command == "list":
        await _list_replicas()
    elif command in ("create-persona", "create_persona"):
        await _create_persona()
    else:
        print(f"Unknown command: {sys.argv[1]!r}", file=sys.stderr)
        _usage()


if __name__ == "__main__":
    asyncio.run(_main())
