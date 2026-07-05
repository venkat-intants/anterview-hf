from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.security import assert_strong_secrets


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    service_name: str = "interview_core"
    app_env: str = "development"
    log_level: str = "INFO"

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalise_app_env(cls, v: object) -> str:
        """Lowercase + strip ``app_env`` so security gates that match
        ``== "production"`` don't silently bypass on ``APP_ENV=Production``
        or ``APP_ENV=PROD``. S4-007 security-auditor follow-up question #1.
        """
        if not isinstance(v, str):
            return str(v)
        return v.strip().lower()
    host: str = "0.0.0.0"
    port: int = 8001

    database_url: str
    database_pool_size: int = 10
    # When non-empty, asyncpg passes ssl="require" + statement_cache_size=0
    # (required for pgBouncer/Prisma pooled endpoints). Set DATABASE_SSL=require
    # in any cloud env. Leave blank for local Postgres (no SSL, no pgBouncer).
    database_ssl: str = ""

    redis_url: str
    redis_session_ttl_seconds: int = 3600

    llm_provider: str = "gemini"

    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    # MUST be >=256 for thinking models (2.5-flash burns ~10-20 tokens on thoughts)
    gemini_max_tokens: int = 1024

    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_api_base_url: str = "https://api.groq.com/openai/v1"
    # Reuse the same token budget as Gemini — Groq is stateless per call.
    groq_max_tokens: int = 1024

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_max_tokens: int = 1024
    anthropic_temperature: float = 0.4
    anthropic_prompt_cache: bool = True

    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-south-1"
    bedrock_model_id: str = ""

    speech_stt_provider: str = "sarvam"
    speech_tts_provider: str = "sarvam"

    sarvam_api_key: str = ""
    # saaras:v3 is the current model (mode="transcribe"). saarika:v2.5 is
    # deprecating — do NOT revert. See research/sarvam-pricing-2026-05.md §4.
    sarvam_stt_model: str = "saaras:v3"
    sarvam_tts_model: str = "bulbul:v2"

    openai_api_key: str = ""
    openai_whisper_model: str = "whisper-1"

    elevenlabs_api_key: str = ""
    elevenlabs_voice_id_en: str = ""
    elevenlabs_voice_id_hi: str = ""
    elevenlabs_voice_id_te: str = ""
    elevenlabs_model: str = "eleven_turbo_v2_5"

    bhashini_user_id: str = ""
    bhashini_api_key: str = ""
    bhashini_inference_url: str = ""

    # S3 / object storage for audio recordings (S3-compatible).
    # All three connection fields are required — no baked defaults so a
    # misconfigured deployment fails fast rather than silently hitting MinIO.
    # CI provides these via env vars (see .github/workflows/ci.yml S3_* block).
    s3_endpoint: str  # required — no default; e.g. "http://localhost:9000" or R2 URL
    s3_region: str = "auto"
    s3_bucket_name: str = "intants-interview-audio"
    s3_access_key_id: str  # required — no default; baked "minioadmin" removed (leaked cred)
    s3_secret_access_key: str  # required — no default; baked "minioadmin" removed (leaked cred)
    s3_use_ssl: bool = False

    # --- Real-time transport (LiveKit) + Avatar (rebuild 2026-05-31) ---
    # See docs/ARCH-realtime-interview.md. D-ID + HeyGen removed; Simli/Tavus demo.
    # LiveKit WebRTC transport. Demo: LiveKit Cloud. Bid: self-host Mumbai.
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    # Graceful shutdown: on SIGTERM (deploy/restart), the LiveKit worker stops
    # accepting NEW interviews and DRAINS in-flight ones for up to this many
    # seconds before terminating them — so a redeploy doesn't drop a live
    # interview mid-turn. Terminated-at-drain jobs still run their close hooks
    # (status update + transcript persist + scoring). MUST be <= the worker's
    # compose stop_grace_period, else Docker SIGKILLs before the drain finishes.
    worker_drain_timeout_seconds: int = 120

    # Admission control: cap concurrent interview jobs to prevent OOM on the VM.
    # The LiveKit load_threshold (0.0–1.0) gates when the worker stops accepting
    # new jobs.  The max_concurrent_jobs ceiling is an additional application-level
    # guard enforced inside request_fnc; 0 = no cap (not recommended for prod).
    #
    # Sizing rationale for a 2-vCPU / 2 GB VM (Oracle Cloud Free Tier):
    #   Each interview job holds: one Silero VAD ONNX thread, one Sarvam STT
    #   WebSocket stream, one Sarvam TTS HTTP stream, one LLM HTTP connection,
    #   and one LiveKit WebRTC connection (≈ 80-150 MB RSS per job at p95).
    #   At p95 < 2 s latency, 2 vCPU safely handles up to 4 concurrent jobs.
    #   Beyond that, scheduling contention pushes turn latency over the 2 s SLA
    #   and combined RSS risks OOM-killing the entire worker process, dropping
    #   ALL live interviews simultaneously.
    #
    # RECONCILED CONCURRENCY VALUE: 4 (enforced here AND in job/container memory
    # defaults below).  All three values must agree — if you raise
    # worker_max_concurrent_jobs, also raise container_memory_limit_mb to at
    # least worker_max_concurrent_jobs * job_memory_limit_mb, or the memory gate
    # in request_fnc will silently reject below the advertised ceiling.
    # test_effective_cap_equals_configured_cap in test_worker_reliability.py
    # asserts this invariant automatically.
    worker_load_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    worker_max_concurrent_jobs: int = Field(default=4, ge=0)

    # Per-job soft memory ceiling in MB.  When non-zero, request_fnc checks
    # whether accepting one more job would push estimated RSS above the VM's
    # hard cap (container_memory_limit_mb).  Set to 0 to disable the check.
    # Recommended: ~150 MB incremental cost per job for the Sarvam + LLM +
    # LiveKit stack (the Silero VAD model, ≈100-200 MB, is prewarmed once and
    # shared across all jobs in the process — not counted per-job here).
    # Invariant: job_memory_limit_mb * worker_max_concurrent_jobs <=
    #            container_memory_limit_mb (or the memory gate fires first).
    job_memory_limit_mb: int = Field(default=150, ge=0)

    # Hard memory cap of the container/VM in MB.  Used with job_memory_limit_mb
    # to estimate whether a new job would OOM.  Must be >= job_memory_limit_mb *
    # worker_max_concurrent_jobs (4 * 150 = 600 MB) so all 4 slots are admitted.
    # Default 1800 MB gives ample headroom on the 2 GB Oracle Free Tier VM
    # (leaving ~200 MB for OS + prewarmed VAD model).
    container_memory_limit_mb: int = Field(default=1800, ge=0)

    # Heartbeat: path written by the asyncio liveness task in the worker process.
    # The deploy cluster's Docker compose healthcheck reads this file's mtime.
    worker_heartbeat_path: str = "/tmp/interview_worker_heartbeat"
    worker_heartbeat_interval_seconds: int = Field(default=15, ge=5)

    # AVATAR_PROVIDER selects the real-time avatar renderer injected into the
    # LiveKit worker. Valid values:
    #   none  — no avatar; voice-only (default, safe for CI / no-avatar envs)
    #   simli — Simli demo avatar (default demo avatar, US-hosted)
    #   tavus — Tavus demo avatar (US-hosted, demo-only, no India residency;
    #            persona MUST be in echo/livekit mode — see scripts/tavus_setup.py)
    #   custom — Three.js + Ready Player Me (production / bid path, Tier-2)
    avatar_provider: str = "none"

    # Simli (demo real-time avatar)
    simli_api_key: str = ""
    simli_face_id: str = ""
    simli_api_base_url: str = "https://api.simli.ai"

    # Tavus (demo real-time avatar — US-hosted, demo-only, no India residency)
    # Persona MUST be created with pipeline_mode="echo", transport_type="livekit"
    # so Tavus only lip-syncs our Sarvam audio and does NOT run its own LLM/TTS.
    # Use scripts/tavus_setup.py to list replicas and create the echo persona.
    tavus_api_key: str = ""
    tavus_replica_id: str = ""
    tavus_persona_id: str = ""
    tavus_api_url: str = "https://tavusapi.com"

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "intants-data-gateway"
    jwt_audience: str = "intants-services"

    feature_avatar_enabled: bool = True
    feature_voice_interruption: bool = True
    feature_multilingual: bool = True

    # S4-004: Streaming STT — default True (streaming path).
    # Operators can set STT_STREAMING_ENABLED=false to force the one-shot
    # batch path during Sarvam streaming-WS incidents without redeploying.
    stt_streaming_enabled: bool = True

    # S4-005: Streaming TTS — default True (parallel sentence TTS).
    # Operators can set TTS_STREAMING_ENABLED=false to revert to the
    # one-shot batch path (full LLM response → single Sarvam call) during
    # incidents or for debugging without redeploying.
    tts_streaming_enabled: bool = True

    # S4-012: Trusted reverse-proxy count.
    # Number of trusted reverse proxies sitting in front of this service.
    # Used by get_client_ip() to extract the real client IP from
    # X-Forwarded-For without blindly trusting attacker-controlled headers.
    # Set to 0 in dev (no proxy). Railway/Vercel: 1.
    trusted_proxy_count: int = Field(default=1, ge=0)  # negative rejected at startup

    sentry_dsn: str = ""

    # S5-006: feedback_billing service URL for fire-and-forget scoring calls.
    feedback_billing_url: str = "http://localhost:8003"

    cors_allowed_origins: str = "http://localhost:5173"

    @field_validator("cors_allowed_origins")
    @classmethod
    def validate_cors_origins(cls, v: str) -> str:
        """Reject wildcard origins when allow_credentials=True.

        RFC 6454 + CORS spec forbid combining credentials with '*'.
        Also validates each origin uses http:// or https:// scheme.
        """
        origins = [o.strip() for o in v.split(",") if o.strip()]
        for origin in origins:
            if origin in ("*", "null"):
                raise ValueError(
                    "CORS allow_credentials=True is incompatible with wildcard '*' origin"
                )
            if not (origin.startswith("http://") or origin.startswith("https://")):
                raise ValueError(
                    f"CORS origin {origin!r} must start with http:// or https://"
                )
        return v

    @model_validator(mode="after")
    def validate_secret_strength(self) -> "Settings":
        """Fail fast in production/staging if JWT_SECRET is a weak placeholder
        (must match data_gateway's). No-op in development/test."""
        assert_strong_secrets(self.app_env, {"JWT_SECRET": self.jwt_secret})
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

settings = Settings()  # type: ignore[call-arg]
