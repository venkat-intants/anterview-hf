"""Reusable question banks, and locking published exam content — PH4-D1.

Revision ID: d2a4c6e8f0b3
Revises: c1e3a5b7d9f4
Create Date: 2026-09-21

WHAT WAS MISSING BEFORE THIS
Published exams were not locked at the database at all — HR could edit a
question, move a round's pass mark, or change a section's time limit while a
candidate was mid-attempt, and the app checked for attempts only, never for
``published``. This migration closes that, and gives HR a bank of reusable
questions to draw on.

``question_banks`` / ``bank_questions``
A company-scoped library, independent of any one exam. A question is
authored as a ``draft``, ``submitted``, ``approved`` by another HR manager (or
the company super admin) — never its own author or submitter — and can be
``retired``. A new version follows an approved-or-retired one in the same
lineage (``root_id``); at most one version per lineage is ever ``approved`` at
a time (the partial unique index below is what makes that true even against a
racing approval). ``bank_question_events`` is the append-only history of what
happened to a question — never its content.

Locking a published round (T2, T3)
``exam_round_content_frozen()`` freezes ``exam_sections`` /
``exam_questions`` / ``coding_questions`` while their round is published or
has been taken. ``exam_rounds_frozen()`` freezes a round's grading fields
(``pass_threshold``, ``time_limit_seconds``, ``advances_to_interview``) the
same way, and lets a round go from published back to draft only while nobody
has taken it — the only way back once taken is to duplicate the round
(``POST .../rounds/{id}/duplicate``, in ``app/routers/hr_rounds.py``). Title,
``round_number`` and ``position`` are never content and stay editable.

Copying a bank question into an exam
``exam_questions`` / ``coding_questions`` gain three nullable provenance
columns. Copying is copy-on-add: the exam's row is independent of the bank
question from the moment it is inserted, and a later new version of the bank
question never touches it. The bank question must be ``approved``, of the
matching ``company_id`` and ``kind`` — checked by T2 at INSERT — and the
partial unique index on ``(exam_id, source_bank_root_id)`` refuses the same
lineage twice in one exam, at any version.

The fingerprint (``app/workflows.py: _EXAM_CONTENT_SQL``) is NOT touched
here — it is a Python-side change (subtracting the three provenance keys so
where a question came from never appears in what a reviewer approves), not a
schema one.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "d2a4c6e8f0b3"
down_revision: str | None = "c1e3a5b7d9f4"
branch_labels: str | None = None
depends_on: str | None = None

BANK_QUESTION_STATUSES = ("draft", "in_review", "approved", "retired")
BANK_QUESTION_ORIGINS = ("authored", "ai_draft", "from_exam")
BANK_QUESTION_KINDS = ("mcq", "coding")
BANK_QUESTION_DIFFICULTIES = ("easy", "medium", "hard")
BANK_QUESTION_LANGUAGES = ("en", "hi", "te")
BANK_QUESTION_EVENTS = (
    "created", "edited", "submitted", "withdrawn", "approved", "changes_requested",
    "retired", "versioned", "copied_to_exam", "saved_from_exam",
)


def _ts(name: str, nullable: bool = True) -> sa.Column:
    return sa.Column(name, sa.TIMESTAMP(timezone=True), nullable=nullable)


# ---------------------------------------------------------------------------
# T1 — a bank question's lifecycle
# ---------------------------------------------------------------------------
BANK_QUESTIONS_LIFECYCLE = """
CREATE OR REPLACE FUNCTION bank_questions_lifecycle() RETURNS trigger AS $$
DECLARE
    root_company uuid;
    root_bank uuid;
    root_kind text;
    root_status text;
    copied boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN
        -- Cascade: the bank (or the company) is going.
        IF NOT EXISTS (SELECT 1 FROM question_banks b WHERE b.id = OLD.bank_id) THEN
            RETURN OLD;
        END IF;
        IF OLD.status <> 'draft' THEN
            RAISE EXCEPTION 'bank question % is % and is kept, not deleted', OLD.id, OLD.status;
        END IF;
        SELECT EXISTS (
            SELECT 1 FROM exam_questions e WHERE e.source_bank_question_id = OLD.id
            UNION ALL
            SELECT 1 FROM coding_questions c WHERE c.source_bank_question_id = OLD.id
        ) INTO copied;
        IF copied THEN
            RAISE EXCEPTION
                'bank question % has been copied into an exam and cannot be deleted', OLD.id;
        END IF;
        RETURN OLD;
    END IF;

    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'draft' THEN
            RAISE EXCEPTION 'a bank question arrives as a draft';
        END IF;
        IF NEW.submitted_by_user_id IS NOT NULL OR NEW.submitted_at IS NOT NULL
           OR NEW.reviewed_by_user_id IS NOT NULL OR NEW.reviewed_at IS NOT NULL
           OR NEW.retired_by_user_id IS NOT NULL OR NEW.retired_at IS NOT NULL THEN
            RAISE EXCEPTION 'a bank question arrives with no review history';
        END IF;
        IF NEW.version = 1 THEN
            IF NEW.root_id <> NEW.id THEN
                RAISE EXCEPTION 'version 1 of a bank question is its own root';
            END IF;
        ELSE
            SELECT company_id, bank_id, kind, status INTO root_company, root_bank, root_kind, root_status
              FROM bank_questions WHERE id = NEW.root_id AND id <> NEW.id;
            IF root_company IS NULL THEN
                RAISE EXCEPTION 'bank question % has no earlier version to follow', NEW.root_id;
            END IF;
            IF root_company <> NEW.company_id OR root_bank <> NEW.bank_id OR root_kind <> NEW.kind THEN
                RAISE EXCEPTION
                    'a new version must stay in the same company, bank and kind as %', NEW.root_id;
            END IF;
            IF root_status NOT IN ('approved', 'retired') THEN
                RAISE EXCEPTION
                    'bank question % must be approved or retired before a new version follows it',
                    NEW.root_id;
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    -- UPDATE
    IF NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id THEN
        RAISE EXCEPTION 'bank question % keeps its original author', OLD.id;
    END IF;

    IF NEW.status IS DISTINCT FROM OLD.status THEN
        IF NOT (
               (OLD.status = 'draft' AND NEW.status = 'in_review')
            OR (OLD.status = 'in_review' AND NEW.status = 'draft')
            OR (OLD.status = 'in_review' AND NEW.status = 'approved')
            OR (OLD.status = 'approved' AND NEW.status = 'retired')
        ) THEN
            RAISE EXCEPTION 'bank question % cannot go from % to %', OLD.id, OLD.status, NEW.status;
        END IF;
        IF NEW.status = 'in_review'
           AND (NEW.submitted_by_user_id IS NULL OR NEW.submitted_at IS NULL) THEN
            RAISE EXCEPTION 'bank question % needs a submitter', OLD.id;
        END IF;
        IF OLD.status = 'in_review' AND NEW.status = 'draft' AND NEW.reviewed_by_user_id IS NOT NULL THEN
            -- Changes requested (rather than withdrawn): a named reviewer who is not the submitter.
            IF NEW.reviewed_by_user_id IS NOT DISTINCT FROM NEW.submitted_by_user_id THEN
                RAISE EXCEPTION
                    'bank question % must have changes requested by someone other than its submitter',
                    OLD.id;
            END IF;
            IF NEW.reviewed_at IS NULL THEN
                RAISE EXCEPTION 'bank question % needs a review time', OLD.id;
            END IF;
        END IF;
        IF NEW.status = 'approved' THEN
            IF NEW.reviewed_by_user_id IS NULL OR NEW.reviewed_at IS NULL THEN
                RAISE EXCEPTION 'bank question % needs a named reviewer', OLD.id;
            END IF;
            IF NEW.reviewed_by_user_id IS NOT DISTINCT FROM NEW.created_by_user_id
               OR NEW.reviewed_by_user_id IS NOT DISTINCT FROM NEW.submitted_by_user_id THEN
                RAISE EXCEPTION
                    'bank question % must be approved by someone other than its author and submitter',
                    OLD.id;
            END IF;
        END IF;
        IF NEW.status = 'retired' AND NEW.retired_by_user_id IS NULL THEN
            RAISE EXCEPTION 'bank question % needs a named retirer', OLD.id;
        END IF;
    END IF;

    IF OLD.status <> 'draft' THEN
        IF NEW.kind IS DISTINCT FROM OLD.kind
           OR NEW.prompt IS DISTINCT FROM OLD.prompt
           OR NEW.options IS DISTINCT FROM OLD.options
           OR NEW.correct_index IS DISTINCT FROM OLD.correct_index
           OR NEW.starter_code IS DISTINCT FROM OLD.starter_code
           OR NEW.reference_solution IS DISTINCT FROM OLD.reference_solution
           OR NEW.allowed_languages IS DISTINCT FROM OLD.allowed_languages
           OR NEW.test_cases IS DISTINCT FROM OLD.test_cases
           OR NEW.time_limit_ms IS DISTINCT FROM OLD.time_limit_ms
           OR NEW.points IS DISTINCT FROM OLD.points
           OR NEW.difficulty IS DISTINCT FROM OLD.difficulty
           OR NEW.language IS DISTINCT FROM OLD.language
           OR NEW.competencies IS DISTINCT FROM OLD.competencies
           OR NEW.competency_ids IS DISTINCT FROM OLD.competency_ids
           OR NEW.tags IS DISTINCT FROM OLD.tags
           OR NEW.content_hash IS DISTINCT FROM OLD.content_hash
           OR NEW.bank_id IS DISTINCT FROM OLD.bank_id
           OR NEW.root_id IS DISTINCT FROM OLD.root_id
           OR NEW.version IS DISTINCT FROM OLD.version
           OR NEW.company_id IS DISTINCT FROM OLD.company_id THEN
            RAISE EXCEPTION 'bank question % is % and its content is fixed', OLD.id, OLD.status;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""
