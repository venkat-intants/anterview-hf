"""hr_roles_companies (HR workflow — Phase 0)

Identity foundation for the HR/ATS expansion:

  - Seeds roles 'super_admin' and 'hr_manager'.
  - Creates the 'companies' table (multi-tenant: each HR + their applicants /
    exams / jobs belong to one company).
  - Adds users.company_id (NULL for super_admin / platform users) and
    users.must_change_password (force a default-password reset on first login).
  - Promotes admin@intants.com -> super_admin (idempotent; no-op if absent).

Revision ID: b3c4d5e6f7a8
Revises:     a2b3c4d5e6f7
Create Date: 2026-06-20 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7a8"
down_revision: str | None = "a2b3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- 1. seed new roles (id auto-generates via Identity) ---
    op.execute(
        sa.text(
            "INSERT INTO roles (name, description) VALUES "
            "('super_admin', 'Platform owner — manages companies and HR managers'), "
            "('hr_manager', 'Company HR — screens applicants, runs exams and interviews') "
            "ON CONFLICT (name) DO NOTHING"
        )
    )

    # --- 2. companies (tenant) ---
    op.create_table(
        "companies",
        sa.Column(
            "id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by_user_id", sa.UUID(), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_companies"),
        sa.UniqueConstraint("slug", name="uq_companies_slug"),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            name="fk_companies_created_by",
            ondelete="SET NULL",
        ),
    )

    # --- 3. users: company scoping + force-password-change flag ---
    op.add_column("users", sa.Column("company_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_users_company_id",
        "users",
        "companies",
        ["company_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_users_company_id", "users", ["company_id"])
    op.add_column(
        "users",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )

    # --- 4. promote the platform owner (idempotent; no-op if the email isn't present) ---
    op.execute(
        sa.text(
            "INSERT INTO user_roles (user_id, role_id) "
            "SELECT u.id, r.id FROM users u CROSS JOIN roles r "
            "WHERE u.email = 'admin@intants.com' AND r.name = 'super_admin' "
            "ON CONFLICT (user_id, role_id) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "DELETE FROM user_roles WHERE role_id IN "
            "(SELECT id FROM roles WHERE name = 'super_admin')"
        )
    )
    op.drop_column("users", "must_change_password")
    op.drop_index("idx_users_company_id", table_name="users")
    op.drop_constraint("fk_users_company_id", "users", type_="foreignkey")
    op.drop_column("users", "company_id")
    op.drop_table("companies")
    op.execute(
        sa.text("DELETE FROM roles WHERE name IN ('super_admin', 'hr_manager')")
    )
