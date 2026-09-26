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

    service_name: str = "admin_ops"
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
    port: int = 8004

    database_url: str  # required — no default; service fails fast if unset
    database_pool_size: int = 5
    # When non-empty, asyncpg passes ssl="require" + statement_cache_size=0
    # (required for pgBouncer/Prisma pooled endpoints). Set DATABASE_SSL=require
    # in any cloud env. Leave blank for local Postgres (no SSL, no pgBouncer).
    # REQUIRED in production/staging — see _validate_database_ssl below. Set it
    # to "loopback-exempt" to acknowledge in writing that TLS is terminated
    # upstream; that sentinel is stripped before it reaches the driver.
    database_ssl: str = ""

    redis_url: str  # required — no default; service fails fast if unset

    jwt_secret: str  # required — no default; service fails fast if unset
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "intants-data-gateway"
    jwt_audience: str = "intants-services"

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

    # DPDP erasure executor — how often (seconds) to check for due requests.
    # 300 -> 900 on 2026-09-26, for the compute-hours reason documented at
    # data_gateway's email_poll_interval_seconds.
    #
    # Weighed separately from the other pollers, because it is the only one
    # with a statutory clock behind it. It moves NO DPDP deadline: the executor
    # honours each request's own due time, and an erasure REQUEST already takes
    # effect immediately everywhere it is visible — the consent ledger is
    # revoked at request time and every read joins that. This interval decides
    # only how soon the physical purge follows, and the Act works in days.
    # Reduce in staging/testing; never below 60 s in prod.
    erasure_poll_interval_seconds: int = 900

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
    # PRODUCTION MUST SET S3_ENDPOINT_URL AND S3_ACCESS_KEY_ID (and the secret).
    # This comment used to say the executor "skips S3 deletes and logs a warning
    # instead of failing the erasure". It does the opposite, deliberately:
    # s3_client.delete_objects raises StorageNotConfiguredError when EITHER is
    # missing and there are objects to delete, the transaction rolls back, and
    # the request stays 'pending' and is retried -- because an erasure that
    # claims completion while the candidate's files are still in the bucket is
    # a DPDP s.12 failure we would then be reporting as a success. An erasure
    # with no objects to delete completes without storage configured, which is
    # why local dev and CI can leave these blank.
    s3_endpoint_url: str = ""          # e.g. https://<acct>.r2.cloudflarestorage.com
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    # Bucket that holds scorecard PDFs + transcript JSON (feedback_billing writes here).
    s3_scorecard_bucket: str = "intants-interview-scorecards"
    # Bucket that holds resume PDFs + JD documents (data_gateway writes here).
    s3_bucket_name: str = "intants-uploads"

    @model_validator(mode="after")
    def _validate_database_ssl(self) -> Settings:
        """Refuse to start in production/staging with a plaintext DB link.

        admin_ops reads every table the erasure executor touches, so an unset
        DATABASE_SSL here means candidate names, emails and transcripts crossing
        the WAN in cleartext (DPDP §8, CWE-319). Shared implementation so all
        four services enforce it identically — and so the ``loopback-exempt``
        acknowledgement token is stripped by the same code that accepts it,
        rather than being handed on to asyncpg, which cannot understand it.

        ``object.__setattr__`` because a plain assignment inside a model
        validator re-enters validation when ``validate_assignment`` is on.
        """
        object.__setattr__(
            self,
            "database_ssl",
            validate_database_ssl(self.app_env, self.database_ssl, self.database_url),
        )
        return self

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