BANK_QUESTIONS_LIFECYCLE_HOOK = """
CREATE TRIGGER bank_questions_lifecycle
    BEFORE INSERT OR UPDATE OR DELETE ON bank_questions
    FOR EACH ROW EXECUTE FUNCTION bank_questions_lifecycle()
"""

BANK_QUESTION_EVENTS_APPEND_ONLY = """
CREATE OR REPLACE FUNCTION bank_question_events_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF NOT EXISTS (SELECT 1 FROM bank_questions q WHERE q.id = OLD.bank_question_id) THEN
            RETURN OLD;  -- cascade: the question (or its bank) is gone
        END IF;
    END IF;
    RAISE EXCEPTION 'bank_question_events is append-only';
END;
$$ LANGUAGE plpgsql;
"""
BANK_QUESTION_EVENTS_HOOK = """
CREATE TRIGGER bank_question_events_append_only
    BEFORE UPDATE OR DELETE ON bank_question_events
    FOR EACH ROW EXECUTE FUNCTION bank_question_events_append_only()
"""

# ---------------------------------------------------------------------------
# T2 — a published (or taken) round's sections and questions are fixed
# ---------------------------------------------------------------------------
EXAM_ROUND_CONTENT_FROZEN = """
CREATE OR REPLACE FUNCTION exam_round_content_frozen() RETURNS trigger AS $$
DECLARE
    rid uuid;
    rid2 uuid;
    r RECORD;
    bq_company uuid;
    bq_kind text;
    bq_status text;
BEGIN
    IF TG_TABLE_NAME = 'exam_sections' THEN
        rid := COALESCE(NEW.round_id, OLD.round_id);
    ELSE
        SELECT s.round_id INTO rid FROM exam_sections s WHERE s.id = COALESCE(NEW.section_id, OLD.section_id);
        IF TG_OP = 'UPDATE' AND NEW.section_id IS DISTINCT FROM OLD.section_id THEN
            SELECT s.round_id INTO rid2 FROM exam_sections s WHERE s.id = OLD.section_id;
        END IF;
    END IF;

    IF rid IS NULL AND rid2 IS NULL THEN
        RETURN COALESCE(NEW, OLD);  -- the parent section/round is already gone: a cascade
    END IF;

    FOR r IN SELECT er.id, er.status FROM exam_rounds er WHERE er.id = ANY(ARRAY[rid, rid2]) LOOP
        IF r.status = 'published' THEN
            RAISE EXCEPTION
                'exam round % is published; its content is fixed — unpublish it while nobody '
                'has taken it, or duplicate it', r.id;
        END IF;
        IF EXISTS (SELECT 1 FROM exam_attempts a WHERE a.round_id = r.id AND a.deleted_at IS NULL) THEN
            RAISE EXCEPTION 'exam round % has been taken; its content is fixed', r.id;
        END IF;
    END LOOP;

    -- NEW is a generic RECORD in a trigger function shared by three tables, and
    -- Postgres does not guarantee left-to-right evaluation of AND — so
    -- `TG_TABLE_NAME IN (...) AND NEW.source_bank_question_id IS NOT NULL` as
    -- ONE condition can still evaluate the field access for an exam_sections
    -- row (which has no such column) and raise "record has no field ...".
    -- Nested IFs are separate statements, so the outer one is fully resolved
    -- — and the table excluded — before the inner one ever names the field.
    IF TG_TABLE_NAME IN ('exam_questions', 'coding_questions') AND TG_OP = 'INSERT' THEN
        IF NEW.source_bank_question_id IS NOT NULL THEN
            SELECT company_id, kind, status INTO bq_company, bq_kind, bq_status
              FROM bank_questions WHERE id = NEW.source_bank_question_id;
            IF bq_company IS NULL THEN
                RAISE EXCEPTION 'bank question % not found', NEW.source_bank_question_id;
            END IF;
            IF bq_company <> NEW.company_id THEN
                RAISE EXCEPTION
                    'bank question % belongs to another company', NEW.source_bank_question_id;
            END IF;
            IF bq_status <> 'approved' THEN
                RAISE EXCEPTION 'bank question % is not approved and cannot be copied into an exam',
                    NEW.source_bank_question_id;
            END IF;
            IF (TG_TABLE_NAME = 'exam_questions' AND bq_kind <> 'mcq')
               OR (TG_TABLE_NAME = 'coding_questions' AND bq_kind <> 'coding') THEN
                RAISE EXCEPTION 'bank question % is % and cannot become a % row',
                    NEW.source_bank_question_id, bq_kind, TG_TABLE_NAME;
            END IF;
        END IF;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;
"""

