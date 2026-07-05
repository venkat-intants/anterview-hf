"""Integration-test conftest.

The two former autouse fixtures (``_install_canned_llm_adapter`` and
``_default_tts_adapter_unavailable``) were removed 2026-05-31 together with the
WebSocket turn-loop they supported (``app.routers.ws``) and the shared
``_ws_fixtures`` helper module. The real-time interview transport
(LiveKit/Pipecat) + avatar layer are being rebuilt from scratch; new
transport-level fixtures will be added here when that work lands.

The retained integration tests (sessions CRUD, cross-service JWT, live
Sarvam/Gemini) do not depend on those fixtures — they either exercise pure
HTTP endpoints via ``TestClient`` or construct the relevant adapter directly.
"""

from __future__ import annotations
