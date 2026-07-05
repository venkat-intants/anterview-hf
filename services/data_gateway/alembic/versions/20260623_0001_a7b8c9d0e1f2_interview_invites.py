"""interview_invites (HR workflow Phase 3: invite an applicant into the avatar interview)

LAZY-PROVISION model: at HR-invite time we create ONLY this opaque-token row — NO
guest user, NO session, NO consent. All PII materialization (a 'guest_candidate'
users row carrying the applicant's resume_text, the sessions row, and the
applicant's own DPDP consent) happens on the applicant's FIRST redeem.
token_hash = hmac_sha256(raw, interview_link_secret); the raw token lives only in
the shared URL #fragment.

Security review fixes baked in:
  B3  one table, lazy lifecycle.
  B6  applicants.user_id FK links applicant -> the minted guest user so DPDP
      erasure can trace + cascade; resume_text is copied onto the guest at redeem.
  B7  guest gets a dedicated low-privilege 'guest_candidate' role (seeded here),
      rejected by every existing candidate/HR route.
  M4  composite FK pins applicant to its tenant (cross-tenant applicant_id rejected).
  M5  unique on applicants.user_id + on interview_invites.session_id make concurrent
      double-redeem collide (IntegrityError -> reuse the winner).
  m1  partial-unique active index frees the slot only on terminal states.

Revision ID: a7b8c9d0e1f2
Revises:     e6f7a8b9c0d1
Create Date: 2026-06-23 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- B7: dedicated low-privilege guest role (idempotent seed) ---
    op.execute(
        "INSERT INTO roles (name, description) "
        "VALUES ('guest_candidate', "
        "'Magic-link interview guest: one bound session, no session-create, no API roam') "
        "ON CONFLICT (name) DO NOTHING"
    )

    # --- B6 + M5: link applicant -> minted guest user (erasure trace + idempotency) ---
    op.add_column("applicants", sa.Column("user_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_applicants_user", "applicants", "users", ["user_id"], ["id"], ondelete="SET NULL"
    )
    # One guest user per applicant (concurrent redeem collides -> reuse winner).
    op.create_index(
        "uq_applicants_user_id", "applicants", ["user_id"], unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    # Composite-FK target so interview_invites can pin (applicant_id, company_id).
    # MUST exist before the interview_invites composite FK references it.
    op.create_unique_constraint("uq_applicants_id_company", "applicants", ["id", "company_id"])

    # --- main table ---
    op.create_table(
        "interview_invites",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("company_id", sa.UUID(), nullable=False),
        sa.Column("applicant_id", sa.UUID(), nullable=False),
        sa.Column("job_id", sa.UUID(), nullable=False),
        # Set on FIRST redeem (lazy). Both NULL until then.
        sa.Column("guest_user_id", sa.UUID(), nullable=True),
        sa.Column("session_id", sa.UUID(), nullable=True),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), server_default="en", nullable=False),
        sa.Column("avatar_id", sa.Text(), nullable=True),  # resolved at redeem if NULL
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        # invited | consumed | completed | expired | revoked
        sa.Column("status", sa.Text(), server_default="invited", nullable=False),
        sa.Column(
            "created_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_interview_invites"),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"],
            name="fk_interview_invites_company", ondelete="CASCADE",
        ),
        # Composite FK pins applicant to THIS tenant (cross-tenant applicant_id rejected).
        sa.ForeignKeyConstraint(
            ["applicant_id", "company_id"], ["applicants.id", "applicants.company_id"],
            name="fk_interview_invites_applicant", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["jobs.id"], name="fk_interview_invites_job", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["guest_user_id"], ["users.id"],
            name="fk_interview_invites_guest_user", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"], ["sessions.id"],
            name="fk_interview_invites_session", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            name="fk_interview_invites_created_by", ondelete="SET NULL",
        ),
        sa.UniqueConstraint("token_hash", name="uq_interview_invites_token_hash"),
        sa.UniqueConstraint("session_id", name="uq_interview_invites_session"),
        sa.UniqueConstraint("id", "company_id", name="uq_interview_invites_id_company"),
        sa.CheckConstraint(
            "status IN ('invited','consumed','completed','expired','revoked')",
            name="ck_interview_invites_status",
        ),
    )
    op.create_index("idx_interview_invites_company", "interview_invites", ["company_id"])
    op.create_index("idx_interview_invites_applicant", "interview_invites", ["applicant_id"])
    op.create_index("idx_interview_invites_job", "interview_invites", ["job_id"])
    op.create_index("idx_interview_invites_guest_user", "interview_invites", ["guest_user_id"])
    # m1: only ONE non-terminal invite per (applicant, job); terminal states free it.
    op.execute(
        "CREATE UNIQUE INDEX uix_interview_invites_active "
        "ON interview_invites (applicant_id, job_id) "
        "WHERE deleted_at IS NULL AND status IN ('invited','consumed')"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uix_interview_invites_active")
    op.drop_index("idx_interview_invites_guest_user", table_name="interview_invites")
    op.drop_index("idx_interview_invites_job", table_name="interview_invites")
    op.drop_index("idx_interview_invites_applicant", table_name="interview_invites")
    op.drop_index("idx_interview_invites_company", table_name="interview_invites")
    op.drop_table("interview_invites")
    op.drop_constraint("uq_applicants_id_company", "applicants", type_="unique")
    op.drop_index("uq_applicants_user_id", table_name="applicants")
    op.drop_constraint("fk_applicants_user", "applicants", type_="foreignkey")
    op.drop_column("applicants", "user_id")
    op.execute("DELETE FROM roles WHERE name = 'guest_candidate'")
