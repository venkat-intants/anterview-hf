from typing import Literal

import structlog
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.security import assert_strong_secrets

log = structlog.get_logger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    service_name: str = "data_gateway"
    app_env: str = "development"
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8002

    database_url: str
    database_pool_size: int = 10
    # When non-empty, asyncpg passes ssl="require" + statement_cache_size=0
    # (required for pgBouncer/Prisma pooled endpoints). Set DATABASE_SSL=require
    # in any cloud env. Leave blank for local Postgres (no SSL, no pgBouncer).
    database_ssl: str = ""

    redis_url: str

    auth_provider: str = "local"

    jwt_secret: str
    jwt_algorithm: str = "HS256"
    jwt_expiry_hours: int = 24
    jwt_issuer: str = "intants-data-gateway"
    jwt_audience: str = "intants-services"
    jwt_refresh_expiry_days: int = 7
    password_hash_rounds: int = 12

    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    google_oauth_redirect_uri: str = ""

    keycloak_issuer_url: str = ""
    keycloak_client_id: str = ""
    keycloak_client_secret: str = ""
    keycloak_redirect_uri: str = ""

    naipunyam_saml_metadata_url: str = ""
    naipunyam_saml_entity_id: str = ""
    naipunyam_saml_acs_url: str = ""
    naipunyam_saml_cert_path: str = ""
    naipunyam_api_base_url: str = ""
    naipunyam_api_key: str = ""
    # OAuth2 client_credentials for the Naipunyam REST API (S5-003a).
    # Left empty until APSSDC issues credentials during the bid process.
    naipunyam_client_id: str = ""
    naipunyam_client_secret: str = ""

    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    email_from: str = "noreply@intants.com"
    email_from_name: str = "Intants AI Interview"
    # Optional Reply-To shown to recipients (e.g. support@intants.com). Blank = none.
    email_reply_to: str = ""

    # --- Transactional email outbox / worker (production email system) ---
    # Every outbound email is persisted to email_events (durable outbox + audit
    # log of sent/failed). A background worker drains queued/failed-retriable rows
    # with exponential backoff so a flaky relay never drops an invite or reset link
    # and never blocks a request. Set EMAIL_OUTBOX_ENABLED=false to disable the
    # worker (rows still queue; useful in tests / one-off scripts).
    email_outbox_enabled: bool = True
    # Worker poll cadence. Small enough that password-reset links feel instant.
    email_poll_interval_seconds: int = 5
    # Rows claimed per drain tick (bounds burst send rate against the relay).
    email_batch_size: int = 20
    # Give up (status='failed', no further retry) after this many attempts.
    email_max_attempts: int = 6
    # Daily purge horizon for delivered/failed rows (the rendered body — which may
    # carry a live magic link — is nulled on success immediately; this drops the
    # whole audit row later). DPDP-aligned; runs on the existing retention cron.
    email_retention_days: int = 30
    # Global kill-switch for the optional per-user login-notification email. When
    # false, no login alerts are sent regardless of the per-user preference.
    email_login_alerts_enabled: bool = True
    # Public frontend base URL for auth links (verify email, reset password). The
    # candidate magic-link bases (exam_link_base_url / interview_link_base_url) are
    # kept separate so they can point at a different host if ever needed.
    app_base_url: str = "http://localhost:5173"

    # --- Auth email tokens (password reset + email verification) ---
    # SEPARATE from jwt_secret / exam_link_secret / interview_link_secret. Left
    # blank by default: when blank, a namespaced secret is DERIVED from jwt_secret
    # (see app.auth_tokens) so the feature works out-of-the-box without a new
    # required env var, while still never reusing jwt_secret verbatim. Set these
    # explicitly in production for independent rotation.
    password_reset_secret: str = ""
    password_reset_ttl_hours: int = 1
    email_verify_secret: str = ""
    # 7 days — verification window.
    email_verify_ttl_hours: int = 168
    # When True, email verification is MANDATORY: self-registered accounts are not
    # auto-logged-in and cannot sign in until they confirm their email. Existing
    # accounts and admin-provisioned accounts (still on their bootstrap password)
    # are exempt so enabling this never locks anyone out. Default False (the smooth,
    # non-blocking flow).
    email_verification_required: bool = False

    rate_limit_login_per_minute: int = 5
    rate_limit_api_per_minute: int = 60

    # feedback_billing base URL — data_gateway calls /internal/score-resume here
    # to ATS-score applicant resumes (HR workflow Phase 1).
    feedback_billing_url: str = "http://localhost:8003"

    # --- Exam magic-link (HR workflow Phase 2) ---
    # SEPARATE signing secret — NEVER reuse jwt_secret. A leaked exam secret must
    # not be able to forge user sessions. Required, no default (explicit secret mgmt).
    exam_link_secret: str
    # Base URL the candidate's browser hits; the HR mint builds {base}/exam#<token>.
    exam_link_base_url: str = "http://localhost:5173"
    # Magic-link lifetime in HOURS (not days) — short windows limit a leaked link.
    exam_link_ttl_hours: int = 72
    # Grace window (seconds) added to the server-side time-limit deadline at submit.
    exam_submit_grace_seconds: int = 30
    # Cap on the submitted-answers payload size (DoS guard).
    exam_max_answers: int = 500
    # Join window (MINUTES) for the FIRST /start of a SCHEDULED round, measured from
    # exam_assignments.scheduled_at. Unscheduled rounds may start any time before the
    # link expires. Mirrors interview_join_window_minutes (exams run longer → wider).
    exam_join_window_minutes: int = 30
    # Number of proctoring violations (fullscreen-exit / tab-switch) after which the
    # exam auto-submits. Surfaced to the candidate UI; enforced client-side, logged
    # server-side via /exam/integrity-event.
    exam_integrity_max_violations: int = 3

    # --- Coding round — code execution (HR workflow Phase 2) ---
    # Swappable provider:
    #   'jdoodle' (default) — hosted API, NO VM needed; free tier ~200 runs/day.
    #   'piston'            — self-hosted sandbox; needs a VM (set PISTON_API_URL).
    execution_provider: str = "jdoodle"
    # JDoodle hosted execution (get free credentials at https://www.jdoodle.com/).
    jdoodle_client_id: str = ""
    jdoodle_client_secret: str = ""
    jdoodle_api_url: str = "https://api.jdoodle.com/v1"
    # Piston (only used when execution_provider='piston').
    piston_api_url: str = "https://emkc.org/api/v2/piston"
    # Per-run wall-clock cap (ms) passed to the runner; also our own client timeout.
    code_run_timeout_ms: int = 5000
    # DoS guards. Grading runs each round's test cases with BOUNDED concurrency
    # (code_grade_concurrency) rather than fully sequentially, so the test-case cap
    # bounds cost but no longer serialises the whole submit. max test cases /
    # question, max questions / coding exam, max submitted source size.
    code_max_test_cases: int = 20
    code_max_questions_per_exam: int = 20
    code_max_source_bytes: int = 64_000
    # Cap on candidate-supplied stdin for the "Run with custom input" feature.
    code_max_stdin_bytes: int = 20_000
    # Max concurrent execution-provider calls per grading pass. >1 grades a round's
    # test cases in parallel (cutting the time the submit holds its DB row-lock +
    # connection); kept modest so we stay within JDoodle's free-tier rate limits.
    # Set to 1 to restore the old strictly-sequential behaviour.
    code_grade_concurrency: int = 4

    # --- Interview invite magic-link (HR workflow Phase 3) ---
    # SEPARATE signing secret — NEVER reuse jwt_secret OR exam_link_secret. A
    # leaked interview secret must forge neither user sessions nor exam links.
    interview_link_secret: str
    # Public base URL the candidate's browser hits; mint builds {base}/interview-invite#<token>.
    interview_link_base_url: str = "http://localhost:5173"
    # Magic-link lifetime in HOURS. Short — the issued guest access token cannot be
    # revoked once minted (no jti denylist), so the link must die fast.
    interview_link_ttl_hours: int = 48
    # Join window (MINUTES) for the FIRST start, measured from scheduled_at (or from
    # the first click when the invite is unscheduled). Within the window the link may
    # be redeemed repeatedly; after it closes a never-started invite is dead. Once
    # started, the candidate may RECONNECT to the same session until it completes or
    # the link hard-expires — the window gates first-start only, not reconnects.
    interview_join_window_minutes: int = 10

    # --- DPDP consent ledger (S3-011) ---
    # sha256(raw_ip + consent_ip_salt) is stored instead of raw IP.
    # Required — no default — forces explicit secret management.
    consent_ip_salt: str

    # --- DPDP retention policy (S4-011) ---
    # Number of days after session completion before the row (+ cascaded turns)
    # is purged by the daily retention cron.  Default: 90 days.
    retention_days: int = 90

    # SAFETY RAIL — default TRUE.
    # When True, the cron logs what WOULD be deleted but performs no DELETE.
    # Flip to False only after a dry-run cycle confirms expected delete counts.
    retention_dry_run: bool = True

    # UTC hour for the daily retention cron.  03:00 UTC = ~08:30 IST (off-peak).
    retention_cron_hour: int = 3

    @field_validator("retention_days")
    @classmethod
    def validate_retention_days(cls, v: int) -> int:
        """Reject values outside 1-3650 (1 day to 10 years) — typo guard."""
        if not (1 <= v <= 3650):
            raise ValueError(
                f"retention_days must be between 1 and 3650 (got {v}). "
                "Check RETENTION_DAYS env var."
            )
        return v

    # --- Trusted proxy count (S4-012) ---
    # Controls how many hops at the right-hand end of X-Forwarded-For are
    # trusted infrastructure (i.e. controlled by us, not the client).
    #
    # 0  — local / development: no reverse proxy in front of the app.
    #       X-Forwarded-For is IGNORED entirely; request.client.host is used.
    #       This is the safe default — a client cannot spoof its IP by setting
    #       an arbitrary X-Forwarded-For header.
    # 1  — Railway demo: one nginx hop sits in front of the app.
    #       The trusted IP is the entry at index -1 from the right (the one
    #       added by our nginx before the request reaches the app).
    #       Example: "client, 10.0.0.1" with trusted_proxy_count=1 → "client".
    # 2  — Staging behind CDN + ingress: two infrastructure hops.
    #       Example: "client, cdn-edge, 10.0.0.1" → "client".
    #
    # Threat model: an attacker can prepend arbitrary IPs to X-Forwarded-For
    # before the packet enters our infrastructure.  With trusted_proxy_count=N,
    # we trust only the last N IPs in the list (added by our own proxies).
    # We pick the entry immediately to the left of those N trusted hops as the
    # "real" client IP.  This prevents a client from claiming any IP it likes
    # while still recovering the original IP through a known-hop proxy chain.
    trusted_proxy_count: int = 0

    # --- S3 / R2 storage (B-031 resume upload, B-032 JD upload) ---
    # For local dev with MinIO: set S3_ENDPOINT=http://localhost:9000
    # For Cloudflare R2: set S3_ENDPOINT=https://<account>.r2.cloudflarestorage.com
    # For real AWS S3: leave S3_ENDPOINT empty and set the region.
    s3_endpoint: str = ""
    s3_bucket_name: str = "intants-uploads"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_region: str = "auto"
    s3_use_ssl: bool = True

    sentry_dsn: str = ""

    # ---------------------------------------------------------------------------
    # httpOnly refresh-token cookie settings
    #
    # Production requirements (Vercel frontend ↔ Railway backend cross-site):
    #   AUTH_COOKIE_SECURE=true    — browsers reject SameSite=None without Secure.
    #   AUTH_COOKIE_SAMESITE=none  — required for cross-site credentialed requests.
    #   AUTH_COOKIE_DOMAIN=        — leave blank to let the browser scope by host.
    #
    # Local development (same-host http://localhost):
    #   AUTH_COOKIE_SECURE=false   — http is fine on localhost.
    #   AUTH_COOKIE_SAMESITE=lax   — sufficient for same-site requests.
    # ---------------------------------------------------------------------------
    auth_refresh_cookie_name: str = "refresh_token"
    auth_cookie_secure: bool = False  # Set True in production (HTTPS required).
    # Must be one of "lax" | "strict" | "none".
    # "none" requires AUTH_COOKIE_SECURE=true (browser hard rule — validated below).
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_cookie_domain: str | None = None  # None = browser defaults to request host.
    auth_cookie_path: str = "/"

    # ---------------------------------------------------------------------------
    # CSRF double-submit cookie settings
    #
    # On login/register/refresh we set a SECOND cookie (csrf_token) with
    # httponly=False so JavaScript can read it and echo it back as the
    # X-CSRF-Token request header on every /auth/refresh call.  The server then
    # compares the header value against the cookie value (double-submit pattern).
    # This defends against CSRF on the refresh endpoint when SameSite=None is
    # required for cross-site Vercel ↔ Railway deployments.
    #
    # The csrf_token cookie shares the same secure/samesite/domain/path/max_age
    # settings as the refresh_token cookie — only httponly differs (must be False
    # so JS can read and re-send the value).
    # ---------------------------------------------------------------------------
    auth_csrf_cookie_name: str = "csrf_token"

    @model_validator(mode="after")
    def validate_cookie_samesite_secure(self) -> "Settings":
        """Enforce cookie security rules at startup.

        Rule 1 — Browser hard rule: SameSite=None requires Secure=True.
          Browsers silently drop cookies that declare SameSite=None without the
          Secure flag.  Catch this misconfiguration so cross-site deployments
          (Vercel ↔ Railway) fail fast rather than silently breaking auth.

        Rule 2 — Production/staging gate: auth_cookie_secure must be True.
          In production or staging environments plain-HTTP cookies are never
          acceptable.  Fail fast at startup if misconfigured.
        """
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError(
                "AUTH_COOKIE_SAMESITE='none' requires AUTH_COOKIE_SECURE=true. "
                "Browsers silently discard SameSite=None cookies that lack the "
                "Secure attribute.  Set AUTH_COOKIE_SECURE=true (HTTPS required) "
                "or switch AUTH_COOKIE_SAMESITE back to 'lax' for local http."
            )
        if self.app_env in ("production", "staging") and not self.auth_cookie_secure:
            raise ValueError(
                f"APP_ENV={self.app_env!r} requires AUTH_COOKIE_SECURE=true. "
                "Plain-HTTP cookies are not acceptable in production or staging. "
                "Set AUTH_COOKIE_SECURE=true in your environment configuration."
            )
        return self

    @model_validator(mode="after")
    def validate_secret_strength(self) -> "Settings":
        """Fail fast in production/staging if any critical secret is still a weak
        placeholder — a known JWT_SECRET forges tokens and a known CONSENT_IP_SALT
        defeats DPDP IP-hashing. No-op in development/test."""
        assert_strong_secrets(
            self.app_env,
            {
                "JWT_SECRET": self.jwt_secret,
                "CONSENT_IP_SALT": self.consent_ip_salt,
                "EXAM_LINK_SECRET": self.exam_link_secret,
                "INTERVIEW_LINK_SECRET": self.interview_link_secret,
            },
        )
        return self

    @model_validator(mode="after")
    def validate_database_ssl(self) -> "Settings":
        """Enforce SSL for the database connection in production/staging.

        Without SSL, PII transits in cleartext between the app and Neon/Postgres —
        this violates DPDP §8 data security requirements and creates a MitM risk.

        Rule: in 'production' or 'staging' APP_ENV, DATABASE_SSL must be set to a
        non-empty value (e.g. 'require').  The check is intentionally permissive
        about the exact value so operators can supply 'require', 'verify-full', etc.

        Operators who genuinely need plain TCP in a prod-like env (e.g. a private
        network with TLS terminated at the load-balancer and the loopback exposed to
        the app) may set DATABASE_SSL=loopback-exempt to acknowledge the risk and
        pass this check — but the value is never passed to asyncpg.
        """
        loopback_exempt = "loopback-exempt"
        if self.app_env in ("production", "staging") and not self.database_ssl:
            raise ValueError(
                f"APP_ENV={self.app_env!r} requires DATABASE_SSL to be set "
                "(e.g. DATABASE_SSL=require).  Without SSL, PII travels in "
                "cleartext to Neon/Postgres.  Set DATABASE_SSL=require in your "
                "environment, or DATABASE_SSL=loopback-exempt if TLS is "
                "terminated upstream and the DB socket is loopback-only."
            )
        # Strip the sentinel value before it reaches asyncpg.
        if self.database_ssl == loopback_exempt:
            object.__setattr__(self, "database_ssl", "")
        return self

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

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allowed_origins.split(",") if o.strip()]


settings = Settings()  # type: ignore[call-arg]
