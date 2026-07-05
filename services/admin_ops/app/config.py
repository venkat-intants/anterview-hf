from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.security import assert_strong_secrets


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    service_name: str = "admin_ops"
    app_env: str = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8004

    database_url: str  # required — no default; service fails fast if unset
    database_pool_size: int = 5
    # When non-empty, asyncpg passes ssl="require" + statement_cache_size=0
    # (required for pgBouncer/Prisma pooled endpoints). Set DATABASE_SSL=require
    # in any cloud env. Leave blank for local Postgres (no SSL, no pgBouncer).
    database_ssl: str = ""

    redis_url: str  # required — no default; service fails fast if unset

    jwt_secret: str  # required — no default; service fails fast if unset
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "intants-data-gateway"
    jwt_audience: str = "intants-services"

    sentry_dsn: str = ""
    cors_allowed_origins: str = "http://localhost:5173"

    # DPDP erasure executor — how often (seconds) to check for due requests.
    # Default 300 s (5 min); reduce in staging/testing; never below 60 s in prod.
    erasure_poll_interval_seconds: int = 300

    # Peer microservice base URLs — used by the /admin/system/health aggregator.
    # Defaults to localhost dev ports; override in cloud via env. All optional so a
    # stale .env never crashes startup.
    interview_core_url: str = "http://localhost:8001"
    data_gateway_url: str = "http://localhost:8002"
    feedback_billing_url: str = "http://localhost:8003"

    # --- Object storage (S3-compatible) for DPDP erasure file purge ---
    # The erasure executor deletes scorecard PDFs + transcripts (s3_scorecard_bucket)
    # and resume files (s3_bucket_name) from object storage as part of §12 compliance.
    # Mirror the same env vars used by feedback_billing (S3_ENDPOINT_URL,
    # S3_SCORECARD_BUCKET) and data_gateway (S3_BUCKET_NAME).
    # Leave all blank in local dev/CI — the executor skips S3 deletes when no
    # endpoint is configured and logs a warning instead of failing the erasure.
    s3_endpoint_url: str = ""          # e.g. https://<acct>.r2.cloudflarestorage.com
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # Bucket that holds scorecard PDFs + transcript JSON (feedback_billing writes here).
    s3_scorecard_bucket: str = "intants-interview-scorecards"
    # Bucket that holds resume PDFs + JD documents (data_gateway writes here).
    s3_bucket_name: str = "intants-uploads"

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
