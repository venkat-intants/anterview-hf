"""applicants semantic search — embedding (halfvec) + full-text index (HR workflow)

Adds the columns/indexes that power hybrid (semantic + exact-keyword) resume
search for HR:

  * applicants.embedding  halfvec(3072)  — Gemini gemini-embedding-001 vectors
    (free, no card; 3072 is the native, normalized size). Populated best-effort
    at resume ingest / rescore / reindex. ORM does NOT map this column — the
    similarity query uses raw text() SQL (same approach as nos_competencies).
  * HNSW index (halfvec_cosine_ops) for the semantic (`<=>` cosine) ranking.
  * GIN index on to_tsvector(resume_text) for the exact-keyword (full-text) leg.

Additive + idempotent: safe to run against the live demo DB while services run.
Existing applicants have a NULL embedding until reindexed; they still match via
the full-text leg.

Revision ID: a9c1e2f3b4d5
Revises:     c3e5f7a9b1d3
Create Date: 2026-06-27 00:01:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "a9c1e2f3b4d5"
down_revision: str | None = "c3e5f7a9b1d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # pgvector is pre-installed on Prisma/Neon cloud Postgres (verified 2026-05-30
    # for nos_competencies); IF NOT EXISTS keeps this a no-op there and locally.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # halfvec(3072): float16 storage; pgvector 0.8 hnsw supports >2000 dims for
    # halfvec (full vector caps at 2000 on this build). Matches nos_competencies.
    op.execute("ALTER TABLE applicants ADD COLUMN IF NOT EXISTS embedding halfvec(3072) NULL")

    # Semantic leg: HNSW cosine index over the embedding.
    # Uses pgvector defaults (m=16, ef_construction=64) — fine for hundreds/low
    # thousands of applicants per company. TECH DEBT: revisit m / ef_construction
    # (and ef_search at query time) if a company exceeds ~10k applicants, where
    # default recall starts to degrade.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_applicants_embedding_hnsw "
        "ON applicants USING hnsw (embedding halfvec_cosine_ops)"
    )

    # Exact-keyword leg: GIN full-text index over the resume text. Expression
    # index so no extra stored column is needed; the search query uses the
    # identical to_tsvector(...) expression so the planner can use this index.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_applicants_resume_fts "
        "ON applicants USING gin (to_tsvector('english', coalesce(resume_text, '')))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_applicants_resume_fts")
    op.execute("DROP INDEX IF EXISTS idx_applicants_embedding_hnsw")
    op.execute("ALTER TABLE applicants DROP COLUMN IF EXISTS embedding")
