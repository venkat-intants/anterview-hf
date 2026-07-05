"""initial_auth_tables

Sprint 1 — S1-002: Create auth tables for data_gateway.

Tables created:
  - users          (id UUID PK, email UNIQUE, password_hash nullable for SSO,
                    preferred_language, naipunyam_id NULLABLE UNIQUE — relaxed
                    from LLD §4 which has NOT NULL. Sprint 1 uses local auth only;
                    naipunyam_id becomes required when AUTH_PROVIDER=naipunyam is
                    implemented in Sprint 5+. Documented intentional divergence per
                    sprint-01/plan.md Risk section.)
  - roles          (id smallserial PK, name UNIQUE, description)
  - user_roles     (user_id FK, role_id FK, composite PK, assigned_at)
  - dpdp_consent_ledger (id UUID PK, user_id FK, consent_type, granted bool,
                    granted_at, revoked_at nullable, purpose, evidence jsonb)

Seed data: roles 'candidate' and 'admin' inserted on upgrade.

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-05-27 00:01:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op  # noqa: F401

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # users
    # ------------------------------------------------------------------
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("preferred_language", sa.Text(), server_default="en", nullable=False),
        # naipunyam_id is NULLABLE in Sprint 1 (local auth has no Naipunyam ID).
        # It will be made NOT NULL and enforced at the app layer when
        # AUTH_PROVIDER=naipunyam is implemented (Sprint 5+).
        sa.Column("naipunyam_id", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.UniqueConstraint("naipunyam_id", name="uq_users_naipunyam_id"),
    )
    op.create_index("idx_users_email", "users", ["email"], unique=False)
    op.create_index(
        "idx_users_active",
        "users",
        ["is_active"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ------------------------------------------------------------------
    # roles
    # ------------------------------------------------------------------
    op.create_table(
        "roles",
        sa.Column(
            "id",
            sa.SmallInteger(),
            sa.Identity(always=False, start=1, increment=1),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_roles"),
        sa.UniqueConstraint("name", name="uq_roles_name"),
    )

    # Seed initial roles
    op.execute(
        sa.text(
            "INSERT INTO roles (name, description) VALUES "
            "('candidate', 'Default role for interview takers'), "
            "('admin', 'Platform admin')"
        )
    )

    # ------------------------------------------------------------------
    # user_roles
    # ------------------------------------------------------------------
    op.create_table(
        "user_roles",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.SmallInteger(), nullable=False),
        sa.Column(
            "assigned_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_roles_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            ["roles.id"],
            name="fk_user_roles_role_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_roles"),
    )
    op.create_index("idx_user_roles_user_id", "user_roles", ["user_id"])

    # ------------------------------------------------------------------
    # dpdp_consent_ledger
    # ------------------------------------------------------------------
    op.create_table(
        "dpdp_consent_ledger",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("consent_type", sa.Text(), nullable=False),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column(
            "granted_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_dpdp_consent_ledger_user_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_dpdp_consent_ledger"),
    )
    op.create_index(
        "idx_dpdp_consent_user_id",
        "dpdp_consent_ledger",
        ["user_id", "granted_at"],
    )


def downgrade() -> None:
    # Drop in reverse FK dependency order
    op.drop_index("idx_dpdp_consent_user_id", table_name="dpdp_consent_ledger")
    op.drop_table("dpdp_consent_ledger")

    op.drop_index("idx_user_roles_user_id", table_name="user_roles")
    op.drop_table("user_roles")

    op.drop_table("roles")

    op.drop_index("idx_users_active", table_name="users")
    op.drop_index("idx_users_email", table_name="users")
    op.drop_table("users")
