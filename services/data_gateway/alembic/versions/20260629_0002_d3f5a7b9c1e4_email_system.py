"""email system — transactional outbox + auth tokens + user email columns

Backs the platform-wide email feature:

  * email_events — durable outbox + delivery log (one row per outbound email).
      status ∈ queued | sending | sent | failed | cancelled is the audit trail
      the spec requires (sent / failed / pending); the worker (app.mailer) drains
      queued/failed-retriable rows with backoff. body_html/body_text are nulled on
      send and the whole row is purged after EMAIL_RETENTION_DAYS by the retention
      cron, so a live magic link never lingers.
  * auth_tokens — single-use HASHED tokens for password reset + email verify
      (same hash-only discipline as exam/interview magic links).
  * users.email_verified_at — NULL = unverified (non-blocking).
  * users.notify_login_email — per-user opt-in for the sign-in alert email.

Additive + reversible. Revision id d3f5a7b9c1e4.
Revises: c2e4a6b8d0f2
Create Date: 2026-06-29 00:02:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3f5a7b9c1e4"
down_revision: str | None = "c2e4a6b8d0f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- users: verification + login-alert opt-in -------------------------
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column(
            "notify_login_email",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # --- email_events: outbox + delivery log -------------------------------
    op.create_table(
        "email_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("template", sa.Text(), nullable=False),
        sa.Column("to_email", sa.Text(), nullable=False),
        sa.Column("to_user_id", sa.UUID(), nullable=True),
        sa.Column("company_id", sa.UUID(), nullable=True),
        sa.Column("lang", sa.Text(), server_default="en", nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body_html", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default="queued", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("6"), nullable=False),
        sa.Column("next_attempt_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("related_kind", sa.Text(), nullable=True),
        sa.Column("related_id", sa.UUID(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_email_events"),
        sa.ForeignKeyConstraint(
            ["to_user_id"], ["users.id"], name="fk_email_events_user", ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name="fk_email_events_company",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("dedupe_key", name="uq_email_events_dedupe_key"),
        sa.CheckConstraint(
            "status IN ('queued','sending','sent','failed','cancelled')",
            name="ck_email_events_status",
        ),
    )
    # The worker's claim query: rows still due for an attempt, oldest first.
    op.create_index(
        "idx_email_events_due",
        "email_events",
        ["next_attempt_at"],
        postgresql_where=sa.text("status IN ('queued','failed')"),
    )
    # Platform-owner log view (newest first, optionally per company).
    op.create_index(
        "idx_email_events_created",
        "email_events",
        [sa.text("created_at DESC")],
    )
    op.create_index("idx_email_events_company", "email_events", ["company_id"])

    # --- auth_tokens: password reset + email verification ------------------
    op.create_table(
        "auth_tokens",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auth_tokens"),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_auth_tokens_user", ondelete="CASCADE"
        ),
        sa.UniqueConstraint("token_hash", name="uq_auth_tokens_token_hash"),
        sa.CheckConstraint(
            "kind IN ('password_reset','email_verify')", name="ck_auth_tokens_kind"
        ),
    )
    # Look up live tokens for a user (and let the cron sweep expired rows).
    op.create_index("idx_auth_tokens_user_kind", "auth_tokens", ["user_id", "kind"])
    op.create_index("idx_auth_tokens_expires", "auth_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("idx_auth_tokens_expires", table_name="auth_tokens")
    op.drop_index("idx_auth_tokens_user_kind", table_name="auth_tokens")
    op.drop_table("auth_tokens")

    op.drop_index("idx_email_events_company", table_name="email_events")
    op.drop_index("idx_email_events_created", table_name="email_events")
    op.drop_index("idx_email_events_due", table_name="email_events")
    op.drop_table("email_events")

    op.drop_column("users", "notify_login_email")
    op.drop_column("users", "email_verified_at")
