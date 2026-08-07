from __future__ import annotations

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.security import assert_strong_secrets, normalise_app_env, validate_cors_origins


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
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
    database_ssl: str = ""

    redis_url: str  # required — no default; service fails fast if unset

    jwt_secret: str  # required — no default; service fails fast if unset (must match data_gateway)
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "intants-data-gateway"
    jwt_audience: str = "intants-services"

    # Gemini settings for end-of-session scorer (S5-006)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # Embeddings for semantic resume search (HR workflow).
    # gemini-embedding-001 is free (no card) and emits up to 3072 dims via
    # outputDimensionality; 3072 is the native size and is already L2-normalized,
    # so it slots straight into the applicants.embedding halfvec(3072) column.
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

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


settings = Settings()
