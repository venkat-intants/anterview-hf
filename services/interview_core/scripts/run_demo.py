"""All-in-one live demo launcher.

1. mints LiveKit tokens
2. renders scripts/test_page.html -> interview_test.html (token injected, clean)
3. serves it on http://localhost:8080  (http origin => mic + WebRTC allowed)
4. runs the interview agent (waits for you to join, then speaks)

Run:  poetry run python scripts/run_demo.py
Then open:  http://localhost:8080/interview_test.html
Ctrl+C to stop.
"""

from __future__ import annotations

import asyncio
import http.server
import logging
import socketserver
import sys
import threading
import uuid
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import structlog  # noqa: E402
from livekit import api  # noqa: E402

from app.agent.livekit_agent import InterviewAgent  # noqa: E402
from app.config import settings  # noqa: E402
from app.llm import build_default_adapter  # noqa: E402

structlog.configure(
    processors=[structlog.processors.add_log_level, structlog.dev.ConsoleRenderer()],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)

PORT = 8080
TEMPLATE = Path(__file__).resolve().parent / "test_page.html"
OUT = ROOT / "interview_test.html"


def _mint(identity: str, name: str, room: str, mins: int = 60) -> str:
    return (
        api.AccessToken(settings.livekit_api_key, settings.livekit_api_secret)
        .with_identity(identity)
        .with_name(name)
        .with_ttl(timedelta(minutes=mins))
        .with_grants(
            api.VideoGrants(
                room_join=True, room=room, can_publish=True,
                can_subscribe=True, can_publish_data=True,
            )
        )
        .to_jwt()
    )


def _serve() -> None:
    handler = http.server.SimpleHTTPRequestHandler
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), handler) as httpd:
        httpd.serve_forever()


async def main() -> int:
    if not (settings.livekit_url and settings.livekit_api_key and settings.livekit_api_secret):
        print("ERROR: LiveKit not configured in .env")
        return 1

    room_name = f"demo-{uuid.uuid4().hex[:8]}"
    candidate_token = _mint(f"cand-{room_name}", "Candidate", room_name)
    agent_token = _mint(f"agent-{room_name}", "Interviewer", room_name)

    # Render the test page with the real URL + token (clean string replace).
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("__URL__", settings.livekit_url).replace("__TOKEN__", candidate_token)
    OUT.write_text(html, encoding="utf-8")

    # Serve over http so the browser allows mic + WebRTC.
    threading.Thread(target=_serve, daemon=True).start()

    print("=" * 72)
    print("  OPEN THIS IN YOUR BROWSER:")
    print(f"      http://localhost:{PORT}/interview_test.html")
    print("  Then click 'Join & Talk' and allow the microphone.")
    print(f"  Room: {room_name}  (avatar={settings.avatar_provider})")
    print("  Ctrl+C here to stop.")
    print("=" * 72)

    agent = InterviewAgent(
        session_id=str(uuid.uuid4()),
        job_id=str(uuid.uuid4()),
        job_title="Junior Python Developer",
        llm_adapter=build_default_adapter(),
        language="en",
        voice="kavya",
        max_turns=5,
    )
    await agent.run(agent_token)
    print("[demo] interview finished.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[demo] stopped.")
