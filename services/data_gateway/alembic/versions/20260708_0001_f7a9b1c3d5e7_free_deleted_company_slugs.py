"""free_deleted_company_slugs

``delete_company`` soft-deletes a company (``deleted_at = now()``) but until
now left its ``slug`` untouched, so the tombstoned row kept holding
``uq_companies_slug`` forever — recreating a company with the same name
returned 409 "already exists" even though the company was deleted.

The route now tombstones the slug on delete (``slug || '-deleted-' || id``,
mirroring how member emails are released). This migration applies the same
tombstone to companies that were already soft-deleted before the fix, freeing
their slugs for reuse.

Revision ID: f7a9b1c3d5e7
Revises:     e4f6a8b0c2d4
Create Date: 2026-07-08 00:01:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7a9b1c3d5e7"
down_revision: str | None = "e4f6a8b0c2d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE companies "
            "SET slug = slug || '-deleted-' || id::text, updated_at = now() "
            "WHERE deleted_at IS NOT NULL "
            "  AND slug NOT LIKE '%-deleted-%'"
        )
    )


def downgrade() -> None:
    # Strip the tombstone suffix. Guarded by the unique constraint: if two
    # deleted rows would collapse to the same bare slug this raises and the
    # downgrade must be resolved manually (acceptable — downgrade-only path).
    op.execute(
        sa.text(
            "UPDATE companies "
            "SET slug = regexp_replace(slug, '-deleted-[0-9a-f-]{36}$', ''), "
            "    updated_at = now() "
            "WHERE deleted_at IS NOT NULL "
            "  AND slug ~ '-deleted-[0-9a-f-]{36}$'"
        )
    )
