"""erasure_requests_security_fixes

S5-004 security-auditor remediation:
  1. Change erasure_requests.user_id FK from CASCADE to RESTRICT so compliance
     evidence survives hard-deletion of the user row (DPDP §8(7) record-keeping).
  2. Add partial unique index ON erasure_requests (user_id) WHERE status='pending'
     to enforce idempotency at the DB level and support race-safe 409 responses
     via IntegrityError catch.

Revision ID: e9f0a1b2c3d4
Revises:     c7d8e9f0a1b2
Create Date: 2026-05-29 00:03:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e9f0a1b2c3d4"
down_revision: str | None = "d8e9f0a1b2c3"  # after scorecards_table migration
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Partial unique index for idempotent erasure requests.
    #    Only one 'pending' request per user is permitted at the DB level.
    op.execute(
        "CREATE UNIQUE INDEX uq_erasure_requests_pending "
        "ON erasure_requests (user_id) WHERE status = 'pending'"
    )

    # 2. Replace CASCADE FK with RESTRICT so erasure evidence outlives the user.
    op.execute(
        "ALTER TABLE erasure_requests "
        "DROP CONSTRAINT fk_erasure_requests_user_id"
    )
    op.execute(
        "ALTER TABLE erasure_requests "
        "ADD CONSTRAINT fk_erasure_requests_user_id "
        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE RESTRICT"
    )


def downgrade() -> None:
    # Restore CASCADE FK
    op.execute(
        "ALTER TABLE erasure_requests "
        "DROP CONSTRAINT fk_erasure_requests_user_id"
    )
    op.execute(
        "ALTER TABLE erasure_requests "
        "ADD CONSTRAINT fk_erasure_requests_user_id "
        "FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
    )

    # Remove partial unique index
    op.execute("DROP INDEX IF EXISTS uq_erasure_requests_pending")
