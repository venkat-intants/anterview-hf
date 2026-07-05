"""audit_log append-only (DPDP audit integrity)

Makes the audit_log tamper-evident: a trigger blocks UPDATE / DELETE / TRUNCATE
so audit entries cannot be silently altered or removed. INSERT is unaffected
(the only operation any code performs on this table today).

DPDP Act 2023 expects the access/processing audit trail to be retained and
intact for ~3 years. If a controlled retention purge of entries OLDER than the
window is ever needed, run it inside a maintenance transaction that first does
`ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_mutation;` (privileged) and
re-enables it afterward — never by relaxing this trigger in normal app code.

Revision ID: c4d5e6f7a8b9
Revises:     b3c4d5e6f7a8
Create Date: 2026-06-20 00:02:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c4d5e6f7a8b9"
down_revision: str | None = "b3c4d5e6f7a8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION audit_log_block_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_log is append-only (DPDP audit integrity): % is not permitted', TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    # Row-level guard for UPDATE / DELETE.
    op.execute(
        """
        CREATE TRIGGER audit_log_no_mutation
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_block_mutation();
        """
    )
    # Statement-level guard for TRUNCATE (row triggers do not fire on TRUNCATE).
    op.execute(
        """
        CREATE TRIGGER audit_log_no_truncate
        BEFORE TRUNCATE ON audit_log
        FOR EACH STATEMENT EXECUTE FUNCTION audit_log_block_mutation();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log;")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_mutation ON audit_log;")
    op.execute("DROP FUNCTION IF EXISTS audit_log_block_mutation();")
