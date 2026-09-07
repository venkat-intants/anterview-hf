from __future__ import annotations

import pathlib

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.security import (
    assert_strong_secrets,
    normalise_app_env,
    validate_cors_origins,
    validate_database_ssl,
)

# app/config.py -> app -> <service> -> services -> repo root
_SERVICE_DIR = pathlib.Path(__file__).resolve().parents[1]
_REPO_ROOT = _SERVICE_DIR.parents[1]


class Settings(BaseSettings):
    # ONE .env for the whole backend, at the repo root, plus an optional
    # per-service file that overrides it. Later files win (verified against
    # pydantic-settings, not assumed), so `services/<name>/.env` can still
    # differ where a service genuinely needs to — but the shared credentials
    # live in exactly one place instead of being copy-pasted four ways and
    # drifting.
    #
    # ABSOLUTE, not ".env". A relative path resolves against the CURRENT
    # WORKING DIRECTORY, so the old value silently loaded nothing whenever a
    # service was started from the repo root rather than its own folder — the
    # service then booted on defaults and failed later, somewhere unrelated.
    #
    # What must NOT go in the shared file: PORT and SERVICE_NAME. Both differ
    # per service (8001-8004), and a shared PORT would have all four fighting
    # over one socket.
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", _SERVICE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    service_name: str = "feedback_billing"
    app_env: str = "development"

    @field_validator("app_env", mode="before")
    @classmethod
    def _normalise_app_env(cls, v: object) -> str:
        """Lowercase/strip APP_ENV so `== "production"` gates cannot be bypassed
        by `APP_ENV=Production`. Shared implementation — see shared/security.py
        for why this must exist in every service, not just interview_core."""
        return normalise_app_env(v)
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8003

    database_url: str  # required — no default; service fails fast if unset
    database_pool_size: int = 5
    # When non-empty, asyncpg passes ssl="require" + statement_cache_size=0
    # (required for pgBouncer/Prisma pooled endpoints). Set DATABASE_SSL=require
    # in any cloud env. Leave blank for local Postgres (no SSL, no pgBouncer).
    # Required (not merely honoured) in production/staging — see
    # _validate_database_ssl below.
    database_ssl: str = ""

    redis_url: str  # required — no default; service fails fast if unset

    jwt_secret: str  # required — no default; service fails fast if unset (must match data_gateway)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "intants-data-gateway"
    jwt_audience: str = "intants-services"

    # Which provider serves the JSON-producing calls in this service: the
    # interview scorer, the resume/ATS scorer and the exam + coding generator.
    # Matches interview_core's LLM_PROVIDER so one env var moves the whole
    # platform rather than half of it.
    llm_provider: str = "gemini"

    # Gemini settings for end-of-session scorer (S5-006)
    gemini_api_key: str = ""
    # gemini-flash-lite-latest, which is also the default CLAUDE.md documents.
    # Was pinned to "gemini-2.5-flash", which Google has since RETIRED for new
    # users: every call returns HTTP 404 "no longer available to new users",
    # naming gemini-3.6-flash as the replacement. Verified live 2026-09-04 --
    # 2.5-flash and 2.5-flash-lite both 404; flash-lite-latest, 3.5-flash-lite,
    # 3.5-flash and 3.6-flash all answer.
    #
    # The floating "-latest" alias is deliberate here rather than a new hard
    # pin: a hard pin is what expired, silently, and the failure only surfaced
    # because a live integration test happened to run. flash-LITE also keeps
    # the per-session cost inside the <= Rs 12 cap that a full flash model
    # would eat into.
    #
    # NOTE this is the chat/completions model only. Embeddings are a separate
    # setting and were never affected -- semantic search kept working
    # throughout, which is part of why this went unnoticed.
    gemini_model: str = "gemini-flash-lite-latest"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # Groq — OpenAI-compatible chat completions. The model is NOT pinned to a
    # narrow default on purpose: availability is per-account on Groq and the
    # catalogue moves, and a pinned id an account cannot reach 404s on every
    # call.
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-120b"
    groq_api_base_url: str = "https://api.groq.com/openai/v1"

    # Embeddings for semantic resume search (HR workflow).
    # gemini-embedding-001 is free (no card) and emits up to 3072 dims via
    # outputDimensionality; 3072 is the native size and is already L2-normalized,
    # so it slots straight into the applicants.embedding halfvec(3072) column.
    #
    # NOT affected by LLM_PROVIDER. Groq serves no embeddings API, so semantic
    # applicant search keeps using Gemini even when everything else is on Groq
    # — which means GEMINI_API_KEY stays required for search alone. Stated here
    # because the failure is silent: search simply stops returning matches.
    embedding_model: str = "gemini-embedding-001"
    embedding_dimensions: int = 3072

    sentry_dsn: str = ""
    # Bearer token required by GET /metrics. Blank = unset, which keeps /metrics
    # open everywhere EXCEPT production, where shared/metrics_auth.py fails it
    # closed with a 404. Blank rather than None because .env.example ships
    # `METRICS_TOKEN=` and pydantic hands that through as "".
    metrics_token: str = ""
    cors_allowed_origins: str = "http://localhost:5173"

    @field_validator("cors_allowed_origins")
    @classmethod
    def _validate_cors_origins(cls, v: str) -> str:
        """Reject wildcard / non-http(s) origins (shared/security.py)."""
        return validate_cors_origins(v)

    # S3 / Cloudflare R2 settings for scorecard PDF storage
    s3_endpoint_url: str = ""
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_scorecard_bucket: str = "intants-interview-scorecards"

    @model_validator(mode="after")
    def validate_secret_strength(self) -> Settings:
        """Fail fast in production/staging if JWT_SECRET is a weak placeholder
        (must match data_gateway's). No-op in development/test."""
        assert_strong_secrets(self.app_env, {"JWT_SECRET": self.jwt_secret})
        return self

    @model_validator(mode="after")
    def _validate_database_ssl(self) -> Settings:
        """Refuse to start in production/staging with an unencrypted DB link.

        XS-04: this guard existed only in data_gateway, so the other three
        services — this one included, and it is the service that reads and
        writes scorecards — could boot against a plaintext Postgres link and say
        nothing (DPDP §8, CWE-319).

        Assigns the RETURN value rather than just calling for the side effect:
        the helper both enforces and normalises, stripping the
        ``loopback-exempt`` acknowledgement token that asyncpg cannot
        understand. ``object.__setattr__`` because a plain assignment inside a
        model validator re-enters validation under ``validate_assignment``;
        this model does not enable it today, but the shared helper documents
        this call shape and a future config flag should not turn it into a
        recursion bug.
        """
        object.__setattr__(
            self,
            "database_ssl",
            validate_database_ssl(self.app_env, self.database_ssl, self.database_url),
        )
        return self

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]

    # ── The active LLM, resolved once ────────────────────────────────────────
    #
    # Three properties rather than a per-call-site branch. The scorer, the
    # resume scorer and the exam generator all need the same triple, and a
    # provider check copy-pasted into each is how one of them ends up sending a
    # Groq key to Gemini's endpoint months later.

    @property
    def _is_groq(self) -> bool:
        return self.llm_provider.strip().lower() == "groq"

    @property
    def llm_api_key(self) -> str:
        return self.groq_api_key if self._is_groq else self.gemini_api_key

    @property
    def llm_model(self) -> str:
        return self.groq_model if self._is_groq else self.gemini_model

    @property
    def llm_api_base_url(self) -> str:
        return self.groq_api_base_url if self._is_groq else self.gemini_api_base_url


settings = Settings()