# ---------------------------------------------------------------------------
# T3 — a published (or taken) round's own settings are fixed
# ---------------------------------------------------------------------------
EXAM_ROUNDS_FROZEN = """
CREATE OR REPLACE FUNCTION exam_rounds_frozen() RETURNS trigger AS $$
DECLARE
    taken boolean;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM exam_attempts a WHERE a.round_id = OLD.id AND a.deleted_at IS NULL
    ) INTO taken;

    IF TG_OP = 'DELETE' THEN
        IF (OLD.status = 'published' OR taken)
           AND EXISTS (SELECT 1 FROM exams x WHERE x.id = OLD.exam_id) THEN
            RAISE EXCEPTION 'exam round % is % and cannot be deleted', OLD.id,
                CASE WHEN taken THEN 'taken' ELSE 'published' END;
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status = 'published' AND NEW.status = 'draft' AND taken THEN
        RAISE EXCEPTION 'exam round % has been taken; it cannot be unpublished', OLD.id;
    END IF;

    IF OLD.status = 'published' OR taken THEN
        IF NEW.pass_threshold IS DISTINCT FROM OLD.pass_threshold
           OR NEW.time_limit_seconds IS DISTINCT FROM OLD.time_limit_seconds
           OR NEW.advances_to_interview IS DISTINCT FROM OLD.advances_to_interview
           OR NEW.exam_id IS DISTINCT FROM OLD.exam_id
           OR NEW.company_id IS DISTINCT FROM OLD.company_id
           OR NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
            RAISE EXCEPTION
                'exam round % is published or has been taken; its settings are fixed '
                '(duplicate the round to make changes)', OLD.id;
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

TRIGGER_HOOKS_UP = (
    ("exam_round_content_frozen", "exam_sections",
     "BEFORE INSERT OR UPDATE OR DELETE"),
    ("exam_round_content_frozen", "exam_questions",
     "BEFORE INSERT OR UPDATE OR DELETE"),
    ("exam_round_content_frozen", "coding_questions",
     "BEFORE INSERT OR UPDATE OR DELETE"),
)


def upgrade() -> None:
    op.create_table(
        "question_banks",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        _ts("created_at", nullable=False), _ts("updated_at", nullable=False), _ts("archived_at"),
        sa.PrimaryKeyConstraint("id", name="pk_question_banks"),
        sa.UniqueConstraint("id", "company_id", name="uq_question_banks_id_company"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"],
                                name="fk_question_banks_company", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name="fk_question_banks_created_by", ondelete="SET NULL"),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 120", name="ck_question_banks_name"),
        sa.CheckConstraint("description IS NULL OR char_length(description) <= 1000",
                           name="ck_question_banks_description"),
    )
    op.alter_column("question_banks", "created_at", server_default=sa.text("now()"))
    op.alter_column("question_banks", "updated_at", server_default=sa.text("now()"))
    op.create_index(
        "uq_question_banks_company_name", "question_banks",
        ["company_id", sa.text("lower(name)")], unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )

    op.create_table(
        "bank_questions",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("bank_id", sa.Uuid(), nullable=False),
        sa.Column("root_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("correct_index", sa.SmallInteger(), nullable=True),
        sa.Column("starter_code", sa.Text(), nullable=True),
        sa.Column("reference_solution", sa.Text(), nullable=True),
        sa.Column("allowed_languages", postgresql.JSONB(), nullable=True),
        sa.Column("test_cases", postgresql.JSONB(), nullable=True),
        sa.Column("time_limit_ms", sa.Integer(), nullable=True),
        sa.Column("difficulty", sa.Text(), nullable=False),
        sa.Column("language", sa.Text(), nullable=False, server_default="en"),
        sa.Column("competencies", postgresql.JSONB(), nullable=True),
        sa.Column("competency_ids", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("tags", sa.ARRAY(sa.Text()), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="draft"),
        sa.Column("origin", sa.Text(), nullable=False, server_default="authored"),
        sa.Column("content_hash", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("submitted_by_user_id", sa.Uuid(), nullable=True),
        _ts("submitted_at"),
        sa.Column("reviewed_by_user_id", sa.Uuid(), nullable=True),
        _ts("reviewed_at"),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.Column("retired_by_user_id", sa.Uuid(), nullable=True),
        _ts("retired_at"),
        _ts("created_at", nullable=False), _ts("updated_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_bank_questions"),
        sa.UniqueConstraint("id", "company_id", name="uq_bank_questions_id_company"),
        sa.UniqueConstraint("root_id", "version", name="uq_bank_questions_root_version"),
        sa.ForeignKeyConstraint(
            ["bank_id", "company_id"], ["question_banks.id", "question_banks.company_id"],
            name="fk_bank_questions_bank", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["root_id", "company_id"], ["bank_questions.id", "bank_questions.company_id"],
            name="fk_bank_questions_root", deferrable=True, initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"],
                                name="fk_bank_questions_created_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["submitted_by_user_id"], ["users.id"],
                                name="fk_bank_questions_submitted_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"],
                                name="fk_bank_questions_reviewed_by", ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["retired_by_user_id"], ["users.id"],
                                name="fk_bank_questions_retired_by", ondelete="SET NULL"),
        sa.CheckConstraint(f"kind IN {BANK_QUESTION_KINDS}", name="ck_bank_questions_kind"),
        sa.CheckConstraint(f"status IN {BANK_QUESTION_STATUSES}", name="ck_bank_questions_status"),
        sa.CheckConstraint(f"origin IN {BANK_QUESTION_ORIGINS}", name="ck_bank_questions_origin"),
        sa.CheckConstraint(f"difficulty IN {BANK_QUESTION_DIFFICULTIES}",
                           name="ck_bank_questions_difficulty"),
        sa.CheckConstraint(f"language IN {BANK_QUESTION_LANGUAGES}", name="ck_bank_questions_language"),
        sa.CheckConstraint("char_length(prompt) BETWEEN 1 AND 20000", name="ck_bank_questions_prompt"),
        sa.CheckConstraint("points >= 1", name="ck_bank_questions_points"),
        sa.CheckConstraint("version >= 1", name="ck_bank_questions_version"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$'", name="ck_bank_questions_content_hash"),
        sa.CheckConstraint("review_note IS NULL OR char_length(review_note) <= 1000",
                           name="ck_bank_questions_review_note"),
        sa.CheckConstraint(
            "competencies IS NULL OR jsonb_array_length(competencies) <= 8",
            name="ck_bank_questions_competencies_count",
        ),
        sa.CheckConstraint(
            "kind <> 'mcq' OR (options IS NOT NULL AND jsonb_array_length(options) BETWEEN 2 AND 6"
            " AND correct_index IS NOT NULL AND correct_index >= 0"
            " AND correct_index < jsonb_array_length(options))",
            name="ck_bank_questions_mcq_shape",
        ),
        sa.CheckConstraint(
            "kind <> 'coding' OR (test_cases IS NOT NULL AND jsonb_array_length(test_cases) >= 1"
            " AND allowed_languages IS NOT NULL AND jsonb_array_length(allowed_languages) >= 1"
            " AND time_limit_ms IS NOT NULL AND time_limit_ms >= 100)",
            name="ck_bank_questions_coding_shape",
        ),
    )
    op.alter_column("bank_questions", "created_at", server_default=sa.text("now()"))
    op.alter_column("bank_questions", "updated_at", server_default=sa.text("now()"))
    op.create_index(
        "uq_bank_questions_one_approved_per_lineage", "bank_questions", ["root_id"], unique=True,
        postgresql_where=sa.text("status = 'approved'"),
    )
    op.create_index("ix_bank_questions_company_bank_status", "bank_questions",
                    ["company_id", "bank_id", "status"])
    op.create_index("ix_bank_questions_company_content_hash", "bank_questions",
                    ["company_id", "content_hash"])
    op.create_index("ix_bank_questions_competency_ids", "bank_questions", ["competency_ids"],
                    postgresql_using="gin")
    op.create_index("ix_bank_questions_tags", "bank_questions", ["tags"], postgresql_using="gin")

    op.create_table(
        "bank_question_events",
        sa.Column("id", sa.Uuid(), nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("bank_question_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(), nullable=True),
        sa.Column("details", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        _ts("created_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_bank_question_events"),
        sa.ForeignKeyConstraint(
            ["bank_question_id", "company_id"], ["bank_questions.id", "bank_questions.company_id"],
            name="fk_bank_question_events_question", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"],
                                name="fk_bank_question_events_actor", ondelete="SET NULL"),
        sa.CheckConstraint(f"action IN {BANK_QUESTION_EVENTS}", name="ck_bank_question_events_action"),
    )
    op.alter_column("bank_question_events", "created_at", server_default=sa.text("now()"))
    op.create_index("ix_bank_question_events_question", "bank_question_events",
                    ["bank_question_id", "created_at"])

    # Provenance columns on the two exam-question tables. Nullable, so every
    # existing row (and the whole candidate take/grading path) is untouched.
    for table in ("exam_questions", "coding_questions"):
        op.add_column(table, sa.Column("source_bank_question_id", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("source_bank_root_id", sa.Uuid(), nullable=True))
        op.add_column(table, sa.Column("source_bank_version", sa.SmallInteger(), nullable=True))
        op.create_foreign_key(
            f"fk_{table}_source_bank_question", table, "bank_questions",
            ["source_bank_question_id", "company_id"], ["id", "company_id"], ondelete="NO ACTION",
        )
        op.create_index(
            f"uq_{table}_exam_source_root", table, ["exam_id", "source_bank_root_id"], unique=True,
            postgresql_where=sa.text("deleted_at IS NULL AND source_bank_root_id IS NOT NULL"),
        )

    for sql in (
        BANK_QUESTIONS_LIFECYCLE, BANK_QUESTIONS_LIFECYCLE_HOOK,
        BANK_QUESTION_EVENTS_APPEND_ONLY, BANK_QUESTION_EVENTS_HOOK,
        EXAM_ROUND_CONTENT_FROZEN,
    ):
        op.execute(sql)
    for _fn, table, when in TRIGGER_HOOKS_UP:
        op.execute(
            f"CREATE TRIGGER exam_round_content_frozen {when} ON {table}"
            " FOR EACH ROW EXECUTE FUNCTION exam_round_content_frozen()"
        )
    op.execute(EXAM_ROUNDS_FROZEN)
    op.execute(
        "CREATE TRIGGER exam_rounds_frozen BEFORE UPDATE OR DELETE ON exam_rounds"
        " FOR EACH ROW EXECUTE FUNCTION exam_rounds_frozen()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS exam_rounds_frozen ON exam_rounds")
    op.execute("DROP FUNCTION IF EXISTS exam_rounds_frozen()")
    for _fn, table, _when in TRIGGER_HOOKS_UP:
        op.execute(f"DROP TRIGGER IF EXISTS exam_round_content_frozen ON {table}")
    op.execute("DROP FUNCTION IF EXISTS exam_round_content_frozen()")
    op.execute("DROP TRIGGER IF EXISTS bank_question_events_append_only ON bank_question_events")
    op.execute("DROP FUNCTION IF EXISTS bank_question_events_append_only()")
    op.execute("DROP TRIGGER IF EXISTS bank_questions_lifecycle ON bank_questions")
    op.execute("DROP FUNCTION IF EXISTS bank_questions_lifecycle()")

    for table in ("exam_questions", "coding_questions"):
        op.drop_index(f"uq_{table}_exam_source_root", table_name=table)
        op.drop_constraint(f"fk_{table}_source_bank_question", table, type_="foreignkey")
        op.drop_column(table, "source_bank_version")
        op.drop_column(table, "source_bank_root_id")
        op.drop_column(table, "source_bank_question_id")

    op.drop_index("ix_bank_question_events_question", table_name="bank_question_events")
    op.drop_table("bank_question_events")

    op.drop_index("ix_bank_questions_tags", table_name="bank_questions")
    op.drop_index("ix_bank_questions_competency_ids", table_name="bank_questions")
    op.drop_index("ix_bank_questions_company_content_hash", table_name="bank_questions")
    op.drop_index("ix_bank_questions_company_bank_status", table_name="bank_questions")
    op.drop_index("uq_bank_questions_one_approved_per_lineage", table_name="bank_questions")
    op.drop_table("bank_questions")

    op.drop_index("uq_question_banks_company_name", table_name="question_banks")
    op.drop_table("question_banks")
