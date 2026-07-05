"""interview_data_model

Sprint 2 — S2-001: Create interview data model tables for data_gateway.

Tables created:
  - jobs             (id UUID PK, title, description, level, language,
                      nos_codes text[], competencies jsonb, is_active,
                      created_at, updated_at, deleted_at soft-delete)
  - nos_competencies (id UUID PK, nos_code UNIQUE, name, description,
                      level smallint 1-9, embedding vector(3072) nullable,
                      sector, created_at)
  - sessions         (id UUID PK, user_id FK→users, job_id FK→jobs,
                      language, status text, started_at, completed_at,
                      duration_seconds, metadata jsonb, created_at, updated_at)
  - turns            (id UUID PK, session_id FK→sessions CASCADE,
                      turn_number int, speaker text, text_content text,
                      audio_s3_key text nullable, prompt_tokens int nullable,
                      completion_tokens int nullable, latency_ms int nullable,
                      created_at; UNIQUE (session_id, turn_number))

Deviations from LLD §4:
  - LLD §4 uses old column names (job_id, session_id, jd_text, seq, role,
    tokens_in/out). Sprint-2 plan §task specifies simpler schema that the
    application layer actually needs. We follow the sprint task spec which is
    the authoritative source for Sprint 2 scope.
  - embedding is halfvec(3072): pgvector 0.8 on this host caps full vector
    indexes at 2000 dims. halfvec (half-precision float16) supports 3072 dims
    with hnsw and uses 50% less storage; OpenAI text-embedding-3-large quality
    is preserved (pgvector docs recommend halfvec for >2000 dims). The ORM
    accesses this column via raw SQL only (Sprint 4).
  - jobs uses id (not job_id) per task spec; sessions uses id (not session_id).
  - Partitioning deferred: LLD §4 partitions sessions/turns monthly.
    pg_partman is not confirmed available on the demo Neon DB. Plain tables
    ship in Sprint 2; partitioning will be added pre-production (Sprint 6+).
  - ivfflat index on nos_competencies.embedding created with CREATE INDEX IF
    NOT EXISTS; index will be populated in Sprint 4 when NOS embeddings are
    ingested (table is empty at migration time, which is valid for ivfflat).

Seed data: 3 jobs inserted with stable UUIDs for frontend/test reliance.

Revision ID: eda3829ec95a
Revises: a1b2c3d4e5f6
Create Date: 2026-05-27 04:18:01.842347
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "eda3829ec95a"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Ensure pgvector extension exists before using halfvec type.
    # On Prisma/cloud Postgres the extension is pre-installed (verified 2026-05-30);
    # this is a safety guard for any fresh DB that doesn't have it yet.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------
    # jobs
    # ------------------------------------------------------------------
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        # level: entry | mid | senior — validated at application layer
        sa.Column("level", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), server_default="en", nullable=False),
        sa.Column("nos_codes", sa.ARRAY(sa.Text()), server_default="{}", nullable=False),
        sa.Column("competencies", JSONB(), server_default=sa.text("'{}'"), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    )
    op.create_index(
        "idx_jobs_active_language",
        "jobs",
        ["is_active", "language"],
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    # ------------------------------------------------------------------
    # nos_competencies
    # ------------------------------------------------------------------
    op.create_table(
        "nos_competencies",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        # nos_code unique: e.g. "SSC/N9001"
        sa.Column("nos_code", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # level: NSQF level 1-9 — validated at application layer
        sa.Column("level", sa.SmallInteger(), nullable=True),
        # embedding: vector(3072) for OpenAI text-embedding-3-large;
        # populated in Sprint 4 by nos/embedding.py ingestion pipeline.
        # Column defined as nullable Text internally — pgvector uses its own
        # type which we register via op.execute.
        sa.Column("sector", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nos_competencies"),
        sa.UniqueConstraint("nos_code", name="uq_nos_competencies_nos_code"),
    )

    # Add embedding column using halfvec(3072) pgvector type.
    # halfvec stores float16 values — half the storage of float32 vector(3072).
    # pgvector 0.8's hnsw index caps full vector() at 2000 dims on this build;
    # halfvec supports up to 4000 dims without that restriction.
    # OpenAI text-embedding-3-large produces 3072 float32 values; storing as
    # float16 (halfvec) is the pgvector-recommended approach for >2000 dims.
    op.execute(
        "ALTER TABLE nos_competencies ADD COLUMN embedding halfvec(3072) NULL"
    )

    # HNSW index for cosine similarity search on halfvec embeddings.
    # Created now (table is empty at migration time — valid for HNSW).
    # Sprint 4 NOS ingestion pipeline (nos/embedding.py) will populate rows
    # before the index is used for production queries.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_nos_embedding_hnsw "
        "ON nos_competencies USING hnsw (embedding halfvec_cosine_ops)"
    )

    # ------------------------------------------------------------------
    # sessions
    # ------------------------------------------------------------------
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), nullable=False),
        sa.Column("language", sa.Text(), server_default="en", nullable=False),
        # status: created | in_progress | completed | abandoned | failed
        # validated at application layer; String kept flexible for future states
        sa.Column("status", sa.Text(), nullable=False, server_default="created"),
        sa.Column(
            "started_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("metadata", JSONB(), server_default=sa.text("'{}'"), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_sessions_user_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["jobs.id"],
            name="fk_sessions_job_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sessions"),
    )
    op.create_index(
        "idx_sessions_user_status_started",
        "sessions",
        ["user_id", "status", sa.text("started_at DESC")],
    )
    op.create_index(
        "idx_sessions_job_status",
        "sessions",
        ["job_id", "status"],
    )

    # ------------------------------------------------------------------
    # turns
    # ------------------------------------------------------------------
    op.create_table(
        "turns",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("session_id", UUID(as_uuid=True), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        # speaker: interviewer | candidate — validated at application layer
        sa.Column("speaker", sa.Text(), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=True),
        # audio_s3_key: placeholder for Sprint 3 voice pipeline
        sa.Column("audio_s3_key", sa.Text(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name="fk_turns_session_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_turns"),
        sa.UniqueConstraint(
            "session_id", "turn_number", name="uq_turns_session_turn_number"
        ),
    )
    # The unique constraint (session_id, turn_number) covers this query pattern;
    # an explicit index is redundant but added for clarity in EXPLAIN output.
    op.create_index(
        "idx_turns_session_id_turn_number",
        "turns",
        ["session_id", "turn_number"],
    )

    # ------------------------------------------------------------------
    # Seed: 3 jobs with stable UUIDs (frontend + tests rely on these)
    # ------------------------------------------------------------------
    op.execute(
        sa.text(
            """
            INSERT INTO jobs
              (id, title, description, level, language,
               nos_codes, competencies, is_active, created_at, updated_at)
            VALUES
              (
                '11111111-1111-1111-1111-111111111111',
                'Junior Java Developer',
                'Entry-level backend developer role focused on Java and Spring Boot',
                'entry',
                'en',
                ARRAY['SSC/N0501', 'SSC/N9001'],
                '{"required": ["java", "sql", "problem_solving"]}'::jsonb,
                true,
                now(),
                now()
              ),
              (
                '22222222-2222-2222-2222-222222222222',
                'Sales Associate',
                'Customer-facing sales role for retail and B2B',
                'entry',
                'en',
                ARRAY['SSC/N0901'],
                '{"required": ["communication", "negotiation", "customer_focus"]}'::jsonb,
                true,
                now(),
                now()
              ),
              (
                '33333333-3333-3333-3333-333333333333',
                'Data Entry Operator',
                'Accuracy-focused data entry and validation role',
                'entry',
                'en',
                ARRAY['SSC/N9007'],
                '{"required": ["typing_speed", "accuracy", "attention_to_detail"]}'::jsonb,
                true,
                now(),
                now()
              )
            """
        )
    )


def downgrade() -> None:
    # Drop in reverse FK dependency order.

    # turns first (references sessions)
    op.drop_index("idx_turns_session_id_turn_number", table_name="turns")
    op.drop_table("turns")

    # sessions next (references users + jobs)
    op.drop_index("idx_sessions_job_status", table_name="sessions")
    op.drop_index("idx_sessions_user_status_started", table_name="sessions")
    op.drop_table("sessions")

    # nos_competencies (standalone)
    op.execute("DROP INDEX IF EXISTS idx_nos_embedding_hnsw")
    op.drop_table("nos_competencies")

    # jobs last (referenced by sessions FK which is now dropped)
    op.drop_index("idx_jobs_active_language", table_name="jobs")
    op.drop_table("jobs")
